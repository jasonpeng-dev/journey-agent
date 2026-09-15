"""Validated provider boundary for generic goal selection and planning."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from time import perf_counter
from typing import Any, Literal, Protocol
from uuid import uuid4

import httpx
import structlog
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from app.core.config import Settings
from app.domain.action_invocation import ActionInvocationBinding
from app.domain.formal_goal import AdHocGoalRequirementCandidateV2
from app.domain.scenario_v2 import StrictScalar

log = structlog.get_logger(__name__)


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoalSelectionRequest(ProviderModel):
    goal: str
    objective_candidates: tuple[dict[str, object], ...]


class GoalSelection(ProviderModel):
    status: Literal["SELECTED", "NEEDS_CLARIFICATION", "UNSUPPORTED"] = "SELECTED"
    objective_keys: tuple[str, ...] = ()
    clarification_prompt: str | None = None


GoalMatchSemantics = Literal[
    "EXACT_OR_AUTHORED",
    "SEMANTIC_EQUIVALENT",
    "RELATED_ONLY",
]

GoalRoleProvenance = Literal[
    "EXPLICIT_USER_MENTION",
    "DETERMINISTIC_EXACT",
    "SEMANTIC_ROLE_EVIDENCE",
    "INFERRED",
    "NOT_SPECIFIED",
]


class DynamicGoalCandidateReference(ProviderModel):
    """One public Scenario definition that Stage 1 may ground.

    ``match_semantics`` is transient evidence about whether this is the same
    referent as the player's wording.  It is intentionally separate from
    ``provenance`` (which describes how the candidate entered the pipeline).
    """

    ref_type: Literal["NODE", "REGION", "RESOURCE", "DERIVED_STATE", "ACTION", "ACTOR"]
    key: StrictStr = Field(min_length=1, max_length=160)
    provenance: Literal["EXACT_USER_MENTION", "TOPOLOGY_ENRICHED", "LLM_SUPPLEMENTED", "OTHER"] = (
        "LLM_SUPPLEMENTED"
    )
    # Optional during the compatibility rollout.  When present it says
    # whether this identity is the same referent as the player's mention;
    # RELATED_ONLY references are contextual hints and must never be bound as
    # a Goal role.
    match_semantics: GoalMatchSemantics | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class DynamicGoalSemanticFamilyEvidence(ProviderModel):
    """Backend-computed advisory evidence for the Family routing call.

    The provider may use this evidence to resolve a semantic disagreement, but
    it never owns the family decision.  Keeping the field typed and explicit
    also makes the operation/state equivalence check auditable without leaking
    runtime state into the provider contract.
    """

    operation_expressed: StrictBool = False
    state_equivalent_available: StrictBool | None = None


class DynamicGoalSemanticActionEvidence(ProviderModel):
    """Backend-computed advisory Action evidence from Semantic Grounding.

    This transient hint keeps the Stage 1 semantic Action visible to the
    Action Router without transferring Action-selection authority out of that
    stage.  The resolver only constructs it for a public, non-contextual
    Action identity.
    """

    action_key: StrictStr = Field(min_length=1, max_length=100)
    surface: StrictStr | None = Field(default=None, max_length=400)
    match_semantics: GoalMatchSemantics | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )


class DynamicGoalFamilyRoutingRequest(ProviderModel):
    """Family-only request over raw intent and deterministic public evidence."""

    goal: str = Field(min_length=1, max_length=4000)
    deterministic_candidate_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    deterministic_ambiguous_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    semantic_candidate_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    explicit_role_evidence: dict[str, object] = Field(default_factory=dict)
    semantic_family_evidence: DynamicGoalSemanticFamilyEvidence = Field(
        default_factory=DynamicGoalSemanticFamilyEvidence
    )
    recovery_attempt: StrictInt = Field(default=0, ge=0, le=1)
    recovery_feedback: tuple[dict[str, object], ...] = ()


class DynamicGoalFamilyRouting(ProviderModel):
    """Frozen semantic family; it deliberately cannot select an Action."""

    family: Literal["STATE", "OPERATION", "AMBIGUOUS"]
    clarification_prompt: StrictStr | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_family_routing(self) -> DynamicGoalFamilyRouting:
        if self.family == "AMBIGUOUS" and self.clarification_prompt is None:
            raise ValueError("AMBIGUOUS family routing requires clarification_prompt")
        if self.family != "AMBIGUOUS" and self.clarification_prompt is not None:
            raise ValueError("Frozen family routing cannot carry clarification_prompt")
        return self


class DynamicGoalActionRoutingRequest(ProviderModel):
    """Action-only request used after the OPERATION family is frozen."""

    goal: str = Field(min_length=1, max_length=4000)
    frozen_family: Literal["OPERATION"] = "OPERATION"
    action_catalog: tuple[dict[str, object], ...]
    relevant_public_entities: tuple[dict[str, object], ...] = ()
    public_topology: dict[str, object] = Field(default_factory=dict)
    deterministic_candidate_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    deterministic_ambiguous_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    semantic_candidate_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    explicit_role_evidence: dict[str, object] = Field(default_factory=dict)
    semantic_action_evidence: DynamicGoalSemanticActionEvidence | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    recovery_attempt: StrictInt = Field(default=0, ge=0, le=1)
    recovery_feedback: tuple[dict[str, object], ...] = ()


ActionNoMatchReason = Literal["NO_SEMANTIC_ACTION", "SEMANTIC_CONFLICT"]


class DynamicGoalActionRouting(ProviderModel):
    """Closed Action selection that cannot reopen the family decision."""

    action_match: Literal["MATCHED", "AMBIGUOUS", "NO_MATCH"]
    action_key: StrictStr | None = Field(default=None, max_length=100)
    candidate_keys: tuple[StrictStr, ...] = ()
    clarification_prompt: StrictStr | None = Field(default=None, max_length=1000)
    no_match_reason: ActionNoMatchReason | None = None

    @model_validator(mode="after")
    def validate_action_routing(self) -> DynamicGoalActionRouting:
        if self.action_match == "MATCHED":
            if self.action_key is None or self.candidate_keys or self.no_match_reason is not None:
                raise ValueError("MATCHED routing requires only action_key")
        elif self.action_match == "AMBIGUOUS":
            if (
                self.action_key is not None
                or len(self.candidate_keys) < 2
                or self.no_match_reason is not None
            ):
                raise ValueError("AMBIGUOUS routing requires at least two candidate_keys")
        elif self.action_key is not None or self.candidate_keys or self.no_match_reason is None:
            raise ValueError("NO_MATCH routing requires no Action candidates and a typed reason")
        if len(set(self.candidate_keys)) != len(self.candidate_keys):
            raise ValueError("Routing candidate_keys must be unique")
        return self


class DynamicGoalSemanticRoutingRequest(ProviderModel):
    """Small, closed routing request over public semantic candidates."""

    goal: str = Field(min_length=1, max_length=4000)
    action_catalog: tuple[dict[str, object], ...]
    state_catalog: tuple[dict[str, object], ...] = ()
    recovery_attempt: StrictInt = Field(default=0, ge=0, le=1)
    recovery_feedback: tuple[dict[str, object], ...] = ()


class DynamicGoalSemanticRouting(ProviderModel):
    """One physical call with logically ordered Family then Action routing."""

    family: Literal["STATE", "OPERATION", "AMBIGUOUS"]
    action_match: Literal["MATCHED", "AMBIGUOUS", "NO_MATCH"] | None = None
    action_key: StrictStr | None = Field(default=None, max_length=100)
    candidate_keys: tuple[StrictStr, ...] = ()
    clarification_prompt: StrictStr | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_routing(self) -> DynamicGoalSemanticRouting:
        if self.family != "OPERATION":
            if self.action_match is not None or self.action_key is not None or self.candidate_keys:
                raise ValueError("STATE/AMBIGUOUS routing cannot carry Action selection")
            return self
        if self.action_match is None:
            raise ValueError("OPERATION routing requires action_match")
        if self.action_match == "MATCHED":
            if self.action_key is None or self.candidate_keys:
                raise ValueError("MATCHED routing requires only action_key")
        elif self.action_match == "AMBIGUOUS":
            if self.action_key is not None or len(self.candidate_keys) < 2:
                raise ValueError("AMBIGUOUS routing requires at least two candidate_keys")
        elif self.action_key is not None or self.candidate_keys:
            raise ValueError("NO_MATCH routing cannot carry Action candidates")
        if len(set(self.candidate_keys)) != len(self.candidate_keys):
            raise ValueError("Routing candidate_keys must be unique")
        return self


class GoalFamilyMatchRequest(ProviderModel):
    goal: str = Field(min_length=1, max_length=4000)


class GoalFamilyMatch(ProviderModel):
    """Stage 0 output.  No entity or requirement fields exist by design."""

    family: Literal["STATE", "OPERATION", "AMBIGUOUS"]
    clarification_prompt: StrictStr | None = Field(default=None, max_length=1000)


class DynamicGoalActionMatchRequest(ProviderModel):
    goal: str = Field(min_length=1, max_length=4000)
    frozen_family: Literal["OPERATION"] = "OPERATION"
    action_catalog: tuple[dict[str, object], ...]
    deterministic_candidate_refs: tuple[DynamicGoalCandidateReference, ...] = ()


class DynamicGoalActionMatch(ProviderModel):
    frozen_family: Literal["OPERATION"] = "OPERATION"
    status: Literal["GROUNDED", "UNRESOLVED", "UNSUPPORTED"]
    action_key: StrictStr | None = Field(default=None, max_length=100)
    clarification_prompt: StrictStr | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_action_match(self) -> DynamicGoalActionMatch:
        if self.status == "GROUNDED" and not self.action_key:
            raise ValueError("A grounded Action match requires action_key")
        if self.status != "GROUNDED" and self.action_key is not None:
            raise ValueError("An unresolved Action match cannot carry action_key")
        return self


OperationSlotExpectedType = Literal[
    "STRING", "ENUM", "INTEGER", "BOOLEAN", "NODE", "REGION", "FACILITY", "RESOURCE", "ACTOR"
]


class OperationContractSlot(ProviderModel):
    """One Action-contract-owned semantic slot returned by operation grounding."""

    slot_key: StrictStr = Field(min_length=1, max_length=100)
    expected_type: OperationSlotExpectedType
    status: Literal["GROUNDED", "UNRESOLVED", "NOT_SPECIFIED"]
    ref_type: Literal["NODE", "REGION", "RESOURCE", "ACTOR"] | None = None
    key: StrictStr | None = Field(default=None, max_length=160)
    value: StrictScalar | None = None
    surface: StrictStr | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def validate_slot_state(self) -> OperationContractSlot:
        has_ref = self.ref_type is not None or self.key is not None
        has_value = self.value is not None
        if self.status == "GROUNDED":
            if self.expected_type in {"NODE", "REGION", "FACILITY", "RESOURCE", "ACTOR"}:
                if self.key is None or self.ref_type is None or has_value:
                    raise ValueError("A grounded reference slot requires ref_type/key only")
            elif has_ref or not has_value:
                raise ValueError("A grounded scalar slot requires value only")
        elif has_ref or has_value:
            raise ValueError("An unresolved or unspecified slot cannot carry a value")
        return self


class OperationIntentDraft(ProviderModel):
    frozen_family: Literal["OPERATION"] = "OPERATION"
    action_key: StrictStr = Field(min_length=1, max_length=100)
    actor: OperationContractSlot
    target: OperationContractSlot
    bindings: tuple[OperationContractSlot, ...] = ()
    parameters: tuple[OperationContractSlot, ...] = ()


class DynamicGoalOperationGroundingRequest(ProviderModel):
    goal: str = Field(min_length=1, max_length=4000)
    frozen_family: Literal["OPERATION"] = "OPERATION"
    action_key: StrictStr = Field(min_length=1, max_length=100)
    action_contract: dict[str, object]
    public_references: tuple[dict[str, object], ...]
    public_topology: dict[str, object] = Field(default_factory=dict)
    deterministic_candidate_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    deterministic_ambiguous_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    semantic_candidate_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    explicit_role_evidence: dict[str, object] = Field(default_factory=dict)
    recovery_attempt: StrictInt = Field(default=0, ge=0, le=1)
    recovery_feedback: tuple[dict[str, object], ...] = ()


class DynamicGoalOperationGrounding(ProviderModel):
    frozen_family: Literal["OPERATION"] = "OPERATION"
    status: Literal["RESOLVED", "NEEDS_CLARIFICATION", "UNSUPPORTED"]
    intent: OperationIntentDraft | None = None
    supplementary_candidate_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    clarification_prompt: StrictStr | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_operation_grounding(self) -> DynamicGoalOperationGrounding:
        if self.status == "RESOLVED" and self.intent is None:
            raise ValueError("Resolved operation grounding requires intent")
        if self.status != "RESOLVED" and self.intent is not None:
            raise ValueError("Unresolved operation grounding cannot carry intent")
        return self


class DynamicGoalMentionSlot(ProviderModel):
    """Transient typed provenance for one explicit Goal semantic slot.

    This is a resolver/provider DTO only.  It is never persisted as part of a
    Formal Goal or Scenario definition.  ``NOT_SPECIFIED`` means the player
    did not express the slot; it is intentionally different from an explicit
    value that still needs semantic grounding.  ``match_semantics`` and
    ``provenance`` make that distinction auditable without freezing Planner
    choices into the Goal.
    """

    status: Literal["GROUNDED", "UNRESOLVED", "NOT_SPECIFIED"]
    ref_type: Literal["NODE", "REGION", "RESOURCE", "ACTION", "ACTOR"] | None = None
    key: StrictStr | None = Field(default=None, max_length=160)
    value: None = None
    surface: StrictStr | None = Field(default=None, max_length=400)
    # These fields are transient grounding provenance only.  They are omitted
    # from legacy wire payloads when unset and are never persisted in a
    # FormalGoal.
    match_semantics: GoalMatchSemantics | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    provenance: GoalRoleProvenance | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_mention_shape(self) -> DynamicGoalMentionSlot:
        has_identity = self.ref_type is not None or self.key is not None
        if self.status == "GROUNDED":
            if not has_identity or self.ref_type is None or self.key is None:
                raise ValueError("A grounded reference mention requires ref_type and key")
        elif self.key is not None:
            raise ValueError("An unresolved or unspecified reference cannot carry a key")
        return self


def _not_specified_dynamic_goal_slot() -> DynamicGoalMentionSlot:
    return DynamicGoalMentionSlot(status="NOT_SPECIFIED")


class DynamicGoalScalarMentionSlot(ProviderModel):
    """Closed scalar counterpart to a public-reference mention slot."""

    status: Literal["GROUNDED", "UNRESOLVED", "NOT_SPECIFIED"]
    ref_type: None = None
    key: None = None
    value: StrictScalar | None = None
    surface: StrictStr | None = Field(default=None, max_length=400)
    provenance: GoalRoleProvenance | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_scalar_shape(self) -> DynamicGoalScalarMentionSlot:
        if self.status == "GROUNDED" and self.value is None:
            raise ValueError("A grounded scalar mention requires a JSON scalar value")
        if self.status != "GROUNDED" and self.value is not None:
            raise ValueError("An unresolved or unspecified scalar cannot carry a value")
        return self


def _not_specified_dynamic_goal_scalar_slot() -> DynamicGoalScalarMentionSlot:
    return DynamicGoalScalarMentionSlot(status="NOT_SPECIFIED")


class DynamicGoalIntentDraft(ProviderModel):
    """Transient, typed semantic provenance returned by Stage 1."""

    intent_kind: Literal["STATE", "OPERATION"]
    action: DynamicGoalMentionSlot = Field(default_factory=_not_specified_dynamic_goal_slot)
    actor: DynamicGoalMentionSlot = Field(default_factory=_not_specified_dynamic_goal_slot)
    source: DynamicGoalMentionSlot = Field(default_factory=_not_specified_dynamic_goal_slot)
    target: DynamicGoalMentionSlot = Field(default_factory=_not_specified_dynamic_goal_slot)
    resource: DynamicGoalMentionSlot = Field(default_factory=_not_specified_dynamic_goal_slot)
    amount: DynamicGoalScalarMentionSlot = Field(
        default_factory=_not_specified_dynamic_goal_scalar_slot
    )


class DynamicGoalEntityGroundingRequest(ProviderModel):
    """Knowledge-safe input for the bounded public Entity Grounding call."""

    goal: str = Field(min_length=1, max_length=4000)
    public_catalog: dict[str, object] = Field(default_factory=dict)
    deterministic_candidate_refs: tuple[DynamicGoalCandidateReference, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    deterministic_ambiguous_refs: tuple[DynamicGoalCandidateReference, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    intent: DynamicGoalIntentDraft | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    recovery_attempt: StrictInt = Field(default=0, ge=0, le=1, exclude_if=lambda value: value == 0)
    recovery_feedback: tuple[dict[str, object], ...] = Field(
        default=(),
        max_length=4,
        exclude_if=lambda value: not value,
    )


class DynamicGoalRecoveryFeedback(ProviderModel):
    """Safe, structured schema feedback for one bounded interpretation retry."""

    requirement_index: StrictInt = Field(ge=0)
    kind: Literal["FACT", "RESOURCE_AT_LEAST", "DERIVED_STATE", "ACTION_COMPLETED"] | None = None
    issue: Literal["MISSING_REQUIRED_FIELD", "INVALID_FIELD", "INVALID_REQUIREMENT_SHAPE"]
    field: (
        Literal[
            "kind",
            "node_key",
            "fact_key",
            "accepted_values",
            "region_key",
            "resource_key",
            "minimum",
            "derived_key",
            "action_key",
            "actor_key",
            "target_key",
            "binding_constraints",
            "parameter_constraints",
            "match_mode",
            "boundary",
            "target_value",
            "<extra_field>",
        ]
        | None
    ) = None
    expected_shape: dict[str, object] = Field(default_factory=dict)
    focused_target_value: StrictStr | StrictInt | StrictBool | None = None


class DynamicGoalGroundedOperation(ProviderModel):
    """Stage 1's lossless public binding for an explicit operation Goal.

    This is a universal semantic lock, not a Scenario-specific shortcut.  It
    contains only public Action/Region/Resource identities and explicitly
    declared parameter values.  Stage 2 may validate and serialize this
    operation, but may not reinterpret it as another requirement kind, add a
    requirement, or ask for an intentionally unconstrained Actor.
    """

    action_key: StrictStr = Field(min_length=1, max_length=100)
    actor_key: StrictStr | None = Field(default=None, max_length=100)
    target_key: StrictStr | None = Field(default=None, max_length=160)
    binding_constraints: tuple[ActionInvocationBinding, ...] = ()
    parameter_constraints: dict[str, JsonValue] | None = None


class DynamicGoalEntityGrounding(ProviderModel):
    """Closed Stage 1 output containing public references and typed intent.

    ``candidate_keys`` remains a compatibility input for older deterministic
    test providers and is normalized to ``NODE`` references.  The provider
    contract itself is ``candidate_refs`` so Stage 1 can ground resources and
    public Derived States without pretending they are world Nodes.  ``intent``
    carries the Stage 1 semantic role/provenance result; it is optional at the
    model boundary so older provider fixtures can fail over to the legacy
    interpretation path during rollout.
    """

    status: Literal["RESOLVED", "NEEDS_CLARIFICATION", "UNSUPPORTED"] = "RESOLVED"
    candidate_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    candidate_keys: tuple[StrictStr, ...] = ()
    intent: DynamicGoalIntentDraft | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    clarification_prompt: str | None = None

    @model_validator(mode="after")
    def validate_grounding(self) -> DynamicGoalEntityGrounding:
        if self.candidate_keys:
            legacy_refs = tuple(
                DynamicGoalCandidateReference(ref_type="NODE", key=key)
                for key in self.candidate_keys
            )
            if self.candidate_refs and self.candidate_refs != legacy_refs:
                raise ValueError("Entity Grounding candidate_refs and candidate_keys disagree")
            if not self.candidate_refs:
                object.__setattr__(self, "candidate_refs", legacy_refs)
        refs = self.candidate_refs
        identities = tuple((item.ref_type, item.key) for item in refs)
        if len(set(identities)) != len(identities):
            raise ValueError("Entity Grounding candidate references must be unique")
        if self.status == "RESOLVED" and not refs:
            raise ValueError("A resolved Entity Grounding needs at least one candidate reference")
        if self.status != "RESOLVED" and refs:
            raise ValueError("An unresolved Entity Grounding cannot carry candidate references")
        if self.status == "NEEDS_CLARIFICATION" and not (
            self.clarification_prompt and self.clarification_prompt.strip()
        ):
            raise ValueError("Entity Grounding clarification needs a prompt")
        return self


class DynamicGoalInterpretationRequest(ProviderModel):
    """Knowledge-safe input for the AD_HOC_DYNAMIC Goal interpreter."""

    goal: str = Field(min_length=1, max_length=4000)
    ontology: dict[str, object] = Field(default_factory=dict)
    grounded_candidate_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    # Kept for compatibility with existing provider integrations.  It is the
    # NODE subset of grounded_candidate_refs, never the complete Stage 1 scope.
    grounded_entity_keys: tuple[str, ...] = ()
    intent: DynamicGoalIntentDraft | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    grounded_operation: DynamicGoalGroundedOperation | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    frozen_family: Literal["STATE"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    recovery_attempt: int = Field(default=0, ge=0, le=1)
    recovery_feedback: tuple[DynamicGoalRecoveryFeedback, ...] = Field(default=(), max_length=4)


class DynamicGoalInterpretation(ProviderModel):
    """Strict provider output for a flat typed Dynamic Goal candidate set.

    The nested candidate model is intentionally closed: the provider cannot
    return a backend identity, authored prerequisite, knowledge gate, or
    hidden completion semantic.
    """

    status: Literal["RESOLVED", "NEEDS_CLARIFICATION", "UNSUPPORTED"] = "RESOLVED"
    requirements: tuple[AdHocGoalRequirementCandidateV2, ...] = ()
    clarification_prompt: str | None = None

    @model_validator(mode="after")
    def validate_interpretation(self) -> DynamicGoalInterpretation:
        if self.status == "RESOLVED" and not self.requirements:
            raise ValueError("A resolved Dynamic Goal needs at least one requirement")
        if self.status != "RESOLVED" and self.requirements:
            raise ValueError("An unresolved Dynamic Goal cannot carry requirements")
        if self.status == "NEEDS_CLARIFICATION" and not (
            self.clarification_prompt and self.clarification_prompt.strip()
        ):
            raise ValueError("Dynamic Goal clarification needs a prompt")
        return self


class PlanningContext(ProviderModel):
    """The entity-once, knowledge-safe Planner input for Checkpoint B.

    The fields intentionally use JSON-shaped mappings.  Scenario authors own
    the vocabulary in the immutable Version, while the provider boundary owns
    only the shape and the hard/soft distinction.  Keeping the values generic
    also lets the same contract serve Starfire, Medical, and future V2
    scenarios without a scenario-specific DTO.
    """

    goal: dict[str, object] = Field(default_factory=dict)
    current_knowledge: dict[str, object] = Field(default_factory=dict)
    relevant_actions: tuple[dict[str, object], ...] = ()
    relevant_actors: tuple[dict[str, object], ...] = ()
    relevant_targets: tuple[dict[str, object], ...] = ()
    operation_goal: OperationGoalProjection | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    previous_execution_context: dict[str, object] = Field(default_factory=dict)
    scenario_planning_hints: dict[str, object] = Field(default_factory=dict)

    def compact_dump(self) -> dict[str, object]:
        """Return the lossless provider projection of this context."""

        payload = self.model_dump(mode="json")
        current_knowledge = payload.get("current_knowledge")
        if isinstance(current_knowledge, dict):
            # The canonical PlannerInput has already consumed this safe
            # adapter from SharedKnowledgeProjection.  Avoid duplicating it
            # in the compatibility payload; it is not a second authority.
            current_knowledge.pop("known_target_action_requirements", None)
        if not payload.get("previous_execution_context"):
            payload.pop("previous_execution_context", None)
        return payload


class PlannerActorState(ProviderModel):
    """Canonical current Knowledge about one Actor."""

    actor_key: str
    role_key: str
    capabilities: tuple[str, ...] = ()
    allowed_action_keys: tuple[str, ...] = ()
    availability: str
    current_region: str | None
    command_reachability: str
    execution_state: dict[str, object] = Field(default_factory=dict)


class PlannerResourceRequirement(ProviderModel):
    """One public minimum Resource requirement for an Action contract.

    ``scope`` is intentionally JSON-shaped: Scenario authors may use an
    explicit Region or a symbolic runtime scope without making the Planner
    schema scenario-specific.  ``known_available`` is emitted only when that
    scoped amount is public Knowledge; an UNKNOWN scope never becomes zero.
    """

    resource_key: StrictStr = Field(min_length=1, max_length=160)
    scope: dict[str, object] = Field(default_factory=dict)
    minimum: StrictInt = Field(gt=0)
    known_status: Literal["KNOWN", "UNKNOWN"] | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    known_available: StrictInt | None = Field(
        default=None,
        ge=0,
        exclude_if=lambda value: value is None,
    )


class PlannerActionContract(ProviderModel):
    """One authoritative Planner-facing contract for an Action."""

    action_key: str
    executor_requirements: dict[str, object] = Field(default_factory=dict)
    target_contract: dict[str, object] = Field(default_factory=dict)
    source_relation_type_key: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    source_preconditions: tuple[dict[str, object], ...] = ()
    locality: dict[str, object] = Field(default_factory=dict)
    parameters: tuple[dict[str, object], ...] = ()
    known_preconditions: tuple[dict[str, object], ...] = ()
    resource_requirements: tuple[PlannerResourceRequirement, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    deterministic_effects: tuple[dict[str, object], ...] = ()
    knowledge_semantics: tuple[dict[str, object], ...] = ()


class PlannerTargetBinding(ProviderModel):
    """Sparse target-specific contract differences for one Action/Target."""

    action_key: str
    target_key: str
    requirements: tuple[dict[str, object], ...] = ()
    resource_requirements: tuple[PlannerResourceRequirement, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    deterministic_effects: tuple[dict[str, object], ...] = ()


class GoalDependencyProjection(ProviderModel):
    """One public, currently active dependency of the frozen Goal.

    This is a projection of the dependency closure, not a second completion
    contract.  It carries the stable typed identity and only the current
    public Knowledge needed for planning.  In particular, UNKNOWN values are
    represented by omitted scalar fields rather than guessed zero/false
    values.
    """

    dependency_id: StrictStr = Field(min_length=1, max_length=200)
    parent_dependency_id: StrictStr | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    kind: Literal["FACT", "RESOURCE_AT_LEAST", "DERIVED_STATE"]
    knowledge_status: Literal["UNKNOWN", "KNOWN"]
    satisfaction_status: Literal["UNKNOWN", "UNSATISFIED"]

    node_key: StrictStr | None = Field(default=None, exclude_if=lambda value: value is None)
    fact_key: StrictStr | None = Field(default=None, exclude_if=lambda value: value is None)
    accepted_values: tuple[StrictScalar, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )

    region_key: StrictStr | None = Field(default=None, exclude_if=lambda value: value is None)
    resource_key: StrictStr | None = Field(default=None, exclude_if=lambda value: value is None)
    minimum: StrictInt | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)
    current_known_available: StrictInt | None = Field(
        default=None,
        ge=0,
        exclude_if=lambda value: value is None,
    )
    deficit: StrictInt | None = Field(
        default=None,
        ge=0,
        exclude_if=lambda value: value is None,
    )

    derived_key: StrictStr | None = Field(default=None, exclude_if=lambda value: value is None)
    current_known_value: StrictScalar | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_projection_shape(self) -> GoalDependencyProjection:
        if self.knowledge_status == "UNKNOWN":
            if self.satisfaction_status != "UNKNOWN":
                raise ValueError("UNKNOWN Goal dependency must have UNKNOWN satisfaction")
            if any(
                value is not None
                for value in (
                    self.current_known_value,
                    self.current_known_available,
                    self.deficit,
                )
            ):
                raise ValueError("UNKNOWN Goal dependency cannot expose current values")
        elif self.satisfaction_status != "UNSATISFIED":
            raise ValueError("Known active Goal dependency must be unsatisfied")

        if self.kind == "FACT":
            if self.node_key is None or self.fact_key is None or not self.accepted_values:
                raise ValueError("FACT Goal dependency needs node/fact/accepted_values")
            if any(
                value is not None
                for value in (
                    self.region_key,
                    self.resource_key,
                    self.minimum,
                    self.current_known_available,
                    self.deficit,
                    self.derived_key,
                )
            ):
                raise ValueError("FACT Goal dependency cannot declare Resource/Derived fields")
        elif self.kind == "RESOURCE_AT_LEAST":
            if (
                self.region_key is None
                or self.resource_key is None
                or self.minimum is None
                or self.accepted_values
            ):
                raise ValueError("RESOURCE_AT_LEAST Goal dependency needs region/resource/minimum")
            if any(
                value is not None
                for value in (
                    self.node_key,
                    self.fact_key,
                    self.current_known_value,
                    self.derived_key,
                )
            ):
                raise ValueError(
                    "RESOURCE_AT_LEAST Goal dependency cannot declare Fact/Derived fields"
                )
            if self.current_known_available is not None and self.deficit is not None:
                expected_deficit = max(0, self.minimum - self.current_known_available)
                if self.deficit != expected_deficit:
                    raise ValueError(
                        "Resource Goal dependency deficit does not match public amount"
                    )
        elif self.kind == "DERIVED_STATE":
            if self.derived_key is None or not self.accepted_values:
                raise ValueError("DERIVED_STATE Goal dependency needs derived_key/accepted_values")
            if any(
                value is not None
                for value in (
                    self.node_key,
                    self.fact_key,
                    self.region_key,
                    self.resource_key,
                    self.minimum,
                    self.current_known_available,
                    self.deficit,
                )
            ):
                raise ValueError(
                    "DERIVED_STATE Goal dependency cannot declare Fact/Resource fields"
                )
        else:
            raise ValueError("Unsupported Goal dependency kind")
        return self


class OperationGoalProjection(ProviderModel):
    """Frozen ACTION_COMPLETED constraint exposed to the Planner."""

    requirement_identity: StrictStr | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    kind: Literal["ACTION_COMPLETED"] = "ACTION_COMPLETED"
    action_key: StrictStr = Field(min_length=1, max_length=100)
    actor_key: StrictStr | None = Field(default=None, max_length=100)
    target_key: StrictStr | None = Field(default=None, max_length=160)
    binding_constraints: tuple[ActionInvocationBinding, ...] = ()
    parameter_constraints: dict[str, JsonValue] | None = None
    match_mode: Literal["ONE_SUCCESSFUL_INVOCATION"] = "ONE_SUCCESSFUL_INVOCATION"
    boundary: Literal["TASK_OWNED_OPERATION"] = "TASK_OWNED_OPERATION"

    @model_validator(mode="after")
    def validate_bindings(self) -> OperationGoalProjection:
        roles = tuple(item.role for item in self.binding_constraints)
        if len(set(roles)) != len(roles):
            raise ValueError("Operation Goal binding roles must be unique")
        ordered = tuple(sorted(self.binding_constraints, key=lambda item: item.role))
        if ordered != self.binding_constraints:
            object.__setattr__(self, "binding_constraints", ordered)
        return self


class PlannerResourceSourceHint(ProviderModel):
    """Quantity-free public guidance for discovering a Resource source."""

    resource_key: StrictStr = Field(min_length=1, max_length=80)
    primary_region_key: StrictStr | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    candidate_region_keys: tuple[StrictStr, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )

    @model_validator(mode="after")
    def validate_regions(self) -> PlannerResourceSourceHint:
        if self.primary_region_key is None and not self.candidate_region_keys:
            raise ValueError("Resource source hint needs a primary or candidate Region")
        if len(set(self.candidate_region_keys)) != len(self.candidate_region_keys):
            raise ValueError("Resource source hint candidate Regions must be unique")
        if (
            self.primary_region_key is not None
            and self.primary_region_key in self.candidate_region_keys
        ):
            raise ValueError("Resource source hint primary Region cannot be a candidate Region")
        return self


class PlannerKnownWorldSlice(ProviderModel):
    """Knowledge-safe world entities selected for the current dependency closure."""

    nodes: tuple[dict[str, object], ...] = ()
    facts: dict[str, object] = Field(default_factory=dict)
    relations: tuple[dict[str, object], ...] = ()
    resources: dict[str, object] = Field(default_factory=dict)
    resource_knowledge: tuple[dict[str, object], ...] = ()
    resource_source_hints: tuple[PlannerResourceSourceHint, ...] = ()
    unknown_dependencies: tuple[dict[str, object], ...] = ()

    @model_validator(mode="after")
    def validate_unknown_dependency_ids(self) -> PlannerKnownWorldSlice:
        if any(item.get("status") != "UNKNOWN" for item in self.unknown_dependencies):
            raise ValueError("unknown_dependencies may contain only UNKNOWN dependencies")
        dependency_ids = [item.get("dependency_id") for item in self.unknown_dependencies]
        if any(not isinstance(item, str) or not item.strip() for item in dependency_ids):
            raise ValueError("Every UNKNOWN dependency needs a non-blank dependency_id")
        if len(dependency_ids) != len(set(dependency_ids)):
            raise ValueError("UNKNOWN dependency_id values must be unique")
        return self


class PlannerInput(ProviderModel):
    """Canonical schema shared by INITIAL, REPAIR, and REPLAN."""

    schema_version: Literal[2] = 2
    objective: dict[str, object] = Field(default_factory=dict)
    actors: tuple[PlannerActorState, ...] = ()
    action_contracts: tuple[PlannerActionContract, ...] = ()
    target_bindings: tuple[PlannerTargetBinding, ...] = ()
    operation_goal: OperationGoalProjection | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    active_goal_dependencies: tuple[GoalDependencyProjection, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    known_world: PlannerKnownWorldSlice = Field(default_factory=PlannerKnownWorldSlice)
    execution_context: dict[str, object] = Field(default_factory=dict)


class ContinuityStep(ProviderModel):
    """Compact public history for one step of an accepted formal plan."""

    action_key: str
    actor_key: str
    target_key: str | None = None
    purpose: str = ""
    short_actor_reason: str | None = None
    execution_status: str
    outcome_code: str | None = None
    failure_code: str | None = None
    knowledge_changes: tuple[dict[str, JsonValue], ...] = ()


class ContinuityPlan(ProviderModel):
    """A compact, public projection of one accepted formal AgentPlan."""

    plan_summary: str
    stop_reason: str
    # Defaults keep persisted/fixture projections from before the intent
    # labels were introduced readable.  New Planner responses are still
    # instructed to provide explicit labels, and PlanningContinuityBuilder
    # supplies richer plan-derived fallbacks when they are absent.
    segment_goal: StrictStr = Field(
        default="Advance the current Objective segment",
        min_length=1,
        max_length=240,
    )
    goal_link: StrictStr = Field(
        default="Re-evaluate against the frozen Formal Goal and active dependency",
        min_length=1,
        max_length=240,
    )
    continuation_intent: StrictStr = Field(
        default="Continue the unfinished mainline toward the frozen Objective",
        min_length=1,
        max_length=240,
    )
    steps: tuple[ContinuityStep, ...] = ()


class PlanningContinuity(ProviderModel):
    """Historical context for a REPLAN cycle, never authoritative state."""

    prior_plans: tuple[ContinuityPlan, ...] = ()
    latest_replan_trigger: str | None = None
    latest_new_knowledge: tuple[dict[str, JsonValue], ...] = ()


class PlanningActionCandidate(ProviderModel):
    """Deprecated compatibility view of an Actor x Action x Target binding.

    It remains available while old FakeProvider tests and persisted diagnostics
    migrate. The canonical OpenAI-compatible payload is ``PlannerInput``;
    no V2 provider call is instructed to choose candidate IDs.
    """

    candidate_id: str
    action_key: str
    action_name: str
    actor_key: str
    actor_name: str
    target_key: str
    target_name: str
    target_kind: str = "NODE"
    parameter_domain: tuple[dict[str, object], ...] = ()
    public_effects: tuple[dict[str, object], ...] = ()
    objective_relevance: tuple[dict[str, object], ...] = ()
    currently_executable: bool
    known_blockers: tuple[dict[str, object], ...] = ()
    public_prerequisites: tuple[dict[str, object], ...] = ()
    authority: dict[str, object] = Field(default_factory=dict)
    action_behavior: str = "RULE"
    action_locality: str = "NONE"


class PlanStepProposal(ProviderModel):
    step_id: str = Field(default_factory=lambda: f"step-{uuid4().hex[:12]}")
    purpose: str = ""
    action_key: str | None = None
    actor_key: str | None = None
    target_key: str | None = None
    # Action parameters are JSON-shaped because transport_resource accepts a
    # structured resources[] cargo list while legacy Actions remain scalar.
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    short_actor_reason: str | None = None
    candidate_id: str | None = None

    @model_validator(mode="after")
    def validate_binding_shape(self) -> PlanStepProposal:
        """Accept direct V1 bindings or the temporary legacy candidate shape."""

        direct = all(
            value is not None for value in (self.action_key, self.actor_key, self.target_key)
        )
        if self.candidate_id is None and not direct:
            raise ValueError("A Plan step needs Action/Actor/Target keys")
        return self


class PlanViolation(ProviderModel):
    """Canonical, knowledge-safe Validator rejection sent to REPAIR."""

    code: str = Field(min_length=1)
    failure_code: str | None = None
    dimension: str | None = None
    step_id: str | None = None
    sequence: int | None = None

    action_key: str | None = None
    actor_key: str | None = None
    target_key: str | None = None

    required: JsonValue | None = None
    actual: JsonValue | None = None
    reason_code: str | None = None
    message: str | None = None

    required_interaction_key: str | None = None
    actual_interactions: tuple[str, ...] = ()

    transport_key: str | None = None
    source_region: str | None = None
    target_region: str | None = None

    resource_key: str | None = None
    scope_region: str | None = None
    required_amount: int | None = None
    projected_known_available_amount: int | None = None
    deficit: int | None = None

    parameter_key: str | None = None
    parameter_error: str | None = None
    validation_error: str | None = None
    actual_parameters: dict[str, JsonValue] | None = None

    cascade_from_step_id: str | None = None
    blocking_condition: dict[str, JsonValue] | None = None
    known_predicate: dict[str, JsonValue] | None = None

    action_keys: tuple[str, ...] = ()
    step_ids: tuple[str, ...] = ()
    candidate_id: str | None = None
    dependency_id: str | None = None
    required_effect_types: tuple[str, ...] = ()
    missing_prior_public_requirements: tuple[dict[str, JsonValue], ...] = ()
    missing_public_requirements: tuple[dict[str, JsonValue], ...] = ()


class AntiRegressionMemoryItem(PlanViolation):
    """Historical contradiction evidence from earlier proposals in one cycle."""

    first_seen_attempt: int = Field(ge=0)
    last_seen_attempt: int = Field(ge=0)
    seen_count: int = Field(default=1, ge=1)


class PlanRequest(ProviderModel):
    call_type: Literal["INITIAL_PLAN", "REPLAN", "REPAIR"]
    goal: str = ""
    objective_keys: tuple[str, ...] = ()
    objective_scope: tuple[dict[str, object], ...] = ()
    replan_reason: str | None = None
    known_world: dict[str, object] = Field(default_factory=dict)
    actors: tuple[dict[str, object], ...] = ()
    planning_metadata: dict[str, object] = Field(default_factory=dict)
    planning_action_catalog: tuple[PlanningActionCandidate, ...] = ()
    planning_context: PlanningContext | None = None
    planner_input: PlannerInput | None = None
    planning_continuity: PlanningContinuity | None = None
    rejected_segment: dict[str, object] | None = None
    repair_attempt: int = 0
    repair_diagnostics: tuple[PlanViolation, ...] = ()
    anti_regression_memory: tuple[AntiRegressionMemoryItem, ...] = ()

    def _violation_payloads(self) -> list[dict[str, JsonValue]]:
        return [
            PlanViolation.model_validate(violation).model_dump(
                mode="json", exclude_none=True, exclude_defaults=True
            )
            for violation in self.repair_diagnostics
        ]

    def _anti_regression_payloads(self) -> list[dict[str, JsonValue]]:
        return [
            item.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
                exclude={"step_id", "sequence", "message", "cascade_from_step_id", "step_ids"},
            )
            for item in self.anti_regression_memory
        ]

    def provider_payload(self) -> dict[str, object]:
        """Return the canonical V2 provider input when available.

        ``planning_action_catalog`` and the other legacy projections stay on
        the in-process request object for compatibility. They are deliberately
        omitted whenever ``planner_input`` is available, so only the canonical
        V2 semantic projection is sent to the provider.
        """

        if self.planner_input is not None:
            payload: dict[str, object] = {
                "call_type": self.call_type,
                "planner_input": self.planner_input.model_dump(mode="json"),
            }
            if self.planning_continuity is not None:
                payload["planning_continuity"] = self.planning_continuity.model_dump(mode="json")
            if self.replan_reason:
                payload["replan_reason"] = self.replan_reason
            if self.call_type == "REPAIR" or self.repair_attempt != 0:
                payload["repair_attempt"] = self.repair_attempt
            if self.call_type == "REPAIR" and self.rejected_segment is not None:
                payload["rejected_segment"] = self.rejected_segment
            if self.call_type == "REPAIR":
                payload["anti_regression_memory"] = self._anti_regression_payloads()
            if self.repair_diagnostics:
                payload["validator_violations"] = self._violation_payloads()
            return payload
        return self.model_dump(mode="json")


class PlanSegment(ProviderModel):
    # These are short, auditable planning labels.  They are historical intent
    # only: Validator legality remains derived from PlannerInput and Runtime
    # Truth, and the next REPLAN must re-check them against fresh Knowledge.
    segment_goal: StrictStr = Field(min_length=1, max_length=240)
    goal_link: StrictStr = Field(min_length=1, max_length=240)
    continuation_intent: StrictStr = Field(min_length=1, max_length=240)
    stop_reason: Literal[
        "OBJECTIVE_COMPLETION",
        "SEGMENT_COMPLETE",
        "INFORMATION_BOUNDARY",
        "BLOCKED",
    ] = "OBJECTIVE_COMPLETION"
    boundary_dependency_id: str | None = None
    plan_summary: str = ""
    steps: tuple[PlanStepProposal, ...]


class PlanProposal(PlanSegment):
    """Compatibility name for the canonical PlanSegment response."""


class ProviderCallMetadata(ProviderModel):
    call_type: str
    call_sequence: int | None = None
    profile: str | None = None
    latency_ms: int
    provider: str | None = None
    model: str | None = None
    thinking_mode: str | None = None
    reasoning_effort: str | None = None
    configured_output_token_limit: int | None = None
    http_timeout_seconds: float | None = None
    plan_timeout_seconds: float | None = None
    plan_total_timeout_seconds: float | None = None
    goal_resolution_deadline_seconds: float | None = None
    started_at: str | None = None
    finished_at: str | None = None
    request_started_at: str | None = None
    request_send_completed_at: str | None = None
    response_headers_received_at: str | None = None
    first_response_byte_at: str | None = None
    response_bytes_received: int | None = None
    request_cancelled_at: str | None = None
    timeout_subtype: str | None = None
    wall_clock_latency_ms: int | None = None
    outcome: str | None = None
    error_category: str | None = None
    context_bytes: int | None = None
    request_size_bytes: int | None = None
    prompt_tokens: int | None = None
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    final_content_bytes: int | None = None
    finish_reason: str | None = None
    validation_diagnostics: tuple[dict[str, object], ...] = ()
    network_calls: tuple[dict[str, object], ...] = ()
    # Goal-resolution calls may retain one bounded public input/output snapshot
    # when the configured observability level is DEBUG.  Planning calls leave
    # this field unset so PlannerInput payloads keep their existing storage
    # contract.
    prompt_template_version: str | None = None
    request_hash: str | None = None
    public_catalog_hash: str | None = None
    focused_ontology_hash: str | None = None
    debug_snapshot: dict[str, object] | None = None
    response_validation: str | None = None
    recovery_attempt: int | None = None


class GenericModelProvider(Protocol):
    @property
    def model_name(self) -> str: ...
    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection: ...
    def propose_plan(self, request: PlanRequest) -> PlanProposal: ...


class DynamicGoalEntityGrounder(Protocol):
    """Optional provider capability for bounded public Entity Grounding."""

    def ground_dynamic_goal_entities(
        self, request: DynamicGoalEntityGroundingRequest
    ) -> DynamicGoalEntityGrounding: ...


class DynamicGoalInterpreter(Protocol):
    """Provider capability used when no deterministic authored match exists."""

    def interpret_dynamic_goal(
        self, request: DynamicGoalInterpretationRequest
    ) -> DynamicGoalInterpretation: ...


class DynamicGoalContractResolver(Protocol):
    """Provider capability for family-frozen, Action-contract Goal resolution."""

    def match_dynamic_goal_family(self, request: GoalFamilyMatchRequest) -> GoalFamilyMatch: ...

    def decide_dynamic_goal_family(
        self, request: DynamicGoalFamilyRoutingRequest
    ) -> DynamicGoalFamilyRouting: ...

    def route_dynamic_goal_action(
        self, request: DynamicGoalActionRoutingRequest
    ) -> DynamicGoalActionRouting: ...

    def route_dynamic_goal(
        self, request: DynamicGoalSemanticRoutingRequest
    ) -> DynamicGoalSemanticRouting: ...

    def match_dynamic_goal_action(
        self, request: DynamicGoalActionMatchRequest
    ) -> DynamicGoalActionMatch: ...

    def ground_dynamic_goal_operation(
        self, request: DynamicGoalOperationGroundingRequest
    ) -> DynamicGoalOperationGrounding: ...


class GenericProviderError(ValueError):
    """Secret-safe provider failure surfaced at the application boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        validation_diagnostics: tuple[dict[str, object], ...] = (),
        recovery_feedback: tuple[DynamicGoalRecoveryFeedback, ...] = (),
        grounding_recovery_feedback: tuple[dict[str, object], ...] = (),
        resolution_observation: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.validation_diagnostics = validation_diagnostics
        self.recovery_feedback = recovery_feedback
        self.grounding_recovery_feedback = grounding_recovery_feedback
        self.resolution_observation = (
            dict(resolution_observation) if resolution_observation is not None else None
        )


_goal_resolution_budget: ContextVar[tuple[float, float] | None] = ContextVar(
    "journey_goal_resolution_budget",
    default=None,
)


@contextmanager
def goal_resolution_operation(timeout_seconds: float) -> Iterator[None]:
    """Install one shared deadline for the synchronous Goal resolver operation."""

    if timeout_seconds <= 0:
        raise ValueError("Goal resolution timeout must be positive")
    token = _goal_resolution_budget.set(
        (perf_counter() + timeout_seconds, float(timeout_seconds))
    )
    try:
        yield
    finally:
        _goal_resolution_budget.reset(token)


def goal_resolution_remaining_seconds() -> float | None:
    budget = _goal_resolution_budget.get()
    if budget is None:
        return None
    return budget[0] - perf_counter()


def goal_resolution_budget_seconds() -> float | None:
    budget = _goal_resolution_budget.get()
    return budget[1] if budget is not None else None


_plan_operation_budget: ContextVar[tuple[float, float] | None] = ContextVar(
    "journey_plan_operation_budget",
    default=None,
)


@contextmanager
def plan_operation(timeout_seconds: float | None) -> Iterator[None]:
    """Install an optional shared deadline for one planning operation."""

    if timeout_seconds is None:
        yield
        return
    if timeout_seconds <= 0:
        raise ValueError("Plan total timeout must be positive")
    token = _plan_operation_budget.set(
        (perf_counter() + timeout_seconds, float(timeout_seconds))
    )
    try:
        yield
    finally:
        _plan_operation_budget.reset(token)


def plan_operation_remaining_seconds() -> float | None:
    budget = _plan_operation_budget.get()
    return budget[0] - perf_counter() if budget is not None else None


def plan_operation_budget_seconds() -> float | None:
    budget = _plan_operation_budget.get()
    return budget[1] if budget is not None else None


def _provider_deadline_timeout_category() -> str:
    goal_remaining = goal_resolution_remaining_seconds()
    if goal_remaining is not None and goal_remaining <= 0:
        return "GOAL_RESOLUTION_DEADLINE"
    plan_operation_remaining = plan_operation_remaining_seconds()
    if plan_operation_remaining is not None and plan_operation_remaining <= 0:
        return "PLAN_TOTAL_DEADLINE"
    return "PLAN_INVOCATION_DEADLINE"


def ensure_goal_resolution_budget() -> None:
    """Raise the canonical provider timeout once the shared Goal budget expires."""

    remaining = goal_resolution_remaining_seconds()
    if remaining is not None and remaining <= 0:
        raise GenericProviderError(
            "MODEL_PROVIDER_TIMEOUT",
            "The Goal resolution operation timed out",
            resolution_observation={
                "stage": "GOAL_RESOLUTION",
                "status": "ERROR",
                "result": "GOAL_RESOLUTION_DEADLINE",
                "rejection_code": "MODEL_PROVIDER_TIMEOUT",
                "timeout_category": "GOAL_RESOLUTION_DEADLINE",
            },
        )


@dataclass(frozen=True, slots=True)
class _ProviderProfile:
    """Purpose-specific request settings for one logical provider call."""

    name: str
    model_name: str
    thinking_mode: str
    reasoning_effort: str
    output_token_limit: int | None


_FAST_SEMANTIC_OUTPUT_TOKEN_LIMIT = 2048


_VALIDATION_EXPECTED_TYPES = {
    "bool_type": "boolean",
    "bool_parsing": "boolean",
    "int_type": "integer",
    "int_parsing": "integer",
    "float_type": "number",
    "float_parsing": "number",
    "string_type": "string",
    "list_type": "array",
    "tuple_type": "array",
    "set_type": "array",
    "dict_type": "object",
    "mapping_type": "object",
    "model_type": "object",
    "model_attributes_type": "object",
    "missing": "required",
    "literal_error": "literal",
}


def provider_validation_diagnostics(
    error: ValidationError,
) -> tuple[dict[str, object], ...]:
    """Extract only schema/type metadata from a Pydantic validation error.

    Pydantic's error entries contain the rejected input and sometimes context.
    Those values are deliberately inspected only for their JSON type; neither
    the input nor the error message/context is returned to telemetry.
    """

    diagnostics: list[dict[str, object]] = []
    for item in error.errors():
        error_type = item.get("type")
        if not isinstance(error_type, str):
            error_type = "unknown"
        diagnostic: dict[str, object] = {
            "validation_error_type": error_type[:80],
            "field_path": _safe_validation_field_path(item.get("loc")),
        }
        expected_type = _VALIDATION_EXPECTED_TYPES.get(error_type)
        if expected_type is not None:
            diagnostic["expected_type"] = expected_type
        if "input" in item:
            actual_type = _safe_json_type(item.get("input"))
            if actual_type is not None:
                diagnostic["actual_json_type"] = actual_type
        diagnostics.append(diagnostic)
        if len(diagnostics) >= 20:
            break
    return tuple(diagnostics)


def _normalize_dynamic_goal_grounding_wire(raw: object) -> object:
    """Normalize the provider-only grounding wire contract.

    Candidate/role provenance is assigned by the backend after validation.  A
    few older provider templates also repeated reference metadata on scalar
    amount slots.  Those exact redundant fields are removed before strict
    DTO validation; every other unknown field is deliberately retained so the
    closed Pydantic models continue to reject it.
    """

    if isinstance(raw, BaseModel):
        raw = raw.model_dump(mode="json")
    if not isinstance(raw, dict):
        return raw

    normalized = dict(raw)
    candidate_refs = normalized.get("candidate_refs")
    if isinstance(candidate_refs, (list, tuple)):
        normalized["candidate_refs"] = [
            (
                {key: value for key, value in reference.items() if key != "provenance"}
                if isinstance(reference, dict)
                else reference
            )
            for reference in candidate_refs
        ]

    intent = normalized.get("intent")
    if not isinstance(intent, dict):
        return normalized
    normalized_intent = dict(intent)
    for role in ("action", "actor", "source", "target", "resource"):
        slot = normalized_intent.get(role)
        if isinstance(slot, dict):
            normalized_intent[role] = {
                key: value for key, value in slot.items() if key != "provenance"
            }
    amount = normalized_intent.get("amount")
    if isinstance(amount, dict):
        normalized_intent["amount"] = {
            key: value
            for key, value in amount.items()
            if key not in {"provenance", "match_semantics", "ref_type", "key"}
        }
    normalized["intent"] = normalized_intent
    return normalized


def _normalize_dynamic_goal_semantic_routing(raw: object) -> object:
    """Normalize only the redundant singleton form of a matched Action."""

    if not isinstance(raw, dict):
        return raw
    action_key = raw.get("action_key")
    if (
        raw.get("family") == "OPERATION"
        and raw.get("action_match") == "MATCHED"
        and isinstance(action_key, str)
        and raw.get("candidate_keys") == [action_key]
    ):
        return {**raw, "candidate_keys": []}
    return raw


def _normalize_dynamic_goal_action_routing(raw: object) -> object:
    """Normalize only the redundant singleton form of a matched Action."""

    if not isinstance(raw, dict):
        return raw
    action_key = raw.get("action_key")
    if (
        raw.get("action_match") == "MATCHED"
        and isinstance(action_key, str)
        and raw.get("candidate_keys") == [action_key]
    ):
        return {**raw, "candidate_keys": []}
    return raw


_OPERATION_REFERENCE_TYPES = frozenset({"NODE", "REGION", "FACILITY", "RESOURCE", "ACTOR"})
_OPERATION_SLOT_METADATA_FIELDS = frozenset(
    {"name", "description", "node_type_keys", "semantic_reference_type"}
)


def _normalized_reference_term(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _operation_reference_value_is_redundant(
    *,
    value: object,
    key: object,
    expected_type: object,
    public_references: tuple[dict[str, object], ...],
) -> bool:
    """Prove that a natural-language value names the same canonical reference."""

    if not isinstance(value, str) or not isinstance(key, str) or not isinstance(expected_type, str):
        return False
    expected_ref_type = "NODE" if expected_type == "FACILITY" else expected_type
    if expected_ref_type not in _OPERATION_REFERENCE_TYPES:
        return False
    normalized_value = _normalized_reference_term(value)
    if normalized_value == _normalized_reference_term(key):
        return True
    reference = next(
        (
            item
            for item in public_references
            if item.get("ref_type") == expected_ref_type and item.get("key") == key
        ),
        None,
    )
    if reference is None:
        return False
    name = reference.get("name")
    return isinstance(name, str) and normalized_value == _normalized_reference_term(name)


def _normalize_operation_slot(
    raw: object,
    *,
    public_references: tuple[dict[str, object], ...],
) -> object:
    if not isinstance(raw, dict):
        return raw
    slot = {key: value for key, value in raw.items() if key not in _OPERATION_SLOT_METADATA_FIELDS}
    if (
        slot.get("status") == "GROUNDED"
        and slot.get("expected_type") in _OPERATION_REFERENCE_TYPES
        and slot.get("key") is not None
        and slot.get("value") is not None
        and _operation_reference_value_is_redundant(
            value=slot.get("value"),
            key=slot.get("key"),
            expected_type=slot.get("expected_type"),
            public_references=public_references,
        )
    ):
        slot.pop("value", None)
    return slot


def _normalize_dynamic_goal_operation_grounding(
    raw: object,
    request: DynamicGoalOperationGroundingRequest,
) -> object:
    """Remove only provably redundant wire fields before strict validation."""

    if not isinstance(raw, dict):
        return raw
    normalized = dict(raw)
    if normalized.get("action_key") == request.action_key:
        normalized.pop("action_key", None)
    intent = normalized.get("intent")
    if not isinstance(intent, dict):
        return normalized
    normalized_intent = dict(intent)
    for field in ("actor", "target"):
        if field in normalized_intent:
            normalized_intent[field] = _normalize_operation_slot(
                normalized_intent[field],
                public_references=request.public_references,
            )
    for field in ("bindings", "parameters"):
        slots = normalized_intent.get(field)
        if isinstance(slots, list):
            normalized_intent[field] = [
                _normalize_operation_slot(
                    item,
                    public_references=request.public_references,
                )
                for item in slots
            ]
    normalized["intent"] = normalized_intent
    return normalized


def _operation_path_value(payload: object, path: str) -> object:
    current = payload
    for field, index_text in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?", path):
        if not isinstance(current, dict) or field not in current:
            return _MISSING_OPERATION_PATH
        current = current[field]
        if index_text:
            index = int(index_text)
            if not isinstance(current, (list, tuple)) or index >= len(current):
                return _MISSING_OPERATION_PATH
            current = current[index]
    return current


_MISSING_OPERATION_PATH = object()


def _operation_recovery_preserve(
    raw: object,
    request: DynamicGoalOperationGroundingRequest,
    invalid_paths: set[str],
) -> dict[str, object]:
    preserve: dict[str, object] = {"frozen_family": "OPERATION"}
    if not isinstance(raw, dict) or raw.get("status") != "RESOLVED":
        return preserve
    preserve.update(
        {
            "intent.frozen_family": "OPERATION",
            "intent.action_key": request.action_key,
        }
    )
    for path in ("intent.actor", "intent.target"):
        value = _operation_path_value(raw, path)
        if value is _MISSING_OPERATION_PATH or any(
            invalid == path or invalid.startswith(f"{path}.") for invalid in invalid_paths
        ):
            continue
        try:
            validated = OperationContractSlot.model_validate(value)
        except ValidationError:
            continue
        preserve[path] = validated.model_dump(mode="json")
    for collection in ("bindings", "parameters"):
        values = _operation_path_value(raw, f"intent.{collection}")
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            path = f"intent.{collection}[{index}]"
            if any(invalid == path or invalid.startswith(f"{path}.") for invalid in invalid_paths):
                continue
            try:
                validated = OperationContractSlot.model_validate(value)
            except ValidationError:
                continue
            preserve[path] = validated.model_dump(mode="json")
    return preserve


def _dynamic_goal_operation_validation_diagnostics(
    raw: object,
    error: ValidationError,
    request: DynamicGoalOperationGroundingRequest,
) -> tuple[dict[str, object], ...]:
    base = provider_validation_diagnostics(error)
    invalid_paths = {
        str(item.get("field_path")) for item in base if item.get("field_path") is not None
    }
    preserve = _operation_recovery_preserve(raw, request, invalid_paths)
    typed: list[dict[str, object]] = []
    if isinstance(raw, dict):
        status = raw.get("status")
        if status not in {"RESOLVED", "NEEDS_CLARIFICATION", "UNSUPPORTED"}:
            typed.append(
                {
                    "code": "INVALID_OPERATION_STATUS",
                    "field_path": "status",
                    "allowed": ["RESOLVED", "NEEDS_CLARIFICATION", "UNSUPPORTED"],
                    "preserve": preserve,
                    "fix_only": ["status", "intent", "clarification_prompt"],
                }
            )
        intent = raw.get("intent")
        if isinstance(intent, dict):
            slots: list[tuple[str, object]] = [
                ("intent.actor", intent.get("actor")),
                ("intent.target", intent.get("target")),
            ]
            for collection in ("bindings", "parameters"):
                values = intent.get(collection)
                if isinstance(values, list):
                    slots.extend(
                        (f"intent.{collection}[{index}]", value)
                        for index, value in enumerate(values)
                    )
            for path, slot in slots:
                if (
                    isinstance(slot, dict)
                    and slot.get("expected_type") in _OPERATION_REFERENCE_TYPES
                    and slot.get("key") is not None
                    and slot.get("value") is not None
                ):
                    typed.append(
                        {
                            "code": "REFERENCE_KEY_AND_VALUE_BOTH_SET",
                            "field_path": path,
                            "expected": "canonical key only",
                            "preserve": preserve,
                            "fix_only": [f"{path}.value"],
                        }
                    )
    return tuple((*typed, *base))


def _validate_dynamic_goal_operation_recovery_preservation(
    result: DynamicGoalOperationGrounding,
    recovery_feedback: tuple[dict[str, object], ...],
) -> None:
    payload = result.model_dump(mode="json")
    for feedback in recovery_feedback:
        preserve = feedback.get("preserve")
        if not isinstance(preserve, dict):
            continue
        for path, expected in preserve.items():
            if not isinstance(path, str):
                continue
            if _operation_path_value(payload, path) != expected:
                raise ValueError(f"Operation recovery changed preserved field {path}")


def _dynamic_goal_routing_validation_diagnostics(
    raw: object,
    error: ValidationError,
) -> tuple[dict[str, object], ...]:
    diagnostics = provider_validation_diagnostics(error)
    if not isinstance(raw, dict):
        return diagnostics
    action_key = raw.get("action_key")
    candidate_keys = raw.get("candidate_keys")
    if (
        raw.get("family") == "OPERATION"
        and raw.get("action_match") == "MATCHED"
        and isinstance(action_key, str)
        and isinstance(candidate_keys, list)
        and candidate_keys
    ):
        return (
            {
                "code": "MATCHED_HAS_CANDIDATE_KEYS",
                "field_path": "candidate_keys",
                "expected_candidate_keys": [],
                "preserve": {
                    "family": "OPERATION",
                    "action_match": "MATCHED",
                    "action_key": action_key,
                },
                "fix_only": ["candidate_keys"],
            },
            *diagnostics,
        )
    return diagnostics


def _dynamic_goal_action_routing_validation_diagnostics(
    raw: object,
    error: ValidationError,
) -> tuple[dict[str, object], ...]:
    diagnostics = provider_validation_diagnostics(error)
    if not isinstance(raw, dict):
        return diagnostics
    action_key = raw.get("action_key")
    candidate_keys = raw.get("candidate_keys")
    if (
        raw.get("action_match") == "MATCHED"
        and isinstance(action_key, str)
        and isinstance(candidate_keys, list)
        and candidate_keys
    ):
        return (
            {
                "code": "MATCHED_HAS_CANDIDATE_KEYS",
                "field_path": "candidate_keys",
                "expected_candidate_keys": [],
                "preserve": {
                    "action_match": "MATCHED",
                    "action_key": action_key,
                },
                "fix_only": ["candidate_keys"],
            },
            *diagnostics,
        )
    action_match = raw.get("action_match")
    no_match_reason = raw.get("no_match_reason")
    allowed_reasons = ["NO_SEMANTIC_ACTION", "SEMANTIC_CONFLICT"]
    if action_match == "NO_MATCH" and no_match_reason not in allowed_reasons:
        return (
            {
                "code": (
                    "MISSING_NO_MATCH_REASON"
                    if no_match_reason is None
                    else "INVALID_NO_MATCH_REASON"
                ),
                "field_path": "no_match_reason",
                "allowed": allowed_reasons,
                "preserve": {
                    "action_match": "NO_MATCH",
                    "action_key": None,
                    "candidate_keys": [],
                },
                "fix_only": ["no_match_reason"],
            },
            *diagnostics,
        )
    if action_match in {"MATCHED", "AMBIGUOUS"} and no_match_reason is not None:
        return (
            {
                "code": f"{action_match}_HAS_NO_MATCH_REASON",
                "field_path": "no_match_reason",
                "expected": None,
                "preserve": {
                    "action_match": action_match,
                    "action_key": action_key,
                    "candidate_keys": candidate_keys or [],
                },
                "fix_only": ["no_match_reason"],
            },
            *diagnostics,
        )
    return diagnostics


def _safe_validation_field_path(location: object) -> str:
    parts = location if isinstance(location, (tuple, list)) else (location,)
    rendered: list[str] = []
    for part in parts:
        if isinstance(part, int) and part >= 0:
            if rendered:
                rendered[-1] = f"{rendered[-1]}[{part}]"
            else:
                rendered.append(f"[{part}]")
        elif isinstance(part, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", part):
            rendered.append(part)
        else:
            rendered.append("<field>")
    return ".".join(rendered) or "<root>"


def _safe_json_type(value: object) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return None


_DYNAMIC_GOAL_REQUIREMENT_KINDS = frozenset(
    {"FACT", "RESOURCE_AT_LEAST", "DERIVED_STATE", "ACTION_COMPLETED"}
)
_DYNAMIC_GOAL_REQUIREMENT_FIELDS = {
    "FACT": ("node_key", "fact_key", "accepted_values"),
    "RESOURCE_AT_LEAST": ("region_key", "resource_key", "minimum"),
    "DERIVED_STATE": ("derived_key", "accepted_values"),
    "ACTION_COMPLETED": ("action_key",),
}
_DYNAMIC_GOAL_REQUIREMENT_ALLOWED_FIELDS = {
    kind: frozenset(
        {
            "kind",
            *fields,
            *(
                {
                    "actor_key",
                    "target_key",
                    "binding_constraints",
                    "parameter_constraints",
                    "match_mode",
                    "boundary",
                }
                if kind == "ACTION_COMPLETED"
                else set()
            ),
        }
    )
    for kind, fields in _DYNAMIC_GOAL_REQUIREMENT_FIELDS.items()
}


def dynamic_goal_grounding_recovery_feedback(
    raw: object,
    *,
    validation_diagnostics: tuple[dict[str, object], ...] = (),
    public_catalog: dict[str, object] | None = None,
) -> tuple[dict[str, object], ...]:
    """Build one bounded, structural retry instruction for Stage 1 grounding.

    The feedback is intentionally shape-only.  It may preserve canonical
    identities that were already present in the rejected response when those
    identities are also present in the public catalog, but it never proposes a
    replacement identity or performs semantic disambiguation.
    """

    allowed_identities: set[tuple[str, str]] = set()
    if isinstance(public_catalog, dict):
        references = public_catalog.get("references")
        if isinstance(references, (list, tuple)):
            for reference in references:
                if not isinstance(reference, dict):
                    continue
                ref_type = reference.get("ref_type")
                key = reference.get("key")
                if isinstance(ref_type, str) and isinstance(key, str):
                    allowed_identities.add((ref_type, key))

    preserve: list[dict[str, object]] = []
    if isinstance(raw, dict):
        candidate_refs = raw.get("candidate_refs")
        if isinstance(candidate_refs, (list, tuple)):
            for index, reference in enumerate(candidate_refs):
                if not isinstance(reference, dict):
                    continue
                ref_type = reference.get("ref_type")
                key = reference.get("key")
                if (
                    isinstance(ref_type, str)
                    and isinstance(key, str)
                    and (ref_type, key) in allowed_identities
                ):
                    preserved_ref: dict[str, object] = {
                        "path": f"candidate_refs[{index}]",
                        "ref_type": ref_type,
                        "key": key,
                    }
                    match_semantics = reference.get("match_semantics")
                    if match_semantics in {
                        "EXACT_OR_AUTHORED",
                        "SEMANTIC_EQUIVALENT",
                        "RELATED_ONLY",
                    }:
                        preserved_ref["match_semantics"] = match_semantics
                    preserve.append(preserved_ref)

        intent = raw.get("intent")
        if isinstance(intent, dict):
            for slot_key in ("action", "actor", "source", "target", "resource"):
                slot = intent.get(slot_key)
                if not isinstance(slot, dict) or slot.get("status") != "GROUNDED":
                    continue
                ref_type = slot.get("ref_type")
                key = slot.get("key")
                if (
                    isinstance(ref_type, str)
                    and isinstance(key, str)
                    and (ref_type, key) in allowed_identities
                ):
                    preserved_slot: dict[str, object] = {
                        "path": f"intent.{slot_key}",
                        "ref_type": ref_type,
                        "key": key,
                    }
                    match_semantics = slot.get("match_semantics")
                    if match_semantics in {
                        "EXACT_OR_AUTHORED",
                        "SEMANTIC_EQUIVALENT",
                        "RELATED_ONLY",
                    }:
                        preserved_slot["match_semantics"] = match_semantics
                    surface = slot.get("surface")
                    if isinstance(surface, str) and surface.strip():
                        preserved_slot["surface"] = surface[:400]
                    preserve.append(preserved_slot)

    issue = "INVALID_JSON"
    fix_only: list[str] = []
    if isinstance(raw, dict):
        unknown_fields = sorted(
            str(field)
            for field in raw
            if field
            not in {"status", "candidate_refs", "candidate_keys", "intent", "clarification_prompt"}
        )
        if unknown_fields:
            issue = "UNKNOWN_FIELD"
            fix_only.extend(unknown_fields)
        elif any(
            isinstance(item.get("field_path"), str)
            and str(item["field_path"]).startswith("candidate_refs")
            for item in validation_diagnostics
            if isinstance(item, dict)
        ):
            issue = "INVALID_REFERENCE"
        elif any(
            isinstance(item.get("field_path"), str) and str(item["field_path"]).startswith("intent")
            for item in validation_diagnostics
            if isinstance(item, dict)
        ):
            issue = "INVALID_SLOT"
        else:
            issue = "INVALID_VARIANT"
        fix_only.extend(
            str(item["field_path"])
            for item in validation_diagnostics
            if isinstance(item, dict) and isinstance(item.get("field_path"), str)
        )

    amount_scalar_shape_error = any(
        isinstance(item.get("field_path"), str)
        and str(item["field_path"]).startswith("intent.amount")
        and item.get("actual_json_type") in {"object", "array"}
        for item in validation_diagnostics
        if isinstance(item, dict)
    )
    if amount_scalar_shape_error:
        fix_only = [path for path in fix_only if not path.startswith("intent.amount")]
        fix_only.append("intent.amount")
    feedback: dict[str, object] = {
        "code": "GROUNDING_STRUCTURAL_REPAIR",
        "issue": issue,
        "expected": (
            "Return one JSON object with only status, candidate_refs, intent, "
            "and clarification_prompt; use catalog identities exactly."
        ),
        "preserve": preserve,
        "fix_only": sorted(set(fix_only))[:8],
    }
    if amount_scalar_shape_error:
        feedback["expected_field_shape"] = {
            "path": "intent.amount",
            "example": {
                "status": "GROUNDED",
                "value": 30,
                "surface": None,
            },
            "rule": "value must be a native JSON scalar, never an object or array",
        }
    if validation_diagnostics:
        feedback["diagnostics"] = [dict(item) for item in validation_diagnostics[:8]]
    return (feedback,)


def dynamic_goal_recovery_feedback(
    raw: object,
    *,
    public_ontology: dict[str, object] | None = None,
) -> tuple[DynamicGoalRecoveryFeedback, ...]:
    """Build bounded, public-only feedback for a typed response retry.

    This inspects only the response shape and focused public metadata.  It
    never copies provider values, exception messages, or hidden runtime data.
    """

    if not isinstance(raw, dict):
        return ()
    requirements = raw.get("requirements")
    if not isinstance(requirements, list):
        return ()

    feedback: list[DynamicGoalRecoveryFeedback] = []
    for index, item in enumerate(requirements[:4]):
        if not isinstance(item, dict):
            feedback.append(
                DynamicGoalRecoveryFeedback(
                    requirement_index=index,
                    issue="INVALID_REQUIREMENT_SHAPE",
                    expected_shape={
                        "kind": "FACT|RESOURCE_AT_LEAST|DERIVED_STATE|ACTION_COMPLETED",
                    },
                )
            )
            continue

        raw_kind = item.get("kind")
        kind = (
            raw_kind
            if isinstance(raw_kind, str) and raw_kind in _DYNAMIC_GOAL_REQUIREMENT_KINDS
            else None
        )
        if kind is None:
            feedback.append(
                DynamicGoalRecoveryFeedback(
                    requirement_index=index,
                    issue="INVALID_FIELD" if "kind" in item else "MISSING_REQUIRED_FIELD",
                    field="kind",
                    expected_shape={
                        "kind": "FACT|RESOURCE_AT_LEAST|DERIVED_STATE|ACTION_COMPLETED",
                    },
                )
            )
            continue

        expected_shape = _dynamic_goal_expected_shape(kind, item, public_ontology)
        extra_fields = set(item) - _DYNAMIC_GOAL_REQUIREMENT_ALLOWED_FIELDS[kind]
        if extra_fields:
            feedback.append(
                DynamicGoalRecoveryFeedback(
                    requirement_index=index,
                    kind=kind,
                    issue="INVALID_FIELD",
                    field=("target_value" if "target_value" in extra_fields else "<extra_field>"),
                    expected_shape=expected_shape,
                    focused_target_value=_dynamic_goal_target_value(kind, item, public_ontology),
                )
            )
            continue

        for field in _DYNAMIC_GOAL_REQUIREMENT_FIELDS[kind]:
            if field not in item or item[field] is None:
                feedback.append(
                    DynamicGoalRecoveryFeedback(
                        requirement_index=index,
                        kind=kind,
                        issue="MISSING_REQUIRED_FIELD",
                        field=field,
                        expected_shape=expected_shape,
                        focused_target_value=_dynamic_goal_target_value(
                            kind, item, public_ontology
                        ),
                    )
                )
                break
            value = item[field]
            if field in {
                "node_key",
                "fact_key",
                "region_key",
                "resource_key",
                "derived_key",
            } and (not isinstance(value, str) or not value):
                feedback.append(
                    DynamicGoalRecoveryFeedback(
                        requirement_index=index,
                        kind=kind,
                        issue="INVALID_FIELD",
                        field=field,
                        expected_shape=expected_shape,
                        focused_target_value=_dynamic_goal_target_value(
                            kind, item, public_ontology
                        ),
                    )
                )
                break
            if field == "minimum" and (type(value) is not int or value < 0):
                feedback.append(
                    DynamicGoalRecoveryFeedback(
                        requirement_index=index,
                        kind=kind,
                        issue="INVALID_FIELD",
                        field=field,
                        expected_shape=expected_shape,
                    )
                )
                break
            if field == "accepted_values":
                if not isinstance(value, list) or not value:
                    feedback.append(
                        DynamicGoalRecoveryFeedback(
                            requirement_index=index,
                            kind=kind,
                            issue=(
                                "MISSING_REQUIRED_FIELD"
                                if isinstance(value, list)
                                else "INVALID_FIELD"
                            ),
                            field=field,
                            expected_shape=expected_shape,
                            focused_target_value=_dynamic_goal_target_value(
                                kind, item, public_ontology
                            ),
                        )
                    )
                    break
                if any(type(candidate_value) not in {str, int, bool} for candidate_value in value):
                    feedback.append(
                        DynamicGoalRecoveryFeedback(
                            requirement_index=index,
                            kind=kind,
                            issue="INVALID_FIELD",
                            field=field,
                            expected_shape=expected_shape,
                            focused_target_value=_dynamic_goal_target_value(
                                kind, item, public_ontology
                            ),
                        )
                    )
                    break
        else:
            continue

    return tuple(feedback)


def _dynamic_goal_expected_shape(
    kind: str,
    item: dict[str, object],
    public_ontology: dict[str, object] | None,
) -> dict[str, object]:
    if kind == "FACT":
        return {
            "kind": "FACT",
            "node_key": "<allowed node key>",
            "fact_key": "<allowed fact key>",
            "accepted_values": ["<allowed target value>"],
        }
    if kind == "RESOURCE_AT_LEAST":
        return {
            "kind": "RESOURCE_AT_LEAST",
            "region_key": "<allowed region key>",
            "resource_key": "<allowed resource key>",
            "minimum": 0,
        }
    if kind == "ACTION_COMPLETED":
        return {
            "kind": "ACTION_COMPLETED",
            "action_key": "<allowed action key>",
            "actor_key": None,
            "target_key": None,
            "binding_constraints": [],
            "parameter_constraints": None,
            "match_mode": "ONE_SUCCESSFUL_INVOCATION",
            "boundary": "TASK_OWNED_OPERATION",
        }
    derived_key = item.get("derived_key")
    public_target = _dynamic_goal_target_value(kind, item, public_ontology)
    return {
        "kind": "DERIVED_STATE",
        "derived_key": (
            derived_key
            if isinstance(derived_key, str) and public_target is not None
            else "<allowed derived key>"
        ),
        "accepted_values": [
            public_target if public_target is not None else "<allowed target value>"
        ],
    }


def _dynamic_goal_target_value(
    kind: str,
    item: dict[str, object],
    public_ontology: dict[str, object] | None,
) -> str | int | bool | None:
    if kind != "DERIVED_STATE" or public_ontology is None:
        return None
    derived_key = item.get("derived_key")
    if not isinstance(derived_key, str):
        return None
    world = public_ontology.get("world")
    if not isinstance(world, dict):
        return None
    states = world.get("derived_states")
    if not isinstance(states, list):
        return None
    for state in states:
        if not isinstance(state, dict) or state.get("key") != derived_key:
            continue
        value = state.get("target_value")
        if type(value) in {str, int, bool}:
            return value
    return None


_GOAL_PROVIDER_PURPOSES = frozenset(
    {
        "dynamic_goal_grounding",
        "dynamic_goal",
        "dynamic_goal_interpretation",
        "dynamic_goal_family_routing",
        "dynamic_goal_action_routing",
        "dynamic_goal_routing",
        "dynamic_goal_operation",
    }
)
_GOAL_PROMPT_TEMPLATE_VERSIONS = {
    "dynamic_goal_grounding": "dynamic-goal-grounding-v8",
    "dynamic_goal": "dynamic-goal-interpretation-v2",
    "dynamic_goal_interpretation": "dynamic-goal-interpretation-v2",
    "dynamic_goal_family_routing": "dynamic-goal-family-routing-v2",
    "dynamic_goal_action_routing": "dynamic-goal-action-routing-v4",
    "dynamic_goal_routing": "dynamic-goal-routing-v3",
    "dynamic_goal_operation": "dynamic-goal-operation-v2",
}
_GOAL_SNAPSHOT_MAX_DEPTH = 8
_GOAL_SNAPSHOT_MAX_ITEMS = 200
_GOAL_SNAPSHOT_MAX_STRING = 4000
_GOAL_REQUEST_SNAPSHOT_MAX_BYTES = 32_768
_GOAL_RESPONSE_SNAPSHOT_MAX_BYTES = 16_384
_SENSITIVE_SNAPSHOT_KEYS = frozenset(
    {
        "authorization",
        "chain_of_thought",
        "content",
        "credentials",
        "headers",
        "hidden_truth",
        "messages",
        "reasoning",
        "secret",
        "thinking",
        "thought",
        "token",
    }
)


def goal_provider_request_snapshot(
    purpose: str,
    payload: dict[str, object],
) -> dict[str, object]:
    """Return a bounded public snapshot of a Dynamic Goal request payload."""

    if purpose not in _GOAL_PROVIDER_PURPOSES:
        return {}
    snapshot = _safe_goal_snapshot(payload, depth=0)
    snapshot = _compact_goal_catalog_snapshot(snapshot)
    return _bounded_goal_snapshot(snapshot, max_bytes=_GOAL_REQUEST_SNAPSHOT_MAX_BYTES)


def goal_provider_response_snapshot(
    purpose: str,
    value: object,
    *,
    public_catalog: dict[str, object] | None = None,
    public_ontology: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return a redacted structured/shape snapshot of a Goal response.

    Valid DTOs are already closed provider models.  Invalid responses are
    reduced to their safe contract fields and JSON shapes; unknown values are
    never copied, which prevents provider reasoning or accidental private
    fields from entering telemetry.
    """

    if purpose not in _GOAL_PROVIDER_PURPOSES:
        return {}
    raw: object = value
    if isinstance(value, BaseModel):
        raw = value.model_dump(mode="json")
    if not isinstance(raw, dict):
        return {"json_type": _safe_json_type(raw) or "unknown"}
    if purpose in {
        "dynamic_goal_family_routing",
        "dynamic_goal_action_routing",
        "dynamic_goal_routing",
        "dynamic_goal_operation",
    }:
        snapshot = _safe_goal_snapshot(raw, depth=0)
        return _bounded_goal_snapshot(snapshot, max_bytes=_GOAL_RESPONSE_SNAPSHOT_MAX_BYTES)
    if purpose == "dynamic_goal_grounding":
        raw = _redact_goal_grounding_identities(raw, public_catalog)
        allowed = {"status", "candidate_refs", "candidate_keys", "intent", "clarification_prompt"}
        nested_allowed = {"ref_type", "key", "match_semantics", "surface", "status", "value"}
    else:
        raw = _redact_goal_interpretation_identities(raw, public_ontology)
        allowed = {"status", "requirements", "clarification_prompt"}
        nested_allowed = {
            "kind",
            "node_key",
            "fact_key",
            "accepted_values",
            "region_key",
            "resource_key",
            "minimum",
            "derived_key",
            "action_key",
            "actor_key",
            "target_key",
            "binding_constraints",
            "parameter_constraints",
            "match_mode",
            "boundary",
        }
    snapshot = _safe_goal_response_mapping(raw, allowed=allowed, nested_allowed=nested_allowed)
    return _bounded_goal_snapshot(snapshot, max_bytes=_GOAL_RESPONSE_SNAPSHOT_MAX_BYTES)


def _redact_goal_grounding_identities(
    value: dict[str, object],
    public_catalog: dict[str, object] | None,
) -> dict[str, object]:
    if public_catalog is None:
        return value
    references = public_catalog.get("references")
    allowed: set[tuple[str, str]] = set()
    if isinstance(references, (list, tuple)):
        for reference in references:
            if not isinstance(reference, dict):
                continue
            ref_type = reference.get("ref_type")
            key = reference.get("key")
            if isinstance(ref_type, str) and isinstance(key, str):
                allowed.add((ref_type, key))
    result = dict(value)
    candidate_refs = value.get("candidate_refs")
    if isinstance(candidate_refs, (list, tuple)):
        result["candidate_refs"] = [
            _redact_goal_reference(item, allowed) for item in candidate_refs
        ]
    candidate_keys = value.get("candidate_keys")
    if isinstance(candidate_keys, (list, tuple)):
        result["candidate_keys"] = [
            item
            if isinstance(item, str) and any(key == item for _, key in allowed)
            else {"json_type": _safe_json_type(item) or "unknown", "value_omitted": True}
            for item in candidate_keys
        ]
    if "intent" in value:
        result["intent"] = _redact_goal_intent(value["intent"], allowed)
    return result


def _redact_goal_intent(
    value: object,
    allowed_references: set[tuple[str, str]],
) -> object:
    """Keep Stage 1 role evidence while omitting ungrounded identities."""

    if not isinstance(value, dict):
        return {"json_type": _safe_json_type(value) or "unknown", "value_omitted": True}
    result: dict[str, object] = {}
    intent_kind = value.get("intent_kind")
    if intent_kind in {"STATE", "OPERATION"}:
        result["intent_kind"] = intent_kind
    else:
        result["intent_kind"] = _redacted_goal_identity(intent_kind)
    slot_keys = ("action", "actor", "source", "target", "resource", "amount")
    for slot_key in slot_keys:
        slot = value.get(slot_key)
        if not isinstance(slot, dict):
            result[slot_key] = {
                "json_type": _safe_json_type(slot) or "unknown",
                "value_omitted": True,
            }
            continue
        safe_slot: dict[str, object] = {}
        status = slot.get("status")
        if status in {"GROUNDED", "UNRESOLVED", "NOT_SPECIFIED"}:
            safe_slot["status"] = status
        else:
            safe_slot["status"] = _redacted_goal_identity(status)
        ref_type = slot.get("ref_type")
        key = slot.get("key")
        if ref_type is not None or key is not None:
            if (
                isinstance(ref_type, str)
                and isinstance(key, str)
                and (
                    ref_type,
                    key,
                )
                in allowed_references
            ):
                safe_slot["ref_type"] = ref_type
                safe_slot["key"] = key
            else:
                if "ref_type" in slot:
                    safe_slot["ref_type"] = _redacted_goal_identity(ref_type)
                if "key" in slot:
                    safe_slot["key"] = _redacted_goal_identity(key)
        match_semantics = slot.get("match_semantics")
        if match_semantics in {
            "EXACT_OR_AUTHORED",
            "SEMANTIC_EQUIVALENT",
            "RELATED_ONLY",
        }:
            safe_slot["match_semantics"] = match_semantics
        surface = slot.get("surface")
        if isinstance(surface, str) and surface.strip():
            safe_slot["surface"] = surface[:400]
        if slot_key == "amount" and "value" in slot:
            amount = slot.get("value")
            if type(amount) in {str, int, bool}:
                safe_slot["value"] = amount
            else:
                safe_slot["value"] = _redacted_goal_identity(amount)
        result[slot_key] = safe_slot
    return result


def _redact_goal_reference(
    value: object,
    allowed: set[tuple[str, str]],
) -> object:
    if not isinstance(value, dict):
        return value
    ref_type = value.get("ref_type")
    key = value.get("key")
    if isinstance(ref_type, str) and isinstance(key, str) and (ref_type, key) in allowed:
        result = {"ref_type": ref_type, "key": key}
        match_semantics = value.get("match_semantics")
        if match_semantics in {
            "EXACT_OR_AUTHORED",
            "SEMANTIC_EQUIVALENT",
            "RELATED_ONLY",
        }:
            result["match_semantics"] = match_semantics
        return result
    return {
        "ref_type": {"json_type": _safe_json_type(ref_type) or "unknown", "value_omitted": True},
        "key": {"json_type": _safe_json_type(key) or "unknown", "value_omitted": True},
    }


def _redact_goal_interpretation_identities(
    value: dict[str, object],
    public_ontology: dict[str, object] | None,
) -> dict[str, object]:
    if public_ontology is None:
        return value
    world = public_ontology.get("world")
    if not isinstance(world, dict):
        return value
    node_keys = _public_ontology_string_keys(world.get("nodes"), key_name="key")
    region_keys = _public_ontology_string_keys(world.get("regions"), key_name="key")
    resource_keys = _public_ontology_string_keys(world.get("resources"), key_name="key")
    derived_keys = _public_ontology_string_keys(world.get("derived_states"), key_name="key")
    action_keys = _public_ontology_string_keys(world.get("actions"), key_name="key")
    actor_keys = _public_ontology_string_keys(world.get("actors"), key_name="key")
    fact_keys: set[tuple[str, str]] = set()
    facts = world.get("facts")
    if isinstance(facts, (list, tuple)):
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            node_key = fact.get("node_key")
            fact_key = fact.get("fact_key")
            if isinstance(node_key, str) and isinstance(fact_key, str):
                fact_keys.add((node_key, fact_key))
    result = dict(value)
    requirements = value.get("requirements")
    if not isinstance(requirements, (list, tuple)):
        return result
    safe_requirements: list[object] = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            safe_requirements.append(requirement)
            continue
        item = dict(requirement)
        kind = item.get("kind")
        if kind == "FACT":
            node_key = item.get("node_key")
            fact_key = item.get("fact_key")
            is_public_fact = (
                isinstance(node_key, str)
                and isinstance(fact_key, str)
                and (node_key, fact_key) in fact_keys
            )
            if not is_public_fact:
                item["node_key"] = _redacted_goal_identity(node_key)
                item["fact_key"] = _redacted_goal_identity(fact_key)
                _omit_goal_requirement_value(item)
        elif kind == "RESOURCE_AT_LEAST":
            region_key = item.get("region_key")
            resource_key = item.get("resource_key")
            if not (
                isinstance(region_key, str)
                and region_key in region_keys
                and isinstance(resource_key, str)
                and resource_key in resource_keys
            ):
                item["region_key"] = _redacted_goal_identity(region_key)
                item["resource_key"] = _redacted_goal_identity(resource_key)
        elif kind == "DERIVED_STATE":
            derived_key = item.get("derived_key")
            if not isinstance(derived_key, str) or derived_key not in derived_keys:
                item["derived_key"] = _redacted_goal_identity(derived_key)
                _omit_goal_requirement_value(item)
        elif kind == "ACTION_COMPLETED":
            action_key = item.get("action_key")
            actor_key = item.get("actor_key")
            target_key = item.get("target_key")
            is_public = (
                isinstance(action_key, str)
                and action_key in action_keys
                and (actor_key is None or actor_key in actor_keys)
                and (target_key is None or target_key in {*node_keys, *region_keys, *actor_keys})
            )
            raw_bindings = item.get("binding_constraints", [])
            bindings_public = isinstance(raw_bindings, list) and all(
                isinstance(binding, dict)
                and binding.get("role") in {"source_region", "destination_region"}
                and binding.get("value") in region_keys
                for binding in raw_bindings
            )
            is_public = is_public and bindings_public
            if not is_public:
                for field in (
                    "action_key",
                    "actor_key",
                    "target_key",
                    "binding_constraints",
                    "parameter_constraints",
                ):
                    if field in item:
                        item[field] = _redacted_goal_identity(item[field])
        safe_requirements.append(item)
    result["requirements"] = safe_requirements
    return result


def _public_ontology_string_keys(value: object, *, key_name: str) -> set[str]:
    if not isinstance(value, (list, tuple)):
        return set()
    keys: set[str] = set()
    for item in value:
        if isinstance(item, str):
            keys.add(item)
        elif isinstance(item, dict) and isinstance(item.get(key_name), str):
            keys.add(item[key_name])
    return keys


def _redacted_goal_identity(value: object) -> object:
    if (
        isinstance(value, dict)
        and value.get("value_omitted") is True
        and isinstance(value.get("json_type"), str)
    ):
        return value
    return {
        "json_type": _safe_json_type(value) or "unknown",
        "value_omitted": True,
    }


def _omit_goal_requirement_value(item: dict[str, object]) -> None:
    if "accepted_values" in item:
        value = item["accepted_values"]
        item["accepted_values"] = _redacted_goal_identity(value)


def _bounded_goal_snapshot(value: object, *, max_bytes: int) -> dict[str, object]:
    """Bound a safe Goal snapshot by encoded size without retaining raw input."""

    snapshot = value if isinstance(value, dict) else {"json_type": _safe_json_type(value)}
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    original_size = len(encoded.encode("utf-8"))
    if original_size <= max_bytes:
        return snapshot

    # The recursively safe snapshot is already depth/item/string capped.  If
    # its encoded form is still too large, retain only a small shape marker and
    # size evidence; never slice the provider's raw response or prompt text.
    truncated: dict[str, object] = {
        "truncated": True,
        "original_size": original_size,
        "snapshot": {"json_type": "object", "value_omitted": True},
        "stored_size": 0,
    }
    for _ in range(3):
        stored_size = len(
            json.dumps(truncated, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        if truncated["stored_size"] == stored_size:
            break
        truncated["stored_size"] = stored_size
    return truncated


def _compact_goal_catalog_snapshot(value: object) -> object:
    """Keep public catalog identities visible when a debug snapshot is large.

    The live grounding request retains the full public descriptions.  Debug
    telemetry only needs the stable catalog identity and shape to explain a
    grounding decision, so omit descriptive text before applying the hard
    snapshot byte bound.  Oversized arbitrary caller payloads still use the
    existing whole-snapshot truncation behavior.
    """

    if not isinstance(value, dict):
        return value
    catalog = value.get("public_catalog")
    if not isinstance(catalog, dict):
        return value
    compact_catalog = dict(catalog)
    for collection_key in ("entities", "regions", "references"):
        collection = compact_catalog.get(collection_key)
        if not isinstance(collection, list):
            continue
        compact_catalog[collection_key] = [
            {key: item_value for key, item_value in item.items() if key != "description"}
            if isinstance(item, dict)
            else item
            for item in collection
        ]
    compact = dict(value)
    compact["public_catalog"] = compact_catalog
    return compact


def _safe_goal_snapshot(value: object, *, depth: int) -> object:
    if depth >= _GOAL_SNAPSHOT_MAX_DEPTH:
        return {
            "json_type": _safe_json_type(value) or "unknown",
            "truncated": True,
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_GOAL_SNAPSHOT_MAX_STRING]
    if isinstance(value, (list, tuple)):
        items = [
            _safe_goal_snapshot(item, depth=depth + 1) for item in value[:_GOAL_SNAPSHOT_MAX_ITEMS]
        ]
        if len(value) > _GOAL_SNAPSHOT_MAX_ITEMS:
            items.append({"truncated": True, "remaining": len(value) - _GOAL_SNAPSHOT_MAX_ITEMS})
        return items
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _GOAL_SNAPSHOT_MAX_ITEMS:
                result["_truncated"] = True
                break
            if not isinstance(key, str):
                continue
            if key.casefold() in _SENSITIVE_SNAPSHOT_KEYS:
                continue
            result[key[:160]] = _safe_goal_snapshot(item, depth=depth + 1)
        return result
    return {"json_type": _safe_json_type(value) or "unknown"}


def _safe_goal_response_mapping(
    value: dict[str, object],
    *,
    allowed: set[str],
    nested_allowed: set[str],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= _GOAL_SNAPSHOT_MAX_ITEMS:
            result["_truncated"] = True
            break
        if not isinstance(key, str) or key.casefold() in _SENSITIVE_SNAPSHOT_KEYS:
            continue
        if key in allowed:
            if key in {"candidate_refs", "requirements"}:
                if isinstance(item, (list, tuple)):
                    result[key] = [
                        _safe_goal_response_mapping(
                            nested,
                            allowed=nested_allowed,
                            nested_allowed=set(),
                        )
                        if isinstance(nested, dict)
                        else {"json_type": _safe_json_type(nested) or "unknown"}
                        for nested in item[:_GOAL_SNAPSHOT_MAX_ITEMS]
                    ]
                else:
                    result[key] = {"json_type": _safe_json_type(item) or "unknown"}
            else:
                result[key] = _safe_goal_snapshot(item, depth=1)
        else:
            # Keep the field name and its shape so schema failures such as an
            # unexpected ``target_value`` are diagnosable without retaining
            # the rejected value itself.
            result[key[:160]] = {
                "json_type": _safe_json_type(item) or "unknown",
                "value_omitted": True,
            }
    return result


def _goal_provider_metadata(
    purpose: str,
    payload: dict[str, object],
    *,
    include_debug_snapshot: bool,
) -> dict[str, Any]:
    if purpose not in _GOAL_PROVIDER_PURPOSES:
        return {}
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    catalog = payload.get("public_catalog")
    ontology = payload.get("ontology")
    metadata: dict[str, Any] = {
        "prompt_template_version": _GOAL_PROMPT_TEMPLATE_VERSIONS[purpose],
        "request_hash": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "public_catalog_hash": (
            hashlib.sha256(
                json.dumps(
                    catalog,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if isinstance(catalog, dict)
            else None
        ),
        "focused_ontology_hash": (
            hashlib.sha256(
                json.dumps(
                    ontology,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if isinstance(ontology, dict)
            else None
        ),
        "recovery_attempt": (
            payload.get("recovery_attempt")
            if isinstance(payload.get("recovery_attempt"), int)
            else None
        ),
    }
    if include_debug_snapshot:
        metadata["debug_snapshot"] = {
            "input": goal_provider_request_snapshot(purpose, payload),
        }
    return metadata


class ProviderTotalTimeout(TimeoutError):
    """The provider call exceeded an active plan or Goal deadline."""

    def __init__(self, timeout_category: str = "PLAN_INVOCATION_DEADLINE") -> None:
        super().__init__(timeout_category)
        self.timeout_category = timeout_category


class _ProviderPhaseTelemetry:
    """Thread-safe phase timestamps for one synchronous HTTP request.

    HTTPX exposes response hooks after transport headers are available, and
    the response stream yields body chunks before the non-streaming client
    call returns.  It does not expose a reliable transport-level
    "request bytes sent" callback, so that field intentionally remains null.
    """

    def __init__(self, *, request_started_at: str) -> None:
        self._lock = Lock()
        self._request_started_at = request_started_at
        self._request_send_completed_at: str | None = None
        self._response_headers_received_at: str | None = None
        self._first_response_byte_at: str | None = None
        self._response_bytes_received: int | None = None
        self._request_cancelled_at: str | None = None
        self._timeout_subtype: str | None = None

    def mark_response_headers_received(self) -> None:
        with self._lock:
            self._response_headers_received_at = datetime.now(UTC).isoformat()
            self._response_bytes_received = 0

    def mark_response_chunk(self, byte_count: int) -> None:
        if byte_count <= 0:
            return
        with self._lock:
            if self._first_response_byte_at is None:
                self._first_response_byte_at = datetime.now(UTC).isoformat()
            self._response_bytes_received = (self._response_bytes_received or 0) + byte_count

    def mark_timeout(self, timeout_subtype: str) -> None:
        with self._lock:
            self._request_cancelled_at = datetime.now(UTC).isoformat()
            self._timeout_subtype = timeout_subtype

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "request_started_at": self._request_started_at,
                "request_send_completed_at": self._request_send_completed_at,
                "response_headers_received_at": self._response_headers_received_at,
                "first_response_byte_at": self._first_response_byte_at,
                "response_bytes_received": self._response_bytes_received,
                "request_cancelled_at": self._request_cancelled_at,
                "timeout_subtype": self._timeout_subtype,
            }


_RETRYABLE_TRANSPORT_ERRORS = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.CloseError,
    httpx.RemoteProtocolError,
)

_MAX_TRANSPORT_RETRIES = 1


def _is_retryable_transport_error(error: httpx.HTTPError) -> bool:
    """Return whether one incomplete transport call may be retried once."""

    return isinstance(error, _RETRYABLE_TRANSPORT_ERRORS) and not isinstance(
        error, httpx.TimeoutException
    )


class _TelemetryResponseStream(httpx.SyncByteStream):
    """Count body bytes delivered by HTTPX without changing the stream."""

    def __init__(self, stream: httpx.SyncByteStream, telemetry: _ProviderPhaseTelemetry) -> None:
        self._stream = stream
        self._telemetry = telemetry

    def __iter__(self) -> Iterator[bytes]:
        try:
            for chunk in self._stream:
                self._telemetry.mark_response_chunk(len(chunk))
                yield chunk
        except httpx.TimeoutException as exc:
            self._telemetry.mark_timeout(type(exc).__name__)
            raise

    def close(self) -> None:
        self._stream.close()


class OpenAICompatibleGenericProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if settings.model_api_key is None or not settings.model_api_key.get_secret_value().strip():
            raise GenericProviderError(
                "MODEL_PROVIDER_CONFIGURATION_INVALID",
                "MODEL_API_KEY is required for the configured model provider",
            )
        self._base_url = settings.model_base_url.rstrip("/")
        self._api_key = settings.model_api_key.get_secret_value()
        self._model_name = settings.model_name
        self._semantic_model_name = settings.semantic_model or self._model_name
        self._goal_resolution_observability = settings.goal_resolution_observability
        self._plan_timeout = settings.plan_timeout_seconds
        self._plan_total_timeout = settings.plan_total_timeout_seconds
        self._max_output_tokens = settings.model_max_output_tokens
        self._thinking_mode = settings.model_thinking_mode
        self._reasoning_effort = settings.model_reasoning_effort
        self._default_profile = _ProviderProfile(
            name="DEFAULT",
            model_name=self._model_name,
            thinking_mode=self._thinking_mode,
            reasoning_effort=self._reasoning_effort,
            output_token_limit=None,
        )
        self._planning_profile = _ProviderProfile(
            name="PLANNING_REASONING",
            model_name=self._model_name,
            thinking_mode=self._thinking_mode,
            reasoning_effort=self._reasoning_effort,
            output_token_limit=self._max_output_tokens,
        )
        self._fast_semantic_profile = _ProviderProfile(
            name="FAST_SEMANTIC",
            model_name=self._semantic_model_name,
            thinking_mode="disabled",
            reasoning_effort="low",
            output_token_limit=_FAST_SEMANTIC_OUTPUT_TOKEN_LIMIT,
        )
        self._purpose_profiles: dict[str, _ProviderProfile] = {
            "goal_selection": self._default_profile,
            "initial_plan": self._planning_profile,
            "repair": self._planning_profile,
            "replan": self._planning_profile,
            "dynamic_goal_grounding": self._fast_semantic_profile,
            "dynamic_goal": self._fast_semantic_profile,
            "dynamic_goal_family": self._fast_semantic_profile,
            "dynamic_goal_action": self._fast_semantic_profile,
            "dynamic_goal_operation": self._fast_semantic_profile,
            "dynamic_goal_family_routing": self._fast_semantic_profile,
            "dynamic_goal_action_routing": self._fast_semantic_profile,
            "dynamic_goal_routing": self._fast_semantic_profile,
        }
        self._transport = transport
        self._last_call_metadata: ProviderCallMetadata | None = None
        self._call_sequence = 0
        self._call_metadata_history: list[ProviderCallMetadata] = []

    @property
    def provider_name(self) -> str:
        return "openai_compatible"

    @property
    def thinking_mode(self) -> str:
        return self._thinking_mode

    @property
    def reasoning_effort(self) -> str:
        return self._reasoning_effort

    @property
    def configured_output_token_limit(self) -> int | None:
        return self._max_output_tokens

    @property
    def http_timeout_seconds(self) -> float | None:
        return None

    @property
    def plan_timeout_seconds(self) -> float:
        return self._plan_timeout

    @property
    def plan_total_timeout_seconds(self) -> float | None:
        return self._plan_total_timeout

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def goal_resolution_observability(self) -> str:
        return self._goal_resolution_observability

    @property
    def last_call_metadata(self) -> ProviderCallMetadata | None:
        return self._last_call_metadata

    @property
    def call_metadata_history(self) -> tuple[ProviderCallMetadata, ...]:
        return tuple(self._call_metadata_history)

    def _record_call_metadata(self, metadata: ProviderCallMetadata) -> None:
        self._last_call_metadata = metadata
        self._call_metadata_history.append(metadata)

    def _record_validation_diagnostics(self, diagnostics: tuple[dict[str, object], ...]) -> None:
        if not diagnostics or not self._call_metadata_history:
            return
        metadata = self._call_metadata_history[-1].model_copy(
            update={"validation_diagnostics": diagnostics}
        )
        self._call_metadata_history[-1] = metadata
        self._last_call_metadata = metadata

    def _record_goal_response_snapshot(
        self,
        response_snapshot: dict[str, object],
        *,
        validation: str,
    ) -> None:
        if not self._call_metadata_history:
            return
        previous_snapshot = self._call_metadata_history[-1].debug_snapshot
        if previous_snapshot is None:
            return
        debug_snapshot = dict(previous_snapshot)
        debug_snapshot["output"] = response_snapshot
        debug_snapshot["response_validation"] = validation
        metadata = self._call_metadata_history[-1].model_copy(
            update={
                "debug_snapshot": debug_snapshot,
                "response_validation": validation,
            }
        )
        self._call_metadata_history[-1] = metadata
        self._last_call_metadata = metadata

    def _record_response_validation(self, validation: str) -> None:
        if not self._call_metadata_history:
            return
        metadata = self._call_metadata_history[-1].model_copy(
            update={"response_validation": validation}
        )
        self._call_metadata_history[-1] = metadata
        self._last_call_metadata = metadata

    def _next_call_sequence(self) -> int:
        self._call_sequence += 1
        return self._call_sequence

    def _profile_for_purpose(self, purpose: str) -> _ProviderProfile:
        return self._purpose_profiles.get(purpose, self._default_profile)

    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection:
        try:
            return GoalSelection.model_validate(
                self._invoke("goal_selection", request.model_dump())
            )
        except ValidationError as exc:
            raise GenericProviderError(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "The model provider returned an invalid Goal selection",
            ) from exc

    def match_dynamic_goal_family(self, request: GoalFamilyMatchRequest) -> GoalFamilyMatch:
        try:
            return GoalFamilyMatch.model_validate(
                self._invoke("dynamic_goal_family", request.model_dump(mode="json"))
            )
        except ValidationError as exc:
            raise GenericProviderError(
                "PROVIDER_SCHEMA_INVALID",
                "The model provider returned an invalid Goal family match",
                validation_diagnostics=provider_validation_diagnostics(exc),
            ) from exc

    def decide_dynamic_goal_family(
        self, request: DynamicGoalFamilyRoutingRequest
    ) -> DynamicGoalFamilyRouting:
        raw = self._invoke("dynamic_goal_family_routing", request.model_dump(mode="json"))
        try:
            result = DynamicGoalFamilyRouting.model_validate(raw)
        except ValidationError as exc:
            diagnostics = provider_validation_diagnostics(exc)
            self._record_validation_diagnostics(diagnostics)
            self._record_response_validation("REJECTED")
            if self._goal_resolution_observability == "DEBUG":
                self._record_goal_response_snapshot(
                    goal_provider_response_snapshot("dynamic_goal_family_routing", raw),
                    validation="REJECTED",
                )
            raise GenericProviderError(
                "PROVIDER_SCHEMA_INVALID",
                "The model provider returned invalid family routing",
                validation_diagnostics=diagnostics,
            ) from exc
        self._record_response_validation("ACCEPTED")
        if self._goal_resolution_observability == "DEBUG":
            self._record_goal_response_snapshot(
                goal_provider_response_snapshot("dynamic_goal_family_routing", result),
                validation="ACCEPTED",
            )
        return result

    def route_dynamic_goal_action(
        self, request: DynamicGoalActionRoutingRequest
    ) -> DynamicGoalActionRouting:
        raw = self._invoke("dynamic_goal_action_routing", request.model_dump(mode="json"))
        raw = _normalize_dynamic_goal_action_routing(raw)
        try:
            result = DynamicGoalActionRouting.model_validate(raw)
        except ValidationError as exc:
            diagnostics = _dynamic_goal_action_routing_validation_diagnostics(raw, exc)
            self._record_validation_diagnostics(diagnostics)
            self._record_response_validation("REJECTED")
            if self._goal_resolution_observability == "DEBUG":
                self._record_goal_response_snapshot(
                    goal_provider_response_snapshot("dynamic_goal_action_routing", raw),
                    validation="REJECTED",
                )
            raise GenericProviderError(
                "PROVIDER_SCHEMA_INVALID",
                "The model provider returned invalid Action routing",
                validation_diagnostics=diagnostics,
            ) from exc
        self._record_response_validation("ACCEPTED")
        if self._goal_resolution_observability == "DEBUG":
            self._record_goal_response_snapshot(
                goal_provider_response_snapshot("dynamic_goal_action_routing", result),
                validation="ACCEPTED",
            )
        return result

    def route_dynamic_goal(
        self, request: DynamicGoalSemanticRoutingRequest
    ) -> DynamicGoalSemanticRouting:
        raw = self._invoke("dynamic_goal_routing", request.model_dump(mode="json"))
        raw = _normalize_dynamic_goal_semantic_routing(raw)
        try:
            result = DynamicGoalSemanticRouting.model_validate(raw)
        except ValidationError as exc:
            diagnostics = _dynamic_goal_routing_validation_diagnostics(raw, exc)
            self._record_validation_diagnostics(diagnostics)
            self._record_response_validation("REJECTED")
            if self._goal_resolution_observability == "DEBUG":
                self._record_goal_response_snapshot(
                    goal_provider_response_snapshot("dynamic_goal_routing", raw),
                    validation="REJECTED",
                )
            raise GenericProviderError(
                "PROVIDER_SCHEMA_INVALID",
                "The model provider returned invalid semantic routing",
                validation_diagnostics=diagnostics,
            ) from exc
        self._record_response_validation("ACCEPTED")
        if self._goal_resolution_observability == "DEBUG":
            self._record_goal_response_snapshot(
                goal_provider_response_snapshot("dynamic_goal_routing", result),
                validation="ACCEPTED",
            )
        return result

    def match_dynamic_goal_action(
        self, request: DynamicGoalActionMatchRequest
    ) -> DynamicGoalActionMatch:
        try:
            return DynamicGoalActionMatch.model_validate(
                self._invoke("dynamic_goal_action", request.model_dump(mode="json"))
            )
        except ValidationError as exc:
            raise GenericProviderError(
                "PROVIDER_SCHEMA_INVALID",
                "The model provider returned an invalid Action match",
                validation_diagnostics=provider_validation_diagnostics(exc),
            ) from exc

    def ground_dynamic_goal_operation(
        self, request: DynamicGoalOperationGroundingRequest
    ) -> DynamicGoalOperationGrounding:
        raw = self._invoke("dynamic_goal_operation", request.model_dump(mode="json"))
        normalized = _normalize_dynamic_goal_operation_grounding(raw, request)
        try:
            result = DynamicGoalOperationGrounding.model_validate(normalized)
            _validate_dynamic_goal_operation_recovery_preservation(
                result,
                request.recovery_feedback,
            )
        except ValidationError as exc:
            diagnostics = _dynamic_goal_operation_validation_diagnostics(
                normalized,
                exc,
                request,
            )
            self._record_validation_diagnostics(diagnostics)
            self._record_response_validation("REJECTED")
            if self._goal_resolution_observability == "DEBUG":
                self._record_goal_response_snapshot(
                    goal_provider_response_snapshot("dynamic_goal_operation", raw),
                    validation="REJECTED",
                )
            raise GenericProviderError(
                "PROVIDER_SCHEMA_INVALID",
                "The model provider returned invalid Action-contract grounding",
                validation_diagnostics=diagnostics,
            ) from exc
        except ValueError as exc:
            diagnostics = (
                {
                    "code": "RECOVERY_CHANGED_PRESERVED_FIELD",
                    "expected": "preserve every field named by recovery_feedback.preserve",
                },
            )
            self._record_validation_diagnostics(diagnostics)
            self._record_response_validation("REJECTED")
            if self._goal_resolution_observability == "DEBUG":
                self._record_goal_response_snapshot(
                    goal_provider_response_snapshot("dynamic_goal_operation", normalized),
                    validation="REJECTED",
                )
            raise GenericProviderError(
                "PROVIDER_SCHEMA_INVALID",
                "Operation recovery changed an already correct frozen field",
                validation_diagnostics=diagnostics,
            ) from exc
        self._record_response_validation("ACCEPTED")
        if self._goal_resolution_observability == "DEBUG":
            self._record_goal_response_snapshot(
                goal_provider_response_snapshot("dynamic_goal_operation", result),
                validation="ACCEPTED",
            )
        return result

    def ground_dynamic_goal_entities(
        self, request: DynamicGoalEntityGroundingRequest
    ) -> DynamicGoalEntityGrounding:
        raw: object | None = None
        normalized: object | None = None
        try:
            raw = self._invoke(
                "dynamic_goal_grounding",
                request.model_dump(mode="json"),
            )
            normalized = _normalize_dynamic_goal_grounding_wire(raw)
            result = DynamicGoalEntityGrounding.model_validate(normalized)
        except GenericProviderError:
            raise
        except (ValidationError, TypeError, ValueError) as exc:
            diagnostics = (
                provider_validation_diagnostics(exc) if isinstance(exc, ValidationError) else ()
            )
            recovery_feedback = dynamic_goal_grounding_recovery_feedback(
                normalized if normalized is not None else raw,
                validation_diagnostics=diagnostics,
                public_catalog=request.public_catalog,
            )
            self._record_response_validation("REJECTED")
            if diagnostics:
                self._record_validation_diagnostics(diagnostics)
            if self._goal_resolution_observability == "DEBUG":
                self._record_goal_response_snapshot(
                    goal_provider_response_snapshot(
                        "dynamic_goal_grounding",
                        normalized if normalized is not None else raw,
                        public_catalog=request.public_catalog,
                    ),
                    validation="REJECTED",
                )
            raise GenericProviderError(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "The model provider returned invalid Dynamic Goal semantic evidence",
                validation_diagnostics=diagnostics,
                grounding_recovery_feedback=recovery_feedback,
            ) from exc
        if self._goal_resolution_observability == "DEBUG":
            self._record_goal_response_snapshot(
                goal_provider_response_snapshot(
                    "dynamic_goal_grounding",
                    result,
                    public_catalog=request.public_catalog,
                ),
                validation="ACCEPTED",
            )
        return result

    def interpret_dynamic_goal(
        self, request: DynamicGoalInterpretationRequest
    ) -> DynamicGoalInterpretation:
        raw = self._invoke("dynamic_goal", request.model_dump(mode="json"))
        try:
            result = DynamicGoalInterpretation.model_validate(raw)
        except ValidationError as exc:
            if self._goal_resolution_observability == "DEBUG":
                self._record_goal_response_snapshot(
                    goal_provider_response_snapshot(
                        "dynamic_goal",
                        raw,
                        public_ontology=request.ontology,
                    ),
                    validation="REJECTED",
                )
            diagnostics = provider_validation_diagnostics(exc)
            self._record_validation_diagnostics(diagnostics)
            raise GenericProviderError(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "The model provider returned an invalid Dynamic Goal interpretation",
                validation_diagnostics=diagnostics,
                recovery_feedback=dynamic_goal_recovery_feedback(
                    raw,
                    public_ontology=request.ontology,
                ),
            ) from exc
        if self._goal_resolution_observability == "DEBUG":
            self._record_goal_response_snapshot(
                goal_provider_response_snapshot(
                    "dynamic_goal",
                    result,
                    public_ontology=request.ontology,
                ),
                validation="ACCEPTED",
            )
        return result

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        try:
            raw = self._invoke(request.call_type.lower(), request.provider_payload())
            # Keep the canonical PlanSegment model strict while accepting a
            # response shape produced by providers configured before the
            # auditable intent labels were introduced.  New responses are
            # still required by the prompt to include all three labels; this
            # narrow boundary normalization prevents old fixtures and stored
            # provider responses from becoming unparsable during rollout.
            if isinstance(raw, dict) and not any(
                field in raw for field in ("segment_goal", "goal_link", "continuation_intent")
            ):
                raw = {
                    **raw,
                    "segment_goal": "Advance the current Objective segment",
                    "goal_link": "Re-evaluate against the frozen Formal Goal and active dependency",
                    "continuation_intent": (
                        "Continue the unfinished mainline toward the frozen Objective"
                    ),
                }
            return PlanProposal.model_validate(raw)
        except ValidationError as exc:
            raise GenericProviderError(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "The model provider returned an invalid Plan proposal",
            ) from exc

    def _build_request_body(
        self, purpose: str, payload: dict[str, object]
    ) -> tuple[dict[str, object], int]:
        profile = self._profile_for_purpose(purpose)
        if purpose == "dynamic_goal_family_routing":
            response_contract = (
                'STATE/OPERATION={"family":"STATE|OPERATION",'
                '"clarification_prompt":null}; '
                'AMBIGUOUS={"family":"AMBIGUOUS",'
                '"clarification_prompt":"focused question"}'
            )
        elif purpose == "dynamic_goal_action_routing":
            response_contract = (
                "exactly one mutually exclusive variant: "
                'MATCHED={"action_match":"MATCHED",'
                '"action_key":"exact_public_action_key","candidate_keys":[],'
                '"clarification_prompt":null,"no_match_reason":null}; '
                'AMBIGUOUS={"action_match":"AMBIGUOUS","action_key":null,'
                '"candidate_keys":["public_action_key_1","public_action_key_2"],'
                '"clarification_prompt":null,"no_match_reason":null}; '
                'NO_MATCH={"action_match":"NO_MATCH","action_key":null,'
                '"candidate_keys":[],"clarification_prompt":null,'
                '"no_match_reason":"NO_SEMANTIC_ACTION|SEMANTIC_CONFLICT"}'
            )
        elif purpose == "dynamic_goal_routing":
            response_contract = (
                "exactly one mutually exclusive variant: "
                'MATCHED={"family":"OPERATION","action_match":"MATCHED",'
                '"action_key":"exact_public_action_key","candidate_keys":[],'
                '"clarification_prompt":null}; '
                'AMBIGUOUS_ACTION={"family":"OPERATION","action_match":"AMBIGUOUS",'
                '"action_key":null,"candidate_keys":['
                '"public_action_key_1","public_action_key_2"],'
                '"clarification_prompt":null}; '
                'NO_MATCH={"family":"OPERATION","action_match":"NO_MATCH",'
                '"action_key":null,"candidate_keys":[],"clarification_prompt":null}; '
                'STATE_OR_AMBIGUOUS_FAMILY={"family":"STATE|AMBIGUOUS",'
                '"action_match":null,"action_key":null,"candidate_keys":[],'
                '"clarification_prompt":"string|null"}'
            )
        elif purpose == "dynamic_goal_family":
            response_contract = '{"family":"STATE|OPERATION|AMBIGUOUS","clarification_prompt":null}'
        elif purpose == "dynamic_goal_action":
            response_contract = (
                '{"frozen_family":"OPERATION",'
                '"status":"GROUNDED|UNRESOLVED|UNSUPPORTED",'
                '"action_key":"exact_public_action_key|null",'
                '"clarification_prompt":null}'
            )
        elif purpose == "dynamic_goal_operation":
            response_contract = (
                '{"frozen_family":"OPERATION",'
                '"status":"RESOLVED|NEEDS_CLARIFICATION|UNSUPPORTED",'
                '"intent":{"frozen_family":"OPERATION","action_key":"exact_frozen_key",'
                '"actor":{"slot_key":"actor","expected_type":"ACTOR",'
                '"status":"GROUNDED|UNRESOLVED|NOT_SPECIFIED","ref_type":null,'
                '"key":null,"value":null,"surface":null},'
                '"target":{"slot_key":"target","expected_type":"contract type",'
                '"status":"GROUNDED|UNRESOLVED|NOT_SPECIFIED","ref_type":null,'
                '"key":null,"value":null,"surface":null},'
                '"bindings":[],"parameters":[]},'
                '"supplementary_candidate_refs":[],"clarification_prompt":null}'
                ". Every slot object may contain only slot_key, expected_type, status, "
                "ref_type, key, value, and surface. A GROUNDED semantic-reference slot uses "
                "canonical ref_type/key and value=null. A GROUNDED scalar slot uses value and "
                "ref_type/key=null. UNRESOLVED and NOT_SPECIFIED use ref_type/key/value=null. "
                "UNRESOLVED is a slot status, never a top-level status. NEEDS_CLARIFICATION or "
                "UNSUPPORTED must use intent=null"
            )
        elif purpose == "goal_selection":
            response_contract = (
                '{"status":"SELECTED|NEEDS_CLARIFICATION|UNSUPPORTED",'
                '"objective_keys":["zero_or_more_candidate_keys"],'
                '"clarification_prompt":null}'
            )
        elif purpose == "dynamic_goal_grounding":
            response_contract = (
                '{"status":"RESOLVED|NEEDS_CLARIFICATION|UNSUPPORTED",'
                '"candidate_refs":[{"ref_type":"NODE|REGION|RESOURCE|DERIVED_STATE|ACTION|ACTOR",'
                '"key":"public_scenario_reference_key",'
                '"match_semantics":"EXACT_OR_AUTHORED|SEMANTIC_EQUIVALENT|RELATED_ONLY|null"}],'
                '"intent":{"intent_kind":"STATE|OPERATION",'
                '"action":{"status":"GROUNDED|UNRESOLVED|NOT_SPECIFIED",'
                '"ref_type":"ACTION|null","key":"public_action_key|null",'
                '"value":null,"surface":null,"match_semantics":"EXACT_OR_AUTHORED|'
                'SEMANTIC_EQUIVALENT|RELATED_ONLY|null"},'
                '"actor":{"status":"GROUNDED|UNRESOLVED|NOT_SPECIFIED",'
                '"ref_type":"ACTOR|null","key":"public_actor_key|null",'
                '"value":null,"surface":null,"match_semantics":"EXACT_OR_AUTHORED|'
                'SEMANTIC_EQUIVALENT|RELATED_ONLY|null"},'
                '"source":{"status":"GROUNDED|UNRESOLVED|NOT_SPECIFIED",'
                '"ref_type":"NODE|REGION|ACTOR|null",'
                '"key":"public_source_key|null","value":null,"surface":null,"match_semantics":"'
                'EXACT_OR_AUTHORED|SEMANTIC_EQUIVALENT|RELATED_ONLY|null"},'
                '"target":{"status":"GROUNDED|UNRESOLVED|NOT_SPECIFIED",'
                '"ref_type":"NODE|REGION|ACTOR|null",'
                '"key":"public_target_key|null","value":null,"surface":null,"match_semantics":"'
                'EXACT_OR_AUTHORED|SEMANTIC_EQUIVALENT|RELATED_ONLY|null"},'
                '"resource":{"status":"GROUNDED|UNRESOLVED|NOT_SPECIFIED",'
                '"ref_type":"RESOURCE|null","key":"public_resource_key|null",'
                '"value":null,"surface":null,"match_semantics":"EXACT_OR_AUTHORED|'
                'SEMANTIC_EQUIVALENT|RELATED_ONLY|null"},'
                '"amount":{"status":"GROUNDED|UNRESOLVED|NOT_SPECIFIED",'
                '"value":30,"surface":null}},'
                '"clarification_prompt":null}. '
                "The top level has no goal field and no unknown fields. A RESOLVED response "
                "must contain catalog-backed candidate_refs and a valid intent. A "
                "NEEDS_CLARIFICATION or UNSUPPORTED response must contain no candidate_refs "
                "or intent; NEEDS_CLARIFICATION must include clarification_prompt. "
                "A GROUNDED reference slot requires ref_type and key from public_catalog; "
                "an UNRESOLVED or NOT_SPECIFIED slot carries no key or value. The amount "
                "example uses native JSON integer 30; use the player's actual native JSON "
                "scalar value. A scalar value is a string, integer, or boolean, never an "
                "object or array. Do not place reference identity inside a value object. "
                "For every entity role, surface is the exact player substring that expressed "
                "that role or null; do not synthesize a surface. "
                "match_semantics EXACT_OR_AUTHORED means the same public referent is directly "
                "named or uses an authored alias; SEMANTIC_EQUIVALENT means the same referent "
                "is expressed differently and requires a real player surface; "
                "RELATED_ONLY means merely related, similar, same-category, same-purpose, or "
                "plausible-substitute context and must never be bound to a role or returned as "
                "the player's canonical identity. Provenance is backend-owned and must not be "
                "returned. Runtime-required is not Goal-required: an "
                "omitted Actor, source, or other Planner-owned role is NOT_SPECIFIED, never "
                "UNRESOLVED or GROUNDED. A grounded role must preserve the same referent as its "
                "surface; never replace an unknown object/resource/location with the nearest "
                "public candidate."
            )
        elif purpose == "dynamic_goal":
            response_contract = (
                "The outer response is "
                '{"status":"RESOLVED|NEEDS_CLARIFICATION|UNSUPPORTED",'
                '"requirements":[...],"clarification_prompt":null}. '
                "Each requirements item must use exactly one of these shapes: "
                'FACT {"kind":"FACT","node_key":"<allowed node key>",'
                '"fact_key":"<allowed fact key>",'
                '"accepted_values":["<allowed target value>"]}; '
                'RESOURCE_AT_LEAST {"kind":"RESOURCE_AT_LEAST",'
                '"region_key":"<allowed region key>",'
                '"resource_key":"<allowed resource key>","minimum":0}; '
                'DERIVED_STATE {"kind":"DERIVED_STATE",'
                '"derived_key":"<allowed derived key>",'
                '"accepted_values":["AVAILABLE"]}; '
                'ACTION_COMPLETED {"kind":"ACTION_COMPLETED",'
                '"action_key":"<allowed action key>","actor_key":null,'
                '"target_key":null,"binding_constraints":[],'
                '"parameter_constraints":null,'
                '"match_mode":"ONE_SUCCESSFUL_INVOCATION",'
                '"boundary":"TASK_OWNED_OPERATION"}'
            )
        else:
            response_contract = (
                '{"plan_summary":"short summary",'
                '"segment_goal":"short current segment goal",'
                '"goal_link":"short link to the frozen Goal or active dependency",'
                '"continuation_intent":"short next mainline intent or no continuation",'
                '"stop_reason":"OBJECTIVE_COMPLETION|SEGMENT_COMPLETE|INFORMATION_BOUNDARY|BLOCKED",'
                '"boundary_dependency_id":null,'
                '"steps":[{"step_id":"segment-local-stable-id",'
                '"purpose":"goal-directed step",'
                '"action_key":"existing_action_key",'
                '"actor_key":"existing_actor_key",'
                '"target_key":"existing_target_key",'
                '"parameters":{},"short_actor_reason":"short reason"}]}'
            )
        grounded_operation = payload.get("grounded_operation")
        if purpose == "dynamic_goal" and isinstance(grounded_operation, dict):
            locked_requirement = {
                "kind": "ACTION_COMPLETED",
                **grounded_operation,
                "match_mode": "ONE_SUCCESSFUL_INVOCATION",
                "boundary": "TASK_OWNED_OPERATION",
            }
            response_contract += (
                " The generic alternatives above are overridden for this request because the "
                "user payload contains grounded_operation. It is the universal locked, "
                "lossless public Stage 1 result. Return status RESOLVED with exactly this one "
                "requirement and no additional requirement: "
                f"{json.dumps(locked_requirement, ensure_ascii=False, separators=(',', ':'))}. "
                "Its action_key, actor_key, target_key, binding_constraints, and declared "
                "parameter_constraints must match grounded_operation exactly. Do not return "
                "NEEDS_CLARIFICATION, UNSUPPORTED, FACT, RESOURCE_AT_LEAST, DERIVED_STATE, "
                "a different Action, a partial binding, or any unrelated requirement; actor_key "
                "and target_key null are intentional unconstrained fields and must not trigger "
                "an actor or target clarification."
            )
        if purpose == "dynamic_goal_family_routing":
            planning_prompt = (
                "Decide only the semantic Goal family from the raw player Goal. STATE means the "
                "player requires a public terminal world state but does not require one specific "
                "Action invocation. OPERATION means the requested behavior or Action invocation "
                "itself must occur, even if it has a terminal State effect. Return AMBIGUOUS with "
                "one focused clarification question only when the raw Goal genuinely permits both "
                "families; otherwise return STATE or OPERATION and freeze that choice. Do not "
                "select, match, rank, or infer any "
                "Action or State requirement. No Action catalog or State candidate catalog is "
                "available at this stage. deterministic_candidate_refs and "
                "deterministic_ambiguous_refs are advisory retrieval hints only; "
                "semantic_candidate_refs plus explicit_role_evidence are the canonical semantic "
                "evidence. They do "
                "not contain an upstream Family or Action decision. Candidate availability must "
                "not decide the family. When a "
                "natural expression permits both a reasonable STATE and OPERATION reading, choose "
                "one reasonable reading rather than requesting clarification merely because both "
                "exist. Judge semantic intent, never keywords, substrings, or language-specific "
                "verb rules. An explicit request to perform behavior remains OPERATION even when "
                "the behavior may produce a terminal State; target, Actor, or a plausible side "
                "effect cannot turn an operation into STATE. Conversely, a sentence that only "
                "states an end condition remains STATE and leaves HOW to later planning. On "
                "the request, semantic_family_evidence is backend-computed advisory evidence: "
                "operation_expressed signals that the frozen semantic intent is an operation, "
                "and state_equivalent_available says whether the selected public Action has a "
                "canonical terminal Fact for its grounded target. Use it to resolve a conflict, "
                "but do not treat it as authority or invent an Action or Fact from it. On "
                "recovery fix only the supplied structural validation error and do not add fields "
                "outside the response contract."
            )
        elif purpose == "dynamic_goal_action_routing":
            planning_prompt = (
                "The Goal family is already immutably OPERATION. Perform only semantic Action "
                "selection from action_catalog; never reconsider, restate, or change the family, "
                "and never produce a State requirement. Return MATCHED for exactly one authored "
                "Action whose name, description, and complete contract semantics express the "
                "requested invocation; AMBIGUOUS only when multiple Actions genuinely compete; "
                "or NO_MATCH when none expresses it. Candidate-reference or slot-shape "
                "compatibility establishes only structural applicability, not semantic "
                "equivalence. Merely "
                "accepting a Node target or fitting Regions into slots is insufficient. Within one "
                "Action candidate, an exact Region endpoint pair plus exactly one "
                "TOPOLOGY_ENRICHED Node is one derived target evidence unit, not three competing "
                "identities. public_topology contains only Goal-relevant public evidence; each "
                "transport_endpoint_pair explicitly maps its exact endpoint Regions to its "
                "derived_target Node. Use that relationship and the derived target's public "
                "semantics to understand the requested invocation, without treating the Node as "
                "an exact user mention. This stage selects only the Action identity. It does not "
                "ground the final target or actor, complete bindings or parameters, prove Action "
                "preconditions, decide Runtime executability, or construct or complete a Formal "
                "Goal. When the Action identity is semantically clear, return MATCHED even if a "
                "target is expressed indirectly through typed topology evidence, slots remain for "
                "Operation Grounding, or an execution precondition is not yet established. Those "
                "conditions alone are not reasons for NO_MATCH. Judge "
                "the raw Goal together with authored Action semantics and typed/topology evidence. "
                "deterministic_candidate_refs are advisory retrieval hints; semantic refs and "
                "explicit role evidence own canonical identities and role binding; "
                "never use keywords, substrings, regexes, or language-specific verb rules. "
                "Positive semantic evidence is sufficient for MATCHED when the raw Goal, public "
                "entity name/type/description, authored Action name/description, and typed "
                "contract form one consistent invocation interpretation; the Goal wording need "
                "not literally repeat the Action name. An inspect or survey Action is a comparable "
                "competitor only when the Goal asks to investigate, check, confirm, or otherwise "
                "observe state; a repair, restore, or remediation request is not equally matched "
                "by inspect merely because it accepts the same target. Return NO_MATCH only when "
                "no candidate can express the invocation, using no_match_reason "
                "NO_SEMANTIC_ACTION, or when the explicit request has a real semantic or typed "
                "contract conflict with the candidates, using SEMANTIC_CONFLICT. If multiple "
                "candidates remain genuinely plausible, return AMBIGUOUS. Never use NO_MATCH for "
                "uncertainty, missing preconditions, incomplete slots, or weak wording alone. "
                "Interpret the requested behavior first: Actor and target mentions only constrain "
                "which invocation is legal after the Action meaning is understood; they cannot "
                "substitute a different Action that merely accepts the same entities. Preserve "
                "an explicit operation such as repair, restore, inspect, transport, or supply as "
                "that operation, and do not select travel/survey/inspect solely because its target "
                "shape happens to fit. On recovery preserve every "
                "recovery_feedback.preserve field exactly and change only "
                "recovery_feedback.fix_only fields."
            )
            if isinstance(payload.get("semantic_action_evidence"), dict):
                planning_prompt += (
                    " semantic_action_evidence is backend-computed advisory evidence from the "
                    "earlier Semantic Grounding stage. It identifies the Action expressed by "
                    "the player's surface when that evidence is public and non-contextual. "
                    "It does not select the Action for you: compare the raw Goal with every "
                    "candidate's complete authored name, description, and contract semantics. "
                    "If recovery_feedback reports ACTION_SEMANTIC_EVIDENCE_CONFLICT, re-check "
                    "the upstream Action and the returned candidate independently; do not use "
                    "target or slot compatibility as a substitute for the requested behavior."
                )
        elif purpose == "dynamic_goal_routing":
            planning_prompt = (
                "Compare the finite action_catalog and state_catalog, then perform only two "
                "logically ordered routing decisions. STATE means the player states a desired "
                "public world state. OPERATION means the player explicitly requests one Action "
                "invocation. AMBIGUOUS means both readings genuinely remain. Only for OPERATION, "
                "match an Action from action_catalog; never return an Action outside it. Return "
                "MATCHED with exactly one action_key, AMBIGUOUS with every equally plausible "
                "candidate_key, or NO_MATCH. Do not return OPERATION or NO_MATCH merely because "
                "a STATE Goal has no corresponding Action. STATE and AMBIGUOUS must not carry "
                "Action data. Never ground entities, slots, parameters, Facts, "
                "requirements, or a FormalGoal. Judge the player's semantic intent, not keyword "
                "matching. OPERATION means the requested behavior or Action invocation itself "
                "must happen; even when it produces a public terminal State, preserve OPERATION "
                "and never rewrite it as STATE. STATE means the player only requires the world "
                "to reach a public terminal State and does not require one specific invocation; "
                "how to reach it remains HOW. Once the intent is OPERATION, if exactly one Action "
                "in action_catalog semantically matches the requested invocation, return MATCHED "
                "even when a corresponding terminal State candidate also exists. If multiple "
                "Actions genuinely compete for an OPERATION intent, return OPERATION with "
                "action_match AMBIGUOUS and every candidate_key; if no Action expresses the "
                "requested invocation, return OPERATION with action_match NO_MATCH. Within one "
                "Action candidate's candidate_refs, an exact Region endpoint pair plus exactly "
                "one TOPOLOGY_ENRICHED Node describes one derived target evidence unit; do not "
                "treat the two Regions and that Node as three competing target identities. "
                "Candidate-reference or slot-shape compatibility establishes only structural "
                "applicability, not a semantic Action match. An Action is equally plausible only "
                "when its authored name, description, and contract semantics express the requested "
                "invocation; merely accepting a Node target or fitting Regions into slots is "
                "insufficient. This topology evidence rule does not override the Family decision: "
                "a genuinely terminal-state-only reading may remain STATE. Contrastive "
                "intent examples (not lexical rules): 把30个燃料运到南部 => OPERATION; "
                "南部至少有30个燃料 => STATE; "
                "修复中央河底隧道 => OPERATION; 让中央河底隧道处于可通行状态 => STATE. "
                "The requested behavior is the primary Action signal. Actor, target, and possible "
                "downstream effects only constrain candidate legality; they must never rewrite a "
                "clear operation as another Action or as STATE. "
                "On recovery correct only the supplied structural validation errors. Preserve "
                "every field named by recovery_feedback.preserve exactly, and change only fields "
                "named by recovery_feedback.fix_only."
            )
        elif purpose == "dynamic_goal_family":
            planning_prompt = (
                "Classify only the natural-language Goal family. OPERATION means the player "
                "requires one Action invocation; STATE means a desired world state; AMBIGUOUS "
                "means the sentence genuinely permits both. Do not identify any Action, entity, "
                "parameter, Fact, or requirement."
            )
        elif purpose == "dynamic_goal_action":
            planning_prompt = (
                "The family is immutably OPERATION. Semantically match exactly one Action from "
                "action_catalog using its complete public contract. Return only its exact key. "
                "Do not ground targets, bindings, parameters, or produce a Goal requirement."
            )
        elif purpose == "dynamic_goal_operation":
            planning_prompt = (
                "The family and Action are immutable. Ground only the slots declared by "
                "action_contract. Return every declared target, binding, and parameter slot "
                "exactly once with its declared slot_key and expected_type. GROUNDED means the "
                "player explicitly constrained the slot and it maps uniquely; UNRESOLVED means "
                "the player explicitly constrained it but no unique public value can be chosen; "
                "NOT_SPECIFIED means the player did not constrain it. Runtime-required does not "
                "mean Goal-required. Use canonical public keys for reference slots and native JSON "
                "scalars for scalar slots. Never change the family or Action, invent a slot, infer "
                "an Actor/source/route, or produce STATE requirements. When exact endpoint Regions "
                "and public_topology provide exactly one contract-compatible mapping to target "
                "Node T, the endpoint pair is an explicit indirect constraint on T: return target "
                "GROUNDED with T's canonical key, or leave target UNRESOLVED for deterministic "
                "backend composition. Do not ask the player to repeat the endpoints or name T. "
                "Other same-type Nodes in public_references are ontology context and do not make "
                "that unique mapping ambiguous. With zero or multiple compatible mappings, do not "
                "guess and retain existing unresolved or clarification semantics. If an already "
                "grounded target X differs from T, do not replace or auto-correct X; backend typed "
                "validation owns that conflict. On recovery, correct only the typed schema "
                "mistakes described by recovery_feedback. Do not copy contract-only fields such "
                "as name, description, node_type_keys, or semantic_reference_type into response "
                "slots. Keep every recovery_feedback.preserve path exactly unchanged and edit "
                "only recovery_feedback.fix_only paths; never reselect a correct target, binding, "
                "parameter, frozen_family, or action_key. Use explicit_role_evidence as the "
                "preserved record of which WHAT slots the player expressed; NOT_SPECIFIED stays "
                "unconstrained even when Runtime requires the slot. References from "
                "deterministic_candidate_refs are advisory retrieval hints and must not override "
                "semantic role evidence. TOPOLOGY_ENRICHED references are contextual "
                "evidence, not player constraints and need not occupy a slot. public_references "
                "has already been filtered to the selected contract; do not search beyond it."
            )
        elif purpose == "dynamic_goal_grounding":
            planning_prompt = (
                "This is evidence-only Semantic Grounding. Parse the entire player's Goal against "
                "public_catalog and return candidate_refs plus explicit semantic-role evidence. "
                "Return candidate_refs with ref_type NODE, REGION, RESOURCE, "
                "DERIVED_STATE, ACTION, or ACTOR and keys copied exactly from the public_catalog. "
                "Use the public Action name, description, target_kind, parameter schema, and "
                "operation_binding_contract to understand the sentence; do not rely on a fixed "
                "natural-language verb or source/destination marker vocabulary. Canonical names "
                "and public_references are semantic evidence, not a closed vocabulary or backend "
                "parser rule. Ground every explicit public mention in the whole Goal in this one "
                "pass; do not stop after an easy exact match or omit a remaining semantic mention. "
                "deterministic_candidate_refs and deterministic_ambiguous_refs are advisory "
                "retrieval candidates: keep, replace, or discard them according to whole-Goal "
                "semantics, and never first-pick an ambiguous hint. The compatibility "
                "field intent_kind is not authoritative. A grounded Action slot is only typed "
                "advisory evidence for the downstream Action Router; that router retains final "
                "Action-selection authority. Classify actor, source, target, resource, and amount "
                "as "
                "GROUNDED, UNRESOLVED, or NOT_SPECIFIED. GROUNDED entity slots must use the "
                "typed public key and GROUNDED amount must use the typed value. UNRESOLVED means "
                "the sentence expresses that slot but the public catalog cannot identify it; "
                "NOT_SPECIFIED means the player did not constrain it. A source or target role "
                "may be a binding declared by the Action contract rather than a literal Action "
                "parameter. Keep every slot role-compatible; topology is context, not a "
                "substitute for a typed role. Return all public references selected by semantic "
                "role evidence. If multiple compatible candidates "
                "remain equally plausible, mark the affected slot UNRESOLVED instead of guessing. "
                "NEEDS_CLARIFICATION and UNSUPPORTED are evidence-completeness statuses here, not "
                "product verdicts. A candidate or role marked RELATED_ONLY is context only "
                "(related, similar, same category/purpose, or a plausible substitute) and cannot "
                "become a canonical identity. EXACT_OR_AUTHORED and SEMANTIC_EQUIVALENT both "
                "require the same referent as the player's wording; SEMANTIC_EQUIVALENT also "
                "requires a real player surface. Provider provenance is backend-owned and must "
                "not be returned. If a role was omitted, return NOT_SPECIFIED; "
                "runtime-required is not Goal-required and must never create UNRESOLVED. If an "
                "unknown airport, resource, location, Actor, or source is merely close to a public "
                "object, keep it unresolved (or UNSUPPORTED when it is explicitly non-public) "
                "instead of substituting the nearest candidate. Never invent a key. Stage 1 must "
                "not emit "
                "a Goal requirement, authored Objective, plan, hidden Truth, or chain-of-thought."
            )
            if payload.get("recovery_attempt"):
                planning_prompt += (
                    " This is the one bounded structural recovery attempt. Return JSON only and "
                    "use only the grounding response fields in the contract; remove unknown "
                    "fields and correct JSON/DTO shape errors. Preserve every canonical identity "
                    "listed in recovery_feedback.preserve exactly, and edit only paths listed in "
                    "recovery_feedback.fix_only. Do not invent, replace, or disambiguate a key. "
                    "For GROUNDED reference slots return a catalog ref_type and canonical key; "
                    "for UNRESOLVED or NOT_SPECIFIED slots do not return a key or scalar value. "
                    "If recovery_feedback identifies intent.amount as an object or array, change "
                    "only that slot so value is a native JSON scalar; for example, an explicit "
                    'amount of 30 is {"status":"GROUNDED","value":30,"surface":null}. '
                    "The example shows wire "
                    "shape only: use the player's actual scalar and never interpret fields from "
                    "the rejected object as a canonical identity. "
                    "A non-RESOLVED response must not carry candidate_refs; use a clarification "
                    "prompt for unresolved or ambiguous semantics. Do not return markdown, prose, "
                    "a code fence, a top-level goal, or any unknown field. Preserve canonical "
                    "ref_type/key, valid match_semantics, and valid player surface when already "
                    "present; provenance is backend-owned and must not be returned. RELATED_ONLY "
                    "must stay contextual and cannot be repaired into a grounded identity. For "
                    "an omitted "
                    "role use NOT_SPECIFIED, never UNRESOLVED merely because Runtime requires it. "
                    "If recovery_feedback reports ROLE_EVIDENCE_INVALID, set the named role to "
                    "NOT_SPECIFIED unless the player surface proves that the player named it; "
                    "do not silently guess a canonical value."
                )
                feedback = payload.get("recovery_feedback")
                feedback_codes = (
                    {item.get("code") for item in feedback if isinstance(item, dict)}
                    if isinstance(feedback, (list, tuple))
                    else set()
                )
                if feedback_codes & {
                    "ROLE_MATCH_SEMANTICS_RECHECK",
                    "ROLE_SEMANTIC_GROUNDING_RECHECK",
                }:
                    planning_prompt += (
                        " This recovery is a bounded semantic grounding recheck, not a schema "
                        "repair. Re-evaluate only the roles named by the feedback against the "
                        "complete public_catalog and the raw player Goal. For each named role, "
                        "return GROUNDED + SEMANTIC_EQUIVALENT only when it is the same unique "
                        "public referent expressed with different wording and retain the real "
                        "player surface; use RELATED_ONLY or UNRESOLVED for a merely related, "
                        "similar, same-purpose, same-category, substitute, or non-unique "
                        "candidate. Do not force the disputed canonical key. Reliable exact "
                        "roles and their surfaces listed in preserve must remain unchanged. A "
                        "valid SEMANTIC_EQUIVALENT result is accepted by the normal pipeline "
                        "without another confirmation call."
                    )
        elif purpose == "dynamic_goal":
            planning_prompt = (
                "Interpret the player's Goal only into the closed V2 typed requirement "
                "vocabulary. Return one or more requirements with implicit AND semantics. "
                "Use only FACT, RESOURCE_AT_LEAST, DERIVED_STATE, or ACTION_COMPLETED. Use only "
                "node, fact, region, resource, derived, action, and actor keys present in the "
                "public ontology supplied "
                "by the user payload. "
                "Use only the grounded candidate references and the explicit projection in "
                "the focused ontology. Do not select a reference merely because it exists in "
                "the ScenarioVersion. "
                "For FACT, node_key, fact_key, and non-empty accepted_values are required. "
                "For RESOURCE_AT_LEAST, region_key, resource_key, and integer minimum are "
                "required. For DERIVED_STATE, derived_key and non-empty accepted_values are "
                "required; choose accepted_values from the focused Derived State "
                "target_value or allowed_values. Do not output target_value, null fields, "
                "or fields belonging to another requirement kind. "
                "Match every FACT accepted_values item to that Fact's declared value_type. "
                "For BOOLEAN Facts, emit native JSON true or false, never quoted strings. "
                "For INTEGER Facts, emit native JSON integers, never quoted numeric text. "
                "A natural-language repair, restore, reopen, or make-usable request for a "
                "public entity may map to a compatible terminal-state FACT such as passable=true; "
                "do not require the player to say the machine key. Pure inspect, survey, or "
                "view requests should be expressed as ACTION_COMPLETED when the public Action "
                "and target are grounded; they are not automatically a terminal FACT goal. "
                "For ACTION_COMPLETED, preserve the player's explicit operation semantics "
                "losslessly: emit the public action_key and any explicitly named actor_key, "
                "target_key, Action-defined binding_constraints, and exact schema-valid "
                "parameter_constraints. Read the selected Action's "
                "operation_binding_contract: a binding role is a canonical invocation "
                "role and may be derived from execution context rather than a literal "
                "Action parameter. Map explicit user language to the declared role; for "
                "example, a role sourced from EXECUTION_START_ACTOR_REGION receives the "
                "explicit source Region, while a target contract sourced from "
                "ACTION_TARGET_KEY receives the explicit destination in target_key. Do "
                "not require a literal source or destination parameter when the contract "
                "defines a derived binding. Keep resource and amount values in "
                "parameter_constraints only when they are declared Action parameters, "
                "using their exact schema types. Use ONE_SUCCESSFUL_INVOCATION and "
                "TASK_OWNED_OPERATION. An omitted actor or target remains unconstrained; do "
                "not invent wildcard values. Do not weaken an explicit operation, source, "
                "destination, resource, or amount into a state or resource threshold. If the "
                "public Action contract cannot represent an explicit binding or parameter, "
                "return clarification or unsupported. "
                "Do not invent keys, values outside a supplied Fact domain, Action plans, "
                "prerequisites, routes, knowledge gates, hidden requirements, completion "
                "rules, or any other Scenario semantics. The backend assigns requirement "
                "identity and performs the final exact-Version validation. If the Goal is "
                "ambiguous or cannot be expressed in this vocabulary, return clarification "
                "or unsupported with no requirements. The transient intent payload is provenance "
                "only: GROUNDED means preserve the typed public value, UNRESOLVED means the "
                "explicit mention still needs grounding, and NOT_SPECIFIED means the player did "
                "not constrain that slot. Do not add a source, Actor, route, or other HOW choice "
                "when it is NOT_SPECIFIED. When grounded_operation is present, it is a universal "
                "exact operation lock: preserve every field exactly, return only that one "
                "ACTION_COMPLETED requirement, and never expand the semantics. Do not expose "
                "chain-of-thought."
            )
            if payload.get("frozen_family") == "STATE":
                planning_prompt += (
                    " The Goal family is already frozen as STATE. Return only FACT, "
                    "RESOURCE_AT_LEAST, or DERIVED_STATE requirements allowed by the focused "
                    "ontology. Do not reclassify the family and never return ACTION_COMPLETED, "
                    "an action_key, operation slots, or an operation intent. Requirements must "
                    "be the minimal terminal WHAT directly requested by the player: do not add "
                    "prerequisites, likely consequences, restored capabilities, downstream "
                    "benefits, side effects, or merely related sibling Facts. If one requirement "
                    "already expresses the requested WHAT, return only that requirement. A clear "
                    "operation may use one direct terminal-state equivalent, but never expand it "
                    "into several post-operation states or substitute a related state."
                )
            if payload.get("recovery_attempt"):
                planning_prompt += (
                    " This is the one bounded recovery attempt. Re-read the focused public "
                    "ontology and the safe recovery_feedback in the user payload. Correct "
                    "the indicated requirement shape without changing grounded references "
                    "or broadening the ontology. For DERIVED_STATE, accepted_values is "
                    "required and non-empty; use the focused target value, and do not emit "
                    "target_value. Do not fall back to an authored Objective. For frozen STATE, "
                    "preserve minimal-WHAT semantics: remove added prerequisites, consequences, "
                    "restored capabilities, downstream benefits, side effects, and sibling "
                    "requirements rather than inventing new requirements."
                )
        elif purpose == "repair":
            planning_prompt = (
                "You are repairing a rejected PlanSegment for the same frozen ObjectiveScope. "
                "Return one complete corrected PlanSegment for exactly that scope. Use "
                "validator_violations as the active typed contradictions in the current "
                "rejected proposal; the repaired segment must eliminate every supplied "
                "current violation. A repaired segment must eliminate every supplied "
                "validator violation. "
                "Keep valid parts only when they still fit, but you may "
                "redesign the entire PlanSegment freely from the current canonical "
                "PlannerInput. Do not merely delete an offending Step, shorten the rejected "
                "segment, or return arbitrary remaining content. Every Step must directly "
                "advance the Objective, establish a public prerequisite, obtain Knowledge "
                "needed for an active unresolved dependency, or perform an explicitly "
                "necessary supporting Action. Do not add speculative, preventive, unrelated, "
                "completeness-driven, downstream, sibling, or broader work. Choose every "
                "Action, Actor, Target, Resource source, route, "
                "parameter, and ordering yourself; Validator feedback does not prescribe a "
                "solution. If the supplied diagnostics indicate that a proposal crossed an "
                "information-dependency boundary, state the semantic correction explicitly: "
                "the rejected Action is not legal in the current PlannerInput; do not assume "
                "an earlier survey, inspection, or observation will reveal the value needed "
                "to make it legal; retain other currently legal steps whose validity is "
                "independent of that observation result; and end the segment before the first "
                "result-dependent Action. "
                "Interpret BLOCKED_SEGMENT_HAS_PROGRESS_OPTIONS as proof that BLOCKED was "
                "invalid because legal progress or legal Knowledge acquisition exists. "
                "Interpret BLOCKED_SEGMENT_HAS_STEPS as proof that BLOCKED cannot contain "
                "partial-progress Steps: return steps=[] only for a genuinely blocked "
                "segment, otherwise redesign it as legal progress or information "
                "acquisition. Interpret INFORMATION_BOUNDARY_NOT_RELEVANT as proof that "
                "the selected dependency is not a valid stopping boundary; do not reuse it "
                "unless it genuinely blocks the next legal Target, Resource Source, "
                "Parameter, or Precondition and a legal Knowledge-acquisition Step is "
                "available before the first later Action that consumes its result. "
                "Independent Actions may follow an observation; do not force them out of "
                "the segment. These meanings do not recommend any Actor, Action, Target, route, or "
                "Resource source. Before returning, re-evaluate the complete corrected "
                "segment sequentially against projected Known state. Preserve the same dimension / "
                "required / actual contradiction evidence while eliminating it. Return a complete, "
                "corrected, revalidated PlanSegment. Apply all "
                "declared deterministic effects from earlier Steps before checking later "
                "Steps. Re-check Actor location, command reachability, locality, Target, "
                "parameters, Resource balance, and public preconditions. "
                "anti_regression_memory is historical contradiction evidence only. It does "
                "not prescribe or preserve any previous Action, Actor, Target, Resource "
                "source, route, or ordering. Do not treat an old violation as active unless "
                "the new proposal reintroduces it. The new segment does not reintroduce "
                "contradictions "
                "represented by this memory. You may redesign the entire PlanSegment freely. "
                "planning_continuity is the same frozen "
                "historical Runtime context for this cycle; rejected Repair attempts do not "
                "change it, and canonical PlannerInput overrides it. Do not expose "
                "chain-of-thought."
            )
        elif purpose in {"initial_plan", "replan"}:
            planning_prompt = (
                "Produce one complete PlanSegment for exactly the frozen ObjectiveScope in "
                "the user payload. Every step needs a concrete purpose supported by the current "
                "Knowledge and task state. Do not add speculative or preventive corrective actions "
                "solely to guard against unobserved or uninferred problems. Repair, clearing, "
                "recovery, and remediation actions must address a currently known failure, "
                "blockage, unmet prerequisite, or other concrete problem. Every Step must directly "
                "advance the current Objective, establish a public prerequisite "
                "needed by the Objective, obtain Knowledge needed for an active unresolved "
                "dependency, or perform an explicitly necessary supporting Action. Do not "
                "expand the ObjectiveScope or add downstream, sibling, broader, unrelated, "
                "speculative, preventive, or completeness-driven work. Choose every Action, "
                "Actor, Target, Resource source, route, parameter, and ordering yourself. "
                "The backend will not insert prerequisites, Travel, Relay, transport, "
                "recovery Actions, or routes for you; include them yourself when required. "
                "A valid PlanSegment may compose multiple supporting Actions, repeated "
                "Actions, and multiple Actors when that causal chain is required. When one "
                "Actor establishes a prerequisite another Actor needs, connect those Actions "
                "in causal order. Actor movement, Resource movement, command reachability, "
                "Knowledge acquisition, world-state repair, and the terminal Objective Action "
                "are separate state transitions. Before declaring the Objective blocked, "
                "work backward from the terminal completion requirement, identify unmet "
                "public executor, locality, Resource, Target, parameter, and precondition "
                "requirements, and connect legal supporting Actions until Known state is "
                "connected to the Objective, a genuinely blocking UNKNOWN dependency blocks "
                "the next legal choice, or no legal progress truly exists. This is planning "
                "guidance only; do not expose this reasoning or chain-of-thought. "
                "Future Steps may currently be unavailable when earlier Steps establish "
                "their prerequisites. Before returning a PlanSegment, validate every Step "
                "in order against the projected known state. Apply all declared deterministic "
                "effects from earlier Steps before evaluating each later Step. Every returned "
                "Step must satisfy all currently known executor, command reachability, "
                "locality, Target, parameter, Resource, and precondition requirements in "
                "that projected state. Do not return a Step with a known deterministic "
                "contradiction. The purpose field must describe causal progress or the "
                "public prerequisite this Step establishes, not merely repeat the Action or "
                "destination. short_actor_reason should briefly explain why the selected "
                "Actor is appropriate. Once the frozen Objective can legally be completed "
                "by the projected segment, stop instead of adding broader work."
            )
        else:
            planning_prompt = (
                "Produce one coherent, ordered, complete multi-step plan toward "
                "the frozen ObjectiveScope. You must choose action_key, actor_key, "
                "target_key, parameters, and order yourself. Future steps may be "
                "currently locked or unavailable when earlier steps are expected "
                "to establish their prerequisites."
            )
        generic_guidance = (
            "The authoritative V2 planning state and semantics are "
            "planner_input.objective, planner_input.actors, planner_input.action_contracts, "
            "planner_input.target_bindings, and planner_input.known_world. Canonical "
            "PlannerInput overrides planning_continuity and all legacy projections. Treat "
            "every earlier Step's declared deterministic effects as updates to the projected "
            "known state used by every later Step, including Actor location, command "
            "reachability, Resource balance, Knowledge, and other declared public effects. "
            "You must choose every Action, Actor, Target, Resource source, parameter, route, "
            "and ordering yourself. The backend will not insert prerequisites, Travel, Relay, "
            "transport, recovery Actions, or routes for you. Include them yourself "
            "when required. UNKNOWN is not false, zero, unavailable, or blocked. Never convert "
            "UNKNOWN into Known state by assumption. Travel is one-hop per Step and may be "
            "used repeatedly by the same Actor. The absence of a direct one-hop transport "
            "relation does not by itself mean a destination is unreachable; compose multiple "
            "legal Travel Steps yourself when needed, because the backend will not compute or "
            "insert a multi-hop route. Travel changes only the executing Actor's location and "
            "never moves a Resource. transport_resource is the Region-to-Region Resource "
            "transfer Action: its source is the projected executing Actor Region, its target "
            "is the destination Region, its parameters use the resources[] cargo format, and "
            "a successful transport moves the executing Actor to that destination. Each "
            "transport crosses exactly one legal Transport edge. Legacy resource_key/amount "
            "parameters remain readable. Do not consume or transport Resources whose required "
            "availability is UNKNOWN. A PlanSegment may compose supporting Actions performed "
            "by multiple Actors; apply earlier causal effects before validating later Steps. "
            "If planner_input.known_world.resource_source_hints contains an entry, treat it as "
            "authored public discovery guidance only for the active unresolved Resource need: "
            "it is not a source selection, source whitelist, quantity estimate, Pool identity, "
            "facility state, availability claim, or route. Prefer already-known sufficient "
            "inventory first; never detour to a hinted Region when current known available "
            "inventory satisfies the requirement. When discovery is needed, combine the hint's "
            "primary/candidate ordering with the current Actor locality, public topology, known "
            "route/passability state, and already completed surveys. Hints do not restrict the "
            "legal candidate catalog, and do not require surveying a hinted Region before a "
            "legal MAY_ATTEMPT. Do not infer hidden quantities, availability, facilities, or "
            "observation results from a hint. "
            "Every returned PlanSegment must include short, non-empty "
            "segment_goal, goal_link, and continuation_intent strings, each at most 240 "
            "characters. segment_goal states the direct unresolved dependency or Objective "
            "progress advanced by this segment; goal_link states its relation to the frozen "
            "Formal Goal or active dependency; continuation_intent states the unfinished "
            "mainline to reconsider next, or says that there is no continuation when the "
            "Objective is projected complete. These are user-auditable intent labels, not "
            "chain-of-thought, a second Objective, a Scope mutation, or a commitment to copy "
            "the prior plan. "
            "Use OBJECTIVE_COMPLETION only when current Known state plus projected deterministic "
            "effects legally satisfy the frozen Objective completion requirements; partial "
            "progress is not completion. Use INFORMATION_BOUNDARY only when an UNKNOWN "
            "dependency in planner_input.known_world.unknown_dependencies genuinely blocks "
            "the next legal Target, Resource Source, Parameter, or Precondition choice, and "
            "the segment includes a legal Knowledge-acquisition Action before the first "
            "future Action that would consume that unresolved result. Knowledge-acquisition "
            "Actions do not automatically terminate a PlanSegment: independent currently "
            "legal Actions may follow them. End the segment before the first Action whose "
            "legality, binding, or choice requires the observation result. Do not predict "
            "survey, inspect, or reveal results; continue after the result through the "
            "existing REPLAN lifecycle. Do not perform unrelated, speculative, preventive, "
            "or completeness-driven work, including completeness scans or remote "
            "reconnaissance; do not take remote detours solely for information or add an "
            "Action merely because something is UNKNOWN or nearby. Do not treat a currently "
            "legal Action "
            "as merely convenient when it addresses an active unresolved dependency, can be "
            "executed at the Actor's current locality, requires no additional Travel or "
            "meaningful Resource cost, and deferring it would likely require returning to the "
            "same locality to address that dependency later. When several valid plans make "
            "equivalent Goal progress, prefer the plan that completes such work before leaving "
            "the location, avoiding unnecessary locality teardown, return Travel, or repeated "
            "setup. Apply the same preference before a risky frontier: if a currently legal "
            "Action has attempt_policy MAY_ATTEMPT and Runtime failure could abort the current "
            "PlanSegment, then, without changing real dependency order, prefer placing before "
            "it any already-legal work that is directly related to the frozen Objective or an "
            "active unresolved dependency, can execute at the relevant Actor's current locality, "
            "requires no additional Travel or meaningful Resource cost, and does not depend on "
            "that MAY_ATTEMPT Action succeeding. This reduces the chance that later independent "
            "Steps are SKIPPED after a Runtime failure. Keep any Action whose legality, locality, "
            "Target, parameters, Resource, or public preconditions depend on MAY_ATTEMPT success "
            "after it; do not move a success-dependent suffix earlier. Do not front-load unrelated "
            "preparation merely because a risky Action exists. For example, a current-region "
            "survey or Relay may precede a MAY_ATTEMPT Travel when independent, but a "
            "destination-dependent inspect must remain after that Travel. These are planning "
            "heuristics and "
            "tie-breakers, not backend-mandated sequences: do not turn them into a requirement to "
            "resolve every active dependency "
            "immediately, survey before every departure, inspect every local target, or "
            "eliminate every UNKNOWN state before leaving a Region. Do not predict an "
            "observation result or consume an unresolved observation result in a later "
            "dependent Action in the same PlanSegment; stop at the existing Information "
            "Boundary. Once the frozen Objective can legally be completed, stop and do not "
            "delay completion for optional Knowledge. "
            "boundary_dependency_id must reference "
            "that dependency. General uncertainty, complexity, lack of confidence, or a "
            "attempt_policy MAY_ATTEMPT is legal under uncertainty, not known safe, and is "
            "not an information boundary; Runtime may fail, reveal public passability, leave "
            "the Actor unmoved, and trigger REPLAN. Do not require inspection first or "
            "assume success for an UNKNOWN route. BLOCKED is a "
            "terminal declaration that the incomplete Objective has no legal progress Action "
            "and no legal Knowledge-acquisition Action. If stop_reason is BLOCKED, steps MUST "
            "be empty. If even one legal progress or legal Knowledge-acquisition Action exists, "
            "the segment is not BLOCKED; inability to think of the causal chain is not BLOCKED. "
            "If planning_continuity is present, treat it only as historical planning context "
            "about earlier accepted plans and public Runtime feedback. Use it to retain "
            "still-relevant causal intent from earlier accepted plans. It is not authoritative "
            "state or a commitment to previous Action, Actor, Target, Resource source, route, "
            "or ordering. Re-evaluate every prior purpose against the current canonical "
            "PlannerInput; preserve still-relevant intent when useful, but freely discard or "
            "redesign obsolete intent when current Knowledge invalidates it. "
            "Canonical PlannerInput always overrides planning_continuity."
            if purpose in {"initial_plan", "replan", "repair"}
            else ""
        )
        request_body: dict[str, object] = {
            "model": profile.model_name,
            "thinking": {"type": profile.thinking_mode},
            "reasoning_effort": profile.reasoning_effort,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Return only valid JSON for generic {purpose}. "
                        f"Use exactly this response shape: {response_contract}. "
                        "Select only keys supplied in the user payload; never invent keys. "
                        "For planning, use only entities supplied in planner_input. "
                        f"{planning_prompt} {generic_guidance}"
                        "Keep purpose and actor reason short, omit chain-of-thought, "
                        "and never infer hidden state."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if profile.output_token_limit is not None:
            request_body["max_tokens"] = profile.output_token_limit
        request_size_bytes = len(
            json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        return request_body, request_size_bytes

    def estimate_request_size_bytes(self, purpose: str, payload: dict[str, object]) -> int:
        """Return the exact compact JSON size of the request body, without I/O."""

        _request_body, request_size_bytes = self._build_request_body(purpose, payload)
        return request_size_bytes

    def _post_with_plan_deadline(
        self,
        *,
        url: str,
        headers: dict[str, str],
        request_body: dict[str, object],
        telemetry: _ProviderPhaseTelemetry,
        timeout_seconds: float | None,
    ) -> httpx.Response:
        """Bound the complete synchronous HTTP call independently of HTTPX phases."""

        if timeout_seconds is None:
            return self._post(
                url=url,
                headers=headers,
                request_body=request_body,
                telemetry=telemetry,
            )
        if timeout_seconds <= 0:
            timeout_category = _provider_deadline_timeout_category()
            telemetry.mark_timeout(timeout_category)
            raise ProviderTotalTimeout(timeout_category)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="journey-provider")
        future = executor.submit(
            self._post,
            url=url,
            headers=headers,
            request_body=request_body,
            telemetry=telemetry,
        )
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeout as exc:
            future.cancel()
            timeout_category = _provider_deadline_timeout_category()
            telemetry.mark_timeout(timeout_category)
            raise ProviderTotalTimeout(timeout_category) from exc
        finally:
            # A late upstream result must never keep the planning lifecycle
            # waiting or get a chance to mutate persistence.  The worker is
            # deliberately detached after the caller has crossed the total
            # deadline; its result is ignored.
            executor.shutdown(wait=False, cancel_futures=True)

    def _remaining_timeout(self, started: float) -> float | None:
        """Return the tightest active per-call or Goal-operation budget."""

        deadlines = [
            self._plan_timeout - (perf_counter() - started),
            plan_operation_remaining_seconds(),
            goal_resolution_remaining_seconds(),
        ]
        finite = [value for value in deadlines if value is not None]
        return min(finite) if finite else None

    def _post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        request_body: dict[str, object],
        telemetry: _ProviderPhaseTelemetry,
    ) -> httpx.Response:
        def response_hook(response: httpx.Response) -> None:
            telemetry.mark_response_headers_received()
            if response.is_stream_consumed:
                telemetry.mark_response_chunk(len(response.content))
                return
            if not isinstance(response.stream, httpx.SyncByteStream):
                return
            telemetry_stream = _TelemetryResponseStream(response.stream, telemetry)
            response.stream = telemetry_stream

        try:
            with httpx.Client(
                timeout=None,
                transport=self._transport,
                event_hooks={"response": [response_hook]},
            ) as client:
                return client.post(url, headers=headers, json=request_body)
        except httpx.TimeoutException as exc:
            telemetry.mark_timeout(type(exc).__name__)
            raise

    def _invoke(self, purpose: str, payload: dict[str, object]) -> object:
        profile = self._profile_for_purpose(purpose)
        request_body, request_size_bytes = self._build_request_body(purpose, payload)
        goal_metadata = _goal_provider_metadata(
            purpose,
            payload,
            include_debug_snapshot=self._goal_resolution_observability == "DEBUG",
        )
        started_at = datetime.now(UTC)
        started = perf_counter()
        network_calls: list[dict[str, object]] = []
        response: httpx.Response | None = None
        telemetry: _ProviderPhaseTelemetry | None = None
        final_error: Exception | None = None
        final_response: httpx.Response | None = None
        final_outcome = "ERROR"
        final_error_code = "MODEL_PROVIDER_HTTP_ERROR"
        final_error_category: str | None = None

        for call_index in range(1, _MAX_TRANSPORT_RETRIES + 2):
            call_started_at = datetime.now(UTC)
            call_started = perf_counter()
            telemetry = _ProviderPhaseTelemetry(request_started_at=call_started_at.isoformat())
            response = None
            remaining_timeout = self._remaining_timeout(started)
            try:
                if remaining_timeout is not None and remaining_timeout <= 0:
                    timeout_category = _provider_deadline_timeout_category()
                    telemetry.mark_timeout(timeout_category)
                    raise ProviderTotalTimeout(timeout_category)
                response = self._post_with_plan_deadline(
                    url=f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    request_body=request_body,
                    telemetry=telemetry,
                    timeout_seconds=remaining_timeout,
                )
                remaining_after_request = self._remaining_timeout(started)
                if remaining_after_request is not None and remaining_after_request <= 0:
                    timeout_category = _provider_deadline_timeout_category()
                    telemetry.mark_timeout(timeout_category)
                    raise ProviderTotalTimeout(timeout_category)
                response.raise_for_status()
            except ProviderTotalTimeout as exc:
                final_error = exc
                final_response = None
                final_outcome = "TIMEOUT"
                final_error_code = "MODEL_PROVIDER_TIMEOUT"
                final_error_category = exc.timeout_category
                network_calls.append(
                    self._network_call_metadata(
                        call_index=call_index,
                        call_started=call_started,
                        telemetry=telemetry,
                        outcome="TIMEOUT",
                        error_category=final_error_category,
                        timeout_category=final_error_category,
                    )
                )
                break
            except httpx.TimeoutException as exc:
                final_error = exc
                final_response = None
                final_outcome = "TIMEOUT"
                final_error_code = "MODEL_PROVIDER_TIMEOUT"
                final_error_category = type(exc).__name__
                network_calls.append(
                    self._network_call_metadata(
                        call_index=call_index,
                        call_started=call_started,
                        telemetry=telemetry,
                        outcome="TIMEOUT",
                        error_category=final_error_category,
                        timeout_category=telemetry.snapshot().get("timeout_subtype"),
                    )
                )
                break
            except httpx.HTTPStatusError as exc:
                final_error = exc
                final_response = exc.response
                final_outcome = "ERROR"
                final_error_code = "MODEL_PROVIDER_HTTP_ERROR"
                final_error_category = type(exc).__name__
                network_calls.append(
                    self._network_call_metadata(
                        call_index=call_index,
                        call_started=call_started,
                        telemetry=telemetry,
                        outcome=final_outcome,
                        error_category=final_error_category,
                        response=final_response,
                    )
                )
                break
            except httpx.HTTPError as exc:
                retryable = _is_retryable_transport_error(exc)
                network_calls.append(
                    self._network_call_metadata(
                        call_index=call_index,
                        call_started=call_started,
                        telemetry=telemetry,
                        outcome="ERROR",
                        error_category=type(exc).__name__,
                        response=getattr(exc, "response", None),
                        retryable=retryable,
                    )
                )
                if retryable and call_index <= _MAX_TRANSPORT_RETRIES:
                    remaining_after_failure = self._remaining_timeout(started)
                    if remaining_after_failure is None or remaining_after_failure > 0:
                        continue
                    final_error = ProviderTotalTimeout()
                    final_outcome = "TIMEOUT"
                    final_error_code = "MODEL_PROVIDER_TIMEOUT"
                    final_error_category = _provider_deadline_timeout_category()
                    break
                final_error = exc
                final_response = getattr(exc, "response", None)
                final_outcome = "ERROR"
                final_error_code = "MODEL_PROVIDER_HTTP_ERROR"
                final_error_category = type(exc).__name__
                break
            else:
                network_calls.append(
                    self._network_call_metadata(
                        call_index=call_index,
                        call_started=call_started,
                        telemetry=telemetry,
                        outcome="SUCCESS",
                        response=response,
                    )
                )
                break

        assert telemetry is not None
        if final_error is not None:
            latency_ms = round((perf_counter() - started) * 1000)
            assert final_error_category is not None
            self._set_failure_metadata(
                purpose=purpose,
                started_at=started_at,
                latency_ms=latency_ms,
                context_bytes=_planning_context_bytes(payload),
                request_size_bytes=request_size_bytes,
                outcome=final_outcome,
                error_category=final_error_category,
                telemetry=telemetry,
                network_calls=tuple(network_calls),
                **goal_metadata,
            )
            _log_provider_failure(
                purpose=purpose,
                model=profile.model_name,
                request_size_bytes=request_size_bytes,
                error=final_error,
                response=final_response,
                latency_ms=latency_ms,
                http_timeout_seconds=None,
                plan_timeout_seconds=self._plan_timeout,
            )
            raise GenericProviderError(
                final_error_code,
                (
                    "The model provider request timed out"
                    if final_error_code == "MODEL_PROVIDER_TIMEOUT"
                    else "The model provider request failed"
                ),
            ) from final_error

        assert response is not None
        try:
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            latency_ms = round((perf_counter() - started) * 1000)
            self._set_failure_metadata(
                purpose=purpose,
                started_at=started_at,
                latency_ms=latency_ms,
                context_bytes=_planning_context_bytes(payload),
                request_size_bytes=request_size_bytes,
                outcome="ERROR",
                error_category=type(exc).__name__,
                telemetry=telemetry,
                network_calls=tuple(network_calls),
                debug_response_snapshot={"json_type": "invalid_json"},
                response_validation="REJECTED",
                **goal_metadata,
            )
            raise GenericProviderError(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "The model provider returned malformed JSON",
            ) from exc
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        finish_reason = (
            choice.get("finish_reason")
            if isinstance(choice, dict) and isinstance(choice.get("finish_reason"), str)
            else None
        )
        metadata = ProviderCallMetadata(
            call_type=purpose.upper(),
            call_sequence=self._next_call_sequence(),
            profile=profile.name,
            latency_ms=round((perf_counter() - started) * 1000),
            provider=self.provider_name,
            model=profile.model_name,
            thinking_mode=profile.thinking_mode,
            reasoning_effort=profile.reasoning_effort,
            configured_output_token_limit=profile.output_token_limit,
            http_timeout_seconds=None,
            plan_timeout_seconds=self._plan_timeout,
            plan_total_timeout_seconds=self._plan_total_timeout,
            goal_resolution_deadline_seconds=goal_resolution_budget_seconds(),
            started_at=started_at.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            **telemetry.snapshot(),
            wall_clock_latency_ms=round((perf_counter() - started) * 1000),
            outcome="SUCCESS",
            context_bytes=_planning_context_bytes(payload),
            request_size_bytes=request_size_bytes,
            prompt_tokens=_optional_int(usage, "prompt_tokens"),
            prompt_cache_hit_tokens=_optional_int(usage, "prompt_cache_hit_tokens"),
            prompt_cache_miss_tokens=_optional_int(usage, "prompt_cache_miss_tokens"),
            completion_tokens=_optional_int(usage, "completion_tokens"),
            reasoning_tokens=_optional_nested_int(
                usage, "completion_tokens_details", "reasoning_tokens"
            ),
            total_tokens=_optional_int(usage, "total_tokens"),
            final_content_bytes=(
                len(content.encode("utf-8")) if isinstance(content, str) else None
            ),
            finish_reason=finish_reason,
            network_calls=tuple(network_calls),
            **goal_metadata,
        )
        self._record_call_metadata(metadata)
        return parsed

    @staticmethod
    def _network_call_metadata(
        *,
        call_index: int,
        call_started: float,
        telemetry: _ProviderPhaseTelemetry,
        outcome: str,
        error_category: str | None = None,
        timeout_category: object | None = None,
        response: httpx.Response | None = None,
        retryable: bool = False,
    ) -> dict[str, object]:
        snapshot = telemetry.snapshot()
        resolved_timeout_category = timeout_category
        if resolved_timeout_category is None:
            resolved_timeout_category = snapshot.get("timeout_subtype")
        if not isinstance(resolved_timeout_category, str):
            resolved_timeout_category = None
        return {
            **snapshot,
            "call_index": call_index,
            "started_at": snapshot.get("request_started_at"),
            "finished_at": datetime.now(UTC).isoformat(),
            "latency_ms": round((perf_counter() - call_started) * 1000),
            "duration_ms": round((perf_counter() - call_started) * 1000),
            "outcome": outcome,
            "error_category": error_category,
            "timeout_category": resolved_timeout_category,
            "http_status_code": response.status_code if response is not None else None,
            "provider_request_id": _provider_request_id(response),
            "retryable": retryable,
        }

    def _set_failure_metadata(
        self,
        *,
        purpose: str,
        started_at: datetime,
        latency_ms: int,
        context_bytes: int | None,
        request_size_bytes: int,
        outcome: str,
        error_category: str,
        telemetry: _ProviderPhaseTelemetry,
        network_calls: tuple[dict[str, object], ...] = (),
        prompt_template_version: str | None = None,
        request_hash: str | None = None,
        public_catalog_hash: str | None = None,
        focused_ontology_hash: str | None = None,
        debug_snapshot: dict[str, object] | None = None,
        debug_response_snapshot: dict[str, object] | None = None,
        response_validation: str | None = None,
        recovery_attempt: int | None = None,
    ) -> None:
        if debug_snapshot is not None and debug_response_snapshot is not None:
            debug_snapshot = {
                **debug_snapshot,
                "output": debug_response_snapshot,
            }
        metadata = ProviderCallMetadata(
            call_type=purpose.upper(),
            call_sequence=self._next_call_sequence(),
            profile=self._profile_for_purpose(purpose).name,
            latency_ms=latency_ms,
            provider=self.provider_name,
            model=self._profile_for_purpose(purpose).model_name,
            thinking_mode=self._profile_for_purpose(purpose).thinking_mode,
            reasoning_effort=self._profile_for_purpose(purpose).reasoning_effort,
            configured_output_token_limit=self._profile_for_purpose(purpose).output_token_limit,
            http_timeout_seconds=None,
            plan_timeout_seconds=self._plan_timeout,
            plan_total_timeout_seconds=self._plan_total_timeout,
            goal_resolution_deadline_seconds=goal_resolution_budget_seconds(),
            started_at=started_at.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            **telemetry.snapshot(),
            wall_clock_latency_ms=latency_ms,
            outcome=outcome,
            error_category=error_category,
            context_bytes=context_bytes,
            request_size_bytes=request_size_bytes,
            network_calls=network_calls,
            prompt_template_version=prompt_template_version,
            request_hash=request_hash,
            public_catalog_hash=public_catalog_hash,
            focused_ontology_hash=focused_ontology_hash,
            debug_snapshot=debug_snapshot,
            response_validation=response_validation,
            recovery_attempt=recovery_attempt,
        )
        self._record_call_metadata(metadata)


def build_generic_provider(settings: Settings) -> GenericModelProvider | None:
    if settings.model_provider == "mock":
        return None
    return OpenAICompatibleGenericProvider(settings)


def provider_call_metadata(provider: GenericModelProvider) -> dict[str, object]:
    metadata = getattr(provider, "last_call_metadata", None)
    return _provider_metadata_dump(metadata)


def provider_call_history_metadata(
    provider: GenericModelProvider | None,
) -> tuple[dict[str, object], ...]:
    """Return safe metadata for every logical provider call made by ``provider``.

    ``last_call_metadata`` remains the compatibility view for existing
    planning code.  Providers without the optional history capability retain
    the old single-call fallback for diagnostics.
    """

    if provider is None:
        return ()
    history = getattr(provider, "call_metadata_history", None)
    if isinstance(history, (list, tuple)):
        return tuple(
            _provider_metadata_dump(item)
            for item in history
            if isinstance(item, ProviderCallMetadata)
        )
    latest = provider_call_metadata(provider)
    return (latest,) if latest else ()


def _provider_metadata_dump(metadata: ProviderCallMetadata | None) -> dict[str, object]:
    if not isinstance(metadata, ProviderCallMetadata):
        return {}
    result = metadata.model_dump(mode="json")
    if result.get("debug_snapshot") is None:
        result.pop("debug_snapshot", None)
    return result


def provider_call_start_metadata(
    provider: GenericModelProvider,
    request: PlanRequest,
) -> dict[str, object]:
    """Build safe, pre-request metadata for the persistent call audit."""

    payload = request.provider_payload()
    estimator = getattr(provider, "estimate_request_size_bytes", None)
    if callable(estimator):
        request_size_bytes = estimator(request.call_type.lower(), payload)
    else:
        request_size_bytes = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    return {
        "provider": getattr(provider, "provider_name", type(provider).__name__),
        "model": getattr(provider, "model_name", None),
        "call_type": request.call_type,
        "context_bytes": _planning_context_bytes(payload),
        "request_size_bytes": request_size_bytes,
        "thinking_mode": getattr(provider, "thinking_mode", None),
        "reasoning_effort": getattr(provider, "reasoning_effort", None),
        "configured_output_token_limit": getattr(provider, "configured_output_token_limit", None),
        "http_timeout_seconds": getattr(provider, "http_timeout_seconds", None),
        "plan_timeout_seconds": getattr(provider, "plan_timeout_seconds", None),
        "plan_total_timeout_seconds": getattr(provider, "plan_total_timeout_seconds", None),
    }


def _planning_context_bytes(payload: dict[str, object]) -> int | None:
    context_key = "planner_input" if "planner_input" in payload else "planning_context"
    if context_key not in payload:
        return None
    return len(
        json.dumps(payload.get(context_key, {}), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _optional_int(value: object, key: str) -> int | None:
    if not isinstance(value, dict):
        return None
    item = value.get(key)
    return item if isinstance(item, int) and not isinstance(item, bool) else None


def _optional_nested_int(value: object, outer_key: str, inner_key: str) -> int | None:
    if not isinstance(value, dict):
        return None
    return _optional_int(value.get(outer_key), inner_key)


def _log_provider_failure(
    *,
    purpose: str,
    model: str,
    request_size_bytes: int,
    error: Exception,
    response: httpx.Response | None,
    latency_ms: int,
    http_timeout_seconds: float | None,
    plan_timeout_seconds: float | None,
) -> None:
    """Record bounded, credential-safe upstream diagnostics for Developer logs."""

    log.error(
        "model_provider_upstream_error",
        purpose=purpose,
        model=model,
        error_type=type(error).__name__,
        upstream_status_code=response.status_code if response is not None else None,
        request_size_bytes=request_size_bytes,
        latency_ms=latency_ms,
        http_timeout_seconds=http_timeout_seconds,
        plan_timeout_seconds=plan_timeout_seconds,
        provider_request_id=_provider_request_id(response),
        response_body_summary=_response_body_summary(response),
    )


def _provider_request_id(response: httpx.Response | None) -> str | None:
    if response is None:
        return None
    for header_name in ("x-request-id", "x-deepseek-request-id", "request-id"):
        value = response.headers.get(header_name)
        if value:
            return _safe_text(value, limit=160)
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        for key in ("request_id", "requestId", "id"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return _safe_text(value, limit=160)
        error = body.get("error")
        if isinstance(error, dict):
            for key in ("request_id", "requestId", "id"):
                value = error.get(key)
                if isinstance(value, str) and value:
                    return _safe_text(value, limit=160)
    return None


def _response_body_summary(response: httpx.Response | None) -> dict[str, object]:
    if response is None:
        return {"available": False}

    raw_body = response.content
    summary: dict[str, object] = {
        "available": True,
        "bytes": len(raw_body),
        "sha256": hashlib.sha256(raw_body).hexdigest(),
    }
    try:
        body = response.json()
    except ValueError:
        summary["format"] = "text"
        summary["content_type"] = response.headers.get("content-type")
        return summary

    summary["format"] = "json"
    if isinstance(body, dict):
        summary["top_level_keys"] = sorted(str(key) for key in body)[:20]
        error = body.get("error")
        if isinstance(error, dict):
            summary["error"] = {
                key: _safe_text(value, limit=240)
                for key, value in error.items()
                if key in {"type", "code", "message"} and isinstance(value, (str, int, float, bool))
            }
        else:
            summary["fields"] = {
                key: _safe_text(value, limit=240)
                for key, value in body.items()
                if key in {"type", "code", "message", "detail"}
                and isinstance(value, (str, int, float, bool))
            }
    else:
        summary["value_type"] = type(body).__name__
    return summary


_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]+\b"),
)


def _safe_text(value: object, *, limit: int) -> str:
    text = str(value)
    for pattern in _SENSITIVE_TEXT_PATTERNS:
        text = pattern.sub("<redacted>", text)
    return text[:limit]


__all__ = [
    "AntiRegressionMemoryItem",
    "DynamicGoalActionMatch",
    "DynamicGoalActionMatchRequest",
    "DynamicGoalActionRouting",
    "DynamicGoalActionRoutingRequest",
    "DynamicGoalCandidateReference",
    "DynamicGoalContractResolver",
    "DynamicGoalEntityGrounder",
    "DynamicGoalEntityGrounding",
    "DynamicGoalEntityGroundingRequest",
    "DynamicGoalFamilyRouting",
    "DynamicGoalFamilyRoutingRequest",
    "DynamicGoalGroundedOperation",
    "DynamicGoalInterpretation",
    "DynamicGoalInterpretationRequest",
    "DynamicGoalInterpreter",
    "DynamicGoalOperationGrounding",
    "DynamicGoalOperationGroundingRequest",
    "DynamicGoalRecoveryFeedback",
    "DynamicGoalSemanticActionEvidence",
    "DynamicGoalSemanticFamilyEvidence",
    "DynamicGoalSemanticRouting",
    "DynamicGoalSemanticRoutingRequest",
    "GenericModelProvider",
    "GenericProviderError",
    "GoalDependencyProjection",
    "GoalFamilyMatch",
    "GoalFamilyMatchRequest",
    "GoalMatchSemantics",
    "GoalRoleProvenance",
    "GoalSelection",
    "GoalSelectionRequest",
    "OpenAICompatibleGenericProvider",
    "OperationContractSlot",
    "OperationGoalProjection",
    "OperationIntentDraft",
    "PlanProposal",
    "PlanRequest",
    "PlanSegment",
    "PlanStepProposal",
    "PlanViolation",
    "PlannerActionContract",
    "PlannerActorState",
    "PlannerInput",
    "PlannerKnownWorldSlice",
    "PlannerResourceRequirement",
    "PlannerResourceSourceHint",
    "PlannerTargetBinding",
    "PlanningActionCandidate",
    "PlanningContext",
    "ProviderCallMetadata",
    "ProviderTotalTimeout",
    "build_generic_provider",
    "dynamic_goal_grounding_recovery_feedback",
    "dynamic_goal_recovery_feedback",
    "ensure_goal_resolution_budget",
    "goal_provider_request_snapshot",
    "goal_provider_response_snapshot",
    "goal_resolution_budget_seconds",
    "goal_resolution_operation",
    "goal_resolution_remaining_seconds",
    "plan_operation",
    "plan_operation_budget_seconds",
    "plan_operation_remaining_seconds",
    "provider_call_history_metadata",
    "provider_call_metadata",
    "provider_call_start_metadata",
    "provider_validation_diagnostics",
]
