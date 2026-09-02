"""Validated provider boundary for generic goal selection and planning."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
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
from app.domain.formal_goal import AdHocGoalRequirementCandidateV1

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


class DynamicGoalEntityGroundingRequest(ProviderModel):
    """Knowledge-safe input for the bounded public Entity Grounding call."""

    goal: str = Field(min_length=1, max_length=4000)
    public_catalog: dict[str, object] = Field(default_factory=dict)


class DynamicGoalCandidateReference(ProviderModel):
    """One public Scenario definition that Stage 1 may ground."""

    ref_type: Literal["NODE", "REGION", "RESOURCE", "DERIVED_STATE"]
    key: StrictStr = Field(min_length=1, max_length=160)


class DynamicGoalRecoveryFeedback(ProviderModel):
    """Safe, structured schema feedback for one bounded interpretation retry."""

    requirement_index: StrictInt = Field(ge=0)
    kind: Literal["FACT", "RESOURCE_AT_LEAST", "DERIVED_STATE"] | None = None
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
            "target_value",
            "<extra_field>",
        ]
        | None
    ) = None
    expected_shape: dict[str, object] = Field(default_factory=dict)
    focused_target_value: StrictStr | StrictInt | StrictBool | None = None


class DynamicGoalEntityGrounding(ProviderModel):
    """Closed provider output containing public heterogeneous candidates only.

    ``candidate_keys`` remains a compatibility input for older deterministic
    test providers and is normalized to ``NODE`` references.  The provider
    contract itself is ``candidate_refs`` so Stage 1 can ground resources and
    public Derived States without pretending they are world Nodes.
    """

    status: Literal["RESOLVED", "NEEDS_CLARIFICATION", "UNSUPPORTED"] = "RESOLVED"
    candidate_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    candidate_keys: tuple[StrictStr, ...] = ()
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
    recovery_attempt: int = Field(default=0, ge=0, le=1)
    recovery_feedback: tuple[DynamicGoalRecoveryFeedback, ...] = Field(default=(), max_length=4)


class DynamicGoalInterpretation(ProviderModel):
    """Strict provider output for a flat typed Dynamic Goal candidate set.

    The nested candidate model is intentionally closed: the provider cannot
    return a backend identity, authored prerequisite, knowledge gate, or
    hidden completion semantic.
    """

    status: Literal["RESOLVED", "NEEDS_CLARIFICATION", "UNSUPPORTED"] = "RESOLVED"
    requirements: tuple[AdHocGoalRequirementCandidateV1, ...] = ()
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
    previous_execution_context: dict[str, object] = Field(default_factory=dict)
    scenario_planning_hints: dict[str, object] = Field(default_factory=dict)

    def compact_dump(self) -> dict[str, object]:
        """Return the lossless provider projection of this context."""

        payload = self.model_dump(mode="json")
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
    deterministic_effects: tuple[dict[str, object], ...] = ()
    knowledge_semantics: tuple[dict[str, object], ...] = ()


class PlannerTargetBinding(ProviderModel):
    """Sparse target-specific contract differences for one Action/Target."""

    action_key: str
    target_key: str
    requirements: tuple[dict[str, object], ...] = ()
    deterministic_effects: tuple[dict[str, object], ...] = ()


class PlannerKnownWorldSlice(ProviderModel):
    """Knowledge-safe world entities selected for the current dependency closure."""

    nodes: tuple[dict[str, object], ...] = ()
    facts: dict[str, object] = Field(default_factory=dict)
    relations: tuple[dict[str, object], ...] = ()
    resources: dict[str, object] = Field(default_factory=dict)
    resource_knowledge: tuple[dict[str, object], ...] = ()
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
    stop_reason: Literal["OBJECTIVE_COMPLETION", "INFORMATION_BOUNDARY", "BLOCKED"] = (
        "OBJECTIVE_COMPLETION"
    )
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
    total_deadline_seconds: float | None = None
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


class GenericProviderError(ValueError):
    """Secret-safe provider failure surfaced at the application boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        validation_diagnostics: tuple[dict[str, object], ...] = (),
        recovery_feedback: tuple[DynamicGoalRecoveryFeedback, ...] = (),
        resolution_observation: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.validation_diagnostics = validation_diagnostics
        self.recovery_feedback = recovery_feedback
        self.resolution_observation = (
            dict(resolution_observation) if resolution_observation is not None else None
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


_DYNAMIC_GOAL_REQUIREMENT_KINDS = frozenset({"FACT", "RESOURCE_AT_LEAST", "DERIVED_STATE"})
_DYNAMIC_GOAL_REQUIREMENT_FIELDS = {
    "FACT": ("node_key", "fact_key", "accepted_values"),
    "RESOURCE_AT_LEAST": ("region_key", "resource_key", "minimum"),
    "DERIVED_STATE": ("derived_key", "accepted_values"),
}
_DYNAMIC_GOAL_REQUIREMENT_ALLOWED_FIELDS = {
    kind: frozenset({"kind", *fields}) for kind, fields in _DYNAMIC_GOAL_REQUIREMENT_FIELDS.items()
}


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
                        "kind": "FACT|RESOURCE_AT_LEAST|DERIVED_STATE",
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
                        "kind": "FACT|RESOURCE_AT_LEAST|DERIVED_STATE",
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
    {"dynamic_goal_grounding", "dynamic_goal", "dynamic_goal_interpretation"}
)
_GOAL_PROMPT_TEMPLATE_VERSIONS = {
    "dynamic_goal_grounding": "dynamic-goal-grounding-v1",
    "dynamic_goal": "dynamic-goal-interpretation-v1",
    "dynamic_goal_interpretation": "dynamic-goal-interpretation-v1",
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
    if purpose == "dynamic_goal_grounding":
        raw = _redact_goal_grounding_identities(raw, public_catalog)
        allowed = {"status", "candidate_refs", "candidate_keys", "clarification_prompt"}
        nested_allowed = {"ref_type", "key"}
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
        return {"ref_type": ref_type, "key": key}
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
    region_keys = _public_ontology_string_keys(world.get("regions"), key_name="key")
    resource_keys = _public_ontology_string_keys(world.get("resources"), key_name="key")
    derived_keys = _public_ontology_string_keys(world.get("derived_states"), key_name="key")
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
    }
    if include_debug_snapshot:
        metadata["debug_snapshot"] = {
            "input": goal_provider_request_snapshot(purpose, payload),
        }
    return metadata


class ProviderTotalTimeout(TimeoutError):
    """The provider call exceeded the configured end-to-end deadline."""


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
        self._timeout = settings.model_timeout_seconds
        self._total_timeout = settings.model_total_timeout_seconds
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
        return self._timeout

    @property
    def total_deadline_seconds(self) -> float | None:
        return self._total_timeout

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

    def ground_dynamic_goal_entities(
        self, request: DynamicGoalEntityGroundingRequest
    ) -> DynamicGoalEntityGrounding:
        raw = self._invoke("dynamic_goal_grounding", request.model_dump(mode="json"))
        try:
            result = DynamicGoalEntityGrounding.model_validate(raw)
        except ValidationError as exc:
            if self._goal_resolution_observability == "DEBUG":
                self._record_goal_response_snapshot(
                    goal_provider_response_snapshot(
                        "dynamic_goal_grounding",
                        raw,
                        public_catalog=request.public_catalog,
                    ),
                    validation="REJECTED",
                )
            diagnostics = provider_validation_diagnostics(exc)
            self._record_validation_diagnostics(diagnostics)
            raise GenericProviderError(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "The model provider returned an invalid Dynamic Goal Entity Grounding",
                validation_diagnostics=diagnostics,
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
            return PlanProposal.model_validate(
                self._invoke(request.call_type.lower(), request.provider_payload())
            )
        except ValidationError as exc:
            raise GenericProviderError(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "The model provider returned an invalid Plan proposal",
            ) from exc

    def _build_request_body(
        self, purpose: str, payload: dict[str, object]
    ) -> tuple[dict[str, object], int]:
        profile = self._profile_for_purpose(purpose)
        if purpose == "goal_selection":
            response_contract = (
                '{"status":"SELECTED|NEEDS_CLARIFICATION|UNSUPPORTED",'
                '"objective_keys":["zero_or_more_candidate_keys"],'
                '"clarification_prompt":null}'
            )
        elif purpose == "dynamic_goal_grounding":
            response_contract = (
                '{"status":"RESOLVED|NEEDS_CLARIFICATION|UNSUPPORTED",'
                '"candidate_refs":[{"ref_type":"NODE|REGION|RESOURCE|DERIVED_STATE",'
                '"key":"public_scenario_reference_key"}],'
                '"clarification_prompt":null}'
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
                '"accepted_values":["AVAILABLE"]}'
            )
        else:
            response_contract = (
                '{"plan_summary":"short summary",'
                '"stop_reason":"OBJECTIVE_COMPLETION|INFORMATION_BOUNDARY|BLOCKED",'
                '"boundary_dependency_id":null,'
                '"steps":[{"step_id":"segment-local-stable-id",'
                '"purpose":"goal-directed step",'
                '"action_key":"existing_action_key",'
                '"actor_key":"existing_actor_key",'
                '"target_key":"existing_target_key",'
                '"parameters":{},"short_actor_reason":"short reason"}]}'
            )
        if purpose == "dynamic_goal_grounding":
            planning_prompt = (
                "Ground only public Scenario references mentioned by the player's Goal. Return "
                "candidate_refs with ref_type NODE, REGION, RESOURCE, or DERIVED_STATE and a "
                "key copied exactly from the supplied public_catalog. Multiple plausible public "
                "references are allowed. If no public reference can be grounded, return "
                "clarification or unsupported with no candidate_refs. Use canonical names, "
                "descriptions, aliases, and public topology as semantic evidence, but never "
                "invent a key. Stage 1 identifies references only: do not emit a Goal kind, "
                "value, threshold, Formal Goal requirement, authored Objective, Action, or "
                "plan. Do not inspect current Fact values, infer hidden Truth, or expose "
                "chain-of-thought."
            )
        elif purpose == "dynamic_goal":
            planning_prompt = (
                "Interpret the player's Goal only into the closed V1 typed requirement "
                "vocabulary. Return one or more requirements with implicit AND semantics. "
                "Use only FACT, RESOURCE_AT_LEAST, or DERIVED_STATE. Use only node, fact, "
                "region, resource, and derived keys present in the public ontology supplied "
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
                "view requests are information/action intent, not automatically a terminal "
                "FACT goal. "
                "Do not invent keys, values outside a supplied Fact domain, Action plans, "
                "prerequisites, routes, knowledge gates, hidden requirements, completion "
                "rules, or any other Scenario semantics. The backend assigns requirement "
                "identity and performs the final exact-Version validation. If the Goal is "
                "ambiguous or cannot be expressed in this vocabulary, return clarification "
                "or unsupported with no requirements. Do not expose chain-of-thought."
            )
            if payload.get("recovery_attempt"):
                planning_prompt += (
                    " This is the one bounded recovery attempt. Re-read the focused public "
                    "ontology and the safe recovery_feedback in the user payload. Correct "
                    "the indicated requirement shape without changing grounded references "
                    "or broadening the ontology. For DERIVED_STATE, accepted_values is "
                    "required and non-empty; use the focused target value, and do not emit "
                    "target_value. Do not fall back to an authored Objective."
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
                "needed to continue, or perform an explicitly necessary supporting Action. "
                "Do not add speculative, preventive, unrelated, downstream, sibling, or "
                "broader work. Choose every Action, Actor, Target, Resource source, route, "
                "parameter, and ordering yourself; Validator feedback does not prescribe a "
                "solution. "
                "Interpret BLOCKED_SEGMENT_HAS_PROGRESS_OPTIONS as proof that BLOCKED was "
                "invalid because legal progress or legal Knowledge acquisition exists. "
                "Interpret BLOCKED_SEGMENT_HAS_STEPS as proof that BLOCKED cannot contain "
                "partial-progress Steps: return steps=[] only for a genuinely blocked "
                "segment, otherwise redesign it as legal progress or information "
                "acquisition. Interpret INFORMATION_BOUNDARY_NOT_RELEVANT as proof that "
                "the selected dependency is not a valid stopping boundary; do not reuse it "
                "unless it genuinely blocks the next legal Target, Resource Source, "
                "Parameter, or Precondition and a final Knowledge-acquisition Step resolves "
                "it. These meanings do not recommend any Actor, Action, Target, route, or "
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
                "needed by the Objective, obtain Knowledge required to "
                "continue, or perform an explicitly necessary supporting Action. Do not "
                "expand the ObjectiveScope or add downstream, sibling, broader, unrelated, "
                "speculative, preventive, or merely convenient work. Choose every Action, "
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
            "Use OBJECTIVE_COMPLETION only when current Known state plus projected deterministic "
            "effects legally satisfy the frozen Objective completion requirements; partial "
            "progress is not completion. Use INFORMATION_BOUNDARY only when an UNKNOWN "
            "dependency in planner_input.known_world.unknown_dependencies genuinely blocks "
            "the next legal Target, Resource Source, Parameter, or Precondition choice, and "
            "the segment includes a legal Knowledge-acquisition Action whose declared effect "
            "resolves that dependency as its final Step. Once the first submitted "
            "Knowledge-acquisition Action that matches the active boundary_dependency_id is "
            "reached, it MUST be the final Step of this PlanSegment. Supporting Travel, Relay, "
            "or resource-positioning Actions may appear before it, but do not schedule another "
            "candidate inspect or survey, or any later Action, in the same segment. Wait for "
            "that Action's result and continue through the existing REPLAN lifecycle. "
            "boundary_dependency_id must reference "
            "that dependency. General uncertainty, complexity, lack of confidence, or a "
            "attempt_policy MAY_ATTEMPT is not an information boundary. BLOCKED is a "
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

    def _post_with_total_deadline(
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
            telemetry.mark_timeout("PROVIDER_TOTAL_DEADLINE")
            raise ProviderTotalTimeout
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
            telemetry.mark_timeout("PROVIDER_TOTAL_DEADLINE")
            raise ProviderTotalTimeout from exc
        finally:
            # A late upstream result must never keep the planning lifecycle
            # waiting or get a chance to mutate persistence.  The worker is
            # deliberately detached after the caller has crossed the total
            # deadline; its result is ignored.
            executor.shutdown(wait=False, cancel_futures=True)

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
                timeout=httpx.Timeout(self._timeout),
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
            remaining_timeout = (
                None
                if self._total_timeout is None
                else self._total_timeout - (perf_counter() - started)
            )
            try:
                response = self._post_with_total_deadline(
                    url=f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    request_body=request_body,
                    telemetry=telemetry,
                    timeout_seconds=remaining_timeout,
                )
                response.raise_for_status()
            except ProviderTotalTimeout as exc:
                final_error = exc
                final_response = None
                final_outcome = "TIMEOUT"
                final_error_code = "MODEL_PROVIDER_TIMEOUT"
                final_error_category = "PROVIDER_TOTAL_DEADLINE"
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
                    remaining_after_failure = (
                        None
                        if self._total_timeout is None
                        else self._total_timeout - (perf_counter() - started)
                    )
                    if remaining_after_failure is None or remaining_after_failure > 0:
                        continue
                    final_error = ProviderTotalTimeout()
                    final_outcome = "TIMEOUT"
                    final_error_code = "MODEL_PROVIDER_TIMEOUT"
                    final_error_category = "PROVIDER_TOTAL_DEADLINE"
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
                http_timeout_seconds=self._timeout,
                total_deadline_seconds=self._total_timeout,
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
            http_timeout_seconds=self._timeout,
            total_deadline_seconds=self._total_timeout,
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
            http_timeout_seconds=self._timeout,
            total_deadline_seconds=self._total_timeout,
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
        "total_deadline_seconds": getattr(provider, "total_deadline_seconds", None),
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
    total_deadline_seconds: float | None,
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
        total_deadline_seconds=total_deadline_seconds,
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
    "DynamicGoalCandidateReference",
    "DynamicGoalEntityGrounder",
    "DynamicGoalEntityGrounding",
    "DynamicGoalEntityGroundingRequest",
    "DynamicGoalInterpretation",
    "DynamicGoalInterpretationRequest",
    "DynamicGoalInterpreter",
    "DynamicGoalRecoveryFeedback",
    "GenericModelProvider",
    "GenericProviderError",
    "GoalSelection",
    "GoalSelectionRequest",
    "OpenAICompatibleGenericProvider",
    "PlanProposal",
    "PlanRequest",
    "PlanSegment",
    "PlanStepProposal",
    "PlanViolation",
    "PlannerActionContract",
    "PlannerActorState",
    "PlannerInput",
    "PlannerKnownWorldSlice",
    "PlannerTargetBinding",
    "PlanningActionCandidate",
    "PlanningContext",
    "ProviderCallMetadata",
    "ProviderTotalTimeout",
    "build_generic_provider",
    "dynamic_goal_recovery_feedback",
    "goal_provider_request_snapshot",
    "goal_provider_response_snapshot",
    "provider_call_history_metadata",
    "provider_call_metadata",
    "provider_call_start_metadata",
    "provider_validation_diagnostics",
]
