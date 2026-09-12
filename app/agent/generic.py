"""Generic exact-Version goal resolution, planning, validation and execution."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import actor_binding_matches, evaluate_authority
from app.agent.formal_goal_projection import (
    formal_goal_planning_objectives,
)
from app.agent.objective_scope import ObjectiveScope
from app.agent.planner_contract import action_goal_terminal_effects, action_planner_effects
from app.agent.planning_context import (
    PlanningActionCatalogBuilder,
    PlanningContextBuilder,
    PlanningContinuityBuilder,
    _action_planning_is_public,
    _known_world_facts,
    _objective_refs,
    objective_context,
)
from app.agent.provider import (
    AntiRegressionMemoryItem,
    DynamicGoalActionMatch,
    DynamicGoalActionMatchRequest,
    DynamicGoalActionRouting,
    DynamicGoalActionRoutingRequest,
    DynamicGoalCandidateReference,
    DynamicGoalEntityGrounding,
    DynamicGoalEntityGroundingRequest,
    DynamicGoalFamilyRouting,
    DynamicGoalFamilyRoutingRequest,
    DynamicGoalGroundedOperation,
    DynamicGoalIntentDraft,
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    DynamicGoalMentionSlot,
    DynamicGoalOperationGrounding,
    DynamicGoalOperationGroundingRequest,
    DynamicGoalRecoveryFeedback,
    DynamicGoalScalarMentionSlot,
    DynamicGoalSemanticActionEvidence,
    DynamicGoalSemanticFamilyEvidence,
    GenericModelProvider,
    GenericProviderError,
    GoalFamilyMatch,
    GoalFamilyMatchRequest,
    OperationContractSlot,
    OperationGoalProjection,
    OperationIntentDraft,
    PlannerActionContract,
    PlannerActorState,
    PlannerInput,
    PlannerTargetBinding,
    PlanningActionCandidate,
    PlanningContext,
    PlanningContinuity,
    PlanProposal,
    PlanRequest,
    PlanViolation,
    dynamic_goal_recovery_feedback,
    goal_provider_request_snapshot,
    goal_provider_response_snapshot,
    provider_call_history_metadata,
    provider_call_metadata,
    provider_call_start_metadata,
    provider_validation_diagnostics,
)
from app.domain.action_invocation import (
    ActionInvocationBinding,
    action_operation_binding_contract,
    canonical_action_invocation,
    canonical_action_invocation_contract,
)
from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    AuthorityOutcome,
    CommandReachability,
    DecisionStatus,
    NodeStatus,
    RelationVisibility,
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
    StepExecutionType,
    WorldOperationStatus,
)
from app.domain.formal_goal import (
    AdHocActionCompletedRequirementCandidateV1,
    AdHocDerivedStateRequirementCandidateV1,
    AdHocFactRequirementCandidateV1,
    AdHocGoalCandidateSetV1,
    AdHocGoalCandidateSetV2,
    AdHocGoalRequirementCandidateV2,
    AdHocResourceAtLeastRequirementCandidateV1,
    FormalGoalContract,
    FormalGoalContractV1,
    FormalGoalContractV2,
    FormalGoalError,
    FormalGoalSourceKind,
    canonicalize_ad_hoc_dynamic_candidates_v2,
    compile_ad_hoc_dynamic_goal,
    compile_ad_hoc_dynamic_goal_v2,
    compile_predefined_formal_goal,
    validate_ad_hoc_dynamic_candidates,
    validate_ad_hoc_dynamic_candidates_v2,
)
from app.domain.resources import is_runtime_known_inflow_pool, resource_state_key
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario import ScenarioVersionSnapshot
from app.domain.scenario_v2 import (
    ActionBehavior,
    ActionDefinitionV2,
    ActionExecutionMode,
    ActionParameters,
    ActionTargetKind,
    ComparisonOperator,
    ConditionKind,
    ConditionV2,
    EffectKind,
    EffectV2,
    NodeSelectorKind,
    NodeSelectorV2,
    ObjectiveDefinitionV2,
    ObjectiveRequirementKind,
    ObjectiveRequirementV2,
    PublicReferenceTypeV2,
    RuleDefinitionV2,
    RulePhase,
    ScenarioDefinitionV2,
    StrictScalar,
    ValueExpressionV2,
    ValueSource,
    knowledge_gate_is_revealed,
    normalize_action_parameters,
    relation_identity,
    transport_resource_entries,
)
from app.domain.world import Visibility
from app.engine.locality import (
    LocalityEngineError,
    region_for_node,
    resolve_resource_scope,
    validate_action_locality,
)
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentPlan,
    AgentStep,
    AgentTask,
    ConversationSession,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceResourceState,
    PlanningAttempt,
    PlanningCycle,
    WorldOperation,
)
from app.scenarios.public_references import (
    PublicReferenceIdentity,
    PublicReferenceIndexBuilder,
    PublicReferenceLookup,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.formal_goal import (
    FormalGoalCompletionEvaluator,
    FormalGoalPersistenceError,
    load_formal_goal_for_task,
)
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import require_scope_writable
from app.services.generic_actions import (
    GenericActionError,
    GenericActionService,
    GenericApprovalRequired,
)
from app.services.knowledge_projection import SharedKnowledgeProjection, resource_knowledge_status
from app.services.objective_requirements import (
    known_requirement_satisfied,
    requirement_gate_is_public,
)

ProviderCallObserver = Callable[
    [str, AgentTask, PlanRequest, dict[str, object]],
    None,
]


@dataclass(slots=True)
class _ProjectedResourcePool:
    pool_key: str
    resource_key: str
    region_key: str | None
    facility_key: str | None
    quantity: int | None
    visibility: ResourcePoolVisibility
    availability: ResourcePoolAvailability
    survey_discoverable: bool


@dataclass(slots=True)
class _ProjectedRegionResourceKnowledge:
    visibility: ResourceInventoryVisibility
    survey_completed: bool


@dataclass(slots=True)
class _ProjectedFact:
    value: StrictScalar
    visibility: Visibility


@dataclass(frozen=True, slots=True)
class _KnownPreflightFailure:
    failure_code: str
    known_predicate: dict[str, object]
    condition_status: str = "CONTRADICTION"
    fact_precondition: bool = False


@dataclass(frozen=True, slots=True)
class _StaticProposalBinding:
    index: int
    raw_step: object
    candidate: PlanningActionCandidate | None
    action: ActionDefinitionV2
    actor: GameInstanceActor
    target_key: str
    parameters: ActionParameters


def _operation_goal_step_matches(
    operation_goal: OperationGoalProjection,
    binding: _StaticProposalBinding,
    definition: ScenarioDefinitionV2,
    projected_actor_locations: dict[str, str],
) -> bool:
    """Match a proposed concrete step to the frozen operation constraint."""

    if binding.action.key != operation_goal.action_key:
        return False
    if operation_goal.actor_key is not None and binding.actor.actor_key != operation_goal.actor_key:
        return False
    if operation_goal.target_key is not None and binding.target_key != operation_goal.target_key:
        return False

    invocation_bindings: dict[str, str] = {}
    if binding.action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
        source_node_key = projected_actor_locations.get(binding.actor.actor_key)
        target_node_key = (
            projected_actor_locations.get(binding.target_key)
            if binding.action.target_kind == ActionTargetKind.ACTOR
            else binding.target_key
        )
        if source_node_key is None or target_node_key is None:
            return False
        try:
            invocation_bindings = {
                "source_region": region_for_node(definition, source_node_key),
                "destination_region": region_for_node(definition, target_node_key),
            }
        except LocalityEngineError:
            return False

    invocation = canonical_action_invocation(
        binding.action,
        actor_key=binding.actor.actor_key,
        target_key=binding.target_key,
        parameters=binding.parameters,
        bindings=invocation_bindings,
    )
    expected_bindings = {item.role: item.value for item in operation_goal.binding_constraints}
    actual_bindings = {item.role: item.value for item in invocation.bindings}
    if any(actual_bindings.get(role) != value for role, value in expected_bindings.items()):
        return False
    if operation_goal.parameter_constraints is not None:
        expected = canonical_action_invocation(
            binding.action,
            actor_key=binding.actor.actor_key,
            target_key=binding.target_key,
            parameters=operation_goal.parameter_constraints,
            bindings=invocation_bindings,
        )
        if invocation.parameters != expected.parameters:
            return False
    return True


_NON_TERMINAL_TASK_STATUSES = (
    AgentTaskStatus.ACTIVE,
    AgentTaskStatus.REQUIRES_PLAYER_DECISION,
    AgentTaskStatus.WAITING_FOR_PLAYER_ACTION,
    AgentTaskStatus.WAITING_FOR_WORLD_EVENT,
)


class GenericAgentError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


PLAN_INVALIDATED_BY_NEW_KNOWLEDGE = "PLAN_INVALIDATED_BY_NEW_KNOWLEDGE"


_DYNAMIC_GOAL_MAX_GROUNDING_ROUNDS = 2
_DYNAMIC_GOAL_MAX_INTERPRETATION_ATTEMPTS = 2
_DYNAMIC_GOAL_RETRYABLE_PROVIDER_ERRORS = frozenset(
    {
        "MODEL_PROVIDER_RESPONSE_INVALID",
        "MODEL_PROVIDER_HTTP_ERROR",
        "MODEL_PROVIDER_TIMEOUT",
        "MODEL_PROVIDER_FAILURE",
        "MODEL_UNSUPPORTED",
    }
)


def _dynamic_goal_provider_error_is_retryable(
    error: GenericProviderError,
    *,
    allow_model_unsupported: bool = True,
) -> bool:
    return error.code in _DYNAMIC_GOAL_RETRYABLE_PROVIDER_ERRORS and (
        error.code != "MODEL_UNSUPPORTED" or allow_model_unsupported
    )


@dataclass(frozen=True, slots=True)
class GenericGoalResolution:
    status: str
    objective_key: str | None = None
    objective_keys: tuple[str, ...] = ()
    candidate_keys: tuple[str, ...] = ()
    clarification_prompt: str | None = None
    source: str = "DETERMINISTIC"
    provider_observation: dict[str, object] | None = None
    dynamic_requirements: tuple[AdHocGoalRequirementCandidateV2, ...] = ()


@dataclass(frozen=True, slots=True)
class _DynamicGoalGrounding:
    status: str
    candidate_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    entity_keys: tuple[str, ...] = ()
    scope_keys: tuple[str, ...] = ()
    source: str = "NONE"
    clarification_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class _FrozenDynamicGoalEvidence:
    """Immutable public evidence shared by every VNext semantic owner.

    Deterministic identities remain separate retrieval hints so downstream fields
    named ``deterministic_candidate_refs`` are truthful. Only semantic references
    and semantic role bindings enter the authoritative frozen evidence. The legacy
    Stage-1 intent kind and Action guess are not authoritative; a typed Action
    hint is retained separately as advisory input for the Action Router.
    """

    deterministic_exact_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    deterministic_ambiguous_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    semantic_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    merged_refs: tuple[DynamicGoalCandidateReference, ...] = ()
    # Normalized Stage-1 role evidence.  ``intent_kind`` and the Action guess
    # are intentionally not authoritative, but retaining the role slots here
    # gives later stages one immutable source of explicit provenance.
    frozen_intent: DynamicGoalIntentDraft | None = None
    explicit_role_evidence: dict[str, object] | None = None
    semantic_action_evidence: DynamicGoalSemanticActionEvidence | None = None
    semantic_status: str = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class _DynamicGoalProjection:
    """The single Stage 1 -> Stage 2 allow-list contract.

    The same projection builds the focused ontology and validates the typed
    requirements returned by Stage 2.  It contains public semantic identities
    only; it never carries runtime Truth or Knowledge values.
    """

    allowed_entity_keys: tuple[str, ...] = ()
    allowed_region_keys: tuple[str, ...] = ()
    allowed_resource_keys: tuple[str, ...] = ()
    allowed_derived_state_keys: tuple[str, ...] = ()
    allowed_fact_keys: tuple[tuple[str, str], ...] = ()
    allowed_action_keys: tuple[str, ...] = ()
    allowed_actor_keys: tuple[str, ...] = ()


def _is_current_derived_objective(
    objective: ObjectiveDefinitionV2,
    definition: ScenarioDefinitionV2,
) -> bool:
    """Identify migrated Objective shells that no longer own routing semantics."""

    if objective.prerequisites or not objective.completion_requirements:
        return False
    return all(
        requirement.kind == ObjectiveRequirementKind.DERIVED_STATE
        and requirement.derived_key is not None
        and (state := definition.derived_state_definitions.get(requirement.derived_key)) is not None
        and state.goal_addressable
        for requirement in objective.completion_requirements
    )


@dataclass(frozen=True, slots=True)
class GenericObjectiveEvaluation:
    objective_keys: tuple[str, ...]
    completed: bool
    requirements: tuple[tuple[str, StrictScalar, bool], ...]
    authoritative_completed: bool


@dataclass(frozen=True, slots=True)
class PlanRevalidationResult:
    invalidated: bool
    reason: str | None = None
    diagnostics: tuple[dict[str, object], ...] = ()


def _validate_family_routing_recovery_preservation(
    routing: DynamicGoalFamilyRouting,
    recovery_feedback: tuple[dict[str, object], ...],
) -> None:
    for feedback in recovery_feedback:
        preserve = feedback.get("preserve")
        if isinstance(preserve, dict) and (
            "family" in preserve and preserve["family"] != routing.family
        ):
            raise GenericProviderError(
                "PROVIDER_SCHEMA_INVALID",
                "Family recovery changed a preserved semantic decision",
                validation_diagnostics=(
                    {
                        "code": "RECOVERY_CHANGED_PRESERVED_FIELD",
                        "field_path": "family",
                    },
                ),
            )


def _validate_action_routing_recovery_preservation(
    routing: DynamicGoalActionRouting,
    recovery_feedback: tuple[dict[str, object], ...],
) -> None:
    actual = routing.model_dump(mode="json")
    for feedback in recovery_feedback:
        preserve = feedback.get("preserve")
        if not isinstance(preserve, dict):
            continue
        changed = tuple(
            key
            for key in ("action_match", "action_key")
            if key in preserve and actual.get(key) != preserve[key]
        )
        if changed:
            raise GenericProviderError(
                "PROVIDER_SCHEMA_INVALID",
                "Action recovery changed a preserved semantic decision",
                validation_diagnostics=tuple(
                    {
                        "code": "RECOVERY_CHANGED_PRESERVED_FIELD",
                        "field_path": key,
                    }
                    for key in changed
                ),
            )


class GenericGoalResolver:
    """Route public catalog matches and legacy exact matches to Dynamic/Predefined Goals.

    The legacy Objective-selection model is intentionally not part of this
    routing path: an unmatched player Goal must never be converted into the
    nearest authored Objective.
    """

    def __init__(
        self,
        provider: GenericModelProvider | None = None,
        *,
        db: Session | None = None,
        scope: RuntimeScope | None = None,
        observability_level: Literal["NORMAL", "DEBUG"] | None = None,
    ) -> None:
        if (db is None) != (scope is None):
            raise ValueError("Dynamic Goal resolution needs both db and runtime scope")
        self.provider = provider
        self.db = db
        self.scope = scope
        configured_level = observability_level or getattr(
            provider, "goal_resolution_observability", "NORMAL"
        )
        self.observability_level: Literal["NORMAL", "DEBUG"] = (
            "DEBUG" if configured_level == "DEBUG" else "NORMAL"
        )

    @staticmethod
    def _resolve_authored_objective(
        objective: ObjectiveDefinitionV2,
    ) -> GenericGoalResolution:
        return GenericGoalResolution("RESOLVED", objective.key, (objective.key,))

    def resolve(
        self,
        goal: str,
        definition: ScenarioDefinitionV2,
    ) -> GenericGoalResolution:
        normalized = _normalize(goal)
        if not definition.goal_resolution.world_goal_state_catalog:
            matches = [
                objective
                for objective in definition.objectives
                if not _is_current_derived_objective(objective, definition)
                if normalized
                in {
                    _normalize(objective.key),
                    _normalize(objective.name),
                    *(_normalize(alias) for alias in objective.goal_aliases),
                    *(_normalize(example) for example in objective.goal_examples),
                }
            ]
            if len(matches) == 1:
                return self._resolve_authored_objective(matches[0])
            if len(matches) > 1:
                return GenericGoalResolution(
                    "NEEDS_CLARIFICATION",
                    candidate_keys=tuple(sorted(item.key for item in matches)),
                    clarification_prompt=definition.goal_resolution.clarification_prompt,
                    source="DETERMINISTIC_AUTHORED_OBJECTIVE",
                )
        dynamic = self._resolve_dynamic_goal(goal, definition)
        if dynamic is not None:
            return dynamic
        return GenericGoalResolution("UNSUPPORTED")

    def _resolve_dynamic_goal(
        self,
        goal: str,
        definition: ScenarioDefinitionV2,
        *,
        frozen_family: Literal["STATE"] | None = None,
        _provider_history_start: int | None = None,
        _pre_grounding: _DynamicGoalGrounding | None = None,
    ) -> GenericGoalResolution | None:
        if not definition.goal_resolution.allow_llm_fallback or self.provider is None:
            return None
        provider = self.provider
        assert provider is not None
        family_matcher = getattr(provider, "match_dynamic_goal_family", None)
        action_matcher = getattr(provider, "match_dynamic_goal_action", None)
        operation_grounder = getattr(provider, "ground_dynamic_goal_operation", None)
        family_router = getattr(provider, "decide_dynamic_goal_family", None)
        action_router = getattr(provider, "route_dynamic_goal_action", None)
        if frozen_family is None and all(
            callable(item) for item in (family_router, action_router, operation_grounder)
        ):
            assert callable(family_router)
            assert callable(action_router)
            assert callable(operation_grounder)
            routing_history_start = len(provider_call_history_metadata(provider))
            deterministic_grounding = _deterministic_dynamic_goal_grounding(
                goal,
                self.db,
                self.scope,
                definition,
            )
            frozen_evidence = _vnext_frozen_dynamic_goal_evidence(
                goal,
                self.db,
                self.scope,
                definition,
                provider,
                deterministic_grounding,
            )
            routing_refs = _merge_dynamic_goal_candidate_refs(
                _merge_dynamic_goal_candidate_refs(
                    frozen_evidence.deterministic_exact_refs,
                    frozen_evidence.deterministic_ambiguous_refs,
                ),
                frozen_evidence.semantic_refs,
            )

            family_routing: DynamicGoalFamilyRouting | None = None
            family_recovery: tuple[dict[str, object], ...] = ()
            family_evidence = _vnext_semantic_family_evidence(frozen_evidence, definition)
            for recovery_attempt in range(2):
                try:
                    family_routing = DynamicGoalFamilyRouting.model_validate(
                        family_router(
                            DynamicGoalFamilyRoutingRequest(
                                goal=goal,
                                deterministic_candidate_refs=(
                                    frozen_evidence.deterministic_exact_refs
                                ),
                                deterministic_ambiguous_refs=(
                                    frozen_evidence.deterministic_ambiguous_refs
                                ),
                                semantic_candidate_refs=frozen_evidence.semantic_refs,
                                explicit_role_evidence=(
                                    frozen_evidence.explicit_role_evidence or {}
                                ),
                                semantic_family_evidence=family_evidence,
                                recovery_attempt=recovery_attempt,
                                recovery_feedback=family_recovery,
                            )
                        )
                    )
                    _validate_family_routing_recovery_preservation(
                        family_routing,
                        family_recovery,
                    )
                    if (
                        family_routing.family == "STATE"
                        and family_evidence.operation_expressed
                        and family_evidence.state_equivalent_available is False
                    ):
                        if recovery_attempt == 0:
                            family_recovery = (
                                {
                                    "code": "FAMILY_OPERATION_STATE_EQUIVALENCE_CONFLICT",
                                    "expected": (
                                        "Return OPERATION when operation_expressed is true "
                                        "and state_equivalent_available is false."
                                    ),
                                    "fix_only": ["family"],
                                },
                            )
                            continue
                        raise GenericProviderError(
                            "PROVIDER_SCHEMA_INVALID",
                            (
                                "Family routing contradicted an explicit operation without "
                                "a terminal equivalent"
                            ),
                            validation_diagnostics=(
                                {
                                    "code": "FAMILY_OPERATION_STATE_EQUIVALENCE_CONFLICT",
                                    "field_path": "family",
                                },
                            ),
                            resolution_observation={
                                "stage": "FAMILY_ROUTING",
                                "status": "ERROR",
                                "result": "FAMILY_OPERATION_STATE_EQUIVALENCE_CONFLICT",
                                "rejection_code": "FAMILY_OPERATION_STATE_EQUIVALENCE_CONFLICT",
                            },
                        )
                    break
                except GenericProviderError as exc:
                    if recovery_attempt == 0 and exc.code in {
                        "MODEL_PROVIDER_RESPONSE_INVALID",
                        "PROVIDER_SCHEMA_INVALID",
                    }:
                        family_recovery = tuple(exc.validation_diagnostics) or (
                            {
                                "code": exc.code,
                                "expected": "DynamicGoalFamilyRouting",
                            },
                        )
                        continue
                    raise
                except (ValidationError, TypeError, ValueError) as exc:
                    if recovery_attempt == 0:
                        family_recovery = (
                            {
                                "code": "PROVIDER_SCHEMA_INVALID",
                                "expected": "DynamicGoalFamilyRouting",
                            },
                        )
                        continue
                    raise GenericProviderError(
                        "PROVIDER_SCHEMA_INVALID",
                        "The model provider returned invalid family routing",
                    ) from exc
            assert family_routing is not None
            if family_routing.family == "AMBIGUOUS":
                return GenericGoalResolution(
                    "NEEDS_CLARIFICATION",
                    clarification_prompt=family_routing.clarification_prompt,
                    source="FAMILY_AMBIGUOUS",
                    provider_observation=_vnext_goal_observation(
                        frozen_evidence,
                        frozen_family="AMBIGUOUS",
                        terminal_stage="FAMILY_ROUTING",
                        result="NEEDS_CLARIFICATION",
                        rejection_code="FAMILY_AMBIGUOUS",
                    ),
                )
            if family_routing.family == "STATE":
                return self._resolve_vnext_state_goal(
                    goal,
                    definition,
                    interpreter=getattr(provider, "interpret_dynamic_goal", None),
                    frozen_evidence=frozen_evidence,
                    provider_history_start=routing_history_start,
                )

            public_action_keys = _dynamic_goal_public_action_keys(
                self.db,
                self.scope,
                definition,
            )
            action_catalog = _dynamic_goal_routing_action_catalog(
                definition,
                public_action_keys,
                (),
            )
            public_entity_catalog = _dynamic_goal_entity_catalog(
                self.db,
                self.scope,
                definition,
            )
            relevant_public_entities = _dynamic_goal_action_semantic_entities(
                public_entity_catalog,
                routing_refs,
            )
            action_topology = _dynamic_goal_action_topology_context(
                self.db,
                self.scope,
                definition,
                routing_refs,
                public_catalog=public_entity_catalog,
            )
            routing_action_keys = {str(item["key"]) for item in action_catalog}
            action_routing: DynamicGoalActionRouting | None = None
            action_recovery: tuple[dict[str, object], ...] = ()
            for recovery_attempt in range(2):
                try:
                    action_routing = DynamicGoalActionRouting.model_validate(
                        action_router(
                            DynamicGoalActionRoutingRequest(
                                goal=goal,
                                action_catalog=action_catalog,
                                relevant_public_entities=relevant_public_entities,
                                public_topology=action_topology,
                                deterministic_candidate_refs=(
                                    frozen_evidence.deterministic_exact_refs
                                ),
                                deterministic_ambiguous_refs=(
                                    frozen_evidence.deterministic_ambiguous_refs
                                ),
                                semantic_candidate_refs=frozen_evidence.semantic_refs,
                                explicit_role_evidence=(
                                    frozen_evidence.explicit_role_evidence or {}
                                ),
                                semantic_action_evidence=(
                                    frozen_evidence.semantic_action_evidence
                                ),
                                recovery_attempt=recovery_attempt,
                                recovery_feedback=action_recovery,
                            )
                        )
                    )
                    _validate_action_routing_recovery_preservation(
                        action_routing,
                        action_recovery,
                    )
                    semantic_action_evidence = frozen_evidence.semantic_action_evidence
                    if (
                        semantic_action_evidence is not None
                        and action_routing.action_match == "MATCHED"
                        and action_routing.action_key is not None
                        and action_routing.action_key != semantic_action_evidence.action_key
                    ):
                        if recovery_attempt == 0:
                            action_recovery = (
                                {
                                    "code": "ACTION_SEMANTIC_EVIDENCE_CONFLICT",
                                    "expected": (
                                        "Re-compare the raw Goal with the complete authored "
                                        "semantics of the upstream and returned Actions."
                                    ),
                                    "upstream_action_key": semantic_action_evidence.action_key,
                                    "returned_action_key": action_routing.action_key,
                                    "surface": semantic_action_evidence.surface,
                                    "fix_only": [
                                        "action_match",
                                        "action_key",
                                        "candidate_keys",
                                        "clarification_prompt",
                                        "no_match_reason",
                                    ],
                                },
                            )
                            continue
                        raise GenericProviderError(
                            "PROVIDER_SCHEMA_INVALID",
                            "Action routing contradicted Stage-1 semantic Action evidence",
                            validation_diagnostics=(
                                {
                                    "code": "ACTION_SEMANTIC_EVIDENCE_CONFLICT",
                                    "upstream_action_key": semantic_action_evidence.action_key,
                                    "returned_action_key": action_routing.action_key,
                                    "surface": semantic_action_evidence.surface,
                                },
                            ),
                            resolution_observation={
                                "stage": "ACTION_ROUTING",
                                "status": "ERROR",
                                "result": "ACTION_SEMANTIC_EVIDENCE_CONFLICT",
                                "rejection_code": "ACTION_SEMANTIC_EVIDENCE_CONFLICT",
                                "upstream_action_key": semantic_action_evidence.action_key,
                                "returned_action_key": action_routing.action_key,
                                "surface": semantic_action_evidence.surface,
                            },
                        )
                    break
                except GenericProviderError as exc:
                    if recovery_attempt == 0 and exc.code in {
                        "MODEL_PROVIDER_RESPONSE_INVALID",
                        "PROVIDER_SCHEMA_INVALID",
                    }:
                        action_recovery = tuple(exc.validation_diagnostics) or (
                            {
                                "code": exc.code,
                                "expected": "DynamicGoalActionRouting",
                            },
                        )
                        continue
                    raise
                except (ValidationError, TypeError, ValueError) as exc:
                    if recovery_attempt == 0:
                        action_recovery = (
                            {
                                "code": "PROVIDER_SCHEMA_INVALID",
                                "expected": "DynamicGoalActionRouting",
                            },
                        )
                        continue
                    raise GenericProviderError(
                        "PROVIDER_SCHEMA_INVALID",
                        "The model provider returned invalid Action routing",
                    ) from exc
            assert action_routing is not None
            if action_routing.action_match == "AMBIGUOUS":
                invalid = set(action_routing.candidate_keys) - routing_action_keys
                if invalid:
                    raise GenericProviderError(
                        "CANONICAL_IDENTITY_CONFLICT",
                        "Action routing returned a non-public Action candidate",
                    )
                observation = _vnext_goal_observation(
                    frozen_evidence,
                    frozen_family="OPERATION",
                    terminal_stage="ACTION_ROUTING",
                    result="NEEDS_CLARIFICATION",
                    rejection_code="ACTION_AMBIGUOUS",
                )
                observation["candidate_keys"] = list(action_routing.candidate_keys)
                return GenericGoalResolution(
                    "NEEDS_CLARIFICATION",
                    candidate_keys=action_routing.candidate_keys,
                    clarification_prompt=(
                        action_routing.clarification_prompt
                        or definition.goal_resolution.clarification_prompt
                    ),
                    source="ACTION_AMBIGUOUS",
                    provider_observation=observation,
                )
            if action_routing.action_match == "NO_MATCH":
                observation = _vnext_goal_observation(
                    frozen_evidence,
                    frozen_family="OPERATION",
                    terminal_stage="ACTION_ROUTING",
                    result="UNSUPPORTED",
                    rejection_code="ACTION_NO_MATCH",
                )
                observation["no_match_reason"] = action_routing.no_match_reason
                return GenericGoalResolution(
                    "UNSUPPORTED",
                    source="ACTION_NO_MATCH",
                    provider_observation=observation,
                )
            assert action_routing.action_key is not None
            if action_routing.action_key not in routing_action_keys:
                raise GenericProviderError(
                    "CANONICAL_IDENTITY_CONFLICT",
                    "Action routing returned an Action outside its candidate catalog",
                )
            return self._resolve_contract_driven_operation(
                goal,
                definition,
                operation_grounder=operation_grounder,
                frozen_action_key=action_routing.action_key,
                frozen_evidence=frozen_evidence,
            )
        if frozen_family is None and all(
            callable(item) for item in (family_matcher, action_matcher, operation_grounder)
        ):
            assert callable(family_matcher)
            try:
                family = GoalFamilyMatch.model_validate(
                    family_matcher(GoalFamilyMatchRequest(goal=goal))
                )
            except (GenericProviderError, ValidationError, TypeError, ValueError) as exc:
                if isinstance(exc, GenericProviderError):
                    raise
                raise GenericProviderError(
                    "PROVIDER_SCHEMA_INVALID",
                    "The model provider returned an invalid Goal family match",
                ) from exc
            if family.family == "AMBIGUOUS":
                return GenericGoalResolution(
                    "NEEDS_CLARIFICATION",
                    clarification_prompt=(
                        family.clarification_prompt
                        or definition.goal_resolution.clarification_prompt
                    ),
                    source="FAMILY_AMBIGUOUS",
                    provider_observation={
                        "stage": "DYNAMIC_GOAL_FAMILY",
                        "frozen_family": "AMBIGUOUS",
                        "rejection_code": "FAMILY_AMBIGUOUS",
                    },
                )
            if family.family == "OPERATION":
                assert callable(action_matcher) and callable(operation_grounder)
                return self._resolve_contract_driven_operation(
                    goal,
                    definition,
                    action_matcher=action_matcher,
                    operation_grounder=operation_grounder,
                )
            return self._resolve_dynamic_goal(goal, definition, frozen_family="STATE")
        interpreter = getattr(provider, "interpret_dynamic_goal", None)
        if not callable(interpreter):
            if frozen_family == "STATE":
                raise GenericProviderError(
                    "MODEL_PROVIDER_FAILURE",
                    "The configured Goal provider cannot interpret STATE Goals",
                    resolution_observation={
                        "stage": "STATE_INTERPRETATION",
                        "status": "ERROR",
                        "result": "NO_STATE_INTERPRETER",
                        "rejection_code": "NO_STATE_INTERPRETER",
                        "frozen_family": "STATE",
                    },
                )
            return None
        provider_history_start = (
            _provider_history_start
            if _provider_history_start is not None
            else len(provider_call_history_metadata(provider))
        )

        # Keep one resolver-local record per logical call.  Provider history
        # supplies latency/model/token metadata when available; the resolver
        # adds the exact public request, safe response shape, round, and
        # validation outcome so G1/I1/I2/G2/I1/I2 can be audited together.
        provider_call_records = [
            dict(item) for item in provider_call_history_metadata(provider)[provider_history_start:]
        ]

        def resolution_provider_calls() -> list[dict[str, object]]:
            return [dict(item) for item in provider_call_records]

        grounding = _pre_grounding or _deterministic_dynamic_goal_grounding(
            goal,
            self.db,
            self.scope,
            definition,
        )
        if frozen_family == "STATE":
            grounding = _dynamic_goal_frozen_state_grounding(
                goal,
                self.db,
                self.scope,
                definition,
                grounding,
            )
        public_catalog = (
            {}
            if frozen_family == "STATE"
            else _dynamic_goal_entity_catalog(
                self.db,
                self.scope,
                definition,
            )
        )
        public_catalog_hash = _dynamic_goal_payload_hash(public_catalog)
        deterministic_candidate_refs = (
            grounding.candidate_refs if grounding.status == "RESOLVED" else ()
        )
        # Exact public identity matches are evidence for Stage 1.  They do not
        # determine the sentence's operation/state intent or its semantic
        # roles; those statuses must come from the typed Stage 1 result.
        intent: DynamicGoalIntentDraft | None = None

        grounding_attempts: list[dict[str, object]] = []
        interpretation_attempts: list[dict[str, object]] = []
        ontology: dict[str, object] | None = None
        projection: _DynamicGoalProjection | None = None
        grounded_operation: DynamicGoalGroundedOperation | None = None
        locked_candidate_set: AdHocGoalCandidateSetV2 | None = None
        grounding_rounds_used = 0

        def record_provider_call(
            *,
            purpose: str,
            grounding_round: int,
            interpretation_attempt: int | None,
            request: DynamicGoalEntityGroundingRequest | DynamicGoalInterpretationRequest,
            response: object | None = None,
            validation_result: dict[str, object] | None = None,
            rejection_code: str | None = None,
            validation_diagnostics: tuple[dict[str, object], ...] = (),
            accepted_requirements: tuple[AdHocGoalRequirementCandidateV2, ...] = (),
            candidate_refs: tuple[DynamicGoalCandidateReference, ...] = (),
            projection_for_call: _DynamicGoalProjection | None = None,
        ) -> None:
            current_history = list(provider_call_history_metadata(provider))[
                provider_history_start:
            ]
            record = (
                dict(current_history[len(provider_call_records)])
                if len(current_history) > len(provider_call_records)
                else {}
            )
            inherited_debug_snapshot = record.get("debug_snapshot")
            request_payload = request.model_dump(mode="json")
            normalized_purpose = (
                "DYNAMIC_GOAL_INTERPRETATION"
                if purpose == "dynamic_goal"
                else "DYNAMIC_GOAL_GROUNDING"
            )
            record.update(
                {
                    "purpose": normalized_purpose,
                    "call_type": normalized_purpose,
                    "logical_call_sequence": len(provider_call_records) + 1,
                    "call_order": len(provider_call_records) + 1,
                    "grounding_round": grounding_round,
                    "request_hash": _dynamic_goal_payload_hash(request_payload),
                    "prompt_template_version": (
                        "dynamic-goal-grounding-v7"
                        if purpose == "dynamic_goal_grounding"
                        else "dynamic-goal-interpretation-v2"
                    ),
                }
            )
            record.pop("debug_snapshot", None)
            if self.observability_level == "DEBUG":
                debug_snapshot: dict[str, object] = {
                    "input": goal_provider_request_snapshot(
                        purpose,
                        request_payload,
                    ),
                }
                # A real provider can fail before the resolver receives a
                # response object (for example malformed JSON).  Preserve
                # the provider's already-sanitized output shape in that case;
                # the resolver still owns the logical-call association.
                if isinstance(inherited_debug_snapshot, dict):
                    inherited_output = inherited_debug_snapshot.get("output")
                    if inherited_output is not None:
                        debug_snapshot["output"] = inherited_output
                    inherited_validation = inherited_debug_snapshot.get("response_validation")
                    if inherited_validation is not None:
                        debug_snapshot["response_validation"] = inherited_validation
                record["debug_snapshot"] = debug_snapshot
            if interpretation_attempt is not None:
                record["interpretation_attempt"] = interpretation_attempt
            if purpose == "dynamic_goal_grounding":
                record["public_catalog_hash"] = public_catalog_hash
            else:
                record["focused_ontology_hash"] = _dynamic_goal_payload_hash(
                    request_payload.get("ontology", {})
                    if isinstance(request_payload.get("ontology", {}), dict)
                    else {}
                )
            if response is not None:
                response_for_observation = response
                if purpose == "dynamic_goal":
                    response_for_observation = _dynamic_goal_response_observation(
                        response,
                        projection,
                    )
                if self.observability_level == "DEBUG":
                    current_debug_snapshot = record.get("debug_snapshot")
                    debug_snapshot = (
                        current_debug_snapshot if isinstance(current_debug_snapshot, dict) else {}
                    )
                    record["debug_snapshot"] = {
                        **debug_snapshot,
                        "output": goal_provider_response_snapshot(
                            purpose,
                            response_for_observation,
                            public_catalog=(
                                public_catalog if purpose == "dynamic_goal_grounding" else None
                            ),
                            public_ontology=(
                                request_payload.get("ontology")
                                if purpose == "dynamic_goal"
                                and isinstance(request_payload.get("ontology"), dict)
                                else None
                            ),
                        ),
                    }
                response_status = (
                    response.status
                    if isinstance(
                        response,
                        (DynamicGoalEntityGrounding, DynamicGoalInterpretation),
                    )
                    else response.get("status")
                    if isinstance(response, dict)
                    else None
                )
                if isinstance(response_status, str):
                    record["response_status"] = response_status
            if validation_result is not None:
                record["validation_result"] = validation_result
                pydantic_result = validation_result.get("pydantic")
                if "response_validation" not in record and isinstance(pydantic_result, str):
                    record["response_validation"] = pydantic_result
            if rejection_code is not None:
                record["rejection_code"] = rejection_code
            if validation_diagnostics:
                record["validation_diagnostics"] = list(validation_diagnostics)
            if candidate_refs:
                record["candidate_refs"] = [item.model_dump(mode="json") for item in candidate_refs]
            if projection_for_call is not None:
                record["projection"] = _dynamic_goal_projection_observation(projection_for_call)
            if accepted_requirements:
                record["accepted_requirements"] = [
                    item.model_dump(mode="json") for item in accepted_requirements
                ]
            provider_call_records.append(record)

        def build_observation(
            *,
            stage: str,
            status: str | None = None,
            result: str | None = None,
            validation: str | None = None,
            rejection_code: str | None = None,
            validation_diagnostics: tuple[dict[str, object], ...] = (),
        ) -> dict[str, object]:
            scalar_provider_metadata = provider_call_metadata(provider)
            scalar_provider_metadata.pop("debug_snapshot", None)
            observation: dict[str, object] = {
                **scalar_provider_metadata,
                "call_type": stage,
                "stage": stage,
                "grounding": _dynamic_goal_grounding_observation(grounding, projection),
                "catalog_hash": public_catalog_hash,
                "provider_calls": resolution_provider_calls(),
                "attempts": [*grounding_attempts, *interpretation_attempts],
                "grounding_attempt_count": len(grounding_attempts),
                "grounding_round_count": grounding_rounds_used,
                "interpretation_attempt_count": len(interpretation_attempts),
                "provider_attempt_count": len(provider_call_records),
            }
            observation["attempt_count"] = (
                len(grounding_attempts)
                if stage == "DYNAMIC_GOAL_ENTITY_GROUNDING"
                else len(interpretation_attempts)
            )
            if status is not None:
                observation["status"] = status
            if result is not None:
                observation["result"] = result
            if validation is not None:
                observation["validation"] = validation
            if rejection_code is not None:
                observation["rejection_code"] = rejection_code
            if validation_diagnostics:
                observation["validation_diagnostics"] = list(validation_diagnostics)
            if ontology is not None:
                observation["ontology_hash"] = _dynamic_goal_payload_hash(ontology)
            if grounded_operation is not None:
                observation["grounded_operation"] = grounded_operation.model_dump(mode="json")
            if intent is not None:
                observation["intent"] = intent.model_dump(mode="json")
            return observation

        def raise_with_observation(
            error: GenericProviderError,
            *,
            stage: str,
        ) -> None:
            error.resolution_observation = build_observation(
                stage=stage,
                status="ERROR",
                result=error.code,
                validation="REJECTED",
                rejection_code=error.code,
                validation_diagnostics=error.validation_diagnostics,
            )
            raise error

        if grounding.status == "NEEDS_CLARIFICATION":
            return GenericGoalResolution(
                "NEEDS_CLARIFICATION",
                candidate_keys=_dynamic_goal_grounding_keys(grounding),
                clarification_prompt=grounding.clarification_prompt,
                source="DETERMINISTIC_ENTITY_GROUNDING",
                provider_observation=build_observation(
                    stage="DETERMINISTIC_ENTITY_GROUNDING",
                    status=grounding.status,
                    result="BACKEND_NEEDS_CLARIFICATION",
                    validation="ACCEPTED",
                ),
            )

        grounder = getattr(provider, "ground_dynamic_goal_entities", None)
        needs_provider_grounding = frozen_family is None and callable(grounder)
        if not needs_provider_grounding and frozen_family is None:
            status = "NEEDS_CLARIFICATION" if grounding.status == "NONE" else "UNSUPPORTED"
            return GenericGoalResolution(
                status,
                clarification_prompt=(
                    definition.goal_resolution.clarification_prompt
                    if status == "NEEDS_CLARIFICATION"
                    else None
                ),
                source="NO_PUBLIC_GROUNDING",
                provider_observation=build_observation(
                    stage="DYNAMIC_GOAL_ENTITY_GROUNDING",
                    status=status,
                    result=(
                        "NO_PUBLIC_IDENTITY"
                        if status == "NEEDS_CLARIFICATION"
                        else "NO_GROUNDING_PROVIDER"
                    ),
                    rejection_code="NO_PUBLIC_GROUNDING",
                    validation="ACCEPTED",
                ),
            )

        max_grounding_rounds = 1 if frozen_family == "STATE" else _DYNAMIC_GOAL_MAX_GROUNDING_ROUNDS
        last_interpretation_error: GenericProviderError | None = None
        last_backend_rejection_code: str | None = None
        last_backend_value_type_diagnostics: list[dict[str, object]] = []
        last_recovery_feedback: tuple[DynamicGoalRecoveryFeedback, ...] = ()

        for grounding_round in range(1, max_grounding_rounds + 1):
            round_intent = intent
            ontology = None
            projection = None
            grounded_operation = None
            locked_candidate_set = None
            last_backend_rejection_code = None
            last_backend_value_type_diagnostics = []
            last_recovery_feedback = ()
            if needs_provider_grounding:
                grounding = _DynamicGoalGrounding(status="NONE")
                grounding_rounds_used = grounding_round
                assert callable(grounder)
                grounding_request = DynamicGoalEntityGroundingRequest(
                    goal=goal,
                    public_catalog=public_catalog,
                    deterministic_candidate_refs=deterministic_candidate_refs,
                    intent=round_intent,
                )
                raw_grounding: object | None = None
                try:
                    raw_grounding = grounder(grounding_request)
                    interpreted_grounding = DynamicGoalEntityGrounding.model_validate(raw_grounding)
                except GenericProviderError as exc:
                    grounding_attempt: dict[str, object] = {
                        "stage": "ENTITY_GROUNDING",
                        "attempt": 1,
                        "grounding_round": grounding_round,
                        "source": "MODEL",
                        "status": "ERROR",
                        "validation": "REJECTED",
                        "result": exc.code,
                    }
                    if exc.validation_diagnostics:
                        grounding_attempt["validation_diagnostics"] = list(
                            exc.validation_diagnostics
                        )
                    grounding_attempts.append(grounding_attempt)
                    record_provider_call(
                        purpose="dynamic_goal_grounding",
                        grounding_round=grounding_round,
                        interpretation_attempt=None,
                        request=grounding_request,
                        response=raw_grounding,
                        validation_result={
                            "pydantic": "REJECTED",
                            "candidate_refs": "NOT_RUN",
                            "result": "REJECTED",
                        },
                        rejection_code=exc.code,
                        validation_diagnostics=exc.validation_diagnostics,
                    )
                    if grounding_round < max_grounding_rounds:
                        continue
                    raise_with_observation(exc, stage="DYNAMIC_GOAL_ENTITY_GROUNDING")
                except ValidationError as exc:
                    diagnostics = provider_validation_diagnostics(exc)
                    error = GenericProviderError(
                        "MODEL_PROVIDER_RESPONSE_INVALID",
                        "The model provider returned an invalid Dynamic Goal Entity Grounding",
                        validation_diagnostics=diagnostics,
                    )
                    grounding_attempts.append(
                        {
                            "stage": "ENTITY_GROUNDING",
                            "attempt": 1,
                            "grounding_round": grounding_round,
                            "source": "MODEL",
                            "status": "ERROR",
                            "validation": "REJECTED",
                            "result": error.code,
                            "validation_diagnostics": list(diagnostics),
                        }
                    )
                    record_provider_call(
                        purpose="dynamic_goal_grounding",
                        grounding_round=grounding_round,
                        interpretation_attempt=None,
                        request=grounding_request,
                        response=raw_grounding,
                        validation_result={
                            "pydantic": "REJECTED",
                            "candidate_refs": "NOT_RUN",
                            "result": "REJECTED",
                        },
                        rejection_code=error.code,
                        validation_diagnostics=diagnostics,
                    )
                    if grounding_round < max_grounding_rounds:
                        continue
                    raise_with_observation(error, stage="DYNAMIC_GOAL_ENTITY_GROUNDING")
                except (TypeError, ValueError):
                    error = GenericProviderError(
                        "MODEL_PROVIDER_RESPONSE_INVALID",
                        "The model provider returned an invalid Dynamic Goal Entity Grounding",
                    )
                    grounding_attempts.append(
                        {
                            "stage": "ENTITY_GROUNDING",
                            "attempt": 1,
                            "grounding_round": grounding_round,
                            "source": "MODEL",
                            "status": "ERROR",
                            "validation": "REJECTED",
                            "result": error.code,
                        }
                    )
                    record_provider_call(
                        purpose="dynamic_goal_grounding",
                        grounding_round=grounding_round,
                        interpretation_attempt=None,
                        request=grounding_request,
                        response=raw_grounding,
                        validation_result={
                            "pydantic": "REJECTED",
                            "candidate_refs": "NOT_RUN",
                            "result": "REJECTED",
                        },
                        rejection_code=error.code,
                    )
                    if grounding_round < max_grounding_rounds:
                        continue
                    raise_with_observation(error, stage="DYNAMIC_GOAL_ENTITY_GROUNDING")

                grounding_attempt = {
                    "stage": "ENTITY_GROUNDING",
                    "attempt": 1,
                    "grounding_round": grounding_round,
                    "source": "MODEL",
                    "status": interpreted_grounding.status,
                    "validation": "ACCEPTED",
                    "result": (
                        "MODEL_NEEDS_CLARIFICATION"
                        if interpreted_grounding.status == "NEEDS_CLARIFICATION"
                        else "MODEL_UNSUPPORTED"
                        if interpreted_grounding.status == "UNSUPPORTED"
                        else "MODEL_ACCEPTED"
                    ),
                }
                if interpreted_grounding.status == "NEEDS_CLARIFICATION":
                    grounding_attempts.append(grounding_attempt)
                    record_provider_call(
                        purpose="dynamic_goal_grounding",
                        grounding_round=grounding_round,
                        interpretation_attempt=None,
                        request=grounding_request,
                        response=interpreted_grounding,
                        validation_result={
                            "pydantic": "ACCEPTED",
                            "candidate_refs": "NOT_RUN",
                            "result": "MODEL_NEEDS_CLARIFICATION",
                        },
                    )
                    return GenericGoalResolution(
                        "NEEDS_CLARIFICATION",
                        clarification_prompt=interpreted_grounding.clarification_prompt,
                        source="MODEL_VALIDATED",
                        provider_observation=build_observation(
                            stage="DYNAMIC_GOAL_ENTITY_GROUNDING",
                            status=interpreted_grounding.status,
                            result="MODEL_NEEDS_CLARIFICATION",
                            validation="ACCEPTED",
                        ),
                    )
                if interpreted_grounding.status == "UNSUPPORTED":
                    grounding_attempts.append(grounding_attempt)
                    record_provider_call(
                        purpose="dynamic_goal_grounding",
                        grounding_round=grounding_round,
                        interpretation_attempt=None,
                        request=grounding_request,
                        response=interpreted_grounding,
                        validation_result={
                            "pydantic": "ACCEPTED",
                            "candidate_refs": "NOT_RUN",
                            "result": "MODEL_UNSUPPORTED",
                        },
                    )
                    if grounding_round < max_grounding_rounds:
                        continue
                    return GenericGoalResolution(
                        "UNSUPPORTED",
                        source="MODEL_VALIDATED",
                        provider_observation=build_observation(
                            stage="DYNAMIC_GOAL_ENTITY_GROUNDING",
                            status=interpreted_grounding.status,
                            result="MODEL_UNSUPPORTED",
                            validation="ACCEPTED",
                        ),
                    )
                try:
                    stage1_intent = interpreted_grounding.intent
                    if (
                        frozen_family == "STATE"
                        and stage1_intent is not None
                        and stage1_intent.intent_kind != "STATE"
                    ):
                        raise FormalGoalError(
                            "FROZEN_FAMILY_CONFLICT",
                            "State grounding attempted to reopen the frozen Goal family",
                        )
                    intent_refs = _dynamic_goal_intent_candidate_refs(stage1_intent)
                    candidate_refs = _merge_dynamic_goal_candidate_refs(
                        deterministic_candidate_refs,
                        (*interpreted_grounding.candidate_refs, *intent_refs),
                    )
                    candidate_refs = _validate_dynamic_goal_candidate_refs(
                        definition,
                        self.db,
                        self.scope,
                        candidate_refs,
                    )
                    grounding = _dynamic_goal_grounding_from_refs(
                        candidate_refs,
                        (
                            "MODEL_ENTITY_GROUNDING_WITH_DETERMINISTIC_REFS"
                            if deterministic_candidate_refs
                            else "MODEL_ENTITY_GROUNDING"
                        ),
                        self.db,
                        self.scope,
                        definition,
                    )
                    intent = stage1_intent if stage1_intent is not None else round_intent
                except FormalGoalError as exc:
                    grounding_attempt.update(
                        {
                            "validation": "REJECTED",
                            "result": "BACKEND_VALIDATION_REJECTED",
                            "rejection_code": exc.code,
                        }
                    )
                    grounding_attempts.append(grounding_attempt)
                    record_provider_call(
                        purpose="dynamic_goal_grounding",
                        grounding_round=grounding_round,
                        interpretation_attempt=None,
                        request=grounding_request,
                        response=interpreted_grounding,
                        validation_result={
                            "pydantic": "ACCEPTED",
                            "candidate_refs": "REJECTED",
                            "result": "REJECTED",
                        },
                        rejection_code=exc.code,
                    )
                    if grounding_round < max_grounding_rounds:
                        continue
                    return GenericGoalResolution(
                        "UNSUPPORTED",
                        source="MODEL_VALIDATED",
                        provider_observation=build_observation(
                            stage="DYNAMIC_GOAL_ENTITY_GROUNDING",
                            status=interpreted_grounding.status,
                            result="BACKEND_VALIDATION_REJECTED",
                            validation="REJECTED",
                            rejection_code=exc.code,
                        ),
                    )
                grounding_attempt.update(
                    {
                        "result": "BACKEND_ACCEPTED",
                        "candidate_refs": [
                            item.model_dump(mode="json") for item in grounding.candidate_refs
                        ],
                    }
                )
                grounding_attempts.append(grounding_attempt)
                record_provider_call(
                    purpose="dynamic_goal_grounding",
                    grounding_round=grounding_round,
                    interpretation_attempt=None,
                    request=grounding_request,
                    response=interpreted_grounding,
                    validation_result={
                        "pydantic": "ACCEPTED",
                        "candidate_refs": "ACCEPTED",
                        "result": "ACCEPTED",
                    },
                    candidate_refs=grounding.candidate_refs,
                )

            if grounding.status != "RESOLVED":
                continue

            try:
                candidate_refs = _validate_dynamic_goal_candidate_refs(
                    definition,
                    self.db,
                    self.scope,
                    grounding.candidate_refs,
                )
                if candidate_refs != grounding.candidate_refs:
                    grounding = _dynamic_goal_grounding_from_refs(
                        candidate_refs,
                        grounding.source,
                        self.db,
                        self.scope,
                        definition,
                    )
                projection = _dynamic_goal_projection(
                    db=self.db,
                    scope=self.scope,
                    definition=definition,
                    grounding=grounding,
                )
            except FormalGoalError as exc:
                if needs_provider_grounding and grounding_round < max_grounding_rounds:
                    continue
                return GenericGoalResolution(
                    "UNSUPPORTED",
                    source="BACKEND_VALIDATED",
                    provider_observation=build_observation(
                        stage="DYNAMIC_GOAL_ENTITY_GROUNDING",
                        result="BACKEND_VALIDATION_REJECTED",
                        validation="REJECTED",
                        rejection_code=exc.code,
                    ),
                )

            if intent is not None and _dynamic_goal_intent_has_unresolved_slots(intent):
                if needs_provider_grounding and grounding_round < max_grounding_rounds:
                    continue
                return GenericGoalResolution(
                    "NEEDS_CLARIFICATION",
                    clarification_prompt=definition.goal_resolution.clarification_prompt,
                    source="PUBLIC_OPERATION_GROUNDING_INCOMPLETE",
                    provider_observation=build_observation(
                        stage="DYNAMIC_GOAL_ENTITY_GROUNDING",
                        status="NEEDS_CLARIFICATION",
                        result="EXPLICIT_OPERATION_SLOT_UNRESOLVED",
                        validation="ACCEPTED",
                    ),
                )

            operation_intent_complete = (
                intent is not None
                and intent.intent_kind == "OPERATION"
                and not _dynamic_goal_intent_has_unresolved_slots(intent)
            )
            if intent is not None:
                grounded_operation = _dynamic_goal_grounded_operation(
                    goal,
                    definition,
                    grounding,
                    intent,
                )
            if operation_intent_complete and grounded_operation is None:
                return GenericGoalResolution(
                    "UNSUPPORTED",
                    source="PUBLIC_OPERATION_LOCK_UNREPRESENTABLE",
                    provider_observation=build_observation(
                        stage="DYNAMIC_GOAL_INTERPRETATION",
                        status="UNSUPPORTED",
                        result="EXPLICIT_OPERATION_LOCK_UNREPRESENTABLE",
                        validation="REJECTED",
                        rejection_code="OPERATION_LOCK_UNREPRESENTABLE",
                    ),
                )
            locked_candidate_set = None
            if grounded_operation is not None:
                candidate = _dynamic_goal_grounded_operation_candidate(grounded_operation)
                candidate_set = AdHocGoalCandidateSetV2(requirements=(candidate,))
                try:
                    locked_candidate_set = canonicalize_ad_hoc_dynamic_candidates_v2(
                        definition,
                        candidate_set,
                    )
                    _validate_dynamic_goal_publicity(
                        self.db,
                        self.scope,
                        definition,
                        locked_candidate_set,
                        projection=projection,
                    )
                except FormalGoalError as exc:
                    if _operation_lock_failure_is_system(exc.code):
                        raise GenericProviderError(
                            "MODEL_PROVIDER_FAILURE",
                            "The frozen public operation could not be validated",
                            resolution_observation={
                                "stage": "DYNAMIC_GOAL_INTERPRETATION",
                                "status": "ERROR",
                                "result": "EXPLICIT_OPERATION_LOCK_INVALID",
                                "rejection_code": exc.code,
                            },
                        ) from exc
                    return GenericGoalResolution(
                        "UNSUPPORTED",
                        source="PUBLIC_OPERATION_LOCK_INVALID",
                        provider_observation=build_observation(
                            stage="DYNAMIC_GOAL_INTERPRETATION",
                            status="UNSUPPORTED",
                            result="EXPLICIT_OPERATION_LOCK_INVALID",
                            validation="REJECTED",
                            rejection_code=exc.code,
                        ),
                    )
            if grounded_operation is not None and locked_candidate_set is not None:
                for record in reversed(provider_call_records):
                    if (
                        record.get("purpose") == "DYNAMIC_GOAL_GROUNDING"
                        and record.get("grounding_round") == grounding_round
                    ):
                        record["projection"] = _dynamic_goal_projection_observation(projection)
                        break
                final_observation = build_observation(
                    stage="DYNAMIC_GOAL_INTERPRETATION",
                    status="RESOLVED",
                    result="DETERMINISTIC_GROUNDED_OPERATION",
                    validation="ACCEPTED",
                )
                final_observation["provider_fallback"] = "GROUNDED_OPERATION_LOCK"
                final_observation["stage_2_skipped"] = True
                return GenericGoalResolution(
                    "RESOLVED",
                    dynamic_requirements=locked_candidate_set.requirements,
                    source=FormalGoalSourceKind.AD_HOC_DYNAMIC.value,
                    provider_observation=final_observation,
                )
            ontology = _dynamic_goal_ontology(
                self.db,
                self.scope,
                definition,
                grounding=grounding,
                projection=projection,
                grounded_operation=grounded_operation,
            )
            for record in reversed(provider_call_records):
                if (
                    record.get("purpose") == "DYNAMIC_GOAL_GROUNDING"
                    and record.get("grounding_round") == grounding_round
                ):
                    record["projection"] = _dynamic_goal_projection_observation(projection)
                    break
            can_retry_unsupported_interpretation = _dynamic_goal_grounding_has_semantics(projection)
            last_interpretation_error = None
            interpretation_failed = False
            interpretation: DynamicGoalInterpretation | None = None

            for interpretation_attempt_index in range(
                1,
                _DYNAMIC_GOAL_MAX_INTERPRETATION_ATTEMPTS + 1,
            ):
                request = DynamicGoalInterpretationRequest(
                    goal=goal,
                    ontology=ontology,
                    grounded_candidate_refs=grounding.candidate_refs,
                    grounded_entity_keys=grounding.entity_keys,
                    intent=intent,
                    grounded_operation=grounded_operation,
                    frozen_family=frozen_family,
                    recovery_attempt=0 if interpretation_attempt_index == 1 else 1,
                    recovery_feedback=last_recovery_feedback,
                )
                raw_interpretation: object | None = None
                try:
                    raw_interpretation = interpreter(request)
                    try:
                        interpretation = DynamicGoalInterpretation.model_validate(
                            raw_interpretation
                        )
                    except ValidationError as exc:
                        diagnostics = provider_validation_diagnostics(exc)
                        raise GenericProviderError(
                            "MODEL_PROVIDER_RESPONSE_INVALID",
                            "The model provider returned an invalid Dynamic Goal interpretation",
                            validation_diagnostics=diagnostics,
                            recovery_feedback=dynamic_goal_recovery_feedback(
                                raw_interpretation,
                                public_ontology=request.ontology,
                            ),
                        ) from exc
                    except (TypeError, ValueError) as exc:
                        raise GenericProviderError(
                            "MODEL_PROVIDER_RESPONSE_INVALID",
                            "The model provider returned an invalid Dynamic Goal interpretation",
                        ) from exc
                except GenericProviderError as exc:
                    interpretation_attempt_record: dict[str, object] = {
                        "stage": "DYNAMIC_GOAL_INTERPRETATION",
                        "attempt": interpretation_attempt_index,
                        "grounding_round": grounding_round,
                        "interpretation_attempt": interpretation_attempt_index,
                        "status": "ERROR",
                        "validation": "REJECTED",
                        "result": exc.code,
                    }
                    if exc.validation_diagnostics:
                        interpretation_attempt_record["validation_diagnostics"] = list(
                            exc.validation_diagnostics
                        )
                    last_recovery_feedback = (
                        (_dynamic_goal_grounded_operation_feedback(grounded_operation),)
                        if grounded_operation is not None
                        else exc.recovery_feedback
                    )
                    interpretation_attempts.append(interpretation_attempt_record)
                    record_provider_call(
                        purpose="dynamic_goal",
                        grounding_round=grounding_round,
                        interpretation_attempt=interpretation_attempt_index,
                        request=request,
                        response=raw_interpretation,
                        projection_for_call=projection,
                        candidate_refs=grounding.candidate_refs,
                        validation_result={
                            "pydantic": "REJECTED",
                            "canonicalization": "NOT_RUN",
                            "projection": "NOT_RUN",
                            "exact_version_public": "NOT_RUN",
                            "result": "REJECTED",
                        },
                        rejection_code=exc.code,
                        validation_diagnostics=exc.validation_diagnostics,
                    )
                    last_interpretation_error = exc
                    interpretation_failed = True
                    if interpretation_attempt_index < _DYNAMIC_GOAL_MAX_INTERPRETATION_ATTEMPTS:
                        interpretation_failed = False
                        continue
                    break

                assert interpretation is not None
                interpretation_attempt_record = {
                    "stage": "DYNAMIC_GOAL_INTERPRETATION",
                    "attempt": interpretation_attempt_index,
                    "grounding_round": grounding_round,
                    "interpretation_attempt": interpretation_attempt_index,
                    "status": interpretation.status,
                    "validation": "ACCEPTED",
                    "result": (
                        "MODEL_UNSUPPORTED"
                        if interpretation.status == "UNSUPPORTED"
                        else "MODEL_NEEDS_CLARIFICATION"
                        if interpretation.status == "NEEDS_CLARIFICATION"
                        else "MODEL_ACCEPTED"
                    ),
                }
                interpretation_attempts.append(interpretation_attempt_record)
                if interpretation.status == "NEEDS_CLARIFICATION":
                    if grounded_operation is not None and locked_candidate_set is not None:
                        last_backend_rejection_code = "FORMAL_GOAL_GROUNDED_OPERATION_REQUIRED"
                        last_recovery_feedback = (
                            _dynamic_goal_grounded_operation_feedback(grounded_operation),
                        )
                        interpretation_attempt_record.update(
                            {
                                "validation": "REJECTED",
                                "result": "BACKEND_VALIDATION_REJECTED",
                                "rejection_code": last_backend_rejection_code,
                            }
                        )
                        interpretation_attempts[-1] = interpretation_attempt_record
                        record_provider_call(
                            purpose="dynamic_goal",
                            grounding_round=grounding_round,
                            interpretation_attempt=interpretation_attempt_index,
                            request=request,
                            response=interpretation,
                            projection_for_call=projection,
                            candidate_refs=grounding.candidate_refs,
                            validation_result={
                                "pydantic": "ACCEPTED",
                                "canonicalization": "REJECTED",
                                "projection": "NOT_RUN",
                                "exact_version_public": "REJECTED",
                                "result": "REJECTED",
                            },
                            rejection_code=last_backend_rejection_code,
                        )
                        if interpretation_attempt_index < _DYNAMIC_GOAL_MAX_INTERPRETATION_ATTEMPTS:
                            continue
                        interpretation_failed = True
                        break
                    record_provider_call(
                        purpose="dynamic_goal",
                        grounding_round=grounding_round,
                        interpretation_attempt=interpretation_attempt_index,
                        request=request,
                        response=interpretation,
                        projection_for_call=projection,
                        candidate_refs=grounding.candidate_refs,
                        validation_result={
                            "pydantic": "ACCEPTED",
                            "canonicalization": "NOT_RUN",
                            "projection": "NOT_RUN",
                            "exact_version_public": "NOT_RUN",
                            "result": "MODEL_NEEDS_CLARIFICATION",
                        },
                    )
                    return GenericGoalResolution(
                        "NEEDS_CLARIFICATION",
                        clarification_prompt=interpretation.clarification_prompt,
                        source="MODEL_VALIDATED",
                        provider_observation={
                            **build_observation(
                                stage="DYNAMIC_GOAL_INTERPRETATION",
                                status=interpretation.status,
                                result="MODEL_NEEDS_CLARIFICATION",
                                validation="ACCEPTED",
                            ),
                        },
                    )
                if interpretation.status == "UNSUPPORTED":
                    if grounded_operation is not None and locked_candidate_set is not None:
                        last_backend_rejection_code = "FORMAL_GOAL_GROUNDED_OPERATION_REQUIRED"
                        last_recovery_feedback = (
                            _dynamic_goal_grounded_operation_feedback(grounded_operation),
                        )
                        interpretation_attempt_record.update(
                            {
                                "validation": "REJECTED",
                                "result": "BACKEND_VALIDATION_REJECTED",
                                "rejection_code": last_backend_rejection_code,
                            }
                        )
                        interpretation_attempts[-1] = interpretation_attempt_record
                        record_provider_call(
                            purpose="dynamic_goal",
                            grounding_round=grounding_round,
                            interpretation_attempt=interpretation_attempt_index,
                            request=request,
                            response=interpretation,
                            projection_for_call=projection,
                            candidate_refs=grounding.candidate_refs,
                            validation_result={
                                "pydantic": "ACCEPTED",
                                "canonicalization": "REJECTED",
                                "projection": "NOT_RUN",
                                "exact_version_public": "REJECTED",
                                "result": "REJECTED",
                            },
                            rejection_code=last_backend_rejection_code,
                        )
                        if interpretation_attempt_index < _DYNAMIC_GOAL_MAX_INTERPRETATION_ATTEMPTS:
                            continue
                        interpretation_failed = True
                        break
                    record_provider_call(
                        purpose="dynamic_goal",
                        grounding_round=grounding_round,
                        interpretation_attempt=interpretation_attempt_index,
                        request=request,
                        response=interpretation,
                        projection_for_call=projection,
                        candidate_refs=grounding.candidate_refs,
                        validation_result={
                            "pydantic": "ACCEPTED",
                            "canonicalization": "NOT_RUN",
                            "projection": "NOT_RUN",
                            "exact_version_public": "NOT_RUN",
                            "result": "MODEL_UNSUPPORTED",
                        },
                    )
                    if (
                        can_retry_unsupported_interpretation
                        and interpretation_attempt_index < _DYNAMIC_GOAL_MAX_INTERPRETATION_ATTEMPTS
                    ):
                        continue
                    interpretation_failed = True
                    break

                candidate_set = AdHocGoalCandidateSetV2(requirements=interpretation.requirements)
                try:
                    if frozen_family == "STATE" and any(
                        isinstance(item, AdHocActionCompletedRequirementCandidateV1)
                        for item in candidate_set.requirements
                    ):
                        raise FormalGoalError(
                            "FROZEN_FAMILY_CONFLICT",
                            "State composition cannot produce ACTION_COMPLETED",
                        )
                    canonical_candidate_set = canonicalize_ad_hoc_dynamic_candidates_v2(
                        definition,
                        candidate_set,
                    )
                    if grounded_operation is not None and locked_candidate_set is not None:
                        _validate_dynamic_goal_operation_lock(
                            canonical_candidate_set,
                            locked_candidate_set,
                        )
                    _validate_dynamic_goal_lossless_operation_semantics(
                        goal,
                        definition,
                        grounding,
                        canonical_candidate_set,
                        intent=intent,
                    )
                except FormalGoalError as exc:
                    last_backend_rejection_code = exc.code
                    if grounded_operation is not None:
                        last_recovery_feedback = (
                            _dynamic_goal_grounded_operation_feedback(grounded_operation),
                        )
                    if exc.details:
                        last_backend_value_type_diagnostics = [exc.details]
                    interpretation_attempt_record.update(
                        {
                            "validation": "REJECTED",
                            "result": "BACKEND_VALIDATION_REJECTED",
                            "rejection_code": exc.code,
                        }
                    )
                    if exc.details:
                        interpretation_attempt_record["value_type_diagnostics"] = [exc.details]
                    interpretation_attempts[-1] = interpretation_attempt_record
                    record_provider_call(
                        purpose="dynamic_goal",
                        grounding_round=grounding_round,
                        interpretation_attempt=interpretation_attempt_index,
                        request=request,
                        response=interpretation,
                        projection_for_call=projection,
                        candidate_refs=grounding.candidate_refs,
                        validation_result={
                            "pydantic": "ACCEPTED",
                            "canonicalization": "REJECTED",
                            "projection": "NOT_RUN",
                            "exact_version_public": "NOT_RUN",
                            "result": "REJECTED",
                        },
                        rejection_code=exc.code,
                    )
                    if interpretation_attempt_index < _DYNAMIC_GOAL_MAX_INTERPRETATION_ATTEMPTS:
                        continue
                    interpretation_failed = True
                    break

                try:
                    _validate_dynamic_goal_publicity(
                        self.db,
                        self.scope,
                        definition,
                        canonical_candidate_set,
                        projection=projection,
                    )
                except FormalGoalError as exc:
                    last_backend_rejection_code = exc.code
                    if grounded_operation is not None:
                        last_recovery_feedback = (
                            _dynamic_goal_grounded_operation_feedback(grounded_operation),
                        )
                    if exc.details:
                        last_backend_value_type_diagnostics = [exc.details]
                    interpretation_attempt_record.update(
                        {
                            "validation": "REJECTED",
                            "result": "BACKEND_VALIDATION_REJECTED",
                            "rejection_code": exc.code,
                        }
                    )
                    if exc.details:
                        interpretation_attempt_record["value_type_diagnostics"] = [exc.details]
                    interpretation_attempts[-1] = interpretation_attempt_record
                    record_provider_call(
                        purpose="dynamic_goal",
                        grounding_round=grounding_round,
                        interpretation_attempt=interpretation_attempt_index,
                        request=request,
                        response=interpretation,
                        projection_for_call=projection,
                        candidate_refs=grounding.candidate_refs,
                        validation_result={
                            "pydantic": "ACCEPTED",
                            "canonicalization": "ACCEPTED",
                            "projection": "REJECTED",
                            "exact_version_public": "REJECTED",
                            "result": "REJECTED",
                        },
                        rejection_code=exc.code,
                    )
                    if interpretation_attempt_index < _DYNAMIC_GOAL_MAX_INTERPRETATION_ATTEMPTS:
                        continue
                    interpretation_failed = True
                    break

                interpretation_attempt_record["validation"] = "ACCEPTED"
                interpretation_attempt_record["result"] = "MODEL_ACCEPTED"
                interpretation_attempts[-1] = interpretation_attempt_record
                record_provider_call(
                    purpose="dynamic_goal",
                    grounding_round=grounding_round,
                    interpretation_attempt=interpretation_attempt_index,
                    request=request,
                    response=interpretation,
                    projection_for_call=projection,
                    candidate_refs=grounding.candidate_refs,
                    validation_result={
                        "pydantic": "ACCEPTED",
                        "canonicalization": "ACCEPTED",
                        "projection": "ACCEPTED",
                        "exact_version_public": "ACCEPTED",
                        "result": "ACCEPTED",
                    },
                    accepted_requirements=canonical_candidate_set.requirements,
                )
                return GenericGoalResolution(
                    "RESOLVED",
                    dynamic_requirements=canonical_candidate_set.requirements,
                    source=FormalGoalSourceKind.AD_HOC_DYNAMIC.value,
                    provider_observation={
                        **build_observation(
                            stage="DYNAMIC_GOAL_INTERPRETATION",
                            status=interpretation.status,
                            result="MODEL_ACCEPTED",
                            validation="ACCEPTED",
                        ),
                        "requirement_count": len(interpretation.requirements),
                    },
                )

            if (
                interpretation_failed
                and needs_provider_grounding
                and grounding_round < max_grounding_rounds
            ):
                continue
            if grounded_operation is not None and locked_candidate_set is not None:
                final_observation = build_observation(
                    stage="DYNAMIC_GOAL_INTERPRETATION",
                    status="RESOLVED",
                    result="DETERMINISTIC_GROUNDED_OPERATION",
                    validation="ACCEPTED",
                )
                final_observation["provider_fallback"] = "GROUNDED_OPERATION_LOCK"
                return GenericGoalResolution(
                    "RESOLVED",
                    dynamic_requirements=locked_candidate_set.requirements,
                    source=FormalGoalSourceKind.AD_HOC_DYNAMIC.value,
                    provider_observation=final_observation,
                )
            if last_interpretation_error is not None:
                raise_with_observation(
                    last_interpretation_error,
                    stage="DYNAMIC_GOAL_INTERPRETATION",
                )
            status = interpretation.status if interpretation is not None else "UNSUPPORTED"
            final_observation = build_observation(
                stage="DYNAMIC_GOAL_INTERPRETATION",
                status=status,
                result=(
                    "MODEL_UNSUPPORTED"
                    if status == "UNSUPPORTED"
                    else "BACKEND_VALIDATION_REJECTED"
                ),
                validation="REJECTED"
                if interpretation_failed
                and interpretation is not None
                and interpretation.status == "RESOLVED"
                else "ACCEPTED",
                rejection_code=last_backend_rejection_code,
            )
            if last_backend_value_type_diagnostics:
                final_observation["value_type_diagnostics"] = last_backend_value_type_diagnostics
            return GenericGoalResolution(
                "UNSUPPORTED",
                source="MODEL_VALIDATED",
                provider_observation=final_observation,
            )

        if frozen_family == "STATE":
            status = (
                "NEEDS_CLARIFICATION"
                if grounding.status in {"NONE", "NEEDS_CLARIFICATION"}
                else "UNSUPPORTED"
            )
            return GenericGoalResolution(
                status,
                clarification_prompt=(
                    grounding.clarification_prompt
                    or definition.goal_resolution.clarification_prompt
                    if status == "NEEDS_CLARIFICATION"
                    else None
                ),
                source="NO_PUBLIC_GROUNDING",
                provider_observation={
                    "stage": "STATE_INTERPRETATION",
                    "status": status,
                    "result": (
                        "NO_PUBLIC_STATE_IDENTITY"
                        if status == "NEEDS_CLARIFICATION"
                        else "NO_PUBLIC_STATE_EVIDENCE"
                    ),
                    "rejection_code": "NO_PUBLIC_GROUNDING",
                    "frozen_family": "STATE",
                },
            )
        return GenericGoalResolution("UNSUPPORTED", source="NO_PUBLIC_GROUNDING")

    def _resolve_vnext_state_goal(
        self,
        goal: str,
        definition: ScenarioDefinitionV2,
        *,
        interpreter: object,
        frozen_evidence: _FrozenDynamicGoalEvidence,
        provider_history_start: int,
    ) -> GenericGoalResolution:
        """Resolve a frozen STATE without entering the legacy grounding-round loop."""

        if not callable(interpreter):
            raise GenericProviderError(
                "MODEL_PROVIDER_FAILURE",
                "The configured Goal provider cannot interpret STATE Goals",
                resolution_observation=_vnext_goal_observation(
                    frozen_evidence,
                    frozen_family="STATE",
                    terminal_stage="STATE_INTERPRETATION",
                    result="NO_STATE_INTERPRETER",
                    rejection_code="NO_STATE_INTERPRETER",
                ),
            )
        if not frozen_evidence.merged_refs:
            status = (
                "UNSUPPORTED"
                if frozen_evidence.semantic_status == "UNSUPPORTED"
                else "NEEDS_CLARIFICATION"
            )
            observation = _vnext_goal_observation(
                frozen_evidence,
                frozen_family="STATE",
                terminal_stage="STATE_INTERPRETATION",
                result=(
                    "NO_PUBLIC_STATE_IDENTITY"
                    if status == "NEEDS_CLARIFICATION"
                    else "NO_PUBLIC_STATE_EVIDENCE"
                ),
                rejection_code="NO_PUBLIC_GROUNDING",
            )
            observation["status"] = status
            return GenericGoalResolution(
                status,
                clarification_prompt=(
                    definition.goal_resolution.clarification_prompt
                    if status == "NEEDS_CLARIFICATION"
                    else None
                ),
                source="NO_PUBLIC_GROUNDING",
                provider_observation=observation,
            )

        grounding = _dynamic_goal_grounding_from_refs(
            frozen_evidence.merged_refs,
            "VNEXT_FROZEN_EVIDENCE",
            self.db,
            self.scope,
            definition,
        )
        grounding = _dynamic_goal_frozen_state_grounding(
            goal,
            self.db,
            self.scope,
            definition,
            grounding,
            include_authored_matches=False,
        )
        if grounding.status != "RESOLVED":
            status = (
                "UNSUPPORTED"
                if frozen_evidence.semantic_status == "UNSUPPORTED"
                else "NEEDS_CLARIFICATION"
            )
            observation = _vnext_goal_observation(
                frozen_evidence,
                frozen_family="STATE",
                terminal_stage="STATE_INTERPRETATION",
                result=(
                    "NO_PUBLIC_STATE_IDENTITY"
                    if status == "NEEDS_CLARIFICATION"
                    else "NO_PUBLIC_STATE_EVIDENCE"
                ),
                rejection_code="NO_PUBLIC_GROUNDING",
            )
            observation["status"] = status
            return GenericGoalResolution(
                status,
                clarification_prompt=(
                    definition.goal_resolution.clarification_prompt
                    if status == "NEEDS_CLARIFICATION"
                    else None
                ),
                source="NO_PUBLIC_GROUNDING",
                provider_observation=observation,
            )

        projection = _dynamic_goal_projection(
            db=self.db,
            scope=self.scope,
            definition=definition,
            grounding=grounding,
        )
        ontology = _dynamic_goal_ontology(
            self.db,
            self.scope,
            definition,
            grounding=grounding,
            projection=projection,
        )
        world = ontology.get("world")
        if isinstance(world, dict):
            # STATE interpretation never receives an Action/Actor catalog.
            world["actions"] = []
            world["actors"] = []
        goal_language = ontology.get("goal_language")
        if isinstance(goal_language, dict):
            goal_language["requirement_kinds"] = [
                item
                for item in goal_language.get("requirement_kinds", [])
                if item in {"FACT", "RESOURCE_AT_LEAST", "DERIVED_STATE"}
            ]

        recovery_feedback: tuple[DynamicGoalRecoveryFeedback, ...] = ()
        for recovery_attempt in range(2):
            request = DynamicGoalInterpretationRequest(
                goal=goal,
                ontology=ontology,
                grounded_candidate_refs=grounding.candidate_refs,
                grounded_entity_keys=grounding.entity_keys,
                frozen_family="STATE",
                recovery_attempt=recovery_attempt,
                recovery_feedback=recovery_feedback,
            )
            raw: object | None = None
            try:
                raw = interpreter(request)
                interpretation = DynamicGoalInterpretation.model_validate(raw)
            except GenericProviderError as exc:
                if recovery_attempt == 0 and exc.code in {
                    "MODEL_PROVIDER_RESPONSE_INVALID",
                    "PROVIDER_SCHEMA_INVALID",
                }:
                    recovery_feedback = exc.recovery_feedback
                    continue
                raise
            except (ValidationError, TypeError, ValueError) as exc:
                if recovery_attempt == 0:
                    continue
                raise GenericProviderError(
                    "PROVIDER_SCHEMA_INVALID",
                    "The model provider returned invalid frozen STATE interpretation",
                    validation_diagnostics=(
                        provider_validation_diagnostics(exc)
                        if isinstance(exc, ValidationError)
                        else ()
                    ),
                ) from exc

            if interpretation.status == "NEEDS_CLARIFICATION":
                return GenericGoalResolution(
                    "NEEDS_CLARIFICATION",
                    clarification_prompt=(
                        interpretation.clarification_prompt
                        or definition.goal_resolution.clarification_prompt
                    ),
                    source="STATE_INTERPRETATION_AMBIGUOUS",
                    provider_observation=_vnext_goal_observation(
                        frozen_evidence,
                        frozen_family="STATE",
                        terminal_stage="STATE_INTERPRETATION",
                        result="NEEDS_CLARIFICATION",
                        attempt=recovery_attempt,
                    ),
                )
            if interpretation.status == "UNSUPPORTED":
                return GenericGoalResolution(
                    "UNSUPPORTED",
                    source="STATE_INTERPRETATION_UNSUPPORTED",
                    provider_observation=_vnext_goal_observation(
                        frozen_evidence,
                        frozen_family="STATE",
                        terminal_stage="STATE_INTERPRETATION",
                        result="UNSUPPORTED",
                        attempt=recovery_attempt,
                    ),
                )

            candidate_set = AdHocGoalCandidateSetV2(requirements=interpretation.requirements)
            try:
                if any(
                    isinstance(item, AdHocActionCompletedRequirementCandidateV1)
                    for item in candidate_set.requirements
                ):
                    raise FormalGoalError(
                        "FROZEN_FAMILY_CONFLICT",
                        "Frozen STATE interpretation cannot produce ACTION_COMPLETED",
                    )
                _validate_vnext_state_minimality(
                    definition,
                    frozen_evidence,
                    candidate_set,
                )
                canonical = canonicalize_ad_hoc_dynamic_candidates_v2(
                    definition,
                    candidate_set,
                )
                _validate_dynamic_goal_publicity(
                    self.db,
                    self.scope,
                    definition,
                    canonical,
                    projection=projection,
                )
            except FormalGoalError as exc:
                if recovery_attempt == 0:
                    if exc.code in {
                        "STATE_REQUIREMENT_NOT_MINIMAL",
                        "STATE_OPERATION_EQUIVALENCE_CONFLICT",
                    }:
                        recovery_feedback = (
                            DynamicGoalRecoveryFeedback(
                                requirement_index=0,
                                issue="INVALID_REQUIREMENT_SHAPE",
                                expected_shape={
                                    "rule": (
                                        "Return only the minimal terminal-WHAT requirement "
                                        "directly expressed by the player; do not add sibling "
                                        "or consequence states."
                                    )
                                },
                            ),
                        )
                    continue
                return GenericGoalResolution(
                    "UNSUPPORTED",
                    source=exc.code,
                    provider_observation=_vnext_goal_observation(
                        frozen_evidence,
                        frozen_family="STATE",
                        terminal_stage="STATE_INTERPRETATION",
                        result="BACKEND_VALIDATION_REJECTED",
                        attempt=recovery_attempt,
                        rejection_code=exc.code,
                    ),
                )

            observation = _vnext_goal_observation(
                frozen_evidence,
                frozen_family="STATE",
                terminal_stage="FORMAL_GOAL",
                result="ACCEPTED",
                attempt=recovery_attempt,
                intermediate_stages=(
                    {"stage": "STATE_INTERPRETATION", "result": "RESOLVED"},
                    {"stage": "CONTRACT_VALIDATION", "result": "ACCEPTED"},
                ),
            )
            provider = self.provider
            observation["provider_calls"] = list(provider_call_history_metadata(provider))[
                provider_history_start:
            ]
            return GenericGoalResolution(
                "RESOLVED",
                dynamic_requirements=canonical.requirements,
                source=FormalGoalSourceKind.AD_HOC_DYNAMIC.value,
                provider_observation=observation,
            )

        raise AssertionError("bounded STATE interpretation recovery exhausted unexpectedly")

    def _resolve_contract_driven_operation(
        self,
        goal: str,
        definition: ScenarioDefinitionV2,
        *,
        action_matcher: Callable[[DynamicGoalActionMatchRequest], object] | None = None,
        operation_grounder: Callable[[DynamicGoalOperationGroundingRequest], object],
        frozen_action_key: str | None = None,
        initial_grounding: _DynamicGoalGrounding | None = None,
        frozen_evidence: _FrozenDynamicGoalEvidence | None = None,
    ) -> GenericGoalResolution:
        """Resolve every Action through the same frozen, contract-owned pipeline."""

        deterministic = initial_grounding or (
            _dynamic_goal_grounding_from_refs(
                frozen_evidence.merged_refs,
                "VNEXT_FROZEN_EVIDENCE",
                self.db,
                self.scope,
                definition,
            )
            if frozen_evidence is not None and frozen_evidence.merged_refs
            else _deterministic_dynamic_goal_grounding(goal, self.db, self.scope, definition)
        )
        if deterministic.status == "NEEDS_CLARIFICATION":
            return GenericGoalResolution(
                "NEEDS_CLARIFICATION",
                candidate_keys=_dynamic_goal_grounding_keys(deterministic),
                clarification_prompt=(
                    deterministic.clarification_prompt
                    or definition.goal_resolution.clarification_prompt
                ),
                source="PUBLIC_REFERENCE_AMBIGUOUS",
                provider_observation={
                    "stage": "DETERMINISTIC_PUBLIC_REFERENCE",
                    "status": "NEEDS_CLARIFICATION",
                    "candidate_refs": [
                        item.model_dump(mode="json") for item in deterministic.candidate_refs
                    ],
                    "rejection_code": "PUBLIC_REFERENCE_AMBIGUOUS",
                },
            )
        deterministic_refs = (
            deterministic.candidate_refs
            if frozen_evidence is None and deterministic.status == "RESOLVED"
            else ()
        )
        deterministic_hints = (
            frozen_evidence.deterministic_exact_refs
            if frozen_evidence is not None
            else deterministic_refs
        )
        authoritative_refs = (
            frozen_evidence.semantic_refs
            if frozen_evidence is not None
            else deterministic_refs
        )
        public_catalog = _dynamic_goal_entity_catalog(self.db, self.scope, definition)
        raw_references = public_catalog.get("references", ())
        references = (
            tuple(item for item in raw_references if isinstance(item, dict))
            if isinstance(raw_references, (list, tuple))
            else ()
        )
        action_catalog = tuple(item for item in references if item.get("ref_type") == "ACTION")
        if frozen_action_key is None:
            assert action_matcher is not None
            try:
                action_match = DynamicGoalActionMatch.model_validate(
                    action_matcher(
                        DynamicGoalActionMatchRequest(
                            goal=goal,
                            action_catalog=action_catalog,
                            deterministic_candidate_refs=deterministic_hints,
                        )
                    )
                )
            except GenericProviderError:
                raise
            except (ValidationError, TypeError, ValueError) as exc:
                raise GenericProviderError(
                    "PROVIDER_SCHEMA_INVALID",
                    "The model provider returned an invalid Action match",
                ) from exc
            if action_match.status != "GROUNDED" or action_match.action_key is None:
                code = (
                    "ACTION_UNRESOLVED"
                    if action_match.status == "UNRESOLVED"
                    else "GOAL_UNREPRESENTABLE"
                )
                return GenericGoalResolution(
                    "NEEDS_CLARIFICATION" if action_match.status == "UNRESOLVED" else "UNSUPPORTED",
                    clarification_prompt=(
                        action_match.clarification_prompt
                        or definition.goal_resolution.clarification_prompt
                    ),
                    source=code,
                    provider_observation={
                        "stage": "DYNAMIC_GOAL_ACTION_MATCHING",
                        "frozen_family": "OPERATION",
                        "rejection_code": code,
                    },
                )
            frozen_action_key = action_match.action_key
        action = next(
            (item for item in definition.actions if item.key == frozen_action_key),
            None,
        )
        if action is None or not any(
            item.get("key") == frozen_action_key for item in action_catalog
        ):
            return GenericGoalResolution(
                "UNSUPPORTED",
                source="CANONICAL_IDENTITY_CONFLICT",
                provider_observation={
                    "stage": "DYNAMIC_GOAL_ACTION_MATCHING",
                    "frozen_family": "OPERATION",
                    "rejection_code": "CANONICAL_IDENTITY_CONFLICT",
                },
            )
        exact_actions = {item.key for item in deterministic_refs if item.ref_type == "ACTION"}
        if exact_actions and action.key not in exact_actions:
            return GenericGoalResolution(
                "UNSUPPORTED",
                source="CANONICAL_IDENTITY_CONFLICT",
                provider_observation={
                    "stage": "DYNAMIC_GOAL_ACTION_MATCHING",
                    "frozen_family": "OPERATION",
                    "rejection_code": "CANONICAL_IDENTITY_CONFLICT",
                    "exact_action_keys": sorted(exact_actions),
                    "action_key": action.key,
                },
            )

        action_contract = _dynamic_goal_action_contract(action)
        topology = public_catalog.get("public_topology", {})
        operation_references, operation_topology = _contract_driven_operation_context(
            definition,
            action_contract,
            references,
            topology if isinstance(topology, dict) else {},
            authoritative_refs,
        )
        recovery_feedback: tuple[dict[str, object], ...] = ()
        last_error: GenericProviderError | None = None
        for recovery_attempt in range(2):
            request = DynamicGoalOperationGroundingRequest(
                goal=goal,
                action_key=action.key,
                action_contract=action_contract,
                public_references=operation_references,
                public_topology=operation_topology,
                deterministic_candidate_refs=deterministic_hints,
                deterministic_ambiguous_refs=(
                    frozen_evidence.deterministic_ambiguous_refs
                    if frozen_evidence is not None
                    else ()
                ),
                semantic_candidate_refs=(
                    frozen_evidence.semantic_refs if frozen_evidence is not None else ()
                ),
                explicit_role_evidence=(
                    frozen_evidence.explicit_role_evidence or {}
                    if frozen_evidence is not None
                    else {}
                ),
                recovery_attempt=recovery_attempt,
                recovery_feedback=recovery_feedback,
            )
            raw: object | None = None
            grounded_from_contract = False
            try:
                raw = operation_grounder(request)
                grounded = DynamicGoalOperationGrounding.model_validate(raw)
            except GenericProviderError as exc:
                last_error = exc
                if recovery_attempt == 0:
                    recovery_feedback = tuple(exc.validation_diagnostics) or (
                        {
                            "code": "PROVIDER_SCHEMA_INVALID",
                            "expected": "DynamicGoalOperationGrounding",
                        },
                    )
                    continue
                raise
            except (ValidationError, TypeError, ValueError) as exc:
                diagnostics = (
                    provider_validation_diagnostics(exc) if isinstance(exc, ValidationError) else ()
                )
                last_error = GenericProviderError(
                    "PROVIDER_SCHEMA_INVALID",
                    "The model provider returned invalid Action-contract grounding",
                    validation_diagnostics=diagnostics,
                )
                if recovery_attempt == 0:
                    recovery_feedback = tuple(diagnostics) or (
                        {
                            "code": "PROVIDER_SCHEMA_INVALID",
                            "expected": "DynamicGoalOperationGrounding",
                        },
                    )
                    continue
                raise last_error from exc

            if grounded.status != "RESOLVED" or grounded.intent is None:
                # A provider may call for clarification only for an explicit
                # unresolved contract slot (or a true ``goal_required`` slot).
                # Generic nouns must not create a phantom Resource/Actor/etc.
                # requirement.  If no legal clarification field exists, use
                # the frozen contract evidence directly and continue through
                # normal composition.
                if (
                    grounded.status == "NEEDS_CLARIFICATION"
                    and frozen_evidence is not None
                    and frozen_evidence.frozen_intent is not None
                ):
                    allowed_fields = _vnext_allowed_clarification_fields(
                        action_contract,
                        frozen_evidence,
                    )
                    if not allowed_fields:
                        try:
                            grounded = DynamicGoalOperationGrounding(
                                status="RESOLVED",
                                intent=_vnext_synthetic_operation_intent(
                                    goal,
                                    definition,
                                    action,
                                    frozen_evidence,
                                    public_catalog,
                                ),
                            )
                            grounded_from_contract = True
                        except FormalGoalError as exc:
                            # No legal clarification field exists.  A frozen
                            # contract failure is therefore a deterministic
                            # unsupported result, never a player-facing
                            # clarification fallback.
                            code = "GOAL_UNREPRESENTABLE"
                            observation = _vnext_goal_observation(
                                frozen_evidence,
                                frozen_family="OPERATION",
                                terminal_stage="OPERATION_GROUNDING",
                                result="UNSUPPORTED",
                                attempt=recovery_attempt,
                                rejection_code=code,
                                intermediate_stages=(
                                    {
                                        "stage": "ACTION_ROUTING",
                                        "action_key": action.key,
                                        "result": "MATCHED",
                                    },
                                ),
                            )
                            observation["allowed_clarification_fields"] = []
                            observation["diagnostics"] = {
                                "cause": exc.code,
                                **dict(exc.details),
                            }
                            return GenericGoalResolution(
                                "UNSUPPORTED",
                                clarification_prompt=None,
                                source=code,
                                provider_observation=observation,
                            )
                    else:
                        prompt = _vnext_sanitize_clarification_prompt(
                            grounded.clarification_prompt,
                            allowed_fields,
                            action_contract,
                        )
                        code = "EXPLICIT_CONSTRAINT_UNRESOLVED"
                        observation = _vnext_goal_observation(
                            frozen_evidence,
                            frozen_family="OPERATION",
                            terminal_stage="OPERATION_GROUNDING",
                            result=code,
                            attempt=recovery_attempt,
                            rejection_code=code,
                            intermediate_stages=(
                                {
                                    "stage": "ACTION_ROUTING",
                                    "action_key": action.key,
                                    "result": "MATCHED",
                                },
                            ),
                        )
                        observation["allowed_clarification_fields"] = list(allowed_fields)
                        return GenericGoalResolution(
                            "NEEDS_CLARIFICATION",
                            clarification_prompt=prompt,
                            source=code,
                            provider_observation=observation,
                        )
                if grounded.status != "RESOLVED" or grounded.intent is None:
                    code = (
                        "EXPLICIT_CONSTRAINT_UNRESOLVED"
                        if grounded.status == "NEEDS_CLARIFICATION"
                        else "GOAL_UNREPRESENTABLE"
                    )
                    observation = (
                        _vnext_goal_observation(
                            frozen_evidence,
                            frozen_family="OPERATION",
                            terminal_stage="OPERATION_GROUNDING",
                            result=code,
                            attempt=recovery_attempt,
                            rejection_code=code,
                            intermediate_stages=(
                                {
                                    "stage": "ACTION_ROUTING",
                                    "action_key": action.key,
                                    "result": "MATCHED",
                                },
                            ),
                        )
                        if frozen_evidence is not None
                        else {
                            "stage": "DYNAMIC_GOAL_OPERATION_GROUNDING",
                            "frozen_family": "OPERATION",
                            "action_key": action.key,
                            "rejection_code": code,
                            "recovery_used": bool(recovery_attempt),
                        }
                    )
                    return GenericGoalResolution(
                        "NEEDS_CLARIFICATION"
                        if grounded.status == "NEEDS_CLARIFICATION"
                        else "UNSUPPORTED",
                        clarification_prompt=(
                            grounded.clarification_prompt
                            or definition.goal_resolution.clarification_prompt
                        ),
                        source=code,
                        provider_observation=observation,
                    )
            try:
                operation_intent = (
                    _vnext_enforce_frozen_operation_intent(
                        goal,
                        definition,
                        action_contract,
                        grounded.intent,
                        frozen_evidence,
                        public_catalog,
                    )
                    if frozen_evidence is not None
                    else grounded.intent
                )
                merged_refs = _merge_dynamic_goal_candidate_refs(
                    (
                        frozen_evidence.merged_refs
                        if frozen_evidence is not None
                        else deterministic_refs
                    ),
                    grounded.supplementary_candidate_refs,
                )
                merged_refs = _merge_dynamic_goal_candidate_refs(
                    merged_refs,
                    _operation_intent_candidate_refs(operation_intent),
                )
                merged_refs = _merge_dynamic_goal_candidate_refs(
                    merged_refs,
                    (DynamicGoalCandidateReference(ref_type="ACTION", key=action.key),),
                )
                merged_refs = _validate_dynamic_goal_candidate_refs(
                    definition, self.db, self.scope, merged_refs
                )
                operation = _compose_contract_driven_operation(
                    definition,
                    action,
                    operation_intent,
                    merged_refs,
                    deterministic_refs,
                    operation_topology,
                )
                _validate_explicit_actor_action_compatibility(
                    definition,
                    action,
                    operation,
                )
                _validate_explicit_operation_relation(
                    self.db,
                    self.scope,
                    definition,
                    action_contract,
                    operation,
                )
                if operation.target_key is not None:
                    target_contract = cast(dict[str, object], action_contract["target"])
                    target_ref_type = (
                        operation_intent.target.ref_type
                        if operation_intent.target.status == "GROUNDED"
                        and operation_intent.target.key == operation.target_key
                        else _slot_expected_reference_type(str(target_contract["expected_type"]))
                    )
                    if target_ref_type is not None:
                        merged_refs = _merge_dynamic_goal_candidate_refs(
                            merged_refs,
                            (
                                DynamicGoalCandidateReference(
                                    ref_type=target_ref_type,
                                    key=operation.target_key,
                                ),
                            ),
                        )
                        merged_refs = _validate_dynamic_goal_candidate_refs(
                            definition, self.db, self.scope, merged_refs
                        )
                candidate_set = AdHocGoalCandidateSetV2(
                    requirements=(_dynamic_goal_grounded_operation_candidate(operation),)
                )
                canonical = canonicalize_ad_hoc_dynamic_candidates_v2(definition, candidate_set)
                grounding = _dynamic_goal_grounding_from_refs(
                    merged_refs,
                    "CONTRACT_DRIVEN_OPERATION_GROUNDING",
                    self.db,
                    self.scope,
                    definition,
                )
                projection = _dynamic_goal_projection(
                    db=self.db,
                    scope=self.scope,
                    definition=definition,
                    grounding=grounding,
                )
                _validate_dynamic_goal_publicity(
                    self.db,
                    self.scope,
                    definition,
                    canonical,
                    projection=projection,
                )
            except FormalGoalError as exc:
                if exc.code == "FROZEN_EVIDENCE_CONFLICT":
                    if recovery_attempt == 0:
                        recovery_feedback = ({"code": exc.code, **dict(exc.details)},)
                        continue
                    raise GenericProviderError(
                        "PROVIDER_SCHEMA_INVALID",
                        "The Operation provider violated frozen explicit evidence",
                        validation_diagnostics=(
                            {"code": exc.code, **dict(exc.details)},
                        ),
                    ) from exc
                if (
                    exc.code
                    in {
                        "PARAMETER_TYPE_INVALID",
                        "BINDING_TYPE_INVALID",
                        "CONTRACT_SCHEMA_MISMATCH",
                    }
                    and recovery_attempt == 0
                ):
                    recovery_feedback = ({"code": exc.code, **dict(exc.details)},)
                    continue
                status = (
                    "NEEDS_CLARIFICATION"
                    if exc.code
                    in {
                        "TARGET_UNRESOLVED",
                        "TARGET_AMBIGUOUS",
                        "EXPLICIT_CONSTRAINT_UNRESOLVED",
                    }
                    else "UNSUPPORTED"
                )
                observation = (
                    _vnext_goal_observation(
                        frozen_evidence,
                        frozen_family="OPERATION",
                        terminal_stage="CONTRACT_VALIDATION",
                        result="REJECTED",
                        attempt=recovery_attempt,
                        rejection_code=exc.code,
                        intermediate_stages=(
                            {
                                "stage": "ACTION_ROUTING",
                                "action_key": action.key,
                                "result": "MATCHED",
                            },
                            {"stage": "OPERATION_GROUNDING", "result": "RESOLVED"},
                        ),
                    )
                    if frozen_evidence is not None
                    else {
                        "stage": "DYNAMIC_GOAL_OPERATION_COMPOSITION",
                        "frozen_family": "OPERATION",
                        "action_key": action.key,
                        "rejection_code": exc.code,
                        "diagnostics": dict(exc.details),
                        "recovery_used": bool(recovery_attempt),
                    }
                )
                observation["diagnostics"] = dict(exc.details)
                return GenericGoalResolution(
                    status,
                    clarification_prompt=(
                        definition.goal_resolution.clarification_prompt
                        if status == "NEEDS_CLARIFICATION"
                        else None
                    ),
                    source=exc.code,
                    provider_observation=observation,
                )
            observation = (
                _vnext_goal_observation(
                    frozen_evidence,
                    frozen_family="OPERATION",
                    terminal_stage="FORMAL_GOAL",
                    result="ACCEPTED",
                    attempt=recovery_attempt,
                    intermediate_stages=(
                        {
                            "stage": "ACTION_ROUTING",
                            "action_key": action.key,
                            "result": "MATCHED",
                        },
                        {
                            "stage": "OPERATION_GROUNDING",
                            "result": (
                                "RESOLVED_FROM_FROZEN_CONTRACT"
                                if grounded_from_contract
                                else "RESOLVED"
                            ),
                        },
                        {"stage": "CONTRACT_VALIDATION", "result": "ACCEPTED"},
                    ),
                )
                if frozen_evidence is not None
                else {
                    "stage": "DYNAMIC_GOAL_OPERATION_COMPOSITION",
                    "frozen_family": "OPERATION",
                    "action_key": action.key,
                    "result": "CONTRACT_OPERATION_ACCEPTED",
                    "validation": "ACCEPTED",
                    "recovery_used": bool(recovery_attempt),
                    "stage_2_skipped": True,
                }
            )
            return GenericGoalResolution(
                "RESOLVED",
                dynamic_requirements=canonical.requirements,
                source=FormalGoalSourceKind.AD_HOC_DYNAMIC.value,
                provider_observation=observation,
            )
        assert last_error is not None
        raise last_error


class GenericAgentService:
    """A compact persistent Agent loop driven only by exact v2 Version data."""

    MAX_REPLANS = 5

    def __init__(
        self,
        db: Session,
        scope: RuntimeScope,
        *,
        goal_resolver: GenericGoalResolver | None = None,
        provider: GenericModelProvider | None = None,
        provider_call_observer: ProviderCallObserver | None = None,
        model_max_repair_attempts_per_cycle: int = 2,
    ) -> None:
        self.db = db
        self.scope = scope
        self.provider = provider
        self.goal_resolver = goal_resolver or GenericGoalResolver(
            provider=provider,
            db=db,
            scope=scope,
        )
        self.provider_call_observer = provider_call_observer
        self.model_max_repair_attempts_per_cycle = model_max_repair_attempts_per_cycle
        self._provider_call_started_at: dict[str, float] = {}
        self._last_provider_plan_summary: str | None = None
        self._last_provider_stop_reason: str | None = None
        self._last_provider_attempt: dict[str, object] | None = None
        self._last_planning_cycle: PlanningCycle | None = None

    def create_task(
        self,
        session: ConversationSession,
        goal: str,
        *,
        resolved_goal: GenericGoalResolution | None = None,
        initialize_plan: bool = True,
    ) -> AgentTask:
        require_scope_writable(self.db, self.scope.game_instance_id)
        existing = self.db.scalar(
            select(AgentTask).where(
                AgentTask.game_instance_id == self.scope.game_instance_id,
                AgentTask.status.in_(_NON_TERMINAL_TASK_STATUSES),
            )
        )
        if existing is not None:
            raise GenericAgentError(
                "AGENT_TASK_ALREADY_ACTIVE",
                "A GameInstance may have only one active Task",
            )
        snapshot = self._snapshot()
        definition = snapshot.definition
        resolution = resolved_goal or self.goal_resolver.resolve(goal, definition)
        if resolution.status != "RESOLVED":
            raise GenericAgentError(
                f"GOAL_{resolution.status}",
                resolution.clarification_prompt or "Goal does not resolve in the exact Version",
            )
        formal_goal = self.compile_formal_goal_for_resolution(resolution, snapshot=snapshot)
        return self.create_task_from_formal_goal(
            session,
            goal,
            formal_goal=formal_goal,
            resolver_source=resolution.source,
            provider_observation=resolution.provider_observation,
            initialize_plan=initialize_plan,
        )

    def compile_formal_goal_for_resolution(
        self,
        resolution: GenericGoalResolution,
        *,
        snapshot: ScenarioVersionSnapshot | None = None,
    ) -> FormalGoalContract:
        """Compile one resolved semantic proposal against the exact Version."""

        if resolution.status != "RESOLVED":
            raise GenericAgentError(
                f"GOAL_{resolution.status}",
                resolution.clarification_prompt or "Goal does not resolve in the exact Version",
            )
        exact_snapshot = snapshot or self._snapshot()
        definition = exact_snapshot.definition
        try:
            if resolution.objective_keys and resolution.dynamic_requirements:
                raise FormalGoalError(
                    "FORMAL_GOAL_SOURCE_AMBIGUOUS",
                    "A Goal resolution cannot contain both authored Objectives and "
                    "dynamic requirements",
                )
            if resolution.objective_keys:
                valid_objectives = {objective.key for objective in definition.objectives}
                if not set(resolution.objective_keys).issubset(valid_objectives):
                    raise FormalGoalError(
                        "FORMAL_GOAL_OBJECTIVE_NOT_EXACT",
                        "Goal does not resolve to Objectives in the exact Version",
                    )
                objective_keys = normalize_objective_keys(definition, resolution.objective_keys)
                objectives = tuple(definition.objective_definitions[key] for key in objective_keys)
                return compile_predefined_formal_goal(exact_snapshot, objectives)
            elif resolution.dynamic_requirements:
                candidate_set = AdHocGoalCandidateSetV2(
                    requirements=resolution.dynamic_requirements
                )
                _validate_dynamic_goal_publicity(
                    self.db,
                    self.scope,
                    definition,
                    candidate_set,
                )
                if any(
                    isinstance(item, AdHocActionCompletedRequirementCandidateV1)
                    for item in candidate_set.requirements
                ):
                    return compile_ad_hoc_dynamic_goal_v2(exact_snapshot, candidate_set)
                else:
                    # Preserve the exact V1 contract/hash for state-only
                    # dynamic Goals and their existing persisted payloads.
                    return compile_ad_hoc_dynamic_goal(
                        exact_snapshot,
                        AdHocGoalCandidateSetV1(requirements=tuple(candidate_set.requirements)),
                    )
            else:
                raise FormalGoalError(
                    "FORMAL_GOAL_REQUIREMENTS_REQUIRED",
                    "A resolved Goal needs authored Objectives or dynamic requirements",
                )
        except FormalGoalError as exc:
            raise GenericAgentError(exc.code, exc.message) from exc

    def create_task_from_formal_goal(
        self,
        session: ConversationSession,
        goal: str,
        *,
        formal_goal: FormalGoalContract,
        resolver_source: str,
        provider_observation: dict[str, object] | None = None,
        initialize_plan: bool = True,
        task_id: UUID | None = None,
    ) -> AgentTask:
        """Create a normal AgentTask from an already frozen FormalGoal contract."""

        require_scope_writable(self.db, self.scope.game_instance_id)
        existing = self.db.scalar(
            select(AgentTask).where(
                AgentTask.game_instance_id == self.scope.game_instance_id,
                AgentTask.status.in_(_NON_TERMINAL_TASK_STATUSES),
            )
        )
        if existing is not None:
            raise GenericAgentError(
                "AGENT_TASK_ALREADY_ACTIVE",
                "A GameInstance may have only one active Task",
            )
        snapshot = self._snapshot()
        definition = snapshot.definition
        if session.game_instance_id != self.scope.game_instance_id or not session.actor_key:
            raise GenericAgentError(
                "GENERIC_SESSION_SCOPE_INVALID",
                "Generic task creation requires the Instance primary Actor session",
            )
        try:
            formal_goal.assert_bound_to(snapshot)
        except FormalGoalError as exc:
            raise GenericAgentError(exc.code, exc.message) from exc
        objective_keys = tuple(item.objective_key for item in formal_goal.predefined_objectives)
        objective_scope = (
            ObjectiveScope.create(
                objective_keys,
                f"scenario-version:{self.scope.scenario_version_id}",
            )
            if objective_keys
            else None
        )
        now = datetime.now(UTC)
        task = AgentTask(
            **({"id": task_id} if task_id is not None else {}),
            player_id=self.scope.player_id,
            game_instance_id=self.scope.game_instance_id,
            owner_actor_key=session.actor_key,
            origin_session_id=session.id,
            last_session_id=session.id,
            goal_description=goal,
            scenario_key=definition.metadata.key,
            objective_resolution_status="CONFIRMED",
            objective_scope_keys=(
                list(objective_scope.objective_keys) if objective_scope is not None else None
            ),
            objective_catalog_version=(
                objective_scope.catalog_version if objective_scope is not None else None
            ),
            # The legacy column is non-null for v0.2 compatibility.  Dynamic
            # Tasks do not have an ObjectiveScope; retain the Formal Goal hash
            # only as an opaque integrity fingerprint in that column.
            objective_scope_hash=(
                objective_scope.content_hash
                if objective_scope is not None
                else formal_goal.content_hash
            ),
            formal_goal_contract_schema_version=formal_goal.schema_version,
            formal_goal_source_kind=formal_goal.source_kind.value,
            formal_goal_contract_json=formal_goal.model_dump(mode="json"),
            formal_goal_contract_hash=formal_goal.content_hash,
            formal_goal_scenario_version_id=formal_goal.scenario.scenario_version_id,
            formal_goal_scenario_content_hash=formal_goal.scenario.scenario_content_hash,
            formal_goal_compiler_version=formal_goal.compiler_version,
            objective_resolver_source=resolver_source,
            objective_resolver_version="generic-goal-resolver@1",
            objective_resolution_metadata={
                "exact_version": str(self.scope.scenario_version_id),
                "provider_calls": (
                    [provider_observation]
                    if provider_observation is not None
                    else []
                ),
            },
            objective_resolved_at=now,
            objective_confirmed_at=now,
            objective_confirmation_source=resolver_source,
            objective_frozen_at=now,
            objective_freeze_source="GENERIC_AGENT",
            planning_mode="PROVIDER" if self.provider is not None else "GENERIC",
        )
        self.db.add(task)
        self.db.flush()
        if self.evaluate(task).completed:
            self._complete_task(task)
        # Formal Play uses the same task/objective construction but lets the
        # player explicitly acknowledge the resolved Goal before initial
        # planning starts.  The default keeps the generic engine's existing
        # eager-planning behavior unchanged for all other callers.
        elif initialize_plan:
            self.plan(task)
        return task

    def plan(
        self,
        task: AgentTask,
        *,
        reason: str | None = None,
        planning_continuity: PlanningContinuity | None = None,
    ) -> AgentPlan:
        require_scope_writable(self.db, self.scope.game_instance_id)
        if reason is not None and task.replan_count >= self.MAX_REPLANS:
            raise GenericAgentError(
                "GENERIC_REPLAN_LIMIT",
                "The Task reached the generic replan safety limit",
            )
        definition = self._definition()
        self._task_scope(task)
        formal_goal = self._formal_goal(task)
        if formal_goal.source_kind.value == "AD_HOC_DYNAMIC" and self.provider is None:
            raise GenericAgentError(
                "DYNAMIC_GOAL_PROVIDER_REQUIRED",
                "An AD_HOC_DYNAMIC Goal requires a planning Provider",
            )
        objectives = formal_goal_planning_objectives(
            formal_goal,
            definition,
            goal_description=task.goal_description,
        )
        next_version = task.current_plan_version + 1
        self._last_provider_stop_reason = None
        self._last_planning_cycle = None
        if self.provider is not None:
            steps = self._provider_steps(
                task,
                definition,
                objectives,
                reason,
                next_version,
                planning_continuity=planning_continuity,
                formal_goal=formal_goal,
            )
            planning_cycle = self._last_planning_cycle
        else:
            steps = self._candidate_steps(
                definition,
                objectives,
                task=task,
                reason=reason,
                plan_version=next_version,
            )
            planning_cycle = None
        if (
            not steps
            and not self.evaluate(task).completed
            and getattr(self, "_last_provider_stop_reason", None) != "BLOCKED"
        ):
            raise GenericAgentError(
                "GENERIC_PLAN_NOT_FOUND",
                "No exact-Version Action can advance the frozen Objective from current Knowledge",
            )
        return self._persist_plan(
            task,
            steps,
            reason=reason,
            plan_version=next_version,
            planning_cycle=planning_cycle,
        )

    def plan_one_attempt(
        self,
        task: AgentTask,
        *,
        reason: str | None = None,
    ) -> tuple[AgentPlan | None, PlanningCycle | None]:
        """Run exactly one Provider attempt for a Formal Play planning cycle.

        Rejected proposals remain on the durable PlanningCycle and never become
        AgentPlan/AgentStep rows.  The next HTTP request calls this method again
        and supplies the prior typed diagnostics as a REPAIR request.
        """

        require_scope_writable(self.db, self.scope.game_instance_id)
        if self.provider is None:
            return self.plan(task, reason=reason), None
        if reason is not None and task.replan_count >= self.MAX_REPLANS:
            raise GenericAgentError(
                "GENERIC_REPLAN_LIMIT",
                "The Task reached the generic replan safety limit",
            )
        definition = self._definition()
        self._task_scope(task)
        formal_goal = self._formal_goal(task)
        objectives = formal_goal_planning_objectives(
            formal_goal,
            definition,
            goal_description=task.goal_description,
        )
        call_type = "INITIAL_PLAN" if reason is None else "REPLAN"
        cycle = self.db.scalar(
            select(PlanningCycle)
            .where(
                PlanningCycle.task_id == task.id,
                PlanningCycle.base_call_type == call_type,
                PlanningCycle.status == "RUNNING",
            )
            .order_by(PlanningCycle.created_at.desc())
        )
        if cycle is None:
            start_attempt = 0
            rejected_segment = None
            diagnostics: tuple[PlanViolation, ...] = ()
            memory: tuple[AntiRegressionMemoryItem, ...] = ()
            planner_input_override = None
        else:
            start_attempt = cycle.current_attempt + 1
            rejected_segment = cycle.rejected_segment
            diagnostics = tuple(
                PlanViolation.model_validate(item)
                for item in cycle.current_violations
                if isinstance(item, dict)
            )
            memory = tuple(
                AntiRegressionMemoryItem.model_validate(item)
                for item in cycle.anti_regression_memory
                if isinstance(item, dict)
            )
            planner_input_override = PlannerInput.model_validate(cycle.planner_input)
            if start_attempt > self.model_max_repair_attempts_per_cycle:
                self._finish_planning_cycle(
                    cycle,
                    status="REJECTED",
                    finished_at=datetime.now(UTC),
                )
                task.status = AgentTaskStatus.BLOCKED
                task.last_error_code = "MODEL_PLAN_REJECTED"
                self.db.flush()
                return None, cycle
        next_version = task.current_plan_version + 1
        steps = self._provider_steps(
            task,
            definition,
            objectives,
            reason,
            next_version,
            single_attempt=True,
            starting_repair_attempt=start_attempt,
            rejected_segment=rejected_segment,
            initial_diagnostics=diagnostics,
            initial_memory=memory,
            planning_cycle=cycle,
            planner_input_override=planner_input_override,
            formal_goal=formal_goal,
        )
        # A fresh cycle is created inside _provider_steps for the first
        # attempt.  Resolve it here so the caller can persist the rejection
        # state and expose it to the next HTTP repair request.
        if cycle is None:
            cycle = self._latest_planning_cycle(task)
        attempt_result = self._last_provider_attempt or {}
        accepted = bool(attempt_result.get("accepted"))
        if not accepted:
            assert cycle is not None
            if start_attempt >= self.model_max_repair_attempts_per_cycle:
                self._finish_planning_cycle(
                    cycle,
                    status="REJECTED",
                    finished_at=datetime.now(UTC),
                )
                task.status = AgentTaskStatus.BLOCKED
                task.last_error_code = "MODEL_PLAN_REJECTED"
                self.db.flush()
            return None, cycle
        plan = self._persist_plan(
            task,
            steps,
            reason=reason,
            plan_version=next_version,
            planning_cycle=cycle,
        )
        return plan, cycle

    def _persist_plan(
        self,
        task: AgentTask,
        steps: list[dict[str, object]],
        *,
        reason: str | None,
        plan_version: int,
        planning_cycle: PlanningCycle | None = None,
    ) -> AgentPlan:
        actor = self._actor(task.owner_actor_key)
        old_plan = self.db.scalar(
            select(AgentPlan).where(
                AgentPlan.task_id == task.id,
                AgentPlan.status == AgentPlanStatus.ACTIVE,
            )
        )
        if old_plan is not None:
            old_plan.status = AgentPlanStatus.SUPERSEDED
        plan = AgentPlan(
            task_id=task.id,
            version=plan_version,
            status=AgentPlanStatus.ACTIVE,
            strategy_summary=(
                getattr(self, "_last_provider_plan_summary", None)
                or "Execute exact-Version actions for the frozen objective scope"
            ),
            replan_reason=reason,
            planning_cycle_id=planning_cycle.id if planning_cycle is not None else None,
            supersedes_plan_id=old_plan.id if old_plan else None,
            created_by_actor_key=actor.actor_key,
            source="PROVIDER" if self.provider is not None else "GENERIC",
            planner_model=(self.provider.model_name if self.provider is not None else None),
            validation_status="PASSED",
            validation_errors=[],
            stop_reason=self._last_provider_stop_reason or "OBJECTIVE_COMPLETION",
        )
        self.db.add(plan)
        self.db.flush()
        for sequence, candidate in enumerate(steps, start=1):
            self.db.add(
                AgentStep(
                    plan_id=plan.id,
                    sequence=sequence,
                    planner_step_id=(
                        str(candidate["planner_step_id"])
                        if candidate.get("planner_step_id")
                        else None
                    ),
                    description=candidate["description"],
                    execution_type=candidate["execution_type"],
                    assigned_actor_key=str(candidate["actor_key"]),
                    action_intent=candidate["action_intent"],
                    constraints={
                        "scenario_version_id": str(self.scope.scenario_version_id),
                        "planner_purpose": str(
                            candidate.get("planner_purpose") or candidate["description"]
                        ),
                        **(
                            {"planner_step_id": candidate["planner_step_id"]}
                            if candidate.get("planner_step_id")
                            else {}
                        ),
                        **(
                            {"short_actor_reason": str(candidate["short_actor_reason"])}
                            if candidate.get("short_actor_reason")
                            else {}
                        ),
                    },
                    allowed_tool_names=(
                        ["execute_action"]
                        if candidate["execution_type"] == StepExecutionType.TOOL
                        else []
                    ),
                    selected_tool_name=(
                        "execute_action"
                        if candidate["execution_type"] == StepExecutionType.TOOL
                        else None
                    ),
                    tool_arguments=candidate["arguments"],
                    expected_outcome=candidate["expected_outcome"],
                    resume_condition=candidate["resume_condition"],
                )
            )
        task.current_plan_version = plan.version
        if getattr(self, "_last_provider_stop_reason", None) == "BLOCKED":
            task.status = AgentTaskStatus.BLOCKED
            task.last_error_code = "PLAN_SEGMENT_BLOCKED"
        if reason is not None:
            task.replan_count += 1
        if task.last_error_code == PLAN_INVALIDATED_BY_NEW_KNOWLEDGE:
            task.last_error_code = None
        self.db.flush()
        return plan

    def execute_next(self, task: AgentTask, *, replan_on_failure: bool = True) -> AgentStep | None:
        require_scope_writable(self.db, self.scope.game_instance_id)
        self._task_scope(task)
        if self.evaluate(task).completed:
            self._complete_task(task)
            return None
        plan = self._active_plan(task)
        step = self.db.scalar(
            select(AgentStep)
            .where(
                AgentStep.plan_id == plan.id,
                AgentStep.status.in_(
                    [
                        AgentStepStatus.PENDING,
                        AgentStepStatus.REQUIRES_PLAYER_DECISION,
                        AgentStepStatus.WAITING_FOR_WORLD_EVENT,
                    ]
                ),
            )
            .order_by(AgentStep.sequence)
        )
        if step is None:
            self.plan(
                task,
                reason=(
                    "INFORMATION_BOUNDARY"
                    if plan.stop_reason == "INFORMATION_BOUNDARY"
                    else "PLAN_EXHAUSTED"
                ),
            )
            return self.execute_next(task, replan_on_failure=replan_on_failure)
        if step.execution_type == StepExecutionType.WAIT_FOR_WORLD_EVENT:
            operation = self.db.scalar(
                select(WorldOperation)
                .where(
                    WorldOperation.game_instance_id == self.scope.game_instance_id,
                    WorldOperation.task_id == task.id,
                )
                .order_by(WorldOperation.created_at.desc())
            )
            if operation is None or operation.status == WorldOperationStatus.PENDING:
                step.status = AgentStepStatus.WAITING_FOR_WORLD_EVENT
                task.status = AgentTaskStatus.WAITING_FOR_WORLD_EVENT
                self.db.flush()
                return step
            step.status = AgentStepStatus.SUCCEEDED
            step.actual_result = operation.outcome
            step.completed_at = datetime.now(UTC)
            task.status = AgentTaskStatus.ACTIVE
            failure_payload = (
                operation.outcome.get("failure") if isinstance(operation.outcome, dict) else None
            )
            if isinstance(failure_payload, dict) and failure_payload.get("code"):
                failure_code = str(failure_payload["code"])
                self._record_action_failure(
                    task,
                    step,
                    failure_code,
                    retryable=bool(failure_payload.get("retryable", False)),
                    replan=replan_on_failure,
                )
                self.db.flush()
                return step
        else:
            decision_id = None
            if step.status == AgentStepStatus.REQUIRES_PLAYER_DECISION:
                decision = self.db.scalar(
                    select(ActionDecisionRequest).where(
                        ActionDecisionRequest.game_instance_id == self.scope.game_instance_id,
                        ActionDecisionRequest.source_step_id == step.id,
                    )
                )
                if decision is None or decision.status == DecisionStatus.PENDING:
                    task.status = AgentTaskStatus.REQUIRES_PLAYER_DECISION
                    return step
                decision_id = decision.id
            step.status = AgentStepStatus.IN_PROGRESS
            step.attempts += 1
            step.started_at = datetime.now(UTC)
            arguments = step.tool_arguments
            try:
                result = GenericActionService(self.db, self.scope).execute_action(
                    actor_key=step.assigned_actor_key or "",
                    action_key=str(arguments["action_key"]),
                    target_key=str(arguments["target_key"]),
                    parameters=dict(arguments["parameters"]),
                    idempotency_key=str(arguments["idempotency_key"]),
                    task_id=task.id,
                    source_step_id=step.id,
                    decision_id=decision_id,
                )
            except GenericApprovalRequired as exc:
                step.status = AgentStepStatus.REQUIRES_PLAYER_DECISION
                step.actual_result = {"decision_id": str(exc.decision.id)}
                task.status = AgentTaskStatus.REQUIRES_PLAYER_DECISION
                self.db.flush()
                return step
            except GenericActionError as exc:
                self._record_action_failure(
                    task,
                    step,
                    exc.code,
                    retryable=exc.retryable,
                    replan=replan_on_failure,
                )
                self.db.flush()
                return step
            step.actual_result = {
                "operation_id": str(result.operation.id),
                "status": result.operation.status.value,
                "outcome": result.operation.outcome,
            }
            task.status = AgentTaskStatus.ACTIVE
            if result.applied is not None and result.applied.outcome.failure is not None:
                failure = result.applied.outcome.failure
                self._record_action_failure(
                    task,
                    step,
                    failure.code,
                    retryable=failure.retryable,
                    replan=replan_on_failure,
                )
                self.db.flush()
                return step
            if result.operation.status == WorldOperationStatus.PENDING:
                step.status = AgentStepStatus.SUCCEEDED
                step.completed_at = datetime.now(UTC)
            else:
                step.status = AgentStepStatus.SUCCEEDED
                step.completed_at = datetime.now(UTC)
        if step.status == AgentStepStatus.SUCCEEDED:
            # A successful public Knowledge acquisition is real progress.  The
            # replan guard limits consecutive replans that make no progress;
            # carrying that counter across a newly revealed Fact, Route, or
            # Resource domain can incorrectly terminalize an otherwise
            # solvable Task before the next PlannerInput is built.
            if self._step_has_knowledge_changes(step):
                task.replan_count = 0
            revalidation = self.revalidate_remaining_plan(task, completed_step=step)
            if revalidation.invalidated and replan_on_failure:
                self.plan(task, reason=PLAN_INVALIDATED_BY_NEW_KNOWLEDGE)
        if self.evaluate(task).completed:
            self._complete_task(task)
        self.db.flush()
        return step

    def revalidate_remaining_plan(
        self,
        task: AgentTask,
        *,
        completed_step: AgentStep,
    ) -> PlanRevalidationResult:
        """Revalidate only the not-yet-executed suffix after new Knowledge.

        This deliberately uses persisted Knowledge and current Runtime actor
        state.  It never consults a hidden Truth value to retroactively reject
        a Plan that was valid when it was generated.
        """

        if not self._step_has_knowledge_changes(completed_step):
            return PlanRevalidationResult(False)
        plan = self.db.scalar(
            select(AgentPlan).where(
                AgentPlan.id == completed_step.plan_id,
                AgentPlan.status == AgentPlanStatus.ACTIVE,
            )
        )
        if plan is None:
            return PlanRevalidationResult(False)
        remaining_steps = tuple(
            self.db.scalars(
                select(AgentStep)
                .where(
                    AgentStep.plan_id == plan.id,
                    AgentStep.sequence > completed_step.sequence,
                    AgentStep.status.in_(
                        (
                            AgentStepStatus.PENDING,
                            AgentStepStatus.REQUIRES_PLAYER_DECISION,
                            AgentStepStatus.WAITING_FOR_WORLD_EVENT,
                        )
                    ),
                )
                .order_by(AgentStep.sequence)
            )
        )
        diagnostics = self._revalidate_remaining_plan_sequential(
            definition=self._definition(),
            remaining_steps=remaining_steps,
        )
        if not diagnostics:
            return PlanRevalidationResult(False)

        knowledge_changes = list(self._step_knowledge_changes(completed_step))
        plan.status = AgentPlanStatus.SUPERSEDED
        for step in remaining_steps:
            step.status = AgentStepStatus.SKIPPED
        task.last_error_code = PLAN_INVALIDATED_BY_NEW_KNOWLEDGE
        metadata = dict(task.objective_resolution_metadata or {})
        metadata["plan_invalidation"] = {
            "reason": PLAN_INVALIDATED_BY_NEW_KNOWLEDGE,
            "plan_version": plan.version,
            "completed_step_sequence": completed_step.sequence,
            "completed_step_action": completed_step.action_intent,
            "knowledge_changes": knowledge_changes,
            "diagnostics": list(diagnostics),
        }
        task.objective_resolution_metadata = metadata
        self.db.flush()
        return PlanRevalidationResult(
            True,
            reason=PLAN_INVALIDATED_BY_NEW_KNOWLEDGE,
            diagnostics=diagnostics,
        )

    def has_pending_plan_invalidation(self, task: AgentTask) -> bool:
        """Return whether Formal Play must obtain a player-approved Replan."""

        if task.last_error_code != PLAN_INVALIDATED_BY_NEW_KNOWLEDGE:
            return False
        marker = self._plan_invalidation(task)
        return (
            marker is not None
            and marker.get("plan_version") == task.current_plan_version
            and self.db.scalar(
                select(AgentPlan.id).where(
                    AgentPlan.task_id == task.id,
                    AgentPlan.status == AgentPlanStatus.ACTIVE,
                )
            )
            is None
        )

    def _revalidate_remaining_plan_sequential(
        self,
        *,
        definition: ScenarioDefinitionV2,
        remaining_steps: tuple[AgentStep, ...],
    ) -> tuple[dict[str, object], ...]:
        """Validate the remaining suffix as one projected Plan.

        The runtime actor locations are the starting state.  Only the same
        narrow projections used by initial proposal validation are advanced:
        travel/transport moves an actor, and a declarative passability effect
        can update the validation-only known-passability map.  No hidden Truth,
        resource simulation, or rule preflight is consulted here.
        """

        actors = {
            actor.actor_key: actor
            for actor in self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == self.scope.game_instance_id
                )
            )
        }
        projected_actor_locations = {
            actor_key: actor.current_node_key for actor_key, actor in actors.items()
        }
        projected_command_reachability = {
            actor_key: _actor_command_reachability(actor) for actor_key, actor in actors.items()
        }
        projected_known_passability = self._known_passability(definition)
        projected_known_facts = self._known_fact_projection()
        projected_known_nodes = self._known_node_keys()
        projected_known_relations = self._known_relation_keys(definition)
        projected_resource_pools, projected_region_resource_knowledge = (
            self._projected_resource_state(definition)
        )
        projected_known_resource_balance: dict[tuple[str | None, str], int] = {}
        actions = {action.key: action for action in definition.actions}

        for step in remaining_steps:
            if step.execution_type != StepExecutionType.TOOL:
                continue
            action_key = step.tool_arguments.get("action_key")
            actor_key = step.assigned_actor_key
            target_key = step.tool_arguments.get("target_key")
            if (
                not isinstance(action_key, str)
                or not action_key
                or not isinstance(actor_key, str)
                or not actor_key
                or not isinstance(target_key, str)
                or not target_key
            ):
                return (
                    {
                        "code": "KNOWN_PLAN_STEP_INVALID",
                        "sequence": step.sequence,
                        "action_key": action_key,
                    },
                )

            action = actions.get(action_key)
            actor = actors.get(actor_key)
            if action is None or actor is None:
                return (
                    {
                        "code": "KNOWN_PLAN_STEP_INVALID",
                        "sequence": step.sequence,
                        "action_key": action_key,
                    },
                )
            parameters_value = step.tool_arguments.get("parameters", {})
            parameters = dict(parameters_value) if isinstance(parameters_value, dict) else {}
            try:
                invocation = canonical_action_invocation(
                    action,
                    actor_key=actor_key,
                    target_key=target_key,
                    parameters=parameters,
                )
                parameters = cast(ActionParameters, dict(invocation.parameters))
            except (TypeError, ValueError) as exc:
                return (
                    {
                        "code": "PARAMETER_INVALID",
                        "failure_code": "GENERIC_PLAN_PARAMETER_INVALID",
                        "dimension": "PARAMETER",
                        "step_id": step.planner_step_id,
                        "sequence": step.sequence,
                        "action_key": action.key,
                        "actor_key": actor_key,
                        "target_key": target_key,
                        "actual_parameters": parameters,
                        "validation_error": str(exc),
                    },
                )
            planning_failure_code = self._planning_action_failure_code(
                definition, action, actor, target_key
            )
            if planning_failure_code is not None:
                diagnostic = _structured_plan_diagnostic(
                    GenericAgentError(
                        planning_failure_code,
                        "The Action assignment no longer satisfies its static contract",
                        details=_planning_failure_details(
                            definition,
                            action,
                            actor,
                            target_key,
                            planning_failure_code,
                        ),
                    ),
                    action=action,
                    step_id=step.planner_step_id or "",
                    actor_key=actor_key,
                    target_key=target_key,
                    projected_command_reachability=projected_command_reachability,
                )
                diagnostic["sequence"] = step.sequence
                return (diagnostic,)
            projected_resolution_effects = self._projected_resolution_effects(
                definition,
                action,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
            try:
                self._validate_projected_command_reachability(
                    action,
                    actor_key,
                    target_key,
                    actors,
                    projected_command_reachability,
                )
                self._validate_projected_action_state(
                    definition,
                    action,
                    actor_key,
                    target_key,
                    parameters,
                    projected_actor_locations,
                    projected_known_passability,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                    actors=actors,
                    projected_command_reachability=projected_command_reachability,
                )
                self._validate_and_advance_projected_resources(
                    definition,
                    action,
                    actor_key,
                    target_key,
                    parameters,
                    projected_actor_locations,
                    projected_resource_pools,
                    projected_region_resource_knowledge,
                    projected_known_resource_balance,
                    projected_resolution_effects,
                )
            except GenericAgentError as exc:
                diagnostic = _structured_plan_diagnostic(
                    exc,
                    action=action,
                    step_id=step.planner_step_id or "",
                    actor_key=actor_key,
                    target_key=target_key,
                    projected_command_reachability=projected_command_reachability,
                )
                diagnostic["sequence"] = step.sequence
                return (diagnostic,)

            self._advance_projected_action_state(
                definition,
                action,
                actor_key,
                target_key,
                parameters,
                projected_actor_locations,
                projected_known_passability,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
                projected_resolution_effects,
                projected_command_reachability=projected_command_reachability,
            )
        return ()

    @staticmethod
    def _step_has_knowledge_changes(step: AgentStep) -> bool:
        return bool(GenericAgentService._step_knowledge_changes(step))

    @staticmethod
    def _step_knowledge_changes(step: AgentStep) -> tuple[dict[str, object], ...]:
        actual_result = step.actual_result
        if not isinstance(actual_result, dict):
            return ()
        outcome = actual_result.get("outcome", actual_result)
        if not isinstance(outcome, dict):
            return ()
        changes = outcome.get("knowledge_changes")
        if not isinstance(changes, list):
            return ()
        return tuple(item for item in changes if isinstance(item, dict))

    @staticmethod
    def _plan_invalidation(task: AgentTask) -> dict[str, object] | None:
        metadata = task.objective_resolution_metadata
        if not isinstance(metadata, dict):
            return None
        marker = metadata.get("plan_invalidation")
        return marker if isinstance(marker, dict) else None

    def evaluate(self, task: AgentTask) -> GenericObjectiveEvaluation:
        contract = self._formal_goal(task)
        definition = self._definition()
        try:
            result = FormalGoalCompletionEvaluator(self.db, self.scope).evaluate(
                contract,
                definition=definition,
                task=task,
            )
        except LookupError:
            raise GenericAgentError(
                "OBJECTIVE_TRUTH_MISSING", "Objective Truth is missing from this Instance"
            ) from None
        player_visible_completed = result.player_visible_completed
        if player_visible_completed is None:
            raise GenericAgentError(
                "OBJECTIVE_KNOWLEDGE_UNAVAILABLE",
                "Player-visible Goal completion requires the exact Scenario definition",
            )
        evaluations = tuple(
            (item.identity, cast(StrictScalar, item.value), item.satisfied)
            for item in result.requirements
        )
        return GenericObjectiveEvaluation(
            tuple(item.objective_key for item in contract.predefined_objectives),
            player_visible_completed,
            evaluations,
            result.completed,
        )

    @staticmethod
    def _action_idempotency_key(
        task_id: UUID,
        *,
        plan_version: int,
        action_key: str,
        step_index: int | None = None,
    ) -> str:
        """Build an Action key unique to one Task's Plan step.

        Action operations are instance-scoped, while a GameInstance may
        contain multiple historical Tasks for the same Objective.  The Task
        identity therefore has to be part of the key; Objective keys alone
        can collide when a player starts the same Objective again.
        """

        step_identity = str(step_index) if step_index is not None else "candidate"
        return f"task-{task_id}-plan-{plan_version}-{step_identity}-{action_key}"[:160]

    def _candidate_steps(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        *,
        task: AgentTask,
        reason: str | None,
        plan_version: int,
    ) -> list[dict[str, object]]:
        objective_needed = [
            requirement.fact_ref
            for objective in objectives
            for prerequisite in objective.prerequisites
            for requirement in prerequisite.requirements
            if requirement.fact_ref is not None
            and not self._known_requirement_satisfied(requirement)
        ] + [
            requirement.fact_ref
            for objective in objectives
            for requirement in objective.completion_requirements
            if requirement.fact_ref is not None
            and not self._known_requirement_satisfied(requirement)
        ]
        needed = list(objective_needed)
        recovery_refs = {
            (item.node_key, item.fact_key)
            for action in definition.actions
            for item in action.planning.supporting_effects
        }
        if reason is not None:
            needed = [*sorted(recovery_refs), *needed]
        actions = sorted(
            definition.actions,
            key=lambda item: (
                0 if reason is not None and item.planning.supporting_effects else 1,
                item.key,
            ),
        )
        candidates: list[dict[str, object]] = []
        covered: set[tuple[str, str]] = set()
        rejected_signatures = set(task.rejected_proposal_signatures)
        successful_signatures = self._successful_proposal_signatures(task)
        for action in actions:
            effects = {
                (item.node_key, item.fact_key)
                for item in (
                    *action.planning.terminal_effects,
                    *action.planning.supporting_effects,
                )
            }
            effects.update(
                (node.key, fact_key)
                for node in definition.world.nodes
                if action.required_interaction_key in node.interaction_keys
                and (
                    not action.target_node_type_keys
                    or node.node_type_key in action.target_node_type_keys
                )
                for effect in action.planning.target_terminal_effects
                for fact_key in (effect.fact_key,)
            )
            matched = [item for item in needed if item in effects and item not in covered]
            if not matched:
                continue
            target_key = matched[0][0]
            try:
                parameters = self._default_parameters(action)
            except GenericAgentError as exc:
                if exc.code != "GENERIC_PLAN_PARAMETER_REQUIRED" or not exc.message.startswith(
                    "Action input is missing required parameter "
                ):
                    raise
                # Required parameters without deterministic defaults belong to
                # the Planner. The pre-provider frontier may not materialize
                # or choose them, so leave this Action unresolved for the
                # canonical PlannerInput/provider boundary.
                continue
            actor = self._delegate_actor(definition, action, target_key, parameters)
            if actor is None:
                continue
            arguments = {
                "action_key": action.key,
                "target_key": target_key,
                "parameters": parameters,
                "idempotency_key": self._action_idempotency_key(
                    task.id,
                    plan_version=plan_version,
                    action_key=action.key,
                ),
            }
            signature = proposal_signature(
                actor.actor_key,
                action.key,
                target_key,
                parameters,
                action=action,
            )
            if signature in rejected_signatures:
                continue
            # A historical success is only used together with the current
            # unsatisfied objective projection.  Supporting effects that are
            # no longer needed are directly proven redundant by that current
            # state; a state-dependent action that still covers an objective
            # requirement remains eligible for re-use.
            if signature in successful_signatures and not set(matched).intersection(
                objective_needed
            ):
                continue
            candidates.append(
                {
                    "description": f"Execute {action.name}",
                    "actor_key": actor.actor_key,
                    "execution_type": StepExecutionType.TOOL,
                    "action_intent": action.key,
                    "arguments": arguments,
                    "expected_outcome": {"codes": list(action.planning.success_outcome_codes)},
                    "resume_condition": None,
                }
            )
            if action.execution_mode == ActionExecutionMode.ASYNC:
                candidates.append(
                    {
                        "description": f"Wait for {action.name}",
                        "actor_key": actor.actor_key,
                        "execution_type": StepExecutionType.WAIT_FOR_WORLD_EVENT,
                        "action_intent": action.key,
                        "arguments": {},
                        "expected_outcome": {
                            "codes": list(action.planning.wait_success_outcome_codes)
                        },
                        "resume_condition": {"action_key": action.key},
                    }
                )
            covered.update(matched)
        return candidates

    def _validate_known_action(
        self,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor: GameInstanceActor,
        target_key: str,
    ) -> bool:
        target = definition.world.node(target_key)
        node_state = self.db.get(GameInstanceNodeState, (self.scope.game_instance_id, target_key))
        return bool(
            target
            and node_state
            and actor_binding_matches(definition, actor)
            and node_state.visibility == Visibility.KNOWN
            and node_state.status != NodeStatus.LOCKED
            and action.required_interaction_key in target.interaction_keys
            and action.key in actor.allowed_action_keys
            and {item.value for item in action.allowed_actor_capabilities}.issubset(
                set(actor.capabilities)
            )
            and (
                action.required_actor_role_for_target(target_key) is None
                or actor.role_key == action.required_actor_role_for_target(target_key)
            )
        )

    def _delegate_actor(
        self,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        target_key: str,
        parameters: ActionParameters,
    ) -> GameInstanceActor | None:
        actors = self.db.scalars(
            select(GameInstanceActor).where(
                GameInstanceActor.game_instance_id == self.scope.game_instance_id,
                GameInstanceActor.status == "ACTIVE",
            )
        ).all()
        for actor in sorted(actors, key=lambda item: (item.is_primary, item.actor_key)):
            if not self._validate_known_action(definition, action, actor, target_key):
                continue
            authority = evaluate_authority(actor, action, parameters, target_key=target_key)
            if authority.outcome != AuthorityOutcome.DENY:
                return actor
        return None

    def _provider_steps(
        self,
        task: AgentTask,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        reason: str | None,
        plan_version: int,
        *,
        single_attempt: bool = False,
        starting_repair_attempt: int = 0,
        rejected_segment: dict[str, object] | None = None,
        initial_diagnostics: tuple[PlanViolation, ...] = (),
        initial_memory: tuple[AntiRegressionMemoryItem, ...] = (),
        planning_cycle: PlanningCycle | None = None,
        planner_input_override: PlannerInput | None = None,
        planning_continuity: PlanningContinuity | None = None,
        formal_goal: FormalGoalContract | None = None,
    ) -> list[dict[str, object]]:
        assert self.provider is not None
        if planning_continuity is None and reason is not None:
            planning_continuity = PlanningContinuityBuilder(self.db, self.scope).build(
                task,
                replan_reason=reason,
            )
        call_type = "INITIAL_PLAN" if reason is None else "REPLAN"
        created_cycle = planning_cycle is None
        if planning_cycle is None:
            planning_cycle = self._start_planning_cycle(
                task,
                call_type=call_type,
                planner_input={},
                objectives=objectives,
                replan_reason=reason,
                started_at=datetime.now(UTC),
            )
        self._last_planning_cycle = planning_cycle
        context_builder = PlanningContextBuilder(self.db, self.scope)
        try:
            planning_context = context_builder.build(
                definition,
                objectives,
                task=task,
                replan_reason=reason,
                formal_goal=formal_goal,
            )
            planner_input = context_builder.build_v2(
                definition,
                objectives,
                task=task,
                replan_reason=reason,
                formal_goal=formal_goal,
            )
            if planner_input_override is not None:
                planner_input = planner_input_override
            if planning_continuity is not None and planning_continuity.prior_plans:
                previous_segment = planning_continuity.prior_plans[-1].model_dump(mode="json")
                continuity_execution_context: dict[str, object] = {
                    **planner_input.execution_context,
                    "previous_segment": previous_segment,
                }
                if planning_continuity.latest_replan_trigger is not None:
                    continuity_execution_context["latest_replan_trigger"] = (
                        planning_continuity.latest_replan_trigger
                    )
                if planning_continuity.latest_new_knowledge:
                    continuity_execution_context["latest_new_knowledge"] = list(
                        planning_continuity.latest_new_knowledge
                    )
                planner_input = planner_input.model_copy(
                    update={"execution_context": continuity_execution_context}
                )
                planning_context = planning_context.model_copy(
                    update={
                        "previous_execution_context": {
                            **planning_context.previous_execution_context,
                            "previous_segment": previous_segment,
                        }
                    }
                )
            # The old catalog is retained only as a compatibility projection for
            # existing in-process FakeProviders. It is never serialized by the
            # OpenAI-compatible provider when ``planner_input`` is present.
            catalog_builder = PlanningActionCatalogBuilder(self.db, self.scope)
            catalog = catalog_builder.build(
                definition,
                objectives,
                task=task,
                replan_reason=reason,
                planner_input=planner_input,
            )
        except Exception:
            self._finish_planning_cycle(
                planning_cycle,
                status="ERROR",
                finished_at=datetime.now(UTC),
            )
            self.db.flush()
            raise
        if created_cycle:
            payload = planner_input.model_dump(mode="json")
            planning_cycle.planner_input = payload
            planning_cycle.planner_input_hash = hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        if (
            not planning_context.relevant_actions
            and planner_input.operation_goal is None
            and not self.evaluate(task).completed
        ):
            self._finish_planning_cycle(
                planning_cycle,
                status="REJECTED",
                finished_at=datetime.now(UTC),
            )
            self.db.flush()
            raise GenericAgentError(
                "GENERIC_PLAN_NOT_FOUND",
                "No known public Action can advance the frozen ObjectiveScope",
            )
        diagnostics: tuple[PlanViolation, ...] = initial_diagnostics
        anti_regression_memory: tuple[AntiRegressionMemoryItem, ...] = initial_memory
        for repair_attempt in (
            [starting_repair_attempt]
            if single_attempt
            else range(self.model_max_repair_attempts_per_cycle + 1)
        ):
            request = PlanRequest(
                call_type=(call_type if repair_attempt == 0 else "REPAIR"),
                goal=task.goal_description,
                objective_keys=tuple(item.key for item in objectives),
                objective_scope=objective_context(
                    objectives,
                    known_fact_refs=catalog_builder.known_fact_refs(),
                    known_facts=_known_world_facts(planning_context.current_knowledge),
                    definition=definition,
                ),
                replan_reason=reason,
                known_world=planning_context.current_knowledge,
                actors=planning_context.relevant_actors,
                planning_metadata=definition.planning.model_dump(mode="json"),
                planning_action_catalog=catalog,
                planning_context=planning_context,
                planner_input=planner_input,
                planning_continuity=planning_continuity,
                rejected_segment=rejected_segment,
                repair_attempt=repair_attempt,
                repair_diagnostics=diagnostics,
                anti_regression_memory=anti_regression_memory,
            )
            planning_attempt = PlanningAttempt(
                cycle_id=planning_cycle.id,
                task_id=task.id,
                attempt_index=repair_attempt,
                call_type=request.call_type,
                status="RUNNING",
                provider_payload=request.provider_payload(),
                anti_regression_memory=[
                    item.model_dump(mode="json", exclude_none=True)
                    for item in anti_regression_memory
                ],
                started_at=datetime.now(UTC),
            )
            self.db.add(planning_attempt)
            planning_cycle.current_attempt = repair_attempt
            self.db.flush()
            audit_id = str(uuid4())
            provider_started_at = perf_counter()
            self._provider_call_started_at[audit_id] = provider_started_at
            start_metadata = provider_call_start_metadata(self.provider, request)
            start_metadata.update(
                {
                    "audit_id": audit_id,
                    "repair_attempt": repair_attempt,
                    "started_at": datetime.now(UTC).isoformat(),
                    "outcome": "RUNNING",
                }
            )
            self._notify_provider_call("STARTED", task, request, start_metadata)
            try:
                proposal = self.provider.propose_plan(request)
            except GenericProviderError as exc:
                self._finish_planning_attempt(
                    planning_attempt,
                    status=("TIMEOUT" if exc.code == "MODEL_PROVIDER_TIMEOUT" else "ERROR"),
                    finished_at=datetime.now(UTC),
                    latency_ms=_duration_ms(provider_started_at),
                    finish_reason=None,
                )
                self._finish_planning_cycle(
                    planning_cycle,
                    status="ERROR",
                    finished_at=datetime.now(UTC),
                )
                self.db.flush()
                self._notify_provider_call(
                    "FINISHED",
                    task,
                    request,
                    {
                        **provider_call_metadata(self.provider),
                        "audit_id": audit_id,
                        "finished_at": datetime.now(UTC).isoformat(),
                        "latency_ms": _duration_ms(provider_started_at),
                        "wall_clock_latency_ms": _duration_ms(provider_started_at),
                        "outcome": ("TIMEOUT" if exc.code == "MODEL_PROVIDER_TIMEOUT" else "ERROR"),
                        "error_code": exc.code,
                        "error_category": _provider_error_category(exc),
                    },
                )
                self._provider_call_started_at.pop(audit_id, None)
                raise
            except Exception as exc:
                self._finish_planning_attempt(
                    planning_attempt,
                    status="ERROR",
                    finished_at=datetime.now(UTC),
                    latency_ms=_duration_ms(provider_started_at),
                    finish_reason=None,
                )
                self._finish_planning_cycle(
                    planning_cycle,
                    status="ERROR",
                    finished_at=datetime.now(UTC),
                )
                self.db.flush()
                self._notify_provider_call(
                    "FINISHED",
                    task,
                    request,
                    {
                        **provider_call_metadata(self.provider),
                        "audit_id": audit_id,
                        "finished_at": datetime.now(UTC).isoformat(),
                        "latency_ms": _duration_ms(provider_started_at),
                        "wall_clock_latency_ms": _duration_ms(provider_started_at),
                        "outcome": "ERROR",
                        "error_code": "MODEL_PROVIDER_ERROR",
                        "error_category": type(exc).__name__,
                    },
                )
                self._provider_call_started_at.pop(audit_id, None)
                raise
            try:
                steps, diagnostics = self._validate_and_persist_provider_attempt(
                    task=task,
                    definition=definition,
                    objectives=objectives,
                    reason=reason,
                    plan_version=plan_version,
                    catalog=catalog,
                    planning_context=planning_context,
                    planner_input=planner_input,
                    request=request,
                    proposal=proposal,
                    planning_attempt=planning_attempt,
                    audit_id=audit_id,
                    provider_started_at=provider_started_at,
                )
            except Exception as exc:
                proposal_payload: dict[str, object] | None
                try:
                    proposal_payload = proposal.model_dump(mode="json")
                except Exception:
                    proposal_payload = None
                self._finish_planning_attempt(
                    planning_attempt,
                    status="ERROR",
                    finished_at=datetime.now(UTC),
                    latency_ms=_duration_ms(provider_started_at),
                    proposal=proposal_payload,
                    finish_reason=None,
                )
                self._finish_planning_cycle(
                    planning_cycle,
                    status="ERROR",
                    finished_at=datetime.now(UTC),
                )
                self.db.flush()
                try:
                    self._notify_provider_call(
                        "FINISHED",
                        task,
                        request,
                        {
                            **provider_call_metadata(self.provider),
                            "audit_id": audit_id,
                            "finished_at": datetime.now(UTC).isoformat(),
                            "latency_ms": _duration_ms(provider_started_at),
                            "wall_clock_latency_ms": _duration_ms(provider_started_at),
                            "outcome": "ERROR",
                            "error_code": "MODEL_PROVIDER_ERROR",
                            "error_category": type(exc).__name__,
                        },
                    )
                finally:
                    self._provider_call_started_at.pop(audit_id, None)
                raise
            self._last_provider_attempt = {
                "accepted": not diagnostics,
                "proposal": proposal.model_dump(mode="json"),
                "diagnostics": diagnostics,
                "repair_attempt": repair_attempt,
            }
            if repair_attempt > 0:
                anti_regression_memory = _remember_prior_contradictions(
                    anti_regression_memory,
                    request.repair_diagnostics,
                    seen_attempt=repair_attempt - 1,
                )
            self._provider_call_started_at.pop(audit_id, None)
            if not diagnostics:
                self._finish_planning_cycle(
                    planning_cycle,
                    status="ACCEPTED",
                    finished_at=datetime.now(UTC),
                )
                planning_cycle.current_violations = []
                planning_cycle.rejected_segment = None
                self._last_provider_plan_summary = proposal.plan_summary.strip() or None
                self._last_provider_stop_reason = proposal.stop_reason
                return steps
            rejected_segment = proposal.model_dump(mode="json")
            planning_cycle.rejected_segment = rejected_segment
            planning_cycle.current_violations = [
                violation.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
                for violation in diagnostics
            ]
            planning_cycle.anti_regression_memory = [
                item.model_dump(mode="json", exclude_none=True) for item in anti_regression_memory
            ]
            if single_attempt:
                self.db.flush()
                return []
        self._finish_planning_cycle(
            planning_cycle,
            status="REJECTED",
            finished_at=datetime.now(UTC),
        )
        self.db.flush()
        raise GenericAgentError(
            "MODEL_PLAN_REJECTED",
            "The model provider could not produce a backend-valid current Plan",
        )

    def _validate_and_persist_provider_attempt(
        self,
        *,
        task: AgentTask,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        reason: str | None,
        plan_version: int,
        catalog: tuple[PlanningActionCandidate, ...],
        planning_context: PlanningContext,
        planner_input: PlannerInput,
        request: PlanRequest,
        proposal: PlanProposal,
        planning_attempt: PlanningAttempt,
        audit_id: str,
        provider_started_at: float,
    ) -> tuple[list[dict[str, object]], tuple[PlanViolation, ...]]:
        assert self.provider is not None
        diagnostics = _validate_plan_segment_contract(proposal, planner_input)
        if diagnostics:
            steps: list[dict[str, object]] = []
        else:
            steps, raw_diagnostics = self._validate_provider_proposal_v1(
                task,
                definition,
                objectives,
                reason,
                plan_version,
                catalog,
                proposal.steps,
                planning_context,
                planner_input=planner_input,
                stop_reason=proposal.stop_reason,
            )
            diagnostics = tuple(PlanViolation.model_validate(item) for item in raw_diagnostics)
        self._record_provider_plan_call(
            task,
            request=request,
            proposal_steps=proposal.steps,
            proposal_candidate_ids=tuple(
                item.candidate_id or "" for item in proposal.steps if item.candidate_id
            ),
            diagnostics=diagnostics,
            proposal_stop_reason=proposal.stop_reason,
            accepted=not diagnostics,
            audit_id=audit_id,
        )
        proposal_payload = proposal.model_dump(mode="json")
        self._finish_planning_attempt(
            planning_attempt,
            status="ACCEPTED" if not diagnostics else "REJECTED",
            finished_at=datetime.now(UTC),
            latency_ms=_duration_ms(provider_started_at),
            proposal=proposal_payload,
            rejected_segment=(proposal_payload if diagnostics else None),
            validator_violations=[
                violation.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
                for violation in diagnostics
            ],
            stop_reason=proposal.stop_reason,
            provider_metadata=provider_call_metadata(self.provider),
        )
        return steps, diagnostics

    def _notify_provider_call(
        self,
        event: str,
        task: AgentTask,
        request: PlanRequest,
        details: dict[str, object],
    ) -> None:
        if self.provider_call_observer is not None:
            self.provider_call_observer(event, task, request, details)

    def _validate_provider_proposal_v1(
        self,
        task: AgentTask,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        reason: str | None,
        plan_version: int,
        catalog: tuple[PlanningActionCandidate, ...],
        proposed_steps: tuple[object, ...],
        planning_context: PlanningContext,
        *,
        planner_input: PlannerInput | None = None,
        stop_reason: str = "OBJECTIVE_COMPLETION",
    ) -> tuple[list[dict[str, object]], tuple[dict[str, object], ...]]:
        """Validate direct V1 bindings while accepting legacy candidate IDs.

        Only hard constraints are enforced here.  Current access, resources,
        Rule preflight, and dynamic approval remain execution-time concerns in
        the existing Generic Action service.  A future locked target is thus a
        valid Plan member when its static visibility/interaction contract is
        valid.
        """

        actors = {
            item.actor_key: item
            for item in self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == self.scope.game_instance_id
                )
            )
        }
        projected_actor_locations = {
            actor_key: actor.current_node_key for actor_key, actor in actors.items()
        }
        projected_command_reachability = {
            actor_key: _actor_command_reachability(actor) for actor_key, actor in actors.items()
        }
        projected_known_passability = self._known_passability(definition)
        projected_known_facts = self._known_fact_projection()
        projected_known_nodes = self._known_node_keys()
        projected_known_relations = self._known_relation_keys(definition)
        projected_resource_pools, projected_region_resource_knowledge = (
            self._projected_resource_state(definition)
        )
        projected_known_resource_balance: dict[tuple[str | None, str], int] = {}

        successful_signatures = self._successful_proposal_signatures(task)
        result: list[dict[str, object]] = []
        step_effects: list[set[tuple[str, str]]] = []
        step_resource_effects: list[set[tuple[str, str]]] = []
        if not proposed_steps and stop_reason != "BLOCKED":
            return [], (
                {
                    "code": "NO_STEPS",
                    "failure_code": "NO_STEPS",
                    "dimension": "PLAN_STRUCTURE",
                    "required": "AT_LEAST_ONE_STEP",
                    "actual": 0,
                    "message": "Proposal contains no steps",
                },
            )
        if not proposed_steps:
            return [], ()

        static_bindings, diagnostics = self._static_proposal_bindings(
            task=task,
            definition=definition,
            objectives=objectives,
            catalog=catalog,
            proposed_steps=proposed_steps,
            planning_context=planning_context,
            planner_input=planner_input,
            actors=actors,
            projected_command_reachability=projected_command_reachability,
        )
        if diagnostics:
            return [], tuple(_diagnostic_with_step_id(item, proposed_steps) for item in diagnostics)

        operation_goal_covered = False

        public_known_facts = {
            identity: projected.value
            for identity, projected in projected_known_facts.items()
            if projected.visibility == Visibility.KNOWN
        }
        objective_resource_refs = _objective_resource_refs(
            objectives,
            definition=definition,
            known_facts=public_known_facts,
        )
        for static_binding in static_bindings:
            index = static_binding.index
            raw_step = static_binding.raw_step
            candidate = static_binding.candidate
            action = static_binding.action
            actor = static_binding.actor
            action_key = action.key
            actor_key = actor.actor_key
            target_key = static_binding.target_key
            parameters = static_binding.parameters
            projected_resolution_effects = self._projected_resolution_effects(
                definition,
                action,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
            try:
                signature = proposal_signature(
                    actor_key,
                    action_key,
                    target_key,
                    parameters,
                    action=action,
                )
                if signature in successful_signatures and self._historical_success_is_redundant(
                    definition=definition,
                    action=action,
                    actor=actor,
                    target_key=target_key,
                    planner_input=planner_input,
                    projected_actor_locations=projected_actor_locations,
                ):
                    raise GenericAgentError(
                        "OBJECTIVE_IRRELEVANT",
                        "The previously successful location is already the projected location",
                        details={
                            "dimension": "OBJECTIVE_RELEVANCE",
                            "required": "ADVANCES_FROZEN_OBJECTIVE_SCOPE",
                            "actual": "ALREADY_SUCCEEDED_WITHOUT_NEEDED_EFFECT",
                        },
                    )
                self._validate_projected_command_reachability(
                    action,
                    actor_key,
                    target_key,
                    actors,
                    projected_command_reachability,
                )
                self._validate_projected_action_state(
                    definition,
                    action,
                    actor_key,
                    target_key,
                    parameters,
                    projected_actor_locations,
                    projected_known_passability,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                    actors=actors,
                    projected_command_reachability=projected_command_reachability,
                )
                self._validate_and_advance_projected_resources(
                    definition,
                    action,
                    actor_key,
                    target_key,
                    parameters,
                    projected_actor_locations,
                    projected_resource_pools,
                    projected_region_resource_knowledge,
                    projected_known_resource_balance,
                    projected_resolution_effects,
                )
            except GenericAgentError as exc:
                diagnostics.append(
                    _structured_plan_diagnostic(
                        exc,
                        action=action,
                        step_id=str(getattr(raw_step, "step_id", "")),
                        actor_key=actor_key,
                        target_key=target_key,
                        projected_command_reachability=projected_command_reachability,
                    )
                )
                break
            effect_refs = self._objective_effect_refs(
                planning_context,
                action_key,
                target_key,
                planner_input=planner_input,
            )
            operation_goal_matches = bool(
                planner_input is not None
                and planner_input.operation_goal is not None
                and _operation_goal_step_matches(
                    planner_input.operation_goal,
                    static_binding,
                    definition,
                    projected_actor_locations,
                )
            )
            resource_effects = _action_resource_goal_effects(
                definition,
                action,
                target_key,
                parameters,
                objective_resource_refs,
            )
            target_definition = definition.world.node(target_key)
            target_actor = (
                actors.get(target_key) if action.target_kind == ActionTargetKind.ACTOR else None
            )
            target_name = (
                target_definition.name
                if target_definition is not None
                else target_actor.name
                if target_actor is not None
                else target_key
            )
            binding = PlanningActionCandidate(
                candidate_id=(
                    candidate.candidate_id
                    if candidate is not None
                    else f"binding:{action_key}:{actor_key}:{target_key}"
                ),
                action_key=action_key,
                action_name=action.name,
                actor_key=actor_key,
                actor_name=actor.name,
                target_key=target_key,
                target_name=target_name,
                target_kind=action.target_kind.value,
                parameter_domain=tuple(item.model_dump(mode="json") for item in action.parameters),
                public_effects=tuple(
                    {
                        "kind": (
                            "TERMINAL"
                            if item
                            in action.planning.terminal_effects_for_target(target_key)
                            else "SUPPORTING"
                        ),
                        "node_key": item.node_key,
                        "fact_key": item.fact_key,
                    }
                    for item in (
                        *action.planning.terminal_effects_for_target(target_key),
                        *action.planning.supporting_effects,
                    )
                    if (item.node_key, item.fact_key) in effect_refs
                ),
                currently_executable=True,
            )
            try:
                generated = self._validated_proposed_step(
                    definition,
                    binding,
                    parameters,
                    objectives,
                    plan_version,
                    index,
                    reason,
                    task.id,
                    allow_epistemic=True,
                    matches_operation_goal=operation_goal_matches,
                )
            except GenericAgentError as exc:
                diagnostics.append(
                    _structured_plan_diagnostic(
                        exc,
                        action=action,
                        step_id=str(getattr(raw_step, "step_id", "")),
                        actor_key=actor_key,
                        target_key=target_key,
                        projected_command_reachability=projected_command_reachability,
                    )
                )
                break
            purpose = getattr(raw_step, "purpose", "")
            short_actor_reason = getattr(raw_step, "short_actor_reason", None)
            if isinstance(purpose, str) and purpose.strip() and generated:
                generated[0]["description"] = purpose.strip()[:400]
                generated[0]["planner_purpose"] = purpose.strip()[:400]
            if isinstance(short_actor_reason, str) and short_actor_reason.strip() and generated:
                generated[0]["short_actor_reason"] = short_actor_reason.strip()[:240]
            if generated:
                generated[0]["planner_step_id"] = getattr(raw_step, "step_id", "")
            result.extend(generated)
            step_effects.append(effect_refs)
            step_resource_effects.append(
                _action_resource_goal_effects(
                    definition,
                    action,
                    target_key,
                    parameters,
                    objective_resource_refs,
                )
            )
            if (
                stop_reason == "OBJECTIVE_COMPLETION"
                and planner_input is not None
                and planner_input.operation_goal is not None
                and operation_goal_matches
            ):
                operation_goal_covered = True
            self._advance_projected_action_state(
                definition,
                action,
                actor_key,
                target_key,
                parameters,
                projected_actor_locations,
                projected_known_passability,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
                projected_resolution_effects,
                planner_input=planner_input,
                projected_command_reachability=projected_command_reachability,
            )

        if diagnostics:
            return [], tuple(_diagnostic_with_step_id(item, proposed_steps) for item in diagnostics)

        if (
            stop_reason == "OBJECTIVE_COMPLETION"
            and planner_input is not None
            and planner_input.operation_goal is not None
            and not operation_goal_covered
        ):
            operation_goal = planner_input.operation_goal
            return [], (
                {
                    "code": "ACTION_COMPLETED_NOT_COVERED",
                    "failure_code": "ACTION_COMPLETED_NOT_COVERED",
                    "dimension": "OBJECTIVE_COMPLETION",
                    "required": {
                        "kind": "ACTION_COMPLETED",
                        "action_key": operation_goal.action_key,
                        "actor_key": operation_goal.actor_key,
                        "target_key": operation_goal.target_key,
                        "match_mode": operation_goal.match_mode,
                        "boundary": operation_goal.boundary,
                    },
                    "actual": "NO_MATCHING_CONCRETE_INVOCATION",
                    "message": (
                        "OBJECTIVE_COMPLETION requires one concrete successful-invocation "
                        "step matching the frozen ACTION_COMPLETED Goal."
                    ),
                },
            )

        if stop_reason == "OBJECTIVE_COMPLETION":
            completion_violation = self._projected_objective_completion_violation(
                definition,
                objectives,
                projected_known_facts=projected_known_facts,
                projected_pools=projected_resource_pools,
                projected_region_knowledge=projected_region_resource_knowledge,
                projected_known_resource_balance=projected_known_resource_balance,
            )
            if completion_violation is not None:
                return [], (completion_violation,)

        covered_before: set[tuple[str, str]] = set()
        covered_resource_before: set[tuple[str, str]] = set()
        for index, (effects, resource_effects) in enumerate(
            zip(step_effects, step_resource_effects, strict=True), start=1
        ):
            for objective in objectives:
                completion_refs = {
                    requirement.fact_ref
                    for requirement in objective.completion_requirements
                    if self._known_requirement_public(requirement)
                    and requirement.fact_ref is not None
                    and not self._known_requirement_satisfied(requirement)
                }
                prerequisite_refs = {
                    requirement.fact_ref
                    for prerequisite in objective.prerequisites
                    for requirement in prerequisite.requirements
                    if self._known_requirement_public(requirement)
                    and requirement.fact_ref is not None
                    and not self._known_requirement_satisfied(requirement)
                }
                missing_before = prerequisite_refs - covered_before
                if effects & completion_refs and missing_before:
                    return [], (
                        {
                            "code": "PLAN_ORDER_INVALID",
                            "failure_code": "PLAN_ORDER_INVALID",
                            "dimension": "PLAN_ORDER",
                            "step_id": str(getattr(proposed_steps[index - 1], "step_id", "")),
                            "required": "PUBLIC_PREREQUISITES_BEFORE_TERMINAL_EFFECT",
                            "actual": [
                                {"node_key": node_key, "fact_key": fact_key}
                                for node_key, fact_key in sorted(missing_before)
                            ],
                            "missing_prior_public_requirements": [
                                {"node_key": node_key, "fact_key": fact_key}
                                for node_key, fact_key in sorted(missing_before)
                            ],
                        },
                    )
                completion_resource_refs = {
                    (requirement.region_key, requirement.resource_key)
                    for requirement in objective.completion_requirements
                    if requirement.kind.value == "RESOURCE_AT_LEAST"
                    and self._known_requirement_public(requirement)
                    and not self._known_requirement_satisfied(requirement)
                }
                prerequisite_resource_refs = {
                    (requirement.region_key, requirement.resource_key)
                    for prerequisite in objective.prerequisites
                    for requirement in prerequisite.requirements
                    if requirement.kind.value == "RESOURCE_AT_LEAST"
                    and self._known_requirement_public(requirement)
                    and not self._known_requirement_satisfied(requirement)
                }
                missing_resource_before = prerequisite_resource_refs - covered_resource_before
                if resource_effects & completion_resource_refs and missing_resource_before:
                    return [], (
                        {
                            "code": "PLAN_ORDER_INVALID",
                            "failure_code": "PLAN_ORDER_INVALID",
                            "dimension": "PLAN_ORDER",
                            "step_id": str(getattr(proposed_steps[index - 1], "step_id", "")),
                            "required": "PUBLIC_RESOURCE_PREREQUISITES_BEFORE_TERMINAL_EFFECT",
                            "actual": [
                                {"region_key": region_key, "resource_key": resource_key}
                                for region_key, resource_key in sorted(missing_resource_before)
                            ],
                        },
                    )
            covered_before.update(effects)
            covered_resource_before.update(resource_effects)
        objective_needed = {
            requirement.fact_ref
            for objective in objectives
            for prerequisite in objective.prerequisites
            for requirement in prerequisite.requirements
            if self._known_requirement_public(requirement)
            and requirement.fact_ref is not None
            and not self._known_requirement_satisfied(requirement)
        } | {
            requirement.fact_ref
            for objective in objectives
            for requirement in objective.completion_requirements
            if self._known_requirement_public(requirement)
            and requirement.fact_ref is not None
            and not self._known_requirement_satisfied(requirement)
        }
        missing_refs = objective_needed - set().union(*step_effects)
        objective_resource_needed = {
            (requirement.region_key, requirement.resource_key)
            for objective in objectives
            for requirement in (
                *objective.completion_requirements,
                *(item for group in objective.prerequisites for item in group.requirements),
            )
            if requirement.kind.value == "RESOURCE_AT_LEAST"
            and self._known_requirement_public(requirement)
            and not self._known_requirement_satisfied(requirement)
        }
        missing_resource_refs = objective_resource_needed - set().union(*step_resource_effects)
        if (missing_refs or missing_resource_refs) and stop_reason == "OBJECTIVE_COMPLETION":
            return [], (
                {
                    "code": "OBJECTIVE_COVERAGE_INCOMPLETE",
                    "failure_code": "OBJECTIVE_COVERAGE_INCOMPLETE",
                    "dimension": "OBJECTIVE_COVERAGE",
                    "required": "ALL_KNOWN_PUBLIC_REQUIREMENTS_COVERED",
                    "actual": [
                        {"node_key": node_key, "fact_key": fact_key}
                        for node_key, fact_key in sorted(missing_refs)
                    ],
                    "missing_public_requirements": [
                        {"node_key": node_key, "fact_key": fact_key}
                        for node_key, fact_key in sorted(missing_refs)
                    ],
                    "missing_resource_requirements": [
                        {"region_key": region_key, "resource_key": resource_key}
                        for region_key, resource_key in sorted(missing_resource_refs)
                    ],
                },
            )
        return result, ()

    @staticmethod
    def _projected_objective_completion_violation(
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        *,
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_pools: dict[str, _ProjectedResourcePool],
        projected_region_knowledge: dict[str, _ProjectedRegionResourceKnowledge],
        projected_known_resource_balance: dict[tuple[str | None, str], int],
    ) -> dict[str, object] | None:
        """Prove an OBJECTIVE_COMPLETION stop from the projected public state."""

        if not objectives:
            # Lightweight legacy callers may validate a segment without an
            # objective projection.  The full Agent path always supplies the
            # frozen Formal Goal's planning projection.
            return None

        def gate_status(gate: object) -> bool | None:
            if gate is None:
                return True
            node_key = getattr(gate, "node_key", None)
            fact_key = getattr(gate, "fact_key", None)
            fact = (
                projected_known_facts.get((node_key, fact_key))
                if isinstance(node_key, str) and isinstance(fact_key, str)
                else None
            )
            if fact is None or fact.visibility != Visibility.KNOWN:
                return None
            accepted_values = getattr(gate, "accepted_values", ())
            return True if fact.value in accepted_values else None

        def resource_status(
            region_key: str,
            resource_key: str,
            minimum: int,
        ) -> tuple[bool | None, int]:
            known_amount = projected_known_resource_balance.get(
                (region_key, resource_key),
                0,
            )
            available_pools = [
                pool
                for pool in projected_pools.values()
                if pool.resource_key == resource_key
                and pool.region_key == region_key
                and pool.visibility == ResourcePoolVisibility.VISIBLE
                and pool.availability == ResourcePoolAvailability.AVAILABLE
            ]
            known_amount += sum(
                pool.quantity for pool in available_pools if pool.quantity is not None
            )
            if known_amount >= minimum:
                return True, known_amount
            knowledge = projected_region_knowledge.get(region_key)
            inventory_unknown = (
                knowledge is None
                or knowledge.visibility != ResourceInventoryVisibility.VISIBLE
                or not knowledge.survey_completed
                or any(pool.quantity is None for pool in available_pools)
            )
            return (None if inventory_unknown else False), known_amount

        derived_cache: dict[str, StrictScalar | None] = {}
        derived_in_progress: set[str] = set()

        def derived_value(derived_key: str) -> StrictScalar | None:
            if derived_key in derived_cache:
                return derived_cache[derived_key]
            state = definition.derived_state_definitions.get(derived_key)
            if state is None or derived_key in derived_in_progress:
                return None
            derived_in_progress.add(derived_key)
            statuses: list[bool | None] = []
            for dependency in state.dependencies:
                if gate_status(dependency.knowledge_gate) is not True:
                    statuses.append(None)
                    continue
                if dependency.kind.value == "FACT":
                    assert dependency.node_key is not None and dependency.fact_key is not None
                    fact = projected_known_facts.get((dependency.node_key, dependency.fact_key))
                    statuses.append(
                        None
                        if fact is None or fact.visibility != Visibility.KNOWN
                        else fact.value in dependency.accepted_values
                    )
                elif dependency.kind.value == "RESOURCE_AT_LEAST":
                    assert dependency.region_key is not None
                    assert dependency.resource_key is not None
                    assert dependency.minimum is not None
                    status, _amount = resource_status(
                        dependency.region_key,
                        dependency.resource_key,
                        dependency.minimum,
                    )
                    statuses.append(status)
                else:
                    assert dependency.derived_key is not None
                    child = derived_value(dependency.derived_key)
                    statuses.append(None if child is None else child in dependency.accepted_values)
            value: StrictScalar | None
            if any(status is False for status in statuses):
                value = state.unavailable_value
            elif statuses and all(status is True for status in statuses):
                value = state.available_value
            else:
                value = None
            derived_in_progress.remove(derived_key)
            derived_cache[derived_key] = value
            return value

        def requirement_status(
            requirement: ObjectiveRequirementV2,
        ) -> bool | None:
            if gate_status(requirement.knowledge_gate) is not True:
                return None
            if requirement.kind == ObjectiveRequirementKind.FACT:
                assert requirement.node_key is not None and requirement.fact_key is not None
                fact = projected_known_facts.get((requirement.node_key, requirement.fact_key))
                if fact is None or fact.visibility != Visibility.KNOWN:
                    return None
                return fact.value in requirement.accepted_values
            if requirement.kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST:
                assert requirement.region_key is not None and requirement.resource_key is not None
                assert requirement.minimum is not None
                status, _amount = resource_status(
                    requirement.region_key,
                    requirement.resource_key,
                    requirement.minimum,
                )
                return status
            assert requirement.derived_key is not None
            value = derived_value(requirement.derived_key)
            return None if value is None else value in requirement.accepted_values

        missing: list[dict[str, object]] = []
        for objective in objectives:
            for requirement in objective.completion_requirements:
                status = requirement_status(requirement)
                if status is True:
                    continue
                missing.append(
                    {
                        "objective_key": objective.key,
                        "requirement_key": requirement.key,
                        "kind": requirement.kind.value,
                        "status": "UNKNOWN" if status is None else "UNSATISFIED",
                    }
                )
        if not missing:
            return None
        return {
            "code": "OBJECTIVE_COMPLETION_NOT_PROVEN",
            "failure_code": "OBJECTIVE_COMPLETION_NOT_PROVEN",
            "dimension": "OBJECTIVE_COMPLETION",
            "required": "ALL_FROZEN_FORMAL_GOAL_REQUIREMENTS_SATISFIED",
            "actual": missing,
            "message": (
                "OBJECTIVE_COMPLETION requires the frozen Formal Goal to be satisfied "
                "by the current projected public state."
            ),
        }

    def _start_planning_cycle(
        self,
        task: AgentTask,
        *,
        call_type: str,
        planner_input: PlannerInput | dict[str, object],
        objectives: tuple[ObjectiveDefinitionV2, ...],
        replan_reason: str | None = None,
        started_at: datetime | None = None,
    ) -> PlanningCycle:
        payload = (
            planner_input.model_dump(mode="json")
            if isinstance(planner_input, PlannerInput)
            else planner_input
        )
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        cycle = PlanningCycle(
            task_id=task.id,
            game_instance_id=self.scope.game_instance_id,
            started_at=started_at or datetime.now(UTC),
            base_call_type=call_type,
            replan_reason=replan_reason,
            frozen_objective_scope=[item.key for item in objectives],
            formal_goal_contract_hash=task.formal_goal_contract_hash,
            planner_input=payload,
            planner_input_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            status="RUNNING",
            current_attempt=0,
            current_violations=[],
            anti_regression_memory=[],
        )
        self.db.add(cycle)
        self.db.flush()
        return cycle

    def _latest_planning_cycle(self, task: AgentTask) -> PlanningCycle:
        cycle = self.db.scalar(
            select(PlanningCycle)
            .where(PlanningCycle.task_id == task.id)
            .order_by(PlanningCycle.created_at.desc())
        )
        if cycle is None:
            raise GenericAgentError("PLANNING_CYCLE_MISSING", "No planning cycle is persisted")
        return cycle

    @staticmethod
    def _finish_planning_cycle(
        cycle: PlanningCycle,
        *,
        status: str,
        finished_at: datetime,
    ) -> None:
        cycle.status = status
        cycle.finished_at = finished_at

    @staticmethod
    def _finish_planning_attempt(
        attempt: PlanningAttempt,
        *,
        status: str,
        finished_at: datetime,
        latency_ms: int,
        proposal: dict[str, object] | None = None,
        rejected_segment: dict[str, object] | None = None,
        validator_violations: list[dict[str, object]] | None = None,
        stop_reason: str | None = None,
        provider_metadata: dict[str, object] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        attempt.status = status
        attempt.finished_at = finished_at
        attempt.latency_ms = latency_ms
        attempt.proposal = proposal
        attempt.rejected_segment = rejected_segment
        if validator_violations is not None:
            attempt.validator_violations = validator_violations
        attempt.stop_reason = stop_reason
        metadata = provider_metadata or {}
        usage = metadata.get("usage")
        attempt.usage = usage if isinstance(usage, dict) else None
        raw_finish = metadata.get("finish_reason")
        attempt.finish_reason = raw_finish if isinstance(raw_finish, str) else finish_reason

    def _static_proposal_bindings(
        self,
        *,
        task: AgentTask,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        catalog: tuple[PlanningActionCandidate, ...],
        proposed_steps: tuple[object, ...],
        planning_context: PlanningContext,
        planner_input: PlannerInput | None = None,
        actors: dict[str, GameInstanceActor],
        projected_command_reachability: dict[str, CommandReachability],
    ) -> tuple[list[_StaticProposalBinding], list[dict[str, object]]]:
        """Validate all ordering-independent proposal facts without projecting state."""

        candidates = {item.candidate_id: item for item in catalog}
        actions = {item.key: item for item in definition.actions}
        target_keys = {
            str(item.get("target_key"))
            for item in planning_context.relevant_targets
            if isinstance(item.get("target_key"), str)
        }
        context_action_keys = {
            str(item.get("action_key"))
            for item in planning_context.relevant_actions
            if isinstance(item.get("action_key"), str)
            and (
                bool(item.get("objective_relevance"))
                or bool(item.get("declared_knowledge_effects"))
                or item.get("behavior") != "RULE"
                or item.get("locality") != "NONE"
            )
        }
        canonical_action_keys = (
            {item.action_key for item in planner_input.action_contracts}
            if planner_input is not None
            else None
        )
        canonical_actor_actions = (
            {item.actor_key: set(item.allowed_action_keys) for item in planner_input.actors}
            if planner_input is not None
            else None
        )
        projected_operation_actor_locations = {
            actor_key: actor.current_node_key for actor_key, actor in actors.items()
        }
        known_facts = _known_world_facts(planning_context.current_knowledge)
        objective_refs = _objective_refs(
            objectives,
            definition=definition,
            known_facts=known_facts,
        )
        objective_resource_refs = _objective_resource_refs(
            objectives,
            definition=definition,
            known_facts=known_facts,
        )
        bindings: list[_StaticProposalBinding] = []
        diagnostics: list[dict[str, object]] = []
        for index, raw_step in enumerate(proposed_steps, start=1):
            candidate_id = getattr(raw_step, "candidate_id", None)
            action_key = getattr(raw_step, "action_key", None)
            actor_key = getattr(raw_step, "actor_key", None)
            target_key = getattr(raw_step, "target_key", None)
            candidate = candidates.get(candidate_id) if isinstance(candidate_id, str) else None
            if isinstance(candidate_id, str) and candidate is None and not action_key:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_CANDIDATE",
                        "failure_code": "UNKNOWN_CANDIDATE",
                        "dimension": "CANDIDATE_BINDING",
                        "step": index,
                        "candidate_id": candidate_id,
                        "required": "KNOWN_CANDIDATE_OR_DIRECT_BINDING",
                        "actual": candidate_id,
                    }
                )
                break
            if candidate is not None:
                action_key = action_key or candidate.action_key
                actor_key = actor_key or candidate.actor_key
                target_key = target_key or candidate.target_key
            if not isinstance(action_key, str) or not action_key:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_ACTION",
                        "failure_code": "UNKNOWN_ACTION",
                        "dimension": "ACTION_BINDING",
                        "step": index,
                        "required": "KNOWN_ACTION_KEY",
                        "actual": "MISSING",
                    }
                )
                continue
            if not isinstance(actor_key, str) or not actor_key:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_ACTOR",
                        "failure_code": "UNKNOWN_ACTOR",
                        "dimension": "ACTOR_BINDING",
                        "step": index,
                        "required": "KNOWN_ACTOR_KEY",
                        "actual": "MISSING",
                    }
                )
                continue
            if not isinstance(target_key, str) or not target_key:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_TARGET",
                        "failure_code": "UNKNOWN_TARGET",
                        "dimension": "TARGET_BINDING",
                        "step": index,
                        "required": "KNOWN_VISIBLE_TARGET_KEY",
                        "actual": "MISSING",
                    }
                )
                continue
            action = actions.get(action_key)
            actor = actors.get(actor_key)
            if action is None:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_ACTION",
                        "failure_code": "UNKNOWN_ACTION",
                        "dimension": "ACTION_BINDING",
                        "step": index,
                        "action_key": action_key,
                        "required": "KNOWN_ACTION_KEY",
                        "actual": action_key,
                    }
                )
                continue
            if canonical_action_keys is not None and action_key not in canonical_action_keys:
                diagnostics.append(
                    {
                        "code": "ACTION_OUTSIDE_PLANNER_CONTEXT",
                        "failure_code": "ACTION_OUTSIDE_PLANNER_CONTEXT",
                        "dimension": "ACTION_BINDING",
                        "step": index,
                        "action_key": action_key,
                        "actor_key": actor_key,
                        "target_key": target_key,
                        "required": "ACTION_IN_CANONICAL_ACTION_CONTRACTS",
                        "actual": action_key,
                    }
                )
                continue
            if actor is None:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_ACTOR",
                        "failure_code": "UNKNOWN_ACTOR",
                        "dimension": "ACTOR_BINDING",
                        "step": index,
                        "actor_key": actor_key,
                        "required": "KNOWN_ACTOR_KEY",
                        "actual": actor_key,
                    }
                )
                continue
            if (
                canonical_actor_actions is not None
                and action_key not in canonical_actor_actions.get(actor_key, set())
            ):
                diagnostics.append(
                    {
                        "code": "ACTOR_ACTION_OUTSIDE_PLANNER_CONTEXT",
                        "failure_code": "ACTOR_ACTION_OUTSIDE_PLANNER_CONTEXT",
                        "dimension": "ACTOR_ELIGIBILITY",
                        "step": index,
                        "action_key": action_key,
                        "actor_key": actor_key,
                        "target_key": target_key,
                        "required": "ACTOR_ALLOWED_ACTION_IN_CANONICAL_CONTEXT",
                        "actual": action_key,
                    }
                )
                continue
            if target_key not in target_keys:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_TARGET",
                        "failure_code": "UNKNOWN_TARGET",
                        "dimension": "TARGET_BINDING",
                        "step": index,
                        "target_key": target_key,
                        "required": "KNOWN_VISIBLE_TARGET_KEY",
                        "actual": target_key,
                    }
                )
                continue
            raw_parameters = dict(getattr(raw_step, "parameters", {}) or {})
            try:
                invocation = canonical_action_invocation(
                    action,
                    actor_key=actor_key,
                    target_key=target_key,
                    parameters=raw_parameters,
                )
                parameters = cast(ActionParameters, dict(invocation.parameters))
            except (TypeError, ValueError) as exc:
                diagnostics.append(
                    {
                        "code": "PARAMETER_INVALID",
                        "failure_code": "GENERIC_PLAN_PARAMETER_INVALID",
                        "step": index,
                        "action_key": action_key,
                        "actor_key": actor_key,
                        "target_key": target_key,
                        "dimension": "PARAMETER",
                        "actual_parameters": raw_parameters,
                        "validation_error": str(exc),
                    }
                )
                continue
            failure_code = self._planning_action_failure_code(definition, action, actor, target_key)
            if failure_code is not None:
                diagnostics.append(
                    _structured_plan_diagnostic(
                        GenericAgentError(
                            failure_code,
                            "The Action assignment does not satisfy its static contract",
                            details=_planning_failure_details(
                                definition, action, actor, target_key, failure_code
                            ),
                        ),
                        action=action,
                        step_id=str(getattr(raw_step, "step_id", "")),
                        actor_key=actor_key,
                        target_key=target_key,
                        projected_command_reachability=projected_command_reachability,
                    )
                )
                continue
            signature = proposal_signature(
                actor_key,
                action_key,
                target_key,
                parameters,
                action=action,
            )
            effect_refs = self._objective_effect_refs(
                planning_context,
                action_key,
                target_key,
                planner_input=planner_input,
            )
            resource_effects = _action_resource_goal_effects(
                definition,
                action,
                target_key,
                parameters,
                objective_resource_refs,
            )
            if signature in set(task.rejected_proposal_signatures):
                diagnostics.append(
                    {
                        "code": "REJECTED_PROPOSAL",
                        "failure_code": "REJECTED_PROPOSAL",
                        "dimension": "PROPOSAL_HISTORY",
                        "step": index,
                        "action_key": action_key,
                        "actor_key": actor_key,
                        "target_key": target_key,
                        "required": "NEW_OR_CORRECTED_BINDING",
                        "actual": "PREVIOUSLY_REJECTED_BINDING",
                    }
                )
                continue
            projected_refs = effect_refs
            binding = _StaticProposalBinding(
                index=index,
                raw_step=raw_step,
                candidate=candidate,
                action=action,
                actor=actor,
                target_key=target_key,
                parameters=parameters,
            )
            operation_goal_matches = bool(
                planner_input is not None
                and planner_input.operation_goal is not None
                and _operation_goal_step_matches(
                    planner_input.operation_goal,
                    binding,
                    definition,
                    projected_operation_actor_locations,
                )
            )
            actual_relevance = (
                "NO_DECLARED_RELEVANT_EFFECT"
                if objective_refs.isdisjoint(projected_refs)
                and not resource_effects
                and not action.planning.supporting_effects
                and action_key not in context_action_keys
                and not operation_goal_matches
                else None
            )
            if actual_relevance is not None:
                diagnostics.append(
                    {
                        "code": "OBJECTIVE_IRRELEVANT",
                        "failure_code": "OBJECTIVE_IRRELEVANT",
                        "step": index,
                        "action_key": action_key,
                        "actor_key": actor_key,
                        "target_key": target_key,
                        "dimension": "OBJECTIVE_RELEVANCE",
                        "required": "ADVANCES_FROZEN_OBJECTIVE_SCOPE",
                        "actual": actual_relevance,
                    }
                )
                continue
            bindings.append(binding)
        return bindings, diagnostics

    @staticmethod
    def _context_effect_refs(
        planning_context: PlanningContext, action_key: str
    ) -> set[tuple[str, str]]:
        refs: set[tuple[str, str]] = set()
        for entry in planning_context.relevant_actions:
            if entry.get("action_key") != action_key:
                continue
            for field in ("declared_world_effects", "declared_knowledge_effects"):
                values = entry.get(field)
                if not isinstance(values, list):
                    continue
                for effect in values:
                    if not isinstance(effect, dict):
                        continue
                    node_key = effect.get("node_key")
                    fact_key = effect.get("fact_key")
                    if isinstance(node_key, str) and isinstance(fact_key, str):
                        refs.add((node_key, fact_key))
        return refs

    @staticmethod
    def _historical_success_is_redundant(
        *,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor: GameInstanceActor,
        target_key: str,
        planner_input: PlannerInput | None,
        projected_actor_locations: dict[str, str] | None = None,
    ) -> bool:
        """Use a prior success only when the current state proves redundancy.

        A historical operation is evidence, not proof that a newly proposed
        operation is still unnecessary. Movement is the one generic case where
        the public current location gives a direct proof. Other resource,
        knowledge, and declarative effects remain state-dependent and are
        validated by the current projected state instead of by this signature.
        """

        effect_changes_location = action.behavior in {
            ActionBehavior.TRAVEL,
            ActionBehavior.TRANSPORT_RESOURCE,
        }
        if planner_input is not None:
            contract = next(
                (item for item in planner_input.action_contracts if item.action_key == action.key),
                None,
            )
            binding = next(
                (
                    item
                    for item in planner_input.target_bindings
                    if item.action_key == action.key and item.target_key == target_key
                ),
                None,
            )
            effect_changes_location = effect_changes_location or bool(
                contract is not None
                and any(
                    effect.get("type") == "ACTOR_LOCATION"
                    and effect.get("value") in {"target_key", "target_node"}
                    for effect in contract.deterministic_effects
                )
            )
            effect_changes_location = effect_changes_location or bool(
                binding is not None
                and any(
                    effect.get("type") == "ACTOR_LOCATION"
                    and effect.get("value") in {"target_key", "target_node"}
                    for effect in binding.deterministic_effects
                )
            )
        if not effect_changes_location:
            return False
        actor_node_key = (
            projected_actor_locations.get(actor.actor_key)
            if projected_actor_locations is not None
            else actor.current_node_key
        )
        if actor_node_key is None:
            return False
        try:
            actor_region = region_for_node(definition, actor_node_key)
            target_region = region_for_node(definition, target_key)
        except LocalityEngineError:
            return False
        return actor_region == target_region

    @classmethod
    def _objective_effect_refs(
        cls,
        planning_context: PlanningContext,
        action_key: str,
        target_key: str,
        *,
        planner_input: PlannerInput | None,
    ) -> set[tuple[str, str]]:
        """Return deterministic FACT effects for the selected Action/Target.

        The canonical V2 contract is authoritative when available.  In
        particular, target bindings are selected by the submitted target;
        effects from another target are never treated as coverage for this
        step.  The legacy context fallback exists only for old in-process
        callers that do not provide a V2 input.
        """

        if planner_input is None:
            return cls._context_effect_refs(planning_context, action_key)

        contracts = {item.action_key: item for item in planner_input.action_contracts}
        contract = contracts.get(action_key)
        if contract is None:
            return set()
        binding = next(
            (
                item
                for item in planner_input.target_bindings
                if item.action_key == action_key and item.target_key == target_key
            ),
            None,
        )
        effects = (
            *(contract.deterministic_effects if contract is not None else ()),
            *(binding.deterministic_effects if binding is not None else ()),
        )
        refs: set[tuple[str, str]] = set()
        for effect in effects:
            if effect.get("type") != "FACT_MUTATION":
                continue
            fact_key = effect.get("fact_key")
            if not isinstance(fact_key, str):
                continue
            effect_target = effect.get("target")
            if effect_target in {"target_key", "target_node"}:
                resolved_target = target_key
            elif isinstance(effect_target, str):
                resolved_target = effect_target
            else:
                continue
            refs.add((resolved_target, fact_key))
        return refs

    @staticmethod
    def _validate_projected_command_reachability(
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        actors: dict[str, GameInstanceActor],
        projected_command_reachability: dict[str, CommandReachability],
    ) -> None:
        if projected_command_reachability.get(actor_key) != CommandReachability.ONLINE:
            raise GenericAgentError(
                "ACTOR_COMMAND_DISCONNECTED",
                "A disconnected Actor cannot receive an ordinary Action",
                details={
                    "dimension": "COMMAND_REACHABILITY",
                    "actor_key": actor_key,
                    "required": CommandReachability.ONLINE.value,
                    "actual": (
                        projected_command_reachability[actor_key].value
                        if actor_key in projected_command_reachability
                        else "UNKNOWN"
                    ),
                },
            )
        if action.target_kind == ActionTargetKind.ACTOR:
            if target_key not in actors:
                raise GenericAgentError(
                    "RELAY_TARGET_INVALID",
                    "The proposed Actor target does not exist",
                    details={
                        "dimension": "TARGET",
                        "target_key": target_key,
                        "required": "ACTOR_EXISTS",
                        "actual": "NOT_FOUND",
                    },
                )
            if action.behavior == ActionBehavior.RELAY_MESSAGE and (
                projected_command_reachability.get(target_key) != CommandReachability.DISCONNECTED
            ):
                raise GenericAgentError(
                    "RELAY_TARGET_NOT_DISCONNECTED",
                    "Relay requires a disconnected target Actor",
                    details={
                        "dimension": "TARGET_COMMAND_REACHABILITY",
                        "target_key": target_key,
                        "required": CommandReachability.DISCONNECTED.value,
                        "actual": (
                            projected_command_reachability[target_key].value
                            if target_key in projected_command_reachability
                            else "UNKNOWN"
                        ),
                    },
                )

    @staticmethod
    def _validate_projected_plan_locality(
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        parameters: ActionParameters,
        projected_actor_locations: dict[str, str],
        actors: dict[str, GameInstanceActor] | None = None,
        projected_command_reachability: dict[str, CommandReachability] | None = None,
    ) -> str | None:
        source_node_key = projected_actor_locations.get(actor_key)
        if source_node_key is None:
            raise GenericAgentError(
                "LOCALITY_ACTOR_REGION_REQUIRED",
                "The Actor has no projected current location",
                details={
                    "dimension": "LOCALITY",
                    "required": "ACTOR_REGION",
                    "actual": "UNKNOWN",
                    "actor_key": actor_key,
                },
            )
        try:
            return validate_action_locality(
                definition,
                action,
                actor_current_node_key=source_node_key,
                target_node_key=target_key,
                parameters=parameters,
                target_actor_node_key=(
                    projected_actor_locations.get(target_key)
                    if action.target_kind == ActionTargetKind.ACTOR
                    else None
                ),
            )
        except LocalityEngineError as exc:
            raise GenericAgentError(
                exc.code,
                exc.message,
                details={"dimension": "LOCALITY", **exc.details},
            ) from exc

    def _validate_projected_action_state(
        self,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        parameters: ActionParameters,
        projected_actor_locations: dict[str, str],
        projected_known_passability: dict[str, bool],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
        *,
        actors: dict[str, GameInstanceActor] | None = None,
        projected_command_reachability: dict[str, CommandReachability] | None = None,
    ) -> str | None:
        if (
            action.target_kind == ActionTargetKind.ACTOR
            and actors is not None
            and target_key not in actors
        ):
            raise GenericAgentError("RELAY_TARGET_INVALID", "The Actor target does not exist")
        actor = actors.get(actor_key) if actors is not None else None
        required_actor_role = action.required_actor_role_for_target(target_key)
        if (
            actor is not None
            and required_actor_role is not None
            and actor.role_key != required_actor_role
        ):
            raise GenericAgentError(
                "ACTOR_ROLE_MISSING",
                "The proposed Actor does not have the Action's required Role",
                details={
                    "dimension": "ACTOR_ROLE",
                    "actor_key": actor_key,
                    "required": required_actor_role,
                    "actual": actor.role_key,
                },
            )
        # Existing rule preconditions remain execution-time guards unless an
        # action opts into proposal-time known-state validation.  The new
        # capability actions always opt in; a declared actor-role contract also
        # opts in so declarative specialist actions can reject known-invalid
        # plans without changing legacy Starfire/Medical sequencing behavior.
        validates_known_preflight = (
            action.behavior
            in {
                ActionBehavior.SUPPLY_POWER,
                ActionBehavior.DEPLOY_HEAVY_ENGINEERING_SUPPORT,
            }
            or required_actor_role is not None
        )
        known_failure = self._known_preflight_failure(
            definition,
            action,
            target_key,
            parameters,
            projected_known_facts,
            projected_known_nodes,
            projected_known_relations,
        )
        if known_failure is not None and (
            validates_known_preflight or known_failure.fact_precondition
        ):
            is_unknown = known_failure.condition_status == "UNKNOWN"
            raise GenericAgentError(
                known_failure.failure_code,
                "A known Action requirement is not satisfied",
                details={
                    "dimension": "ACTION_PRECONDITION",
                    "known_predicate": known_failure.known_predicate,
                    "required": (
                        "KNOWN_SATISFIED_PRECONDITION"
                        if is_unknown
                        else "PREFLIGHT_CONDITION_NOT_MATCHED"
                    ),
                    "actual": ("UNKNOWN" if is_unknown else "KNOWN_FAILURE_CONDITION_MATCHED"),
                },
            )
        if action.behavior == ActionBehavior.SUPPLY_POWER:
            self._validate_projected_supply_power(
                definition,
                action,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
        if action.locality.value == "NONE" and action.behavior not in {
            ActionBehavior.TRAVEL,
            ActionBehavior.TRANSPORT_RESOURCE,
        }:
            return None
        connector = self._validate_projected_plan_locality(
            definition,
            action,
            actor_key,
            target_key,
            parameters,
            projected_actor_locations,
            actors=actors,
            projected_command_reachability=projected_command_reachability,
        )
        if (
            action.behavior in {ActionBehavior.TRAVEL, ActionBehavior.TRANSPORT_RESOURCE}
            and connector is not None
            and projected_known_passability.get(connector) is False
        ):
            source_region = region_for_node(definition, projected_actor_locations[actor_key])
            target_node_key = (
                projected_actor_locations[target_key]
                if action.target_kind == ActionTargetKind.ACTOR
                else target_key
            )
            raise GenericAgentError(
                "KNOWN_TRANSPORT_BLOCKED",
                "The proposed route is known to be blocked",
                details={
                    "dimension": "TRANSPORT_PASSABILITY",
                    "transport_key": connector,
                    "source_region": source_region,
                    "target_region": region_for_node(definition, target_node_key),
                    "required": "PASSABLE",
                    "actual": "BLOCKED",
                },
            )
        return connector

    @staticmethod
    def _validate_projected_supply_power(
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        target_key: str,
        parameters: ActionParameters,
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> None:
        source_key = parameters.get("source_key")
        if not isinstance(source_key, str) or source_key not in projected_known_nodes:
            raise GenericAgentError(
                "SUPPLY_POWER_RELATION_UNKNOWN",
                "The proposed power source is not currently known",
                details={
                    "dimension": "POWER_SOURCE_REQUIREMENT",
                    "required": "KNOWN_SOURCE_NODE",
                    "actual": "UNKNOWN",
                    "known_predicate": {
                        "parameter_key": "source_key",
                        "operator": "REFERENCES_KNOWN_NODE",
                        "expected": True,
                        "actual": False,
                    },
                },
            )
        if target_key not in projected_known_nodes:
            raise GenericAgentError(
                "SUPPLY_POWER_RELATION_UNKNOWN",
                "The proposed power target is not currently known",
                details={
                    "dimension": "POWER_SOURCE_REQUIREMENT",
                    "required": "KNOWN_TARGET_NODE",
                    "actual": "UNKNOWN",
                    "known_predicate": {
                        "node_key": target_key,
                        "operator": "VISIBLE",
                        "expected": True,
                        "actual": False,
                    },
                },
            )
        if not any(
            relation_identity(relation) in projected_known_relations
            and relation.source_node_key == source_key
            and relation.relation_type_key == action.source_relation_type_key
            and relation.target_node_key == target_key
            and relation.source_node_key in projected_known_nodes
            and relation.target_node_key in projected_known_nodes
            for relation in definition.world.relations
        ):
            raise GenericAgentError(
                "SUPPLY_POWER_RELATION_UNKNOWN",
                "No known direct power relation connects the source and target",
                details={
                    "dimension": "POWER_SOURCE_REQUIREMENT",
                    "required": "KNOWN_DIRECT_RELATION",
                    "actual": "UNKNOWN",
                    "known_predicate": {
                        "node_key": source_key,
                        "relation_type": action.source_relation_type_key,
                        "target_key": target_key,
                        "operator": "EXISTS",
                        "expected": True,
                        "actual": False,
                    },
                },
            )
        for fact_key, expected, code in (
            ("operational", True, "SUPPLY_POWER_SOURCE_NOT_OPERATIONAL"),
            ("power_supply", "AVAILABLE", "SUPPLY_POWER_SOURCE_UNAVAILABLE"),
        ):
            fact = projected_known_facts.get((source_key, fact_key))
            if fact is not None and fact.visibility == Visibility.KNOWN and fact.value != expected:
                raise GenericAgentError(
                    code,
                    "The power source does not satisfy its known power requirement",
                    details={
                        "dimension": "POWER_SOURCE_REQUIREMENT",
                        "required": expected,
                        "actual": fact.value,
                        "known_predicate": {
                            "node_key": source_key,
                            "fact_key": fact_key,
                            "operator": "EQ",
                            "expected": expected,
                            "actual": fact.value,
                        },
                    },
                )

    @classmethod
    def _known_preflight_failure(
        cls,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        target_key: str,
        parameters: ActionParameters,
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> _KnownPreflightFailure | None:
        matches: list[tuple[int, _KnownPreflightFailure]] = []
        for rule in definition.rules:
            if rule.phase != RulePhase.PREFLIGHT or rule.action_key != action.key:
                continue
            status = cls._known_condition_status(
                definition,
                rule.condition,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
            failure = next(
                (effect.failure_code for effect in rule.effects if effect.failure_code),
                None,
            )
            if failure is None:
                continue
            if status is True:
                witness = cls._known_condition_witness(
                    definition,
                    rule.condition,
                    target_key,
                    parameters,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                    expected_status=True,
                )
                matches.append(
                    (
                        rule.priority,
                        _KnownPreflightFailure(
                            failure,
                            witness or {"condition": "KNOWN_TRUE"},
                            fact_precondition=cls._condition_contains_fact(rule.condition),
                        ),
                    )
                )
            elif status is None:
                witness = cls._unknown_fact_condition_witness(
                    definition,
                    rule.condition,
                    target_key,
                    parameters,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                )
                if witness is not None:
                    matches.append(
                        (
                            rule.priority,
                            _KnownPreflightFailure(
                                "ACTION_PRECONDITION_UNKNOWN",
                                witness,
                                condition_status="UNKNOWN",
                                fact_precondition=True,
                            ),
                        )
                    )
        if not matches:
            return None
        return max(matches, key=lambda item: item[0])[1]

    @classmethod
    def _unknown_fact_condition_witness(
        cls,
        definition: ScenarioDefinitionV2,
        condition: ConditionV2 | None,
        target_key: str,
        parameters: ActionParameters,
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> dict[str, object] | None:
        """Return one public FACT predicate whose value is still UNKNOWN."""

        if condition is None:
            return None
        kind = condition.kind.value
        if kind in {"ALL", "ANY"}:
            for child in condition.conditions:
                if (
                    cls._known_condition_status(
                        definition,
                        child,
                        target_key,
                        parameters,
                        projected_known_facts,
                        projected_known_nodes,
                        projected_known_relations,
                    )
                    is not None
                ):
                    continue
                witness = cls._unknown_fact_condition_witness(
                    definition,
                    child,
                    target_key,
                    parameters,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                )
                if witness is not None:
                    return {"parent_operator": kind, **witness}
            return None
        if kind == "NOT":
            witness = cls._unknown_fact_condition_witness(
                definition,
                condition.condition,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
            return {"negated": True, **witness} if witness is not None else None

        if kind not in {"FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN", "FACT_COMPARE"}:
            return None
        node_key = cls._projected_selector_key(
            definition,
            condition.node,
            target_key,
            parameters,
            projected_known_facts,
            projected_known_nodes,
            projected_known_relations,
        )
        if node_key is None or not isinstance(condition.fact_key, str):
            return None
        fact = projected_known_facts.get((node_key, condition.fact_key))
        if fact is not None and fact.visibility == Visibility.KNOWN:
            return None
        operator: object = {
            "FACT_EQUALS": "EQ",
            "FACT_NOT_EQUALS": "NE",
            "FACT_IN": "IN",
        }.get(kind, condition.operator.value if condition.operator is not None else None)
        expected: object = condition.values if kind == "FACT_IN" else condition.value
        return {
            "kind": kind,
            "node_key": node_key,
            "fact_key": condition.fact_key,
            "operator": operator,
            "expected": expected,
            "actual": "UNKNOWN",
        }

    @staticmethod
    def _condition_contains_fact(condition: ConditionV2 | None) -> bool:
        if condition is None:
            return False
        if condition.kind in {ConditionKind.ALL, ConditionKind.ANY}:
            return any(
                GenericAgentService._condition_contains_fact(child)
                for child in condition.conditions
            )
        if condition.kind == ConditionKind.NOT:
            return GenericAgentService._condition_contains_fact(condition.condition)
        return condition.kind.value in {
            "FACT_EQUALS",
            "FACT_NOT_EQUALS",
            "FACT_IN",
            "FACT_COMPARE",
        }

    @classmethod
    def _known_condition_witness(
        cls,
        definition: ScenarioDefinitionV2,
        condition: ConditionV2 | None,
        target_key: str,
        parameters: ActionParameters,
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
        *,
        expected_status: bool,
    ) -> dict[str, object] | None:
        """Return one public Known predicate explaining a condition result."""

        if condition is None:
            return {"condition": "ALWAYS", "actual": True}
        kind = condition.kind.value
        if kind == "ALL" and expected_status:
            predicates: list[dict[str, object]] = []
            for child in condition.conditions:
                witness = cls._known_condition_witness(
                    definition,
                    child,
                    target_key,
                    parameters,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                    expected_status=True,
                )
                if witness is not None:
                    predicates.append(witness)
            return {"operator": "ALL", "predicates": predicates} if predicates else None
        if kind in {"ALL", "ANY"}:
            for child in condition.conditions:
                status = cls._known_condition_status(
                    definition,
                    child,
                    target_key,
                    parameters,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                )
                if status is expected_status:
                    witness = cls._known_condition_witness(
                        definition,
                        child,
                        target_key,
                        parameters,
                        projected_known_facts,
                        projected_known_nodes,
                        projected_known_relations,
                        expected_status=expected_status,
                    )
                    if witness is not None:
                        return {"parent_operator": kind, **witness}
            return None
        if kind == "NOT":
            witness = cls._known_condition_witness(
                definition,
                condition.condition,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
                expected_status=not expected_status,
            )
            return {"negated": True, **witness} if witness is not None else None

        node_key = cls._projected_selector_key(
            definition,
            condition.node,
            target_key,
            parameters,
            projected_known_facts,
            projected_known_nodes,
            projected_known_relations,
        )
        if kind in {"FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN", "FACT_COMPARE"}:
            if node_key is None or not isinstance(condition.fact_key, str):
                return None
            fact = projected_known_facts.get((node_key, condition.fact_key))
            if fact is None or fact.visibility != Visibility.KNOWN:
                return None
            operator: object = {
                "FACT_EQUALS": "EQ",
                "FACT_NOT_EQUALS": "NE",
                "FACT_IN": "IN",
            }.get(kind, condition.operator.value if condition.operator is not None else None)
            expected: object = condition.values if kind == "FACT_IN" else condition.value
            return {
                "kind": kind,
                "node_key": node_key,
                "fact_key": condition.fact_key,
                "operator": operator,
                "expected": expected,
                "actual": fact.value,
            }
        if kind == "PARAMETER_COMPARE" and isinstance(condition.parameter_key, str):
            return {
                "kind": kind,
                "parameter_key": condition.parameter_key,
                "operator": condition.operator.value if condition.operator is not None else None,
                "expected": condition.value,
                "actual": parameters.get(condition.parameter_key),
            }
        if kind == "RELATION_EXISTS" and condition.relation_type_key is not None:
            return {
                "kind": kind,
                "node_key": node_key,
                "relation_type": condition.relation_type_key,
                "operator": "EXISTS",
                "expected": expected_status,
                "actual": expected_status,
            }
        if kind == "NODE_VISIBLE":
            return {
                "kind": kind,
                "node_key": node_key,
                "operator": "VISIBILITY_EQUALS",
                "expected": (
                    condition.visibility.value if condition.visibility is not None else None
                ),
                "actual": (
                    Visibility.KNOWN.value
                    if node_key in projected_known_nodes
                    else Visibility.HIDDEN.value
                ),
            }
        return None

    @classmethod
    def _known_condition_status(
        cls,
        definition: ScenarioDefinitionV2,
        condition: ConditionV2 | None,
        target_key: str,
        parameters: ActionParameters,
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> bool | None:
        if condition is None:
            return True
        kind = condition.kind
        if kind.value == "ALL":
            statuses = [
                cls._known_condition_status(
                    definition,
                    child,
                    target_key,
                    parameters,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                )
                for child in condition.conditions
            ]
            if any(status is False for status in statuses):
                return False
            return True if all(status is True for status in statuses) else None
        if kind.value == "ANY":
            statuses = [
                cls._known_condition_status(
                    definition,
                    child,
                    target_key,
                    parameters,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                )
                for child in condition.conditions
            ]
            if any(status is True for status in statuses):
                return True
            return False if all(status is False for status in statuses) else None
        if kind.value == "NOT":
            status = cls._known_condition_status(
                definition,
                condition.condition,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
            return None if status is None else not status
        node_key = cls._projected_selector_key(
            definition,
            condition.node,
            target_key,
            parameters,
            projected_known_facts,
            projected_known_nodes,
            projected_known_relations,
        )
        if kind.value in {"FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN", "FACT_COMPARE"}:
            if node_key is None or not isinstance(condition.fact_key, str):
                return None
            fact = projected_known_facts.get((node_key, condition.fact_key))
            if fact is None or fact.visibility != Visibility.KNOWN:
                return None
            if kind.value == "FACT_EQUALS":
                return fact.value == condition.value
            if kind.value == "FACT_NOT_EQUALS":
                return fact.value != condition.value
            if kind.value == "FACT_IN":
                return fact.value in condition.values
            return cls._compare_projected(
                fact.value,
                condition.operator,
                condition.value,
            )
        if kind.value == "PARAMETER_COMPARE":
            if not isinstance(condition.parameter_key, str):
                return None
            return cls._compare_projected(
                parameters.get(condition.parameter_key),
                condition.operator,
                condition.value,
            )
        if kind.value == "RELATION_EXISTS":
            if (
                node_key is None
                or condition.relation_type_key is None
                or condition.relation_direction is None
            ):
                return None
            relation_direction = condition.relation_direction
            return any(
                (
                    relation.relation_type_key == condition.relation_type_key
                    and (
                        (
                            relation_direction.value == "SOURCE"
                            and relation.source_node_key == node_key
                            and relation.target_node_key in projected_known_nodes
                        )
                        or (
                            relation_direction.value == "TARGET"
                            and relation.target_node_key == node_key
                            and relation.source_node_key in projected_known_nodes
                        )
                    )
                )
                for relation in definition.world.relations
                if relation_identity(relation) in projected_known_relations
            )
        if kind.value == "NODE_VISIBLE":
            if node_key is None or condition.visibility is None:
                return None
            return (node_key in projected_known_nodes) == (condition.visibility == Visibility.KNOWN)
        return None

    @staticmethod
    def _compare_projected(
        actual: object,
        operator: ComparisonOperator | None,
        expected: object,
    ) -> bool | None:
        if operator is None or actual is None:
            return None
        if not isinstance(actual, (bool, int, str)) or not isinstance(expected, (bool, int, str)):
            return None
        actual_value = cast(Any, actual)
        expected_value = cast(Any, expected)
        try:
            if operator == ComparisonOperator.EQ:
                return bool(actual_value == expected_value)
            if operator == ComparisonOperator.NE:
                return bool(actual_value != expected_value)
            if operator == ComparisonOperator.LT:
                return bool(actual_value < expected_value)
            if operator == ComparisonOperator.LTE:
                return bool(actual_value <= expected_value)
            if operator == ComparisonOperator.GT:
                return bool(actual_value > expected_value)
            return bool(actual_value >= expected_value)
        except TypeError:
            return None

    @staticmethod
    def _projected_selector_key(
        definition: ScenarioDefinitionV2,
        selector: NodeSelectorV2 | None,
        target_key: str,
        parameters: ActionParameters,
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> str | None:
        if selector is None:
            return None
        kind = selector.kind
        if kind == NodeSelectorKind.CURRENT_TARGET:
            return target_key
        if kind == NodeSelectorKind.ACTION_SOURCE:
            source = parameters.get("source_key")
            return source if isinstance(source, str) else None
        if kind == NodeSelectorKind.EXPLICIT:
            node_key = selector.node_key
            return node_key if isinstance(node_key, str) else None
        if kind != NodeSelectorKind.RELATED:
            return None
        if selector.relation_type_key is None:
            return None
        anchor = selector.anchor_node_key or target_key
        direction = selector.direction.value if selector.direction is not None else None
        candidates = []
        for relation in definition.world.relations:
            if relation_identity(relation) not in projected_known_relations:
                continue
            if relation.relation_type_key != selector.relation_type_key:
                continue
            if direction == "SOURCE" and relation.source_node_key == anchor:
                candidate = relation.target_node_key
            elif direction == "TARGET" and relation.target_node_key == anchor:
                candidate = relation.source_node_key
            else:
                continue
            if candidate not in projected_known_nodes:
                continue
            if selector.required_fact_key is not None:
                fact = projected_known_facts.get((candidate, selector.required_fact_key))
                if fact is None:
                    continue
            candidates.append(candidate)
        unique = sorted(set(candidates))
        return unique[0] if len(unique) == 1 else None

    def _projected_resource_state(
        self,
        definition: ScenarioDefinitionV2,
    ) -> tuple[
        dict[str, _ProjectedResourcePool],
        dict[str, _ProjectedRegionResourceKnowledge],
    ]:
        projection = SharedKnowledgeProjection(self.db, self.scope, definition)
        known_pools = projection.visible_resource_pools()
        known_identities = {
            resource_state_key(item.resource_key, item.region_key, item.pool_key)
            for item in known_pools
        }
        pools = {
            resource_state_key(
                item.resource_key, item.region_key, item.pool_key
            ): _ProjectedResourcePool(
                pool_key=item.pool_key,
                resource_key=item.resource_key,
                region_key=item.region_key,
                facility_key=item.facility_key,
                quantity=item.available_quantity,
                visibility=ResourcePoolVisibility.VISIBLE,
                availability=item.availability,
                survey_discoverable=False,
            )
            for item in known_pools
        }
        for row in self.db.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == self.scope.game_instance_id
            )
        ):
            identity = resource_state_key(row.resource_key, row.scope_node_key, row.pool_key)
            if identity in pools:
                continue
            visibility = ResourcePoolVisibility(row.visibility)
            pools[identity] = _ProjectedResourcePool(
                pool_key=row.pool_key,
                resource_key=row.resource_key,
                region_key=row.scope_node_key,
                facility_key=row.facility_key,
                quantity=(row.value if identity in known_identities else None),
                visibility=visibility,
                availability=ResourcePoolAvailability(row.availability),
                survey_discoverable=row.survey_discoverable,
            )
        # A fully surveyed, visible Region with no visible Pool row is a
        # deterministic known-zero inventory.  Represent that fact in the
        # in-memory projected state without creating a persisted Resource row.
        resource_keys = {item.key for item in definition.world.resources}
        public_pool_pairs = {
            (item.resource_key, item.region_key)
            for item in known_pools
            if item.region_key is not None
        }
        for region_key, knowledge in projection.region_states().items():
            for resource_key in resource_keys:
                if (resource_key, region_key) in public_pool_pairs:
                    continue
                if (
                    resource_knowledge_status(
                        inventory_visibility=knowledge.resource_inventory_visibility,
                        survey_completed=knowledge.resource_survey_completed,
                        has_visible_pool=False,
                    )
                    != "KNOWN_ZERO"
                ):
                    continue
                identity = resource_state_key(resource_key, region_key, "__known_zero__")
                pools[identity] = _ProjectedResourcePool(
                    pool_key="__known_zero__",
                    resource_key=resource_key,
                    region_key=region_key,
                    facility_key=None,
                    quantity=0,
                    visibility=ResourcePoolVisibility.VISIBLE,
                    availability=ResourcePoolAvailability.AVAILABLE,
                    survey_discoverable=False,
                )
        region_knowledge = {
            key: _ProjectedRegionResourceKnowledge(
                visibility=value.resource_inventory_visibility,
                survey_completed=value.resource_survey_completed,
            )
            for key, value in projection.region_states().items()
        }
        return pools, region_knowledge

    def _validate_and_advance_projected_resources(
        self,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        parameters: ActionParameters,
        projected_actor_locations: dict[str, str],
        projected_pools: dict[str, _ProjectedResourcePool],
        projected_region_knowledge: dict[str, _ProjectedRegionResourceKnowledge],
        projected_known_resource_balance: dict[tuple[str | None, str], int],
        projected_resolution_effects: Sequence[EffectV2],
    ) -> None:
        if action.behavior == ActionBehavior.SURVEY_RESOURCES:
            knowledge = projected_region_knowledge.get(target_key)
            if knowledge is None:
                raise GenericAgentError(
                    "RESOURCE_REGION_KNOWLEDGE_MISSING",
                    "The target Region resource knowledge is missing",
                )
            if knowledge.survey_completed:
                raise GenericAgentError(
                    "RESOURCE_SURVEY_ALREADY_COMPLETED",
                    "The target Region has already completed a resource survey",
                    details={
                        "dimension": "RESOURCE_SURVEY_STATE",
                        "target_key": target_key,
                        "required": "NOT_COMPLETED",
                        "actual": "COMPLETED",
                    },
                )
            knowledge.visibility = ResourceInventoryVisibility.VISIBLE
            knowledge.survey_completed = True
            for pool in projected_pools.values():
                if (
                    pool.region_key == target_key
                    and pool.facility_key is not None
                    and pool.visibility == ResourcePoolVisibility.HIDDEN
                    and pool.survey_discoverable
                ):
                    pool.visibility = ResourcePoolVisibility.VISIBLE
            self._apply_projected_resource_effects(
                definition,
                actor_key,
                target_key,
                parameters,
                projected_actor_locations,
                projected_pools,
                projected_region_knowledge,
                projected_known_resource_balance,
                projected_resolution_effects,
            )
            return

        if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
            try:
                cargo = transport_resource_entries(parameters)
            except ValueError as exc:
                raise GenericAgentError("TRANSPORT_PARAMETERS_INVALID", str(exc)) from exc
            source_node_key = projected_actor_locations.get(actor_key)
            if source_node_key is None:
                raise GenericAgentError(
                    "LOCALITY_ACTOR_REGION_REQUIRED",
                    "The Actor has no projected current location",
                )
            source_region = region_for_node(definition, source_node_key)
            destination_region = region_for_node(definition, target_key)
            known_resource_keys = {item.key for item in definition.world.resources}
            for resource_key, amount in cargo:
                if resource_key not in known_resource_keys:
                    raise GenericAgentError(
                        "TRANSPORT_RESOURCE_INVALID",
                        "Transport references an unknown Resource",
                    )
                self._consume_projected_resource(
                    source_region,
                    resource_key,
                    amount,
                    projected_pools,
                    projected_region_knowledge,
                    projected_known_resource_balance=projected_known_resource_balance,
                )
                self._add_projected_resource(
                    destination_region,
                    resource_key,
                    amount,
                    projected_known_resource_balance,
                )

        self._apply_projected_resource_effects(
            definition,
            actor_key,
            target_key,
            parameters,
            projected_actor_locations,
            projected_pools,
            projected_region_knowledge,
            projected_known_resource_balance,
            projected_resolution_effects,
        )

    def _consume_projected_resource(
        self,
        region_key: str | None,
        resource_key: str,
        amount: int,
        projected_pools: dict[str, _ProjectedResourcePool],
        projected_region_knowledge: dict[str, _ProjectedRegionResourceKnowledge],
        *,
        require_known: bool = True,
        projected_known_resource_balance: dict[tuple[str | None, str], int] | None = None,
    ) -> bool:
        if amount < 0:
            raise GenericAgentError(
                "RESOURCE_AMOUNT_INVALID",
                "A Resource operation amount cannot be negative",
            )
        if projected_known_resource_balance is None:
            projected_known_resource_balance = {}

        candidates = [
            pool
            for pool in projected_pools.values()
            if (
                pool.resource_key == resource_key
                and pool.region_key == region_key
                and pool.visibility == ResourcePoolVisibility.VISIBLE
                and (
                    region_key is None
                    or is_runtime_known_inflow_pool(pool.pool_key)
                    or (
                        region_key in projected_region_knowledge
                        and projected_region_knowledge[region_key].visibility
                        == ResourceInventoryVisibility.VISIBLE
                        and projected_region_knowledge[region_key].survey_completed
                    )
                )
            )
        ]
        available = [
            pool for pool in candidates if pool.availability == ResourcePoolAvailability.AVAILABLE
        ]
        projected_balance_key = (region_key, resource_key)
        projected_available = projected_known_resource_balance.get(projected_balance_key, 0)
        known_available = projected_available + sum(
            pool.quantity for pool in available if pool.quantity is not None
        )
        has_unknown_available = any(pool.quantity is None for pool in available)

        if known_available < amount:
            if region_key is None and not candidates:
                raise GenericAgentError(
                    "RESOURCE_SOURCE_UNKNOWN",
                    "No public Resource source is known",
                    details={
                        "dimension": "RESOURCE_SOURCE",
                        "resource_key": resource_key,
                        "scope_region": region_key,
                        "required_amount": amount,
                        "required": "KNOWN_PUBLIC_SOURCE",
                        "actual": "UNKNOWN",
                    },
                )
            knowledge = (
                projected_region_knowledge.get(region_key) if region_key is not None else None
            )
            inventory_complete = region_key is None or (
                knowledge is not None
                and knowledge.visibility == ResourceInventoryVisibility.VISIBLE
                and knowledge.survey_completed
            )
            if has_unknown_available or not inventory_complete:
                raise GenericAgentError(
                    "RESOURCE_INVENTORY_UNKNOWN",
                    "The source Region Resource inventory is not known",
                    details={
                        "dimension": "RESOURCE_KNOWLEDGE",
                        "resource_key": resource_key,
                        "scope_region": region_key,
                        "required_amount": amount,
                        "required": "KNOWN_VISIBLE_AVAILABLE",
                        "actual": "UNKNOWN",
                    },
                )
            raise GenericAgentError(
                "KNOWN_RESOURCE_INSUFFICIENT",
                "Known available Resource quantity is insufficient",
                details={
                    "dimension": "RESOURCE_QUANTITY",
                    "resource_key": resource_key,
                    "scope_region": region_key,
                    "required_amount": amount,
                    "projected_known_available_amount": known_available,
                    "deficit": amount - known_available,
                },
            )

        remaining = amount
        for pool in sorted(available, key=lambda item: item.pool_key):
            if remaining <= 0:
                break
            if pool.quantity is None:
                continue
            consumed = min(pool.quantity, remaining)
            pool.quantity -= consumed
            remaining -= consumed

        if remaining > 0:
            consumed_from_projected = min(projected_available, remaining)
            projected_known_resource_balance[projected_balance_key] = (
                projected_available - consumed_from_projected
            )
            remaining -= consumed_from_projected

        return remaining == 0

    @staticmethod
    def _add_projected_resource(
        region_key: str | None,
        resource_key: str,
        amount: int,
        projected_known_resource_balance: dict[tuple[str | None, str], int],
    ) -> None:
        identity = (region_key, resource_key)
        projected_known_resource_balance[identity] = (
            projected_known_resource_balance.get(identity, 0) + amount
        )

    def _apply_projected_resource_effects(
        self,
        definition: ScenarioDefinitionV2,
        actor_key: str,
        target_key: str,
        parameters: ActionParameters,
        projected_actor_locations: dict[str, str],
        projected_pools: dict[str, _ProjectedResourcePool],
        projected_region_knowledge: dict[str, _ProjectedRegionResourceKnowledge],
        projected_known_resource_balance: dict[tuple[str | None, str], int],
        projected_resolution_effects: Sequence[EffectV2],
    ) -> None:
        actor_node_key = projected_actor_locations.get(actor_key)
        for effect in projected_resolution_effects:
            if effect.kind == EffectKind.SET_REGION_RESOURCE_VISIBILITY:
                if effect.region_key is not None and effect.visibility is not None:
                    region = projected_region_knowledge.get(effect.region_key)
                    if region is not None:
                        region.visibility = ResourceInventoryVisibility(effect.visibility.value)
            elif effect.kind == EffectKind.SET_RESOURCE_POOL_VISIBILITY:
                if effect.pool_key is not None and effect.visibility is not None:
                    for pool in projected_pools.values():
                        if pool.pool_key == effect.pool_key:
                            pool.visibility = ResourcePoolVisibility(effect.visibility.value)
            elif effect.kind == EffectKind.SET_RESOURCE_POOL_AVAILABILITY:
                if effect.pool_key is not None and effect.availability is not None:
                    for pool in projected_pools.values():
                        if pool.pool_key == effect.pool_key:
                            pool.availability = effect.availability
            elif effect.kind == EffectKind.ADJUST_RESOURCE:
                if effect.resource_key is None or effect.amount is None or actor_node_key is None:
                    continue
                amount = self._projected_integer_effect(effect.amount, parameters)
                try:
                    scope = resolve_resource_scope(
                        definition,
                        effect.resource_scope,
                        actor_current_node_key=actor_node_key,
                        target_node_key=target_key,
                    )
                except LocalityEngineError as exc:
                    raise GenericAgentError(exc.code, exc.message) from exc
                if amount < 0:
                    self._consume_projected_resource(
                        scope,
                        effect.resource_key,
                        -amount,
                        projected_pools,
                        projected_region_knowledge,
                        require_known=True,
                        projected_known_resource_balance=projected_known_resource_balance,
                    )
                elif amount > 0:
                    self._add_projected_resource(
                        scope,
                        effect.resource_key,
                        amount,
                        projected_known_resource_balance,
                    )

    @staticmethod
    def _projected_integer_effect(expression: object, parameters: ActionParameters) -> int:
        source = getattr(expression, "source", None)
        multiplier = getattr(expression, "multiplier", 1)
        if getattr(source, "value", source) == ValueSource.LITERAL.value:
            literal = getattr(expression, "literal", None)
            if isinstance(literal, int) and not isinstance(literal, bool):
                return literal * multiplier
        parameter_key = getattr(expression, "parameter_key", None)
        value = parameters.get(parameter_key) if isinstance(parameter_key, str) else None
        if isinstance(value, int) and not isinstance(value, bool):
            return value * multiplier
        raise GenericAgentError(
            "RESOURCE_EFFECT_PARAMETER_UNKNOWN",
            "A Resource effect parameter is not available for Plan validation",
        )

    @staticmethod
    def _advance_projected_action_state(
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        parameters: ActionParameters,
        projected_actor_locations: dict[str, str],
        projected_known_passability: dict[str, bool],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
        projected_resolution_effects: Sequence[EffectV2],
        *,
        planner_input: PlannerInput | None = None,
        projected_command_reachability: dict[str, CommandReachability] | None = None,
    ) -> None:
        if action.behavior in {ActionBehavior.TRAVEL, ActionBehavior.TRANSPORT_RESOURCE}:
            projected_actor_locations[actor_key] = target_key
        location_effects: tuple[dict[str, object], ...]
        if planner_input is None:
            location_effects = tuple(action_planner_effects(action))
        else:
            contract = next(
                (item for item in planner_input.action_contracts if item.action_key == action.key),
                None,
            )
            binding = next(
                (
                    item
                    for item in planner_input.target_bindings
                    if item.action_key == action.key and item.target_key == target_key
                ),
                None,
            )
            location_effects = tuple(
                effect
                for item in (contract, binding)
                if item is not None
                for effect in item.deterministic_effects
            )
            if contract is None:
                location_effects = tuple(action_planner_effects(action))
        if any(
            effect.get("type") == "ACTOR_LOCATION"
            and effect.get("actor") in {"executor", "actor"}
            and effect.get("value") in {"target_key", "target_node"}
            for effect in location_effects
        ):
            projected_actor_locations[actor_key] = target_key
        GenericAgentService._apply_projected_passability_effect(
            definition,
            target_key,
            projected_resolution_effects,
            projected_known_passability,
        )
        GenericAgentService._apply_projected_fact_effects(
            definition,
            action,
            actor_key,
            target_key,
            parameters,
            projected_resolution_effects,
            projected_known_facts,
            projected_known_nodes,
            projected_known_relations,
        )
        if projected_command_reachability is not None:
            GenericAgentService._apply_projected_actor_reachability_effect(
                action,
                actor_key,
                target_key,
                projected_resolution_effects,
                projected_command_reachability,
            )
            if action.behavior == ActionBehavior.RELAY_MESSAGE:
                projected_command_reachability[target_key] = CommandReachability.ONLINE

    def _known_passability(self, definition: ScenarioDefinitionV2) -> dict[str, bool]:
        fact_key = definition.metadata.locality.passability_fact_key
        if fact_key is None:
            return {}
        return {
            row.node_key: row.truth_value
            for row in self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceFactState.fact_key == fact_key,
                    GameInstanceFactState.visibility == Visibility.KNOWN,
                )
            )
            if isinstance(row.truth_value, bool)
        }

    def _known_fact_projection(self) -> dict[tuple[str, str], _ProjectedFact]:
        return {
            (row.node_key, row.fact_key): _ProjectedFact(
                value=row.truth_value,
                visibility=row.visibility,
            )
            for row in self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == self.scope.game_instance_id
                )
            )
        }

    def _known_node_keys(self) -> set[str]:
        return {
            row.node_key
            for row in self.db.scalars(
                select(GameInstanceNodeState).where(
                    GameInstanceNodeState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceNodeState.visibility == Visibility.KNOWN,
                )
            )
        }

    def _known_relation_keys(self, definition: ScenarioDefinitionV2) -> set[str]:
        return {
            key
            for item in SharedKnowledgeProjection(
                self.db,
                self.scope,
                definition,
            ).known_relations()
            if isinstance((key := item.get("relation_key")), str)
        }

    @staticmethod
    def _apply_projected_fact_effects(
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        parameters: ActionParameters,
        projected_resolution_effects: Sequence[EffectV2],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> None:
        if action.behavior == ActionBehavior.INSPECT:
            target = definition.world.node(target_key)
            if target is not None:
                for fact in target.facts:
                    current = projected_known_facts.get((target_key, fact.key))
                    if current is not None:
                        current.visibility = Visibility.KNOWN
        if action.behavior == ActionBehavior.REPAIR_COMMUNICATIONS:
            facility_type = definition.metadata.locality.facility_node_type_key
            if facility_type is not None:
                try:
                    region = region_for_node(definition, target_key)
                except LocalityEngineError:
                    region = None
                if region is not None:
                    for node in definition.world.nodes:
                        if node.node_type_key != facility_type:
                            continue
                        try:
                            node_region = region_for_node(definition, node.key)
                        except LocalityEngineError:
                            continue
                        if node_region != region:
                            continue
                        projected_known_nodes.add(node.key)
                        for fact in node.facts:
                            current = projected_known_facts.get((node.key, fact.key))
                            if current is not None:
                                current.visibility = Visibility.KNOWN
        if any(
            effect.kind == EffectKind.REVEAL_TARGET_REGION_FACILITY_FACTS
            for effect in projected_resolution_effects
        ):
            facility_type = definition.metadata.locality.facility_node_type_key
            if facility_type is not None:
                try:
                    region = region_for_node(definition, target_key)
                except LocalityEngineError:
                    region = None
                if region is not None:
                    for node in definition.world.nodes:
                        if node.node_type_key != facility_type:
                            continue
                        try:
                            node_region = region_for_node(definition, node.key)
                        except LocalityEngineError:
                            continue
                        if node_region != region:
                            continue
                        projected_known_nodes.add(node.key)
                        for fact in node.facts:
                            current = projected_known_facts.get((node.key, fact.key))
                            if current is not None:
                                current.visibility = Visibility.KNOWN

        fact_values: dict[tuple[str, str], set[StrictScalar]] = {}
        fact_visibility: dict[tuple[str, str], set[Visibility]] = {}
        node_visibility: dict[str, set[Visibility]] = {}
        relation_visibility: dict[str, set[RelationVisibility]] = {}
        for effect in projected_resolution_effects:
            node_key = GenericAgentService._projected_effect_node_key(
                effect.node,
                target_key,
                parameters,
            )
            if effect.kind == EffectKind.SET_FACT and node_key is not None:
                value = GenericAgentService._projected_value(effect.value, parameters)
                if value is not None and effect.fact_key is not None:
                    fact_values.setdefault((node_key, effect.fact_key), set()).add(value)
            elif effect.kind in {EffectKind.REVEAL_FACT, EffectKind.HIDE_FACT} and node_key:
                if effect.fact_key is not None:
                    fact_visibility.setdefault((node_key, effect.fact_key), set()).add(
                        Visibility.KNOWN
                        if effect.kind == EffectKind.REVEAL_FACT
                        else Visibility.HIDDEN
                    )
            elif effect.kind in {EffectKind.REVEAL_NODE, EffectKind.HIDE_NODE} and node_key:
                node_visibility.setdefault(node_key, set()).add(
                    Visibility.KNOWN if effect.kind == EffectKind.REVEAL_NODE else Visibility.HIDDEN
                )
            elif (
                effect.kind == EffectKind.SET_RELATION_VISIBILITY
                and effect.relation_key is not None
                and effect.visibility is not None
            ):
                relation_visibility.setdefault(effect.relation_key, set()).add(
                    RelationVisibility(effect.visibility.value)
                )

        for identity, fact_value_options in fact_values.items():
            if len(fact_value_options) != 1:
                continue
            current = projected_known_facts.get(identity)
            visibility = current.visibility if current is not None else Visibility.HIDDEN
            projected_known_facts[identity] = _ProjectedFact(fact_value_options.pop(), visibility)
        for identity, visibility_options in fact_visibility.items():
            if len(visibility_options) != 1:
                continue
            current = projected_known_facts.get(identity)
            if current is not None:
                current.visibility = visibility_options.pop()
        for node_key, node_visibility_options in node_visibility.items():
            if len(node_visibility_options) != 1:
                continue
            if node_visibility_options.pop() == Visibility.KNOWN:
                projected_known_nodes.add(node_key)
            else:
                projected_known_nodes.discard(node_key)
        for relation_key, relation_visibility_options in relation_visibility.items():
            if len(relation_visibility_options) != 1:
                continue
            if relation_visibility_options.pop() == RelationVisibility.VISIBLE:
                projected_known_relations.add(relation_key)
            else:
                projected_known_relations.discard(relation_key)

    @classmethod
    def _projected_resolution_rules(
        cls,
        definition: ScenarioDefinitionV2,
        rules: Sequence[RuleDefinitionV2],
        target_key: str,
        parameters: ActionParameters,
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> list[RuleDefinitionV2]:
        potential: list[tuple[RuleDefinitionV2, bool | None]] = []
        for rule in rules:
            status = cls._known_condition_status(
                definition,
                rule.condition,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
            if status is not False:
                potential.append((rule, status))
        known_true = [item for item in potential if item[1] is True]
        if not known_true:
            return [item[0] for item in potential]
        highest_true_priority = max(item[0].priority for item in known_true)
        highest_known_winners = [
            rule for rule, status in known_true if rule.priority == highest_true_priority
        ]
        possible_unknown_winners = [
            rule
            for rule, status in potential
            if status is None and rule.priority >= highest_true_priority
        ]
        return [*highest_known_winners, *possible_unknown_winners]

    @classmethod
    def _projected_resolution_effects(
        cls,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        target_key: str,
        parameters: ActionParameters,
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> tuple[EffectV2, ...]:
        """Return effects certain across all possible winning resolution rules."""

        rules = [
            rule
            for rule in definition.rules
            if rule.phase == RulePhase.RESOLVE and rule.action_key == action.key
        ]
        projected_rules = cls._projected_resolution_rules(
            definition,
            rules,
            target_key,
            parameters,
            projected_known_facts,
            projected_known_nodes,
            projected_known_relations,
        )
        if not projected_rules:
            return ()
        if len(projected_rules) == 1:
            return projected_rules[0].effects

        identities_by_rule = [
            {effect.model_dump_json() for effect in rule.effects} for rule in projected_rules
        ]
        common_identities = set.intersection(*identities_by_rule)
        result: list[EffectV2] = []
        seen: set[str] = set()
        for effect in projected_rules[0].effects:
            identity = effect.model_dump_json()
            if identity in common_identities and identity not in seen:
                result.append(effect)
                seen.add(identity)
        return tuple(result)

    @staticmethod
    def _projected_effect_node_key(
        selector: NodeSelectorV2 | None,
        target_key: str,
        parameters: ActionParameters,
    ) -> str | None:
        if selector is None:
            return None
        kind = selector.kind
        if kind == NodeSelectorKind.CURRENT_TARGET:
            return target_key
        if kind == NodeSelectorKind.ACTION_SOURCE:
            source = parameters.get("source_key")
            return source if isinstance(source, str) else None
        if kind == NodeSelectorKind.EXPLICIT:
            return selector.node_key if isinstance(selector.node_key, str) else None
        return None

    @staticmethod
    def _projected_value(
        expression: ValueExpressionV2 | None,
        parameters: ActionParameters,
    ) -> StrictScalar | None:
        if expression is None:
            return None
        if getattr(getattr(expression, "source", None), "value", None) == ValueSource.LITERAL.value:
            return getattr(expression, "literal", None)
        parameter_key = getattr(expression, "parameter_key", None)
        value = parameters.get(parameter_key) if isinstance(parameter_key, str) else None
        if isinstance(value, (str, int, bool)):
            return value
        return None

    @staticmethod
    def _apply_projected_passability_effect(
        definition: ScenarioDefinitionV2,
        target_key: str,
        projected_resolution_effects: Sequence[EffectV2],
        projected_known_passability: dict[str, bool],
    ) -> None:
        fact_key = definition.metadata.locality.passability_fact_key
        if fact_key is None:
            return
        values: set[bool] = set()
        for effect in projected_resolution_effects:
            if (
                effect.kind == EffectKind.SET_FACT
                and effect.node is not None
                and effect.node.kind == NodeSelectorKind.CURRENT_TARGET
                and effect.fact_key == fact_key
                and effect.value is not None
                and effect.value.source == ValueSource.LITERAL
                and isinstance(effect.value.literal, bool)
            ):
                values.add(effect.value.literal)
        if len(values) == 1:
            projected_known_passability[target_key] = values.pop()

    @staticmethod
    def _apply_projected_actor_reachability_effect(
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        projected_resolution_effects: Sequence[EffectV2],
        projected_command_reachability: dict[str, CommandReachability],
    ) -> None:
        for effect in projected_resolution_effects:
            if (
                effect.kind == EffectKind.SET_ACTOR_COMMAND_REACHABILITY
                and effect.command_reachability is not None
            ):
                recipient = effect.actor_key or (
                    target_key if action.target_kind == ActionTargetKind.ACTOR else actor_key
                )
                projected_command_reachability[recipient] = effect.command_reachability

    def _record_provider_plan_call(
        self,
        task: AgentTask,
        *,
        request: PlanRequest,
        proposal_steps: tuple[object, ...],
        proposal_candidate_ids: tuple[str, ...],
        diagnostics: tuple[PlanViolation, ...],
        proposal_stop_reason: str,
        accepted: bool,
        audit_id: str | None = None,
    ) -> None:
        assert self.provider is not None
        metadata = dict(task.objective_resolution_metadata or {})
        calls = list(metadata.get("provider_calls", []))
        provider_metadata = provider_call_metadata(self.provider)
        started_at = self._provider_call_started_at.get(audit_id or "")
        call_record: dict[str, object] = {
            "audit_id": audit_id,
            "call_type": request.call_type,
            "model": self.provider.model_name,
            "repair_attempt": request.repair_attempt,
            "provider_payload": request.provider_payload(),
            "planning_context": (
                request.planning_context.compact_dump()
                if request.planning_context is not None
                else None
            ),
            "planning_context_bytes": (
                len(
                    json.dumps(
                        request.planning_context.compact_dump(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                if request.planning_context is not None
                else None
            ),
            "candidate_catalog": [
                {
                    "candidate_id": item.candidate_id,
                    "action_key": item.action_key,
                    "actor_key": item.actor_key,
                    "target_key": item.target_key,
                    "currently_executable": item.currently_executable,
                    "known_blockers": list(item.known_blockers),
                }
                for item in request.planning_action_catalog
            ],
            "proposal_steps": [
                {
                    "purpose": getattr(item, "purpose", ""),
                    "action_key": getattr(item, "action_key", None),
                    "actor_key": getattr(item, "actor_key", None),
                    "target_key": getattr(item, "target_key", None),
                    "parameters": dict(getattr(item, "parameters", {}) or {}),
                    "order": index,
                }
                for index, item in enumerate(proposal_steps, start=1)
            ],
            "proposal_candidate_ids": list(proposal_candidate_ids),
            "proposal_stop_reason": proposal_stop_reason,
            "validator_violations": [
                violation.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
                for violation in diagnostics
            ],
            "validation": "ACCEPTED" if accepted else "REJECTED",
            "outcome": "SUCCESS",
            "finished_at": datetime.now(UTC).isoformat(),
            "wall_clock_latency_ms": (
                provider_metadata.get("wall_clock_latency_ms")
                if isinstance(provider_metadata.get("wall_clock_latency_ms"), int)
                else (_duration_ms(started_at) if started_at is not None else None)
            ),
            **provider_metadata,
        }
        existing_index = next(
            (
                index
                for index, item in enumerate(calls)
                if isinstance(item, dict) and item.get("audit_id") == audit_id
            ),
            None,
        )
        if existing_index is None:
            calls.append(call_record)
        else:
            existing = calls[existing_index]
            calls[existing_index] = {
                **(existing if isinstance(existing, dict) else {}),
                **call_record,
            }
        task.objective_resolution_metadata = {**metadata, "provider_calls": calls}
        self.db.flush()

    def _successful_proposal_signatures(self, task: AgentTask) -> set[str]:
        actions = {item.key: item for item in self._definition().actions}
        operations = self.db.scalars(
            select(WorldOperation).where(
                WorldOperation.game_instance_id == self.scope.game_instance_id,
                WorldOperation.task_id == task.id,
                WorldOperation.status == WorldOperationStatus.RESOLVED,
            )
        )
        signatures: set[str] = set()
        for operation in operations:
            outcome = operation.outcome
            if not isinstance(outcome, dict) or outcome.get("failure") is not None:
                continue
            signatures.add(
                proposal_signature(
                    operation.actor_key,
                    operation.action_key,
                    operation.target_key,
                    dict(operation.parameters),
                    action=actions.get(operation.action_key),
                )
            )
        return signatures

    def _validated_proposed_step(
        self,
        definition: ScenarioDefinitionV2,
        candidate: PlanningActionCandidate,
        parameters: ActionParameters,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        plan_version: int,
        index: int,
        reason: str | None,
        task_id: UUID,
        *,
        allow_epistemic: bool = False,
        matches_operation_goal: bool = False,
    ) -> list[dict[str, object]]:
        action = next(
            (item for item in definition.actions if item.key == candidate.action_key), None
        )
        actor = self.db.get(GameInstanceActor, (self.scope.game_instance_id, candidate.actor_key))
        if action is None or actor is None:
            raise GenericAgentError("GENERIC_PROVIDER_PLAN_INVALID", "Unknown Action or Actor")
        try:
            invocation = canonical_action_invocation(
                action,
                actor_key=actor.actor_key,
                target_key=candidate.target_key,
                parameters=parameters,
            )
            parameters = cast(ActionParameters, dict(invocation.parameters))
        except (TypeError, ValueError) as exc:
            raise GenericAgentError(
                "GENERIC_PLAN_PARAMETER_INVALID",
                str(exc),
                details={
                    "dimension": "PARAMETER",
                    "actual_parameters": parameters,
                    "validation_error": str(exc),
                },
            ) from exc
        planning_failure_code = self._planning_action_failure_code(
            definition,
            action,
            actor,
            candidate.target_key,
        )
        if planning_failure_code is not None:
            raise GenericAgentError(
                planning_failure_code,
                "The Action target does not satisfy the declared planning contract",
                details=_planning_failure_details(
                    definition,
                    action,
                    actor,
                    candidate.target_key,
                    planning_failure_code,
                ),
            )
        if not self._validate_planning_action(definition, action, actor, candidate.target_key):
            raise GenericAgentError("GENERIC_PROVIDER_PLAN_INVALID", "Action assignment is invalid")
        public_known_facts = {
            identity: projected.value
            for identity, projected in self._known_fact_projection().items()
            if projected.visibility == Visibility.KNOWN
        }
        objective_refs = _objective_refs(
            objectives,
            definition=definition,
            known_facts=public_known_facts,
        )
        resource_effects = _action_resource_goal_effects(
            definition,
            action,
            candidate.target_key,
            parameters,
            _objective_resource_refs(
                objectives,
                definition=definition,
                known_facts=public_known_facts,
            ),
        )
        projected_refs = {
            (item.node_key, item.fact_key)
            for item in (
                *action.planning.terminal_effects_for_target(candidate.target_key),
                *action.planning.supporting_effects,
            )
        }
        if (
            objective_refs.isdisjoint(projected_refs)
            and not resource_effects
            and not action.planning.supporting_effects
            and not allow_epistemic
            and not matches_operation_goal
        ):
            raise GenericAgentError(
                "OBJECTIVE_IRRELEVANT",
                "Action does not advance the frozen scope",
                details={
                    "dimension": "OBJECTIVE_RELEVANCE",
                    "required": "ADVANCES_FROZEN_OBJECTIVE_SCOPE",
                    "actual": "NO_DECLARED_RELEVANT_EFFECT",
                },
            )
        authority = evaluate_authority(
            actor,
            action,
            parameters,
            target_key=candidate.target_key,
        )
        if authority.outcome == AuthorityOutcome.DENY:
            raise GenericAgentError(authority.reason_code, "Action authority denied")
        arguments = {
            "action_key": action.key,
            "target_key": candidate.target_key,
            "parameters": parameters,
            "idempotency_key": self._action_idempotency_key(
                task_id,
                plan_version=plan_version,
                step_index=index,
                action_key=action.key,
            ),
        }
        steps: list[dict[str, object]] = [
            {
                "description": f"Execute {action.name}",
                "actor_key": actor.actor_key,
                "execution_type": StepExecutionType.TOOL,
                "action_intent": action.key,
                "arguments": arguments,
                "expected_outcome": {"codes": list(action.planning.success_outcome_codes)},
                "resume_condition": None,
            }
        ]
        if action.execution_mode == ActionExecutionMode.ASYNC:
            steps.append(
                {
                    "description": f"Wait for {action.name}",
                    "actor_key": actor.actor_key,
                    "execution_type": StepExecutionType.WAIT_FOR_WORLD_EVENT,
                    "action_intent": action.key,
                    "arguments": {},
                    "expected_outcome": {"codes": list(action.planning.wait_success_outcome_codes)},
                    "resume_condition": {"action_key": action.key},
                }
            )
        return steps

    def _validate_planning_action(
        self,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor: GameInstanceActor,
        target_key: str,
    ) -> bool:
        """Validate static Plan membership without applying current runtime access gates."""

        return self._planning_action_failure_code(definition, action, actor, target_key) is None

    def _planning_action_failure_code(
        self,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor: GameInstanceActor,
        target_key: str,
    ) -> str | None:
        """Return a safe static-contract diagnostic, when one is available.

        The validator deliberately keeps the public boolean contract used by
        runtime and test callers.  Provider-facing plan validation needs one
        additional distinction, though: a real target interaction mismatch is
        actionable repair feedback and must not be mislabeled as an objective
        relevance failure.  This helper reports only non-sensitive static
        contract information; dynamic accessibility and hidden Truth stay in
        the existing boolean/knowledge-aware validation paths.
        """

        if action.target_kind == ActionTargetKind.ACTOR:
            target_actor = self.db.get(
                GameInstanceActor,
                (self.scope.game_instance_id, target_key),
            )
            if target_actor is None:
                return "TARGET_INVALID"
            if target_actor.status != "ACTIVE":
                return "TARGET_INVALID"
            target_interaction_valid = True
            target_visible = True
        else:
            target = definition.world.node(target_key)
            node_state = self.db.get(
                GameInstanceNodeState,
                (self.scope.game_instance_id, target_key),
            )
            if target is None:
                return "TARGET_INVALID"
            if (
                action.target_node_type_keys
                and target.node_type_key not in action.target_node_type_keys
            ):
                return "TARGET_TYPE_INVALID"
            target_interaction_valid = bool(
                target is not None and action.required_interaction_key in target.interaction_keys
            )
            target_visible = bool(node_state and node_state.visibility == Visibility.KNOWN)
            if not target_visible:
                return "TARGET_NOT_VISIBLE"
            if not target_interaction_valid:
                return "TARGET_INTERACTION_INVALID"
        if actor.status != "ACTIVE":
            return "ACTOR_NOT_AVAILABLE"
        if action.key not in actor.allowed_action_keys:
            return "ACTOR_NOT_ALLOWED"
        if not {item.value for item in action.allowed_actor_capabilities}.issubset(
            set(actor.capabilities)
        ):
            return "ACTOR_CAPABILITY_MISSING"
        required_actor_role = action.required_actor_role_for_target(target_key)
        if required_actor_role is not None and actor.role_key != required_actor_role:
            return "ACTOR_ROLE_MISSING"
        if not actor_binding_matches(definition, actor):
            return "ACTOR_BINDING_INVALID"
        return None

    @staticmethod
    def _default_parameters(action: ActionDefinitionV2) -> ActionParameters:
        try:
            return normalize_action_parameters(action, {})
        except ValueError as exc:
            raise GenericAgentError("GENERIC_PLAN_PARAMETER_REQUIRED", str(exc)) from exc

    def _known_requirement_satisfied(self, requirement) -> bool:  # type: ignore[no-untyped-def]
        return known_requirement_satisfied(
            self.db,
            self.scope,
            self._definition(),
            requirement,
        )

    def _known_requirement_public(self, requirement) -> bool:  # type: ignore[no-untyped-def]
        if not requirement_gate_is_public(self.db, self.scope, requirement):
            return False
        if requirement.kind == ObjectiveRequirementKind.DERIVED_STATE:
            definition = self._definition()
            state = definition.derived_state_definitions.get(requirement.derived_key or "")
            return bool(state is not None and state.goal_addressable)
        if requirement.fact_ref is None:
            return True
        row = self.db.get(
            GameInstanceFactState,
            (self.scope.game_instance_id, requirement.node_key, requirement.fact_key),
        )
        return bool(row and row.visibility == Visibility.KNOWN)

    def _record_action_failure(
        self,
        task: AgentTask,
        step: AgentStep,
        code: str,
        *,
        retryable: bool,
        replan: bool = True,
    ) -> None:
        step.status = AgentStepStatus.FAILED
        step.failure_code = code
        task.last_error_code = code
        self.retire_failed_plan_suffix(task, step)
        if retryable and replan:
            self.plan(task, reason=code)
        elif not retryable:
            task.status = AgentTaskStatus.BLOCKED

    def retire_failed_plan_suffix(self, task: AgentTask, failed_step: AgentStep) -> None:
        """Retire the unreachable suffix after an Action failure.

        The failed Step and all prior history remain auditable.  The failed
        Plan itself is no longer executable, and only non-terminal suffix
        Steps are marked ``SKIPPED`` so a later REPLAN cannot accidentally
        resume the old execution path.
        """

        plan = self.db.get(AgentPlan, failed_step.plan_id)
        if plan is None or plan.task_id != task.id:
            return
        for suffix_step in self.db.scalars(
            select(AgentStep).where(
                AgentStep.plan_id == plan.id,
                AgentStep.sequence > failed_step.sequence,
                AgentStep.status.in_(
                    (
                        AgentStepStatus.PENDING,
                        AgentStepStatus.IN_PROGRESS,
                        AgentStepStatus.REQUIRES_PLAYER_DECISION,
                        AgentStepStatus.WAITING_FOR_PLAYER_ACTION,
                        AgentStepStatus.WAITING_FOR_WORLD_EVENT,
                    )
                ),
            )
        ):
            suffix_step.status = AgentStepStatus.SKIPPED
        if plan.status == AgentPlanStatus.ACTIVE:
            plan.status = AgentPlanStatus.SUPERSEDED
        self.db.flush()

    def _snapshot(self) -> ScenarioVersionSnapshot:
        persisted = GameInstanceService(self.db).load(self.scope.game_instance_id)
        self.scope.assert_compatible(persisted)
        snapshot = ScenarioVersionRepository(self.db).load(self.scope.scenario_version_id)
        definition = snapshot.definition
        if not isinstance(definition, ScenarioDefinitionV2):
            raise GenericAgentError(
                "GENERIC_RUNTIME_SCHEMA_REQUIRED", "Generic Agent requires ScenarioDefinition v2"
            )
        return snapshot

    def _definition(self) -> ScenarioDefinitionV2:
        return self._snapshot().definition

    def _formal_goal(self, task: AgentTask) -> FormalGoalContract:
        try:
            return load_formal_goal_for_task(self.db, self.scope, task)
        except FormalGoalPersistenceError as exc:
            raise GenericAgentError(exc.code, exc.message) from exc

    def _task_scope(self, task: AgentTask) -> None:
        if (
            task.game_instance_id != self.scope.game_instance_id
            or task.player_id != self.scope.player_id
        ):
            raise GenericAgentError(
                "GENERIC_TASK_SCOPE_INVALID", "Task does not belong to this exact Version scope"
            )
        if task.objective_frozen_at is None:
            raise GenericAgentError(
                "GENERIC_TASK_SCOPE_INVALID", "Task does not have a frozen Formal Goal"
            )
        self._formal_goal(task)

    @staticmethod
    def _objectives(
        task: AgentTask, definition: ScenarioDefinitionV2
    ) -> tuple[ObjectiveDefinitionV2, ...]:
        keys = tuple(task.objective_scope_keys or ())
        if not keys and task.formal_goal_contract_json is not None:
            try:
                contract_model = (
                    FormalGoalContractV2
                    if task.formal_goal_contract_json.get("schema_version") == 2
                    else FormalGoalContractV1
                )
                contract = contract_model.model_validate(task.formal_goal_contract_json)
            except (TypeError, ValueError) as exc:
                raise GenericAgentError(
                    "FORMAL_GOAL_PERSISTENCE_INVALID",
                    "Task Formal Goal contract is invalid",
                ) from exc
            return formal_goal_planning_objectives(
                contract,
                definition,
                goal_description=task.goal_description,
            )
        catalog = {item.key: item for item in definition.objectives}
        if not keys or any(key not in catalog for key in keys):
            raise GenericAgentError(
                "GENERIC_OBJECTIVE_SCOPE_INVALID", "Objective is absent from exact Version"
            )
        return tuple(catalog[key] for key in keys)

    def _actor(self, actor_key: str | None) -> GameInstanceActor:
        if actor_key is None:
            raise GenericAgentError("GENERIC_ACTOR_REQUIRED", "Task has no Versioned Actor")
        actor = self.db.get(GameInstanceActor, (self.scope.game_instance_id, actor_key))
        if actor is None:
            raise GenericAgentError("GENERIC_ACTOR_REQUIRED", "Task Actor is not in this Instance")
        return actor

    def _active_plan(self, task: AgentTask) -> AgentPlan:
        plan = self.db.scalar(
            select(AgentPlan).where(
                AgentPlan.task_id == task.id,
                AgentPlan.status == AgentPlanStatus.ACTIVE,
            )
        )
        if plan is None:
            raise GenericAgentError("GENERIC_PLAN_REQUIRED", "Task has no active Plan")
        return plan

    @staticmethod
    def _complete_task(task: AgentTask) -> None:
        task.status = AgentTaskStatus.SUCCEEDED
        task.last_error_code = None
        task.completed_at = datetime.now(UTC)


def _dynamic_goal_public_keys(
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
) -> tuple[set[str], set[tuple[str, str]], set[str]]:
    """Return public nodes, goal-addressable Facts, and Regions.

    Runtime Fact visibility is Knowledge about a Fact's current value, not the
    allow-list for public semantic properties that a player may choose as a
    Dynamic Goal.  The authored Fact-level ``goal_addressable`` flag is the
    only semantic allow-list here, so it applies uniformly to Facility,
    Transport, and every other Node type without reading runtime Truth.
    """

    if db is not None and scope is not None:
        projection = SharedKnowledgeProjection(db, scope, definition)
        public_nodes = {row.node_key for row in projection.known_node_rows()}
    else:
        public_nodes = {
            node.key
            for node in definition.world.nodes
            if node.initial_visibility == Visibility.KNOWN
        }
    locality = definition.metadata.locality
    public_facts = {
        (node.key, fact.key)
        for node in definition.world.nodes
        if node.key in public_nodes
        for fact in node.facts
        if fact.goal_addressable
    }
    public_regions = {
        node.key
        for node in definition.world.nodes
        if node.key in public_nodes
        and locality.enabled
        and node.node_type_key == locality.region_node_type_key
    }
    return public_nodes, public_facts, public_regions


def _dynamic_goal_public_action_keys(
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
) -> set[str]:
    """Return Actions whose authored planning gate is public in this Instance."""

    if db is not None and scope is not None:
        projection = SharedKnowledgeProjection(db, scope, definition)
        known_facts = {
            (row.node_key, row.fact_key): row.truth_value for row in projection.known_fact_rows()
        }
    else:
        known_facts = {
            (node.key, fact.key): fact.initial_value
            for node in definition.world.nodes
            if node.initial_visibility == Visibility.KNOWN
            for fact in node.facts
            if fact.initial_visibility == Visibility.KNOWN
        }
    return {
        action.key
        for action in definition.actions
        if _action_planning_is_public(action, known_facts)
    }


def _dynamic_goal_public_actor_keys(
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
) -> set[str]:
    """Return public Actor identities without exposing runtime Actor Truth."""

    if db is not None and scope is not None:
        return {
            row.actor_key for row in SharedKnowledgeProjection(db, scope, definition).actor_rows()
        }
    return {actor.key for actor in definition.actors.actor_profiles}


def _dynamic_goal_public_relations(
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
    public_nodes: set[str],
) -> tuple[dict[str, object], ...]:
    """Return public topology only, with no route condition or Fact value."""

    if db is not None and scope is not None:
        raw_relations = SharedKnowledgeProjection(db, scope, definition).known_relations()
    else:
        raw_relations = tuple(
            {
                **relation.model_dump(mode="json"),
                "relation_key": relation_identity(relation),
            }
            for relation in definition.world.relations
            if relation.initial_visibility == RelationVisibility.VISIBLE
        )
    relations = [
        dict(relation)
        for relation in raw_relations
        if (
            relation.get("source_node_key") in public_nodes
            and relation.get("target_node_key") in public_nodes
        )
    ]
    return tuple(
        sorted(
            relations,
            key=lambda item: str(item.get("relation_key", "")),
        )
    )


def _dynamic_goal_entity_catalog(
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
) -> dict[str, object]:
    """Build the smallest public-only catalog accepted by Entity Grounding."""

    public_nodes, _goal_addressable_facts, public_regions = _dynamic_goal_public_keys(
        db,
        scope,
        definition,
    )
    public_action_keys = _dynamic_goal_public_action_keys(db, scope, definition)
    public_actor_keys = _dynamic_goal_public_actor_keys(db, scope, definition)
    reference_index = PublicReferenceIndexBuilder.build(definition)
    nodes_by_key = {node.key: node for node in definition.world.nodes}
    nodes = [
        {
            "key": node.key,
            "name": node.name,
            "description": node.description,
            "node_type_key": node.node_type_key,
            **(
                {"public_references": list(terms)}
                if (
                    terms := reference_index.terms_for(
                        PublicReferenceTypeV2.REGION
                        if node.key in public_regions
                        else PublicReferenceTypeV2.NODE,
                        node.key,
                    )
                )
                else {}
            ),
        }
        for node in sorted(definition.world.nodes, key=lambda item: item.key)
        if node.key in public_nodes
    ]
    public_relations = _dynamic_goal_public_relations(
        db,
        scope,
        definition,
        public_nodes,
    )
    locality = definition.metadata.locality
    topology: list[dict[str, object]] = []
    if locality.enabled and locality.transport_node_type_key is not None:
        endpoint_type = locality.transport_endpoint_relation_type_key
        for node in sorted(definition.world.nodes, key=lambda item: item.key):
            if (
                node.key not in public_nodes
                or node.node_type_key != locality.transport_node_type_key
            ):
                continue
            endpoints = sorted(
                {
                    str(item["target_node_key"])
                    for item in public_relations
                    if (
                        item.get("source_node_key") == node.key
                        and item.get("relation_type_key") == endpoint_type
                        and item.get("target_node_key") in public_regions
                    )
                }
            )
            if not endpoints:
                continue
            topology.append(
                {
                    "entity_key": node.key,
                    "entity_name": node.name,
                    "endpoint_region_keys": endpoints,
                    "endpoint_region_names": [
                        nodes_by_key[key].name for key in endpoints if key in nodes_by_key
                    ],
                }
            )
    references: list[dict[str, object]] = []
    for node in sorted(definition.world.nodes, key=lambda item: item.key):
        if node.key not in public_nodes:
            continue
        ref_type = "REGION" if node.key in public_regions else "NODE"
        references.append(
            {
                "ref_type": ref_type,
                "key": node.key,
                "name": node.name,
                "description": node.description,
                **(
                    {"public_references": list(terms)}
                    if (
                        terms := reference_index.terms_for(
                            PublicReferenceTypeV2.REGION
                            if node.key in public_regions
                            else PublicReferenceTypeV2.NODE,
                            node.key,
                        )
                    )
                    else {}
                ),
            }
        )
    references.extend(
        {
            "ref_type": "RESOURCE",
            "key": resource.key,
            "name": resource.name,
            "description": resource.description,
            **(
                {"public_references": list(terms)}
                if (
                    terms := reference_index.terms_for(PublicReferenceTypeV2.RESOURCE, resource.key)
                )
                else {}
            ),
        }
        for resource in sorted(definition.world.resources, key=lambda item: item.key)
    )
    references.extend(
        {
            "ref_type": "DERIVED_STATE",
            "key": state.key,
            "name": state.name,
            "description": state.description,
            "value_type": state.value_type.value,
            "allowed_values": list(state.allowed_values),
            **({"goal_aliases": list(state.goal_aliases)} if state.goal_aliases else {}),
            **({"goal_examples": list(state.goal_examples)} if state.goal_examples else {}),
            **(
                {"public_references": list(terms)}
                if (
                    terms := reference_index.terms_for(
                        PublicReferenceTypeV2.DERIVED_STATE, state.key
                    )
                )
                else {}
            ),
        }
        for state in sorted(definition.derived_states, key=lambda item: item.key)
        if state.goal_addressable
    )
    references.extend(
        {
            "ref_type": "ACTION",
            "key": action.key,
            "name": action.name,
            "description": action.description,
            "behavior": action.behavior.value,
            "target_kind": action.target_kind.value,
            "target_node_type_keys": list(action.target_node_type_keys),
            "parameters": [item.model_dump(mode="json") for item in action.parameters],
            "operation_binding_contract": action_operation_binding_contract(action),
            **(
                {"public_references": list(terms)}
                if (terms := reference_index.terms_for(PublicReferenceTypeV2.ACTION, action.key))
                else {}
            ),
        }
        for action in sorted(definition.actions, key=lambda item: item.key)
        if action.key in public_action_keys
    )
    references.extend(
        {
            "ref_type": "ACTOR",
            "key": actor.key,
            "name": actor.name,
            "description": actor.persona,
            **(
                {"public_references": list(terms)}
                if (terms := reference_index.terms_for(PublicReferenceTypeV2.ACTOR, actor.key))
                else {}
            ),
        }
        for actor in sorted(definition.actors.actor_profiles, key=lambda item: item.key)
        if actor.key in public_actor_keys
    )
    return {
        "schema_version": 1,
        "entities": nodes,
        "regions": [
            {
                "key": node.key,
                "name": node.name,
                "description": node.description,
            }
            for node in sorted(definition.world.nodes, key=lambda item: item.key)
            if node.key in public_regions
        ],
        "public_topology": {
            "relations": list(public_relations),
            "transport_endpoint_pairs": topology,
        },
        "references": references,
        "grounding_language": {
            "selection": "PUBLIC_CANDIDATE_REFERENCES_ONLY",
            "reference_types": [
                "NODE",
                "REGION",
                "RESOURCE",
                "DERIVED_STATE",
                "ACTION",
                "ACTOR",
            ],
            "outcomes": ["RESOLVED", "NEEDS_CLARIFICATION", "UNSUPPORTED"],
        },
    }


def _contains_public_term(text: str, term: str) -> bool:
    if not term:
        return False
    if any(ord(character) > 127 for character in term):
        return term in text
    return (
        re.search(
            rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
            text,
        )
        is not None
    )


def _dynamic_goal_exact_public_matches(
    goal: str,
    definition: ScenarioDefinitionV2,
    public_nodes: set[str],
    public_action_keys: set[str],
    public_actor_keys: set[str],
) -> tuple[DynamicGoalCandidateReference, ...]:
    lookup = _dynamic_goal_public_reference_lookup(
        goal,
        definition,
        public_nodes,
        public_action_keys,
        public_actor_keys,
    )
    return tuple(
        DynamicGoalCandidateReference(
            ref_type=identity.ref_type.value,
            key=identity.ref_key,
            provenance="EXACT_USER_MENTION",
            match_semantics="EXACT_OR_AUTHORED",
        )
        for identity in lookup.identities
    )


def _dynamic_goal_public_reference_lookup(
    goal: str,
    definition: ScenarioDefinitionV2,
    public_nodes: set[str],
    public_action_keys: set[str],
    public_actor_keys: set[str],
) -> PublicReferenceLookup:
    public_regions = {
        node.key
        for node in definition.world.nodes
        if node.key in public_nodes
        and definition.metadata.locality.enabled
        and node.node_type_key == definition.metadata.locality.region_node_type_key
    }
    allowed = {
        *(
            PublicReferenceIdentity(
                PublicReferenceTypeV2.REGION
                if node_key in public_regions
                else PublicReferenceTypeV2.NODE,
                node_key,
            )
            for node_key in public_nodes
        ),
        *(
            PublicReferenceIdentity(PublicReferenceTypeV2.RESOURCE, item.key)
            for item in definition.world.resources
        ),
        *(
            PublicReferenceIdentity(PublicReferenceTypeV2.DERIVED_STATE, item.key)
            for item in definition.derived_states
            if item.goal_addressable
        ),
        *(PublicReferenceIdentity(PublicReferenceTypeV2.ACTION, key) for key in public_action_keys),
        *(PublicReferenceIdentity(PublicReferenceTypeV2.ACTOR, key) for key in public_actor_keys),
    }
    return PublicReferenceIndexBuilder.build(definition).lookup(goal, allowed_identities=allowed)


def _merge_dynamic_goal_candidate_refs(
    deterministic_refs: tuple[DynamicGoalCandidateReference, ...],
    semantic_refs: tuple[DynamicGoalCandidateReference, ...],
) -> tuple[DynamicGoalCandidateReference, ...]:
    """Retain exact public refs while adding semantically grounded refs."""

    provenance_priority = {
        "OTHER": 0,
        "LLM_SUPPLEMENTED": 1,
        "TOPOLOGY_ENRICHED": 2,
        "EXACT_USER_MENTION": 3,
    }
    match_semantics_priority = {
        None: -1,
        "RELATED_ONLY": 0,
        "SEMANTIC_EQUIVALENT": 1,
        "EXACT_OR_AUTHORED": 2,
    }
    unique: dict[tuple[str, str], DynamicGoalCandidateReference] = {}
    for item in (*deterministic_refs, *semantic_refs):
        identity = (item.ref_type, item.key)
        previous = unique.get(identity)
        if (
            previous is None
            or provenance_priority[item.provenance] > provenance_priority[previous.provenance]
            or (
                provenance_priority[item.provenance] == provenance_priority[previous.provenance]
                and match_semantics_priority[item.match_semantics]
                > match_semantics_priority[previous.match_semantics]
            )
        ):
            unique[identity] = item
    return tuple(unique[key] for key in sorted(unique))


def _dynamic_goal_intent_candidate_refs(
    intent: DynamicGoalIntentDraft | None,
) -> tuple[DynamicGoalCandidateReference, ...]:
    """Project only Stage 1's grounded typed slots into public references."""

    if intent is None:
        return ()
    slots = (
        intent.action,
        intent.actor,
        intent.source,
        intent.target,
        intent.resource,
    )
    refs = tuple(
        DynamicGoalCandidateReference(
            ref_type=slot.ref_type,
            key=slot.key,
            match_semantics=slot.match_semantics,
        )
        for slot in slots
        if (
            slot.status == "GROUNDED"
            and slot.match_semantics != "RELATED_ONLY"
            and slot.ref_type is not None
            and slot.key is not None
        )
    )
    unique = {(item.ref_type, item.key): item for item in refs}
    return tuple(unique[key] for key in sorted(unique))


def _dynamic_goal_routing_action_catalog(
    definition: ScenarioDefinitionV2,
    public_action_keys: set[str],
    candidate_refs: tuple[DynamicGoalCandidateReference, ...] = (),
) -> tuple[dict[str, object], ...]:
    """Conservatively remove only Actions incompatible with public reference types."""

    result: list[dict[str, object]] = []
    reference_index = PublicReferenceIndexBuilder.build(definition)
    for action in sorted(definition.actions, key=lambda item: item.key):
        if action.key not in public_action_keys:
            continue
        contract = _dynamic_goal_action_contract(action)
        compatible_refs = _dynamic_goal_action_compatible_refs(
            definition,
            contract,
            candidate_refs,
        )
        if compatible_refs is None:
            continue
        result.append(
            {
                "key": action.key,
                "name": action.name,
                "description": action.description[:400],
                **(
                    {"public_references": list(terms)}
                    if (
                        terms := reference_index.terms_for(PublicReferenceTypeV2.ACTION, action.key)
                    )
                    else {}
                ),
                "target": contract["target"],
                "bindings": [
                    {
                        "slot_key": item["slot_key"],
                        "expected_type": item["expected_type"],
                        "description": item["description"],
                    }
                    for item in cast(list[dict[str, object]], contract["bindings"])
                ],
                "parameters": [
                    {
                        "slot_key": item["slot_key"],
                        "name": item["name"],
                        "expected_type": item["expected_type"],
                    }
                    for item in cast(list[dict[str, object]], contract["parameters"])
                ],
                "candidate_refs": [item.model_dump(mode="json") for item in compatible_refs],
            }
        )
    return tuple(result)


def _dynamic_goal_action_semantic_entities(
    public_catalog: dict[str, object],
    candidate_refs: tuple[DynamicGoalCandidateReference, ...],
) -> tuple[dict[str, object], ...]:
    """Enrich relevant identities with a bounded, public semantic view."""

    raw_references = public_catalog.get("references")
    references = (
        {
            (str(item["ref_type"]), str(item["key"])): item
            for item in raw_references
            if isinstance(item, dict)
            and isinstance(item.get("ref_type"), str)
            and isinstance(item.get("key"), str)
        }
        if isinstance(raw_references, (list, tuple))
        else {}
    )
    raw_entities = public_catalog.get("entities")
    entities = (
        {
            str(item["key"]): item
            for item in raw_entities
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }
        if isinstance(raw_entities, (list, tuple))
        else {}
    )

    result: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidate_refs:
        identity = (candidate.ref_type, candidate.key)
        if identity in seen:
            continue
        seen.add(identity)
        reference = references.get(identity)
        if reference is None:
            continue
        entity = entities.get(candidate.key, {})
        semantic_view: dict[str, object] = {
            "ref_type": candidate.ref_type,
            "key": candidate.key,
            "provenance": candidate.provenance,
            "name": reference["name"],
            "description": str(reference.get("description", ""))[:400],
        }
        if isinstance(entity.get("node_type_key"), str):
            semantic_view["node_type_key"] = entity["node_type_key"]
        public_references = reference.get("public_references")
        if isinstance(public_references, (list, tuple)) and public_references:
            semantic_view["public_references"] = list(public_references)
        result.append(semantic_view)
    return tuple(result)


def _dynamic_goal_action_topology_context(
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
    candidate_refs: tuple[DynamicGoalCandidateReference, ...],
    *,
    public_catalog: dict[str, object] | None = None,
) -> dict[str, object]:
    """Project only the public topology relationship already derived for this Goal."""

    exact_regions = {
        item.key
        for item in candidate_refs
        if item.ref_type == "REGION" and item.provenance == "EXACT_USER_MENTION"
    }
    topology_nodes = {
        item.key
        for item in candidate_refs
        if item.ref_type == "NODE" and item.provenance == "TOPOLOGY_ENRICHED"
    }
    empty: dict[str, object] = {"relations": [], "transport_endpoint_pairs": []}
    if len(exact_regions) != 2 or not topology_nodes:
        return empty

    if public_catalog is None:
        public_catalog = _dynamic_goal_entity_catalog(db, scope, definition)
    raw_topology = public_catalog.get("public_topology")
    if not isinstance(raw_topology, dict):
        return empty
    raw_entities = public_catalog.get("entities")
    entities = (
        {
            str(item["key"]): item
            for item in raw_entities
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }
        if isinstance(raw_entities, (list, tuple))
        else {}
    )

    raw_pairs = raw_topology.get("transport_endpoint_pairs")
    pairs: list[dict[str, object]] = []
    if isinstance(raw_pairs, (list, tuple)):
        for item in raw_pairs:
            if not isinstance(item, dict):
                continue
            entity_key = item.get("entity_key")
            endpoints = item.get("endpoint_region_keys")
            if (
                not isinstance(entity_key, str)
                or entity_key not in topology_nodes
                or not isinstance(endpoints, (list, tuple))
                or set(endpoints) != exact_regions
            ):
                continue
            entity = entities.get(entity_key, {})
            pairs.append(
                {
                    **item,
                    "derived_target": {
                        "ref_type": "NODE",
                        "key": entity_key,
                        "name": entity.get("name"),
                        "node_type_key": entity.get("node_type_key"),
                        "description": str(entity.get("description", ""))[:400],
                        "provenance": "TOPOLOGY_ENRICHED",
                    },
                }
            )

    pair_keys = {
        str(item["entity_key"]) for item in pairs if isinstance(item.get("entity_key"), str)
    }
    raw_relations = raw_topology.get("relations")
    relations = (
        [
            item
            for item in raw_relations
            if isinstance(item, dict)
            and item.get("source_node_key") in pair_keys
            and item.get("target_node_key") in exact_regions
        ]
        if isinstance(raw_relations, (list, tuple))
        else []
    )
    return {"relations": relations, "transport_endpoint_pairs": pairs}


def _dynamic_goal_action_compatible_refs(
    definition: ScenarioDefinitionV2,
    contract: dict[str, object],
    candidate_refs: tuple[DynamicGoalCandidateReference, ...],
) -> tuple[DynamicGoalCandidateReference, ...] | None:
    """Retain an Action when explicit refs have any legal semantic-role assignment.

    Topology-enriched Nodes are evidence, never an exact target or frozen Action.
    Endpoint Regions may therefore describe that Node instead of consuming Region
    slots. Missing references never exclude an Action.
    """

    semantic_refs = tuple(
        item for item in candidate_refs if item.ref_type not in {"ACTION", "ACTOR"}
    )
    if not semantic_refs:
        return candidate_refs

    target = cast(dict[str, object], contract["target"])
    bindings = cast(list[dict[str, object]], contract["bindings"])
    parameters = cast(list[dict[str, object]], contract["parameters"])
    nodes_by_key = {item.key: item for item in definition.world.nodes}
    target_node_types = {str(item) for item in cast(list[object], target.get("node_type_keys", []))}
    target_ref_type = _slot_expected_reference_type(str(target["expected_type"]))

    def slot_accepts(slot: dict[str, object], reference: DynamicGoalCandidateReference) -> bool:
        expected_type = str(slot["expected_type"])
        if reference.ref_type != _slot_expected_reference_type(expected_type):
            return False
        if expected_type not in {"NODE", "REGION", "FACILITY"}:
            return True
        node = nodes_by_key.get(reference.key)
        if node is None:
            return False
        if expected_type == "REGION":
            return node.node_type_key == definition.metadata.locality.region_node_type_key
        if expected_type == "FACILITY":
            return node.node_type_key == definition.metadata.locality.facility_node_type_key
        return True

    def target_accepts(reference: DynamicGoalCandidateReference) -> bool:
        if not slot_accepts(target, reference):
            return False
        if reference.ref_type not in {"NODE", "REGION"}:
            return True
        node = nodes_by_key.get(reference.key)
        if node is None or (target_node_types and node.node_type_key not in target_node_types):
            return False
        return (
            target.get("required_interaction_key") is None
            or target.get("required_interaction_key") in node.interaction_keys
        )

    roles: list[dict[str, object]] = []
    if target_ref_type is not None:
        roles.append(target)
    for slot in (*bindings, *parameters):
        if _slot_expected_reference_type(str(slot["expected_type"])) is not None:
            roles.append(slot)

    topology_nodes = tuple(
        item
        for item in semantic_refs
        if item.ref_type == "NODE" and item.provenance == "TOPOLOGY_ENRICHED"
    )
    topology_target = any(target_accepts(item) for item in topology_nodes)
    explicit_refs = tuple(
        item
        for item in semantic_refs
        if item.provenance == "EXACT_USER_MENTION"
        # Region pairs may be endpoint evidence for a compatible topology Node.
        and not (topology_target and item.ref_type == "REGION")
    )

    def role_accepts(role: dict[str, object], reference: DynamicGoalCandidateReference) -> bool:
        return (
            target_accepts(reference)
            if role.get("logical_role") == "target"
            else slot_accepts(role, reference)
        )

    relation = contract.get("relation_semantics")

    def relation_assignment_is_valid(assignments: dict[int, DynamicGoalCandidateReference]) -> bool:
        if not isinstance(relation, dict):
            return True
        source_slot_key = relation.get("source_slot_key")
        relation_type = relation.get("source_relation_type_key")
        source = next(
            (
                assignments[index]
                for index, role in enumerate(roles)
                if role.get("slot_key") == source_slot_key and index in assignments
            ),
            None,
        )
        if source is None or not isinstance(relation_type, str):
            return True
        explicit_target = next(
            (
                assignments[index]
                for index, role in enumerate(roles)
                if role.get("logical_role") == "target" and index in assignments
            ),
            None,
        )
        return any(
            item.source_node_key == source.key
            and item.relation_type_key == relation_type
            and (
                item.target_node_key == explicit_target.key
                if explicit_target is not None
                else target_accepts(
                    DynamicGoalCandidateReference(
                        ref_type=target_ref_type or "NODE",
                        key=item.target_node_key,
                        provenance="OTHER",
                    )
                )
            )
            for item in definition.world.relations
        )

    def assignment_exists(
        index: int,
        used_roles: set[int],
        assignments: dict[int, DynamicGoalCandidateReference],
    ) -> bool:
        if index == len(explicit_refs):
            return relation_assignment_is_valid(assignments)
        reference = explicit_refs[index]
        for role_index, role in enumerate(roles):
            if role_index in used_roles or not role_accepts(role, reference):
                continue
            used_roles.add(role_index)
            assignments[role_index] = reference
            if assignment_exists(index + 1, used_roles, assignments):
                return True
            used_roles.remove(role_index)
            assignments.pop(role_index)
        return False

    return candidate_refs if assignment_exists(0, set(), {}) else None


def _dynamic_goal_routing_state_catalog(
    goal: str,
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
    candidate_refs: tuple[DynamicGoalCandidateReference, ...],
) -> tuple[dict[str, object], ...]:
    """Project relevant authored State semantics without Truth or execution data."""

    public_nodes, public_facts, public_regions = _dynamic_goal_public_keys(
        db,
        scope,
        definition,
    )
    normalized_goal = _normalize(goal)
    refs_by_identity = {(item.ref_type, item.key): item for item in candidate_refs}
    referenced_nodes = {item.key for item in candidate_refs if item.ref_type in {"NODE", "REGION"}}
    entries: list[dict[str, object]] = []
    for node in sorted(definition.world.nodes, key=lambda item: item.key):
        if node.key not in public_nodes:
            continue
        node_ref = refs_by_identity.get(
            ("REGION" if node.key in public_regions else "NODE", node.key)
        )
        for fact in sorted(node.facts, key=lambda item: item.key):
            if (node.key, fact.key) not in public_facts:
                continue
            terms = (
                _normalize(fact.key),
                _normalize(fact.name),
                *(_normalize(item) for item in fact.goal_aliases),
                *(_normalize(item) for item in fact.goal_examples),
            )
            authored_match = any(_contains_public_term(normalized_goal, term) for term in terms)
            if node.key not in referenced_nodes and not authored_match:
                continue
            entries.append(
                {
                    "kind": "FACT",
                    "node_key": node.key,
                    "node_name": node.name,
                    "fact_key": fact.key,
                    "name": fact.name,
                    "description": fact.description[:300],
                    "target_values": list(fact.goal_target_values),
                    "candidate_refs": (
                        [node_ref.model_dump(mode="json")] if node_ref is not None else []
                    ),
                }
            )

    region_refs = tuple(item for item in candidate_refs if item.ref_type == "REGION")
    resource_refs = tuple(item for item in candidate_refs if item.ref_type == "RESOURCE")
    if region_refs and resource_refs:
        entries.append(
            {
                "kind": "RESOURCE_AT_LEAST",
                "regions": [item.model_dump(mode="json") for item in region_refs],
                "resources": [item.model_dump(mode="json") for item in resource_refs],
            }
        )

    for state in sorted(definition.derived_states, key=lambda item: item.key):
        if not state.goal_addressable:
            continue
        state_ref = refs_by_identity.get(("DERIVED_STATE", state.key))
        terms = (
            _normalize(state.key),
            _normalize(state.name),
            *(_normalize(item) for item in state.goal_aliases),
            *(_normalize(item) for item in state.goal_examples),
        )
        authored_match = any(_contains_public_term(normalized_goal, term) for term in terms)
        if state_ref is None and not authored_match:
            continue
        entries.append(
            {
                "kind": "DERIVED_STATE",
                "key": state.key,
                "name": state.name,
                "description": state.description[:300],
                "target_value": state.available_value,
                "candidate_refs": (
                    [state_ref.model_dump(mode="json")] if state_ref is not None else []
                ),
            }
        )
    return tuple(entries)


def _dynamic_goal_frozen_state_grounding(
    goal: str,
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
    deterministic: _DynamicGoalGrounding,
    *,
    include_authored_matches: bool = True,
) -> _DynamicGoalGrounding:
    """Build STATE-only public evidence without reopening family or Action intent."""

    if deterministic.status == "NEEDS_CLARIFICATION":
        return deterministic

    public_nodes, _public_facts, public_regions = _dynamic_goal_public_keys(
        db,
        scope,
        definition,
    )
    refs = {
        (item.ref_type, item.key): item
        for item in deterministic.candidate_refs
        if item.ref_type in {"NODE", "REGION", "RESOURCE", "DERIVED_STATE"}
    }
    state_catalog = _dynamic_goal_routing_state_catalog(
        goal if include_authored_matches else "",
        db,
        scope,
        definition,
        tuple(refs.values()),
    )
    for item in state_catalog:
        kind = item.get("kind")
        if kind == "FACT":
            node_key = item.get("node_key")
            if isinstance(node_key, str) and node_key in public_nodes:
                reference = DynamicGoalCandidateReference(
                    ref_type="REGION" if node_key in public_regions else "NODE",
                    key=node_key,
                )
                refs.setdefault(
                    (reference.ref_type, node_key),
                    reference,
                )
        elif kind == "DERIVED_STATE":
            derived_key = item.get("key")
            if isinstance(derived_key, str):
                refs.setdefault(
                    ("DERIVED_STATE", derived_key),
                    DynamicGoalCandidateReference(
                        ref_type="DERIVED_STATE",
                        key=derived_key,
                    ),
                )

    if not refs:
        return _DynamicGoalGrounding(status="NONE")
    candidate_refs = _validate_dynamic_goal_candidate_refs(
        definition,
        db,
        scope,
        tuple(refs.values()),
    )
    return _dynamic_goal_grounding_from_refs(
        candidate_refs,
        "FROZEN_STATE_PUBLIC_CATALOG",
        db,
        scope,
        definition,
    )


def _dynamic_goal_action_contract(action: ActionDefinitionV2) -> dict[str, object]:
    """Return the single canonical invocation contract consumed by Resolver stages."""

    return canonical_action_invocation_contract(action)


def _operation_lock_failure_is_system(code: str) -> bool:
    """Classify lock validation failures that indicate infrastructure drift.

    Contract/publicity failures are player Goal semantics and remain typed
    ``UNSUPPORTED``.  Provider/schema and exact-version/invariant failures
    must use the existing system-error path instead of looking like a rejected
    Goal.
    """

    if code in {
        "FROZEN_EVIDENCE_CONFLICT",
        "OPERATION_LOCK_MISMATCH",
        "MODEL_PROVIDER_RESPONSE_INVALID",
        "PROVIDER_SCHEMA_INVALID",
        "MODEL_PROVIDER_FAILURE",
    }:
        return True
    return code.startswith(("MODEL_PROVIDER_", "PROVIDER_", "INTERNAL_", "RUNTIME_"))


def _contract_driven_operation_context(
    definition: ScenarioDefinitionV2,
    action_contract: dict[str, object],
    references: tuple[dict[str, object], ...],
    public_topology: dict[str, object],
    candidate_refs: tuple[DynamicGoalCandidateReference, ...],
) -> tuple[tuple[dict[str, object], ...], dict[str, object]]:
    """Shrink grounding context only from the selected Action's declared types."""

    target = cast(dict[str, object], action_contract["target"])
    bindings = cast(list[dict[str, object]], action_contract["bindings"])
    parameters = cast(list[dict[str, object]], action_contract["parameters"])
    expected_types = {
        str(target["expected_type"]),
        *(str(item["expected_type"]) for item in bindings),
        *(str(item["expected_type"]) for item in parameters),
    }
    allowed_ref_types = {
        ref_type
        for expected_type in expected_types
        if (ref_type := _slot_expected_reference_type(expected_type)) is not None
    }
    exact_actor_requested = any(
        item.provenance == "EXACT_USER_MENTION" and item.ref_type == "ACTOR"
        for item in candidate_refs
    )
    explicit_actor_contract = any(expected_type == "ACTOR" for expected_type in expected_types)
    if exact_actor_requested or explicit_actor_contract:
        allowed_ref_types.add("ACTOR")

    slot_type_counts: dict[str, int] = {}
    for expected_type in (
        str(target["expected_type"]),
        *(str(item["expected_type"]) for item in bindings),
        *(str(item["expected_type"]) for item in parameters),
    ):
        ref_type = _slot_expected_reference_type(expected_type)
        if ref_type is not None:
            slot_type_counts[ref_type] = slot_type_counts.get(ref_type, 0) + 1
    exact_keys_by_type: dict[str, set[str]] = {}
    for item in candidate_refs:
        if item.provenance == "EXACT_USER_MENTION":
            exact_keys_by_type.setdefault(item.ref_type, set()).add(item.key)

    filtered: list[dict[str, object]] = []
    target_type = str(target["expected_type"])
    for raw_reference in references:
        raw_ref_type = raw_reference.get("ref_type")
        key = raw_reference.get("key")
        if not isinstance(raw_ref_type, str) or raw_ref_type not in allowed_ref_types:
            continue
        if not isinstance(key, str):
            continue
        exact_keys = exact_keys_by_type.get(raw_ref_type, set())
        if (
            exact_keys
            and len(exact_keys) >= slot_type_counts.get(raw_ref_type, 0)
            and key not in exact_keys
        ):
            continue
        if raw_ref_type == "NODE" and target_type in {"NODE", "FACILITY"}:
            candidate = DynamicGoalCandidateReference(ref_type="NODE", key=key, provenance="OTHER")
            if not _reference_matches_operation_type(definition, target, candidate):
                continue
        filtered.append(raw_reference)

    exact_regions = {
        item.key
        for item in candidate_refs
        if item.provenance == "EXACT_USER_MENTION" and item.ref_type == "REGION"
    }
    if target_type not in {"NODE", "FACILITY"} or not exact_regions:
        return tuple(filtered), {"relations": [], "transport_endpoint_pairs": []}

    allowed_node_keys = {
        str(item["key"])
        for item in filtered
        if item.get("ref_type") == "NODE" and isinstance(item.get("key"), str)
    }
    raw_pairs = public_topology.get("transport_endpoint_pairs", ())
    pairs = (
        [
            item
            for item in raw_pairs
            if isinstance(item, dict)
            and item.get("entity_key") in allowed_node_keys
            and isinstance(item.get("endpoint_region_keys"), (list, tuple))
            and exact_regions.issubset(set(item["endpoint_region_keys"]))
        ]
        if isinstance(raw_pairs, (list, tuple))
        else []
    )
    topology_node_keys = {
        str(item["entity_key"]) for item in pairs if isinstance(item.get("entity_key"), str)
    }
    raw_relations = public_topology.get("relations", ())
    relations = (
        [
            item
            for item in raw_relations
            if isinstance(item, dict)
            and item.get("source_node_key") in topology_node_keys
            and item.get("target_node_key") in exact_regions
        ]
        if isinstance(raw_relations, (list, tuple))
        else []
    )
    return tuple(filtered), {
        "relations": relations,
        "transport_endpoint_pairs": pairs,
    }


def _operation_intent_candidate_refs(
    intent: OperationIntentDraft,
) -> tuple[DynamicGoalCandidateReference, ...]:
    slots = (intent.actor, intent.target, *intent.bindings, *intent.parameters)
    refs = [
        DynamicGoalCandidateReference(
            ref_type=slot.ref_type,
            key=slot.key,
            provenance="LLM_SUPPLEMENTED",
        )
        for slot in slots
        if slot.status == "GROUNDED" and slot.ref_type is not None and slot.key is not None
    ]
    unique = {(item.ref_type, item.key): item for item in refs}
    return tuple(unique[key] for key in sorted(unique))


def _slot_expected_reference_type(expected_type: str) -> str | None:
    if expected_type in {"NODE", "FACILITY"}:
        return "NODE"
    if expected_type in {"REGION", "RESOURCE", "ACTOR"}:
        return expected_type
    return None


def _validate_contract_slot_shape(
    slot: OperationContractSlot,
    *,
    slot_key: str,
    expected_type: str,
) -> None:
    if slot.slot_key != slot_key or slot.expected_type != expected_type:
        raise FormalGoalError(
            "CONTRACT_SCHEMA_MISMATCH",
            "Operation grounding changed an Action-declared slot",
            details={
                "slot": slot_key,
                "expected_type": expected_type,
                "actual_slot": slot.slot_key,
                "actual_type": slot.expected_type,
            },
        )
    if slot.status != "GROUNDED":
        return
    expected_ref = _slot_expected_reference_type(expected_type)
    if expected_ref is not None:
        compatible_ref_types = {"NODE", "REGION"} if expected_type == "NODE" else {expected_ref}
        if slot.ref_type not in compatible_ref_types or slot.key is None:
            raise FormalGoalError(
                "BINDING_TYPE_INVALID",
                "A reference slot does not match its Action contract",
                details={
                    "slot": slot_key,
                    "expected": expected_type,
                    "actual": slot.ref_type,
                },
            )
        return
    value = slot.value
    valid = (
        (expected_type == "INTEGER" and isinstance(value, int) and not isinstance(value, bool))
        or (expected_type == "BOOLEAN" and isinstance(value, bool))
        or (expected_type in {"STRING", "ENUM"} and isinstance(value, str))
    )
    if not valid:
        raise FormalGoalError(
            "PARAMETER_TYPE_INVALID",
            "A parameter value does not match its Action contract",
            details={
                "slot": slot_key,
                "expected": expected_type,
                "actual": type(value).__name__,
            },
        )


def _reference_matches_operation_type(
    definition: ScenarioDefinitionV2,
    slot_contract: dict[str, object],
    reference: DynamicGoalCandidateReference,
) -> bool:
    expected_type = str(slot_contract["expected_type"])
    expected_ref = _slot_expected_reference_type(expected_type)
    compatible_ref_types = {"NODE", "REGION"} if expected_type == "NODE" else {expected_ref}
    if reference.ref_type not in compatible_ref_types:
        return False
    if expected_type in {"NODE", "FACILITY", "REGION"}:
        node = definition.world.node(reference.key)
        if node is None:
            return False
        if expected_type == "FACILITY":
            facility_type = definition.metadata.locality.facility_node_type_key
            return facility_type is not None and node.node_type_key == facility_type
        if expected_type == "REGION":
            region_type = definition.metadata.locality.region_node_type_key
            return region_type is not None and node.node_type_key == region_type
        return not slot_contract.get("node_type_keys") or node.node_type_key in cast(
            list[object], slot_contract["node_type_keys"]
        )
    return True


def _operation_target_from_public_topology(
    target_contract: dict[str, object],
    candidate_refs: tuple[DynamicGoalCandidateReference, ...],
    public_topology: dict[str, object],
) -> str | None:
    """Compose a node target from grounded regions and public topology metadata."""

    if target_contract.get("expected_type") != "NODE" or not target_contract.get("node_type_keys"):
        return None
    region_keys = {item.key for item in candidate_refs if item.ref_type == "REGION"}
    pairs = public_topology.get("transport_endpoint_pairs", ())
    if len(region_keys) < 2 or not isinstance(pairs, (list, tuple)):
        return None
    matches: list[str] = []
    for item in pairs:
        if not isinstance(item, dict):
            continue
        entity_key = item.get("entity_key")
        endpoints = item.get("endpoint_region_keys")
        if (
            isinstance(entity_key, str)
            and isinstance(endpoints, (list, tuple))
            and set(endpoints) == region_keys
        ):
            matches.append(entity_key)
    unique = tuple(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else None


def _vnext_role_contract_specs(
    action_contract: dict[str, object],
    role: Literal["actor", "source", "target", "resource", "amount"],
) -> tuple[dict[str, object], ...]:
    """Return Action-contract slots that can own one semantic role."""

    if role in {"actor", "target"}:
        raw = action_contract.get(role)
        return (cast(dict[str, object], raw),) if isinstance(raw, dict) else ()
    raw_bindings = action_contract.get("bindings", ())
    raw_parameters = action_contract.get("parameters", ())
    bindings = (
        tuple(item for item in raw_bindings if isinstance(item, dict))
        if isinstance(raw_bindings, (list, tuple))
        else ()
    )
    parameters = (
        tuple(item for item in raw_parameters if isinstance(item, dict))
        if isinstance(raw_parameters, (list, tuple))
        else ()
    )
    if role == "source":
        return tuple(
            item
            for item in (*bindings, *parameters)
            if item.get("logical_role") == "source"
        )
    if role == "resource":
        return tuple(
            item
            for item in parameters
            if item.get("semantic_reference_type") == "RESOURCE"
        )
    return tuple(
        item
        for item in parameters
        if item.get("scalar_value_type") == "INTEGER"
        and item.get("semantic_reference_type") is None
    )


def _vnext_operation_slot_for_spec(
    intent: OperationIntentDraft,
    spec: dict[str, object],
) -> OperationContractSlot | None:
    slot_key = spec.get("slot_key")
    if not isinstance(slot_key, str):
        return None
    if spec.get("storage_channel") == "actor":
        return intent.actor
    if spec.get("storage_channel") == "target":
        return intent.target
    if spec.get("storage_channel") == "binding":
        return next((item for item in intent.bindings if item.slot_key == slot_key), None)
    if spec.get("storage_channel") == "parameter":
        return next((item for item in intent.parameters if item.slot_key == slot_key), None)
    return None


def _vnext_operation_slot_from_frozen_role(
    definition: ScenarioDefinitionV2,
    role: Literal["actor", "source", "target", "resource", "amount"],
    frozen: DynamicGoalMentionSlot | DynamicGoalScalarMentionSlot,
    spec: dict[str, object],
) -> OperationContractSlot:
    """Serialize one frozen Stage-1 role into the Action contract shape."""

    slot_key = spec.get("slot_key")
    expected_type = spec.get("expected_type")
    if not isinstance(slot_key, str) or not isinstance(expected_type, str):
        raise FormalGoalError(
            "FROZEN_EVIDENCE_CONFLICT",
            "A frozen role has no valid Action-contract slot",
            details={"role": role},
        )
    if frozen.status == "GROUNDED":
        if role == "amount":
            if frozen.value is None or expected_type not in {
                "INTEGER",
                "STRING",
                "ENUM",
                "BOOLEAN",
            }:
                raise FormalGoalError(
                    "FROZEN_EVIDENCE_CONFLICT",
                    "A frozen scalar role is incompatible with its Action contract",
                    details={"role": role, "slot": slot_key},
                )
            return OperationContractSlot(
                slot_key=slot_key,
                expected_type=cast(Any, expected_type),
                status="GROUNDED",
                value=frozen.value,
                surface=frozen.surface,
            )
        if frozen.ref_type is None or frozen.key is None:
            raise FormalGoalError(
                "FROZEN_EVIDENCE_CONFLICT",
                "A frozen reference role has no canonical identity",
                details={"role": role, "slot": slot_key},
            )
        reference = DynamicGoalCandidateReference(
            ref_type=frozen.ref_type,
            key=frozen.key,
        )
        if role in {"target", "source"} and not _reference_matches_operation_type(
            definition, spec, reference
        ):
            raise FormalGoalError(
                "FROZEN_EVIDENCE_CONFLICT",
                "A frozen reference role is incompatible with its Action contract",
                details={
                    "role": role,
                    "slot": slot_key,
                    "expected_type": expected_type,
                    "ref_type": frozen.ref_type,
                    "key": frozen.key,
                },
            )
        expected_ref = _slot_expected_reference_type(expected_type)
        if expected_ref is not None and frozen.ref_type != expected_ref and not (
            expected_type in {"NODE", "FACILITY"} and frozen.ref_type == "REGION"
        ):
            raise FormalGoalError(
                "FROZEN_EVIDENCE_CONFLICT",
                "A frozen reference role changed its reference type",
                details={
                    "role": role,
                    "slot": slot_key,
                    "expected_ref_type": expected_ref,
                    "actual_ref_type": frozen.ref_type,
                },
            )
        return OperationContractSlot(
            slot_key=slot_key,
            expected_type=cast(Any, expected_type),
            status="GROUNDED",
            ref_type=cast(Any, frozen.ref_type),
            key=frozen.key,
            surface=frozen.surface,
        )
    if frozen.status == "UNRESOLVED":
        return OperationContractSlot(
            slot_key=slot_key,
            expected_type=cast(Any, expected_type),
            status="UNRESOLVED",
            surface=frozen.surface,
        )
    return OperationContractSlot(
        slot_key=slot_key,
        expected_type=cast(Any, expected_type),
        status="NOT_SPECIFIED",
    )


def _vnext_operation_slot_matches_frozen(
    slot: OperationContractSlot,
    expected: OperationContractSlot,
) -> bool:
    if expected.status != slot.status:
        return False
    if expected.status == "GROUNDED":
        if expected.ref_type is not None or expected.key is not None:
            return _vnext_operation_reference_slots_equivalent(slot, expected)
        return slot.value == expected.value and type(slot.value) is type(expected.value)
    return True


def _vnext_operation_reference_slots_equivalent(
    slot: OperationContractSlot,
    expected: OperationContractSlot,
) -> bool:
    """Compare frozen/reference identities using the owning contract type.

    A ``NODE`` Action slot may be represented by either a NODE or REGION
    reference when the canonical key is identical.  Other contract types do
    not inherit that compatibility; their reference type remains part of the
    frozen identity.
    """

    if (
        slot.key is None
        or expected.key is None
        or slot.ref_type is None
        or expected.ref_type is None
        or slot.key != expected.key
    ):
        return False
    if slot.ref_type == expected.ref_type:
        return True
    return (
        slot.expected_type == "NODE"
        and expected.expected_type == "NODE"
        and {slot.ref_type, expected.ref_type} <= {"NODE", "REGION"}
    )


def _vnext_operation_frozen_conflict(
    role: str,
    expected: OperationContractSlot,
    actual: OperationContractSlot | None,
) -> FormalGoalError:
    return FormalGoalError(
        "FROZEN_EVIDENCE_CONFLICT",
        "Operation grounding changed frozen explicit evidence",
        details={
            "role": role,
            "slot": expected.slot_key,
            "expected": expected.model_dump(mode="json"),
            "actual": actual.model_dump(mode="json") if actual is not None else None,
        },
    )


def _vnext_enforce_frozen_operation_intent(
    goal: str,
    definition: ScenarioDefinitionV2,
    action_contract: dict[str, object],
    intent: OperationIntentDraft,
    evidence: _FrozenDynamicGoalEvidence,
    public_catalog: dict[str, object] | None = None,
) -> OperationIntentDraft:
    """Apply the immutable Stage-1 role lock to an Operation response.

    The provider still returns a complete contract-shaped DTO for wire
    compatibility, but every frozen role is checked and then serialized from
    the frozen value. Deterministic lookup results are retrieval hints only and
    never fill or override a semantic role.
    """

    frozen_intent = evidence.frozen_intent
    if frozen_intent is None:
        return intent

    actor = intent.actor
    target = intent.target
    bindings = list(intent.bindings)
    parameters = list(intent.parameters)
    # Keep identities already owned by explicit Actor/source evidence out of
    # the omitted-target promotion path below.  A single public identity can
    # legitimately appear in several catalog views, but an omitted role must
    # never be filled merely because another role happened to name the same
    # identity.
    assigned_semantic: set[tuple[str, str]] = {
        (slot.ref_type, slot.key)
        for slot in (frozen_intent.actor, frozen_intent.source)
        if slot.status == "GROUNDED" and slot.ref_type is not None and slot.key is not None
    }
    explicit_semantic_identities = {
        (item.ref_type, item.key)
        for item in (
            *evidence.deterministic_exact_refs,
            *evidence.semantic_refs,
        )
        if item.provenance == "EXACT_USER_MENTION"
        or (
            item.provenance != "TOPOLOGY_ENRICHED"
            and (
                item.match_semantics == "EXACT_OR_AUTHORED"
                or _vnext_reference_is_named_in_goal(goal, item, public_catalog)
            )
        )
    }
    explicit_semantic_identities.update(
        (slot.ref_type, slot.key)
        for slot in (
            frozen_intent.actor,
            frozen_intent.source,
            frozen_intent.target,
            frozen_intent.resource,
        )
        if slot.status == "GROUNDED" and slot.ref_type is not None and slot.key is not None
    )

    def replace(spec: dict[str, object], slot: OperationContractSlot) -> None:
        nonlocal actor, target
        channel = spec.get("storage_channel")
        key = spec.get("slot_key")
        if channel == "actor":
            actor = slot
        elif channel == "target":
            target = slot
        elif channel == "binding" and isinstance(key, str):
            bindings[:] = [item for item in bindings if item.slot_key != key]
            bindings.append(slot)
        elif channel == "parameter" and isinstance(key, str):
            parameters[:] = [item for item in parameters if item.slot_key != key]
            parameters.append(slot)

    def current(spec: dict[str, object]) -> OperationContractSlot | None:
        probe = OperationIntentDraft(
            action_key=intent.action_key,
            actor=actor,
            target=target,
            bindings=tuple(bindings),
            parameters=tuple(parameters),
        )
        return _vnext_operation_slot_for_spec(probe, spec)

    role_slots: tuple[
        tuple[
            Literal["actor", "source", "target", "resource", "amount"],
            DynamicGoalMentionSlot | DynamicGoalScalarMentionSlot | None,
        ],
        ...,
    ] = (
        ("actor", frozen_intent.actor if frozen_intent is not None else None),
        ("target", frozen_intent.target if frozen_intent is not None else None),
        ("source", frozen_intent.source if frozen_intent is not None else None),
        ("resource", frozen_intent.resource if frozen_intent is not None else None),
        ("amount", frozen_intent.amount if frozen_intent is not None else None),
    )
    for role, frozen in role_slots:
        specs = _vnext_role_contract_specs(action_contract, role)
        if frozen is None:
            continue
        if not specs:
            if frozen.status == "UNRESOLVED":
                # The selected Action has no contract slot for this semantic
                # noun (for example, ``resources`` in survey_resources).
                # It is contextual language, not a clarification target.
                continue
            if frozen.status != "NOT_SPECIFIED":
                raise FormalGoalError(
                    "FROZEN_EVIDENCE_CONFLICT",
                    "An explicit frozen role is not representable by the selected Action",
                    details={"role": role, "status": frozen.status},
                )
            continue
        if len(specs) != 1:
            raise FormalGoalError(
                "FROZEN_EVIDENCE_CONFLICT",
                "A frozen role maps to multiple Action-contract slots",
                details={"role": role, "slot_count": len(specs)},
            )
        spec = specs[0]
        actual = current(spec)
        expected = _vnext_operation_slot_from_frozen_role(definition, role, frozen, spec)
        if (
            frozen.status == "GROUNDED"
            and actual is not None
            and actual.status in {"NOT_SPECIFIED", "UNRESOLVED"}
        ):
            # A downstream omission/downgrade cannot erase the immutable
            # value.  Restore it locally; only a competing grounded identity
            # is an output-contract conflict that needs provider recovery.
            replace(spec, expected)
            continue
        if (
            frozen.status == "NOT_SPECIFIED"
            and role not in {"actor", "source"}
            and actual is not None
            and actual.status == "GROUNDED"
            and actual.ref_type is not None
            and actual.key is not None
            and (actual.ref_type, actual.key) in explicit_semantic_identities
            and (actual.ref_type, actual.key) not in assigned_semantic
        ):
            # A semantic candidate may fill an omitted non-actor/source slot
            # only when the candidate itself is explicit evidence (deterministic
            # exact, authored exact, a named public identity, or a frozen
            # grounded role). Contextual/topology candidates are never promoted
            # to a Goal constraint. Actor/source remain Planner-owned unless
            # their own frozen role evidence is explicit; this prevents a target
            # identity from accidentally becoming an omitted source.
            replace(spec, actual)
            assigned_semantic.add((actual.ref_type, actual.key))
            continue
        if (
            frozen.status == "NOT_SPECIFIED"
            and role == "amount"
            and actual is not None
            and actual.status == "GROUNDED"
            and _vnext_scalar_value_is_explicit(goal, actual.value)
        ):
            replace(spec, actual)
            continue
        if frozen.status == "NOT_SPECIFIED":
            # NOT_SPECIFIED is an intentional absence of a player
            # constraint.  Operation output cannot promote it to a Goal
            # value; discard that suggestion and leave the runtime/planner
            # choice unbound.
            replace(spec, expected)
            continue
        if actual is None or not _vnext_operation_slot_matches_frozen(actual, expected):
            raise _vnext_operation_frozen_conflict(role, expected, actual)
        replace(spec, expected)

    return OperationIntentDraft(
        frozen_family="OPERATION",
        action_key=intent.action_key,
        actor=actor,
        target=target,
        bindings=tuple(sorted(bindings, key=lambda item: item.slot_key)),
        parameters=tuple(sorted(parameters, key=lambda item: item.slot_key)),
    )


def _vnext_frozen_role(
    evidence: _FrozenDynamicGoalEvidence | None,
    role: Literal["actor", "source", "target", "resource", "amount"],
) -> DynamicGoalMentionSlot | DynamicGoalScalarMentionSlot | None:
    if evidence is None or evidence.frozen_intent is None:
        return None
    return cast(
        DynamicGoalMentionSlot | DynamicGoalScalarMentionSlot,
        getattr(evidence.frozen_intent, role),
    )


def _vnext_clarification_field_terms(
    action_contract: dict[str, object],
) -> dict[str, set[str]]:
    """Build conservative prompt terms from the selected Action contract."""

    terms: dict[str, set[str]] = {
        "actor": {"actor", "performer", "team", "agent"},
        "target": {"target", "destination", "dest", "where", "to"},
        "source": {"source", "origin", "from"},
        "resource": {"resource", "material", "item", "cargo"},
        "amount": {"amount", "quantity", "count", "number", "how many"},
        "action": {"action", "operation", "verb"},
    }
    raw_bindings = action_contract.get("bindings", ())
    raw_parameters = action_contract.get("parameters", ())
    slot_groups = (
        raw_bindings if isinstance(raw_bindings, (list, tuple)) else (),
        raw_parameters if isinstance(raw_parameters, (list, tuple)) else (),
    )
    specs = tuple(item for group in slot_groups for item in group if isinstance(item, dict))
    for spec in specs:
        key = spec.get("slot_key")
        if not isinstance(key, str):
            continue
        field = key.casefold()
        logical = spec.get("logical_role")
        semantic = spec.get("semantic_reference_type")
        if logical == "source":
            terms.setdefault("source", set()).add(field)
        elif semantic == "RESOURCE":
            terms.setdefault("resource", set()).add(field)
        elif spec.get("scalar_value_type") == "INTEGER":
            terms.setdefault("amount", set()).add(field)
        else:
            terms.setdefault(field, set()).add(field)
        name = spec.get("name")
        if isinstance(name, str) and name.strip():
            terms.setdefault(field, set()).add(name.casefold())
    return terms


def _vnext_prompt_mentioned_fields(
    prompt: str,
    action_contract: dict[str, object],
) -> set[str]:
    normalized = prompt.casefold()
    terms = _vnext_clarification_field_terms(action_contract)
    mentioned: set[str] = set()
    for field, values in terms.items():
        if any(term and term in normalized for term in values):
            mentioned.add(field)
    return mentioned


def _vnext_allowed_clarification_fields(
    action_contract: dict[str, object],
    evidence: _FrozenDynamicGoalEvidence | None,
) -> tuple[str, ...]:
    """Return only explicit unresolved or contract ``goal_required`` fields."""

    allowed: set[str] = set()
    role_to_field = {
        "actor": "actor",
        "target": "target",
        "source": "source",
        "resource": "resource",
        "amount": "amount",
    }
    for role, field in role_to_field.items():
        frozen = _vnext_frozen_role(evidence, cast(Any, role))
        if frozen is None or frozen.status != "UNRESOLVED":
            continue
        specs = _vnext_role_contract_specs(
            action_contract, cast(Any, role)
        )
        if len(specs) == 1:
            key = specs[0].get("slot_key")
            allowed.add(str(key) if isinstance(key, str) else field)

    raw_actor = action_contract.get("actor")
    raw_target = action_contract.get("target")
    raw_bindings = action_contract.get("bindings", ())
    raw_parameters = action_contract.get("parameters", ())
    slot_groups = (
        (raw_actor,) if isinstance(raw_actor, dict) else (),
        (raw_target,) if isinstance(raw_target, dict) else (),
        raw_bindings if isinstance(raw_bindings, (list, tuple)) else (),
        raw_parameters if isinstance(raw_parameters, (list, tuple)) else (),
    )
    contract_slots = tuple(
        item for group in slot_groups for item in group if isinstance(item, dict)
    )
    frozen_by_slot: dict[str, str] = {}
    for role, _field in role_to_field.items():
        frozen = _vnext_frozen_role(evidence, cast(Any, role))
        if frozen is None:
            continue
        specs = _vnext_role_contract_specs(action_contract, cast(Any, role))
        if len(specs) == 1 and isinstance(specs[0].get("slot_key"), str):
            frozen_by_slot[str(specs[0]["slot_key"])] = frozen.status
    for spec in contract_slots:
        key = spec.get("slot_key")
        if not isinstance(key, str) or spec.get("goal_required") is not True:
            continue
        if frozen_by_slot.get(key) == "GROUNDED":
            continue
        allowed.add(key)
    return tuple(sorted(allowed))


def _vnext_template_clarification_prompt(fields: tuple[str, ...]) -> str:
    if not fields:
        return "Please provide the missing information required by the selected Action."
    labels = ", ".join(fields)
    return f"Please specify: {labels}."


def _vnext_sanitize_clarification_prompt(
    prompt: str | None,
    allowed_fields: tuple[str, ...],
    action_contract: dict[str, object],
) -> str:
    """Keep provider prose only when it stays inside the contract boundary."""

    if prompt is None or not prompt.strip():
        return _vnext_template_clarification_prompt(allowed_fields)
    mentioned = _vnext_prompt_mentioned_fields(prompt, action_contract)
    allowed = set(allowed_fields)
    if mentioned and not mentioned.issubset(allowed):
        return _vnext_template_clarification_prompt(allowed_fields)
    return prompt


def _vnext_blank_operation_intent(
    action: ActionDefinitionV2,
) -> OperationIntentDraft:
    contract = _dynamic_goal_action_contract(action)
    target = cast(dict[str, object], contract["target"])
    raw_bindings = contract.get("bindings", ())
    raw_parameters = contract.get("parameters", ())
    bindings = tuple(
        OperationContractSlot(
            slot_key=cast(str, item["slot_key"]),
            expected_type=cast(Any, item["expected_type"]),
            status="NOT_SPECIFIED",
        )
        for item in (raw_bindings if isinstance(raw_bindings, (list, tuple)) else ())
        if isinstance(item, dict)
    )
    parameters = tuple(
        OperationContractSlot(
            slot_key=cast(str, item["slot_key"]),
            expected_type=cast(Any, item["expected_type"]),
            status="NOT_SPECIFIED",
        )
        for item in (raw_parameters if isinstance(raw_parameters, (list, tuple)) else ())
        if isinstance(item, dict)
    )
    return OperationIntentDraft(
        frozen_family="OPERATION",
        action_key=action.key,
        actor=OperationContractSlot(
            slot_key="actor",
            expected_type="ACTOR",
            status="NOT_SPECIFIED",
        ),
        target=OperationContractSlot(
            slot_key="target",
            expected_type=cast(Any, target["expected_type"]),
            status="NOT_SPECIFIED",
        ),
        bindings=bindings,
        parameters=parameters,
    )


def _vnext_synthetic_operation_intent(
    goal: str,
    definition: ScenarioDefinitionV2,
    action: ActionDefinitionV2,
    evidence: _FrozenDynamicGoalEvidence,
    public_catalog: dict[str, object] | None = None,
) -> OperationIntentDraft:
    """Construct a contract-shaped intent when clarification has no legal field.

    This is deliberately limited to the case where all player-visible
    requirements are already frozen.  It handles semantic nouns such as
    ``resources`` for Actions whose contract has no Resource slot without
    inventing a new constraint.
    """

    blank = _vnext_blank_operation_intent(action)
    return _vnext_enforce_frozen_operation_intent(
        goal,
        definition,
        _dynamic_goal_action_contract(action),
        blank,
        evidence,
        public_catalog,
    )


def _validate_exact_operation_identities(
    intent: OperationIntentDraft,
    deterministic_refs: tuple[DynamicGoalCandidateReference, ...],
    *,
    target_ref_type: str | None,
    target_key: str | None,
    topology_region_keys_consumed: bool,
) -> None:
    grounded: set[tuple[str, str]] = {
        (slot.ref_type, slot.key)
        for slot in (intent.actor, *intent.bindings, *intent.parameters)
        if slot.status == "GROUNDED" and slot.ref_type is not None and slot.key is not None
    }
    if target_ref_type is not None and target_key is not None:
        grounded.add((target_ref_type, target_key))
    missing = [
        item
        for item in deterministic_refs
        if item.provenance == "EXACT_USER_MENTION"
        and item.ref_type != "ACTION"
        and (item.ref_type, item.key) not in grounded
        and not (topology_region_keys_consumed and item.ref_type == "REGION")
    ]
    if missing:
        raise FormalGoalError(
            "CANONICAL_IDENTITY_CONFLICT",
            "Semantic grounding replaced an exact canonical identity",
            details={"missing_exact_refs": [item.model_dump(mode="json") for item in missing]},
        )


def _compose_contract_driven_operation(
    definition: ScenarioDefinitionV2,
    action: ActionDefinitionV2,
    intent: OperationIntentDraft,
    candidate_refs: tuple[DynamicGoalCandidateReference, ...],
    deterministic_refs: tuple[DynamicGoalCandidateReference, ...],
    public_topology: dict[str, object],
) -> DynamicGoalGroundedOperation:
    if intent.frozen_family != "OPERATION" or intent.action_key != action.key:
        raise FormalGoalError(
            "CANONICAL_IDENTITY_CONFLICT",
            "Operation grounding changed the frozen family or Action",
        )
    contract = _dynamic_goal_action_contract(action)
    target_contract = cast(dict[str, object], contract["target"])
    target_type = str(target_contract["expected_type"])
    _validate_contract_slot_shape(intent.actor, slot_key="actor", expected_type="ACTOR")
    _validate_contract_slot_shape(intent.target, slot_key="target", expected_type=target_type)
    binding_contracts = cast(list[dict[str, object]], contract["bindings"])
    parameter_contracts = cast(list[dict[str, object]], contract["parameters"])
    bindings = {item.slot_key: item for item in intent.bindings}
    parameters = {item.slot_key: item for item in intent.parameters}
    expected_bindings = {str(item["slot_key"]): item for item in binding_contracts}
    expected_parameters = {str(item["slot_key"]): item for item in parameter_contracts}
    if set(bindings) != set(expected_bindings) or set(parameters) != set(expected_parameters):
        raise FormalGoalError(
            "CONTRACT_SCHEMA_MISMATCH",
            "Operation grounding slots do not equal the selected Action contract",
            details={
                "expected_bindings": sorted(expected_bindings),
                "actual_bindings": sorted(bindings),
                "expected_parameters": sorted(expected_parameters),
                "actual_parameters": sorted(parameters),
            },
        )
    all_slots = (intent.actor, intent.target, *intent.bindings, *intent.parameters)
    unresolved = [item.slot_key for item in all_slots if item.status == "UNRESOLVED"]
    for key, spec in expected_bindings.items():
        _validate_contract_slot_shape(
            bindings[key], slot_key=key, expected_type=str(spec["expected_type"])
        )
    for key, spec in expected_parameters.items():
        slot = parameters[key]
        expected_type = str(spec["expected_type"])
        _validate_contract_slot_shape(slot, slot_key=key, expected_type=expected_type)
        if slot.status == "GROUNDED" and expected_type == "ENUM":
            allowed = spec.get("allowed_values", [])
            if not isinstance(allowed, (list, tuple)):
                raise FormalGoalError(
                    "CONTRACT_SCHEMA_MISMATCH",
                    "An ENUM Action contract has an invalid allowed-values domain",
                    details={"slot": key},
                )
            if slot.value not in allowed:
                raise FormalGoalError(
                    "PARAMETER_TYPE_INVALID",
                    "An ENUM parameter is outside its Action contract",
                    details={"slot": key, "expected": allowed, "actual": slot.value},
                )

    target_slot = intent.target
    target_key = target_slot.key if target_slot.status == "GROUNDED" else None
    topology_target_key = _operation_target_from_public_topology(
        target_contract,
        candidate_refs,
        public_topology,
    )
    topology_region_keys_consumed = (
        target_key is not None
        and topology_target_key is not None
        and target_key == topology_target_key
    )
    if target_slot.status == "UNRESOLVED":
        compatible = tuple(
            item.key
            for item in candidate_refs
            if _reference_matches_operation_type(definition, target_contract, item)
        )
        compatible = tuple(dict.fromkeys(compatible))
        if len(compatible) == 1:
            target_key = compatible[0]
            unresolved.remove("target")
            topology_region_keys_consumed = topology_target_key == target_key
        elif len(compatible) > 1:
            raise FormalGoalError(
                "TARGET_AMBIGUOUS",
                "More than one grounded identity satisfies the Action target contract",
                details={"candidate_keys": list(compatible)},
            )
        else:
            target_key = topology_target_key
            if target_key is not None:
                unresolved.remove("target")
                topology_region_keys_consumed = True
    if unresolved:
        raise FormalGoalError(
            "EXPLICIT_CONSTRAINT_UNRESOLVED",
            "An explicit player constraint could not be grounded uniquely",
            details={"slots": unresolved},
        )
    if target_key is not None:
        target_ref_type = (
            target_slot.ref_type
            if target_slot.status == "GROUNDED" and target_slot.key == target_key
            else _slot_expected_reference_type(target_type)
        )
        if target_ref_type is None:
            raise FormalGoalError(
                "CONTRACT_SCHEMA_MISMATCH",
                "Action target contract is not reference-valued",
                details={"expected": target_type},
            )
        target_ref = DynamicGoalCandidateReference(
            ref_type=target_ref_type,
            key=target_key,
        )
        if not _reference_matches_operation_type(definition, target_contract, target_ref):
            raise FormalGoalError(
                "BINDING_TYPE_INVALID",
                "The grounded target is incompatible with the Action target contract",
                details={"slot": "target", "expected": target_type, "actual": target_key},
            )
    _validate_exact_operation_identities(
        intent,
        deterministic_refs,
        target_ref_type=(
            target_slot.ref_type
            if target_slot.status == "GROUNDED" and target_slot.key == target_key
            else _slot_expected_reference_type(target_type)
        ),
        target_key=target_key,
        topology_region_keys_consumed=topology_region_keys_consumed,
    )

    binding_constraints = tuple(
        ActionInvocationBinding(role=key, value=slot.key)
        for key, slot in sorted(bindings.items())
        if slot.status == "GROUNDED" and slot.key is not None
    )
    parameter_constraints: dict[str, object] = {}
    for key, slot in sorted(parameters.items()):
        if slot.status != "GROUNDED":
            continue
        parameter_constraints[key] = slot.key if slot.key is not None else slot.value
    return DynamicGoalGroundedOperation(
        action_key=action.key,
        actor_key=(intent.actor.key if intent.actor.status == "GROUNDED" else None),
        target_key=target_key,
        binding_constraints=binding_constraints,
        parameter_constraints=parameter_constraints or None,
    )


def _validate_explicit_actor_action_compatibility(
    definition: ScenarioDefinitionV2,
    action: ActionDefinitionV2,
    operation: DynamicGoalGroundedOperation,
) -> None:
    """Reject only a player-selected Actor with a deterministic static conflict."""

    if operation.actor_key is None:
        return
    actor = next(
        (item for item in definition.actors.actor_profiles if item.key == operation.actor_key),
        None,
    )
    if actor is None:
        raise FormalGoalError(
            "EXPLICIT_ACTOR_ACTION_CONFLICT",
            "The explicit Actor is not part of the exact ScenarioVersion",
            details={"actor_key": operation.actor_key, "action_key": action.key},
        )
    role = next((item for item in definition.actors.roles if item.key == actor.role_key), None)
    actor_capabilities = {item.value for item in role.capabilities} if role is not None else set()
    required_capabilities = {item.value for item in action.allowed_actor_capabilities}
    conflicts: list[str] = []
    if action.key not in actor.allowed_action_keys:
        conflicts.append("ACTION_NOT_ALLOWED")
    required_actor_role = action.required_actor_role_for_target(operation.target_key)
    if required_actor_role is not None and actor.role_key != required_actor_role:
        conflicts.append("ROLE_MISMATCH")
    if not required_capabilities.issubset(actor_capabilities):
        conflicts.append("CAPABILITY_MISMATCH")
    if conflicts:
        raise FormalGoalError(
            "EXPLICIT_ACTOR_ACTION_CONFLICT",
            "The explicit Actor is statically incompatible with the frozen Action",
            details={
                "actor_key": actor.key,
                "action_key": action.key,
                "conflicts": conflicts,
            },
        )


def _validate_explicit_operation_relation(
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
    action_contract: dict[str, object],
    operation: DynamicGoalGroundedOperation,
) -> None:
    """Enforce an Action-declared public source-to-target relation generically."""

    raw_semantics = action_contract.get("relation_semantics")
    if not isinstance(raw_semantics, dict) or operation.target_key is None:
        return
    source_channel = raw_semantics.get("source_storage_channel")
    source_slot_key = raw_semantics.get("source_slot_key")
    relation_type_key = raw_semantics.get("source_relation_type_key")
    direction = raw_semantics.get("direction")
    if not all(
        isinstance(item, str)
        for item in (source_channel, source_slot_key, relation_type_key, direction)
    ):
        raise FormalGoalError(
            "CONTRACT_SCHEMA_MISMATCH",
            "Action relation semantics are incomplete",
        )
    source_key: object | None = None
    if source_channel == "binding":
        source_key = next(
            (item.value for item in operation.binding_constraints if item.role == source_slot_key),
            None,
        )
    elif source_channel == "parameter":
        if operation.parameter_constraints is not None:
            source_key = operation.parameter_constraints.get(str(source_slot_key))
    else:
        raise FormalGoalError(
            "CONTRACT_SCHEMA_MISMATCH",
            "Action relation semantics name an unsupported storage channel",
        )
    # A missing source/target remains a Planner choice.  Only two explicit
    # canonical identities create a Goal-time relation assertion.
    if not isinstance(source_key, str):
        return

    public_nodes, _facts, _regions = _dynamic_goal_public_keys(db, scope, definition)
    relations = _dynamic_goal_public_relations(db, scope, definition, public_nodes)
    if direction == "SOURCE_TO_TARGET":
        matches = any(
            item.get("source_node_key") == source_key
            and item.get("target_node_key") == operation.target_key
            and item.get("relation_type_key") == relation_type_key
            for item in relations
        )
    else:
        raise FormalGoalError(
            "CONTRACT_SCHEMA_MISMATCH",
            "Action relation semantics use an unsupported direction",
        )
    if not matches:
        raise FormalGoalError(
            "SOURCE_TARGET_RELATION_CONFLICT",
            "The explicit source and target violate the frozen Action relation contract",
            details={
                "relation_type_key": relation_type_key,
                "source_key": source_key,
                "target_key": operation.target_key,
            },
        )


def _dynamic_goal_intent_has_unresolved_slots(intent: DynamicGoalIntentDraft) -> bool:
    return any(
        slot.status == "UNRESOLVED"
        for slot in (
            intent.action,
            intent.actor,
            intent.source,
            intent.target,
            intent.resource,
            intent.amount,
        )
    )


def _dynamic_goal_action_parameter(
    action: ActionDefinitionV2,
    *,
    semantic: Literal["RESOURCE", "AMOUNT"],
) -> str | None:
    contract = canonical_action_invocation_contract(action)
    raw_parameters = contract.get("parameters")
    parameters = (
        tuple(item for item in raw_parameters if isinstance(item, dict))
        if isinstance(raw_parameters, (list, tuple))
        else ()
    )
    if semantic == "RESOURCE":
        matches = tuple(
            str(item["slot_key"])
            for item in parameters
            if item.get("semantic_reference_type") == "RESOURCE"
        )
    else:
        matches = tuple(
            str(item["slot_key"])
            for item in parameters
            if item.get("scalar_value_type") == "INTEGER"
            and item.get("semantic_reference_type") is None
        )
    return matches[0] if len(matches) == 1 else None


def _dynamic_goal_grounded_operation(
    goal: str,
    definition: ScenarioDefinitionV2,
    grounding: _DynamicGoalGrounding,
    intent: DynamicGoalIntentDraft,
) -> DynamicGoalGroundedOperation | None:
    """Build the exact lock from typed public intent provenance.

    The lock contains only slots that the player explicitly grounded.  It does
    not infer an Actor, source, target, route, or parameter value from planner
    context.  Action-defined binding contracts decide how an explicit source
    is represented; the resolver never looks for a literal ``source`` Action
    parameter.
    """

    del goal, grounding
    if intent.intent_kind != "OPERATION" or _dynamic_goal_intent_has_unresolved_slots(intent):
        return None
    if (
        intent.action.status != "GROUNDED"
        or intent.action.ref_type != "ACTION"
        or intent.action.key is None
    ):
        return None
    action = next((item for item in definition.actions if item.key == intent.action.key), None)
    if action is None:
        return None

    operation_contract = canonical_action_invocation_contract(action)
    raw_slots = operation_contract.get("slots")
    slots = (
        tuple(item for item in raw_slots if isinstance(item, dict))
        if isinstance(raw_slots, (list, tuple))
        else ()
    )
    binding_constraints: list[ActionInvocationBinding] = []
    parameter_constraints: dict[str, object] = {}
    if intent.source.status == "GROUNDED":
        if intent.source.ref_type not in {"NODE", "REGION", "ACTOR"} or intent.source.key is None:
            return None
        source_specs = tuple(
            item
            for item in slots
            if item.get("logical_role") == "source"
            and item.get("semantic_reference_type") == intent.source.ref_type
            and isinstance(item.get("slot_key"), str)
        )
        if len(source_specs) != 1:
            return None
        source_slot = source_specs[0]
        role = source_slot["slot_key"]
        assert isinstance(role, str)
        if source_slot.get("storage_channel") == "binding":
            binding_constraints.append(ActionInvocationBinding(role=role, value=intent.source.key))
        elif source_slot.get("storage_channel") == "parameter":
            parameter_constraints[role] = intent.source.key
        else:
            return None
    elif intent.source.status != "NOT_SPECIFIED":
        return None

    target_key: str | None = None
    if intent.target.status == "GROUNDED":
        if intent.target.ref_type not in {"NODE", "REGION", "ACTOR"} or intent.target.key is None:
            return None
        target = operation_contract.get("target")
        if target is not None and (
            not isinstance(target, dict)
            or target.get("field") != "target_key"
            or target.get("storage_channel") != "target"
            or target.get("semantic_reference_type") != intent.target.ref_type
        ):
            return None
        target_key = intent.target.key
    elif intent.target.status != "NOT_SPECIFIED":
        return None

    if intent.resource.status == "GROUNDED":
        if intent.resource.ref_type != "RESOURCE" or intent.resource.key is None:
            return None
        resource_parameter_key = _dynamic_goal_action_parameter(action, semantic="RESOURCE")
        if resource_parameter_key is None:
            return None
        parameter_constraints[resource_parameter_key] = intent.resource.key
    elif intent.resource.status != "NOT_SPECIFIED":
        return None
    if intent.amount.status == "GROUNDED":
        if not isinstance(intent.amount.value, int) or isinstance(intent.amount.value, bool):
            return None
        amount_parameter_key = _dynamic_goal_action_parameter(action, semantic="AMOUNT")
        if amount_parameter_key is None:
            return None
        parameter_constraints[amount_parameter_key] = intent.amount.value
    elif intent.amount.status != "NOT_SPECIFIED":
        return None

    actor_key: str | None = None
    if intent.actor.status == "GROUNDED":
        if intent.actor.ref_type != "ACTOR" or intent.actor.key is None:
            return None
        actor_key = intent.actor.key
    elif intent.actor.status != "NOT_SPECIFIED":
        return None

    return DynamicGoalGroundedOperation(
        action_key=action.key,
        actor_key=actor_key,
        target_key=target_key,
        binding_constraints=tuple(binding_constraints),
        parameter_constraints=(parameter_constraints or None),
    )


def _dynamic_goal_grounded_operation_candidate(
    operation: DynamicGoalGroundedOperation,
) -> AdHocActionCompletedRequirementCandidateV1:
    return AdHocActionCompletedRequirementCandidateV1(
        kind="ACTION_COMPLETED",
        action_key=operation.action_key,
        actor_key=operation.actor_key,
        target_key=operation.target_key,
        binding_constraints=operation.binding_constraints,
        parameter_constraints=operation.parameter_constraints,
    )


def _dynamic_goal_grounded_operation_feedback(
    operation: DynamicGoalGroundedOperation,
) -> DynamicGoalRecoveryFeedback:
    """Tell a retry to preserve the already grounded public operation."""

    return DynamicGoalRecoveryFeedback(
        requirement_index=0,
        kind="ACTION_COMPLETED",
        issue="INVALID_REQUIREMENT_SHAPE",
        field="action_key",
        expected_shape={
            "kind": "ACTION_COMPLETED",
            **operation.model_dump(mode="json"),
            "match_mode": "ONE_SUCCESSFUL_INVOCATION",
            "boundary": "TASK_OWNED_OPERATION",
        },
    )


def _dynamic_goal_matches_grounded_operation(
    candidates: AdHocGoalCandidateSetV2,
    locked_candidates: AdHocGoalCandidateSetV2,
) -> bool:
    return candidates.requirements == locked_candidates.requirements


def _validate_dynamic_goal_operation_lock(
    candidates: AdHocGoalCandidateSetV2,
    locked_candidates: AdHocGoalCandidateSetV2,
) -> None:
    """Reject every Stage-2 response that is not the exact operation lock."""

    if _dynamic_goal_matches_grounded_operation(candidates, locked_candidates):
        return
    expected_requirements = [
        item.model_dump(mode="json") for item in locked_candidates.requirements
    ]
    actual_requirements = [item.model_dump(mode="json") for item in candidates.requirements]
    raise FormalGoalError(
        "OPERATION_LOCK_MISMATCH",
        "The provider changed the exact public operation lock",
        details={
            "mismatch": "EXACT_OPERATION_LOCK",
            "expected": expected_requirements,
            "actual": actual_requirements,
            "unexpected_requirement_count": max(
                0,
                len(actual_requirements) - len(expected_requirements),
            ),
        },
    )


def _validate_dynamic_goal_lossless_operation_semantics(
    goal: str,
    definition: ScenarioDefinitionV2,
    grounding: _DynamicGoalGrounding,
    candidates: AdHocGoalCandidateSetV2,
    *,
    intent: DynamicGoalIntentDraft | None = None,
) -> None:
    """Reject a state downgrade when Stage 1 classifies the Goal as an operation."""

    del definition, goal, grounding
    operation_intent = intent is not None and intent.intent_kind == "OPERATION"
    if not operation_intent:
        return
    if any(
        isinstance(item, AdHocActionCompletedRequirementCandidateV1)
        for item in candidates.requirements
    ):
        return
    operation_keys = (
        [intent.action.key]
        if intent is not None
        and intent.action.status == "GROUNDED"
        and intent.action.ref_type == "ACTION"
        and intent.action.key is not None
        else []
    )
    if not operation_keys:
        return
    raise FormalGoalError(
        "FORMAL_GOAL_OPERATION_SEMANTICS_LOST",
        "The Goal names a concrete Action but the provider returned only a state requirement",
        details={"action_keys": operation_keys},
    )


def _dynamic_goal_topology_matches(
    region_keys: tuple[str, ...],
    definition: ScenarioDefinitionV2,
    public_nodes: set[str],
    public_regions: set[str],
    public_relations: tuple[dict[str, object], ...],
) -> tuple[str, ...]:
    locality = definition.metadata.locality
    if (
        not locality.enabled
        or locality.transport_node_type_key is None
        or locality.transport_endpoint_relation_type_key is None
        or len(region_keys) != 2
    ):
        return ()
    expected_regions = set(region_keys)
    matches: list[str] = []
    for node in sorted(definition.world.nodes, key=lambda item: item.key):
        if node.key not in public_nodes or node.node_type_key != locality.transport_node_type_key:
            continue
        endpoints = {
            str(item["target_node_key"])
            for item in public_relations
            if (
                item.get("source_node_key") == node.key
                and item.get("relation_type_key") == locality.transport_endpoint_relation_type_key
                and item.get("target_node_key") in public_regions
            )
        }
        if endpoints == expected_regions:
            matches.append(node.key)
    return tuple(matches)


def _dynamic_goal_grounding_from_refs(
    candidate_refs: tuple[DynamicGoalCandidateReference, ...],
    source: str,
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
) -> _DynamicGoalGrounding:
    public_nodes, _goal_addressable_facts, public_regions = _dynamic_goal_public_keys(
        db,
        scope,
        definition,
    )
    public_relations = _dynamic_goal_public_relations(
        db,
        scope,
        definition,
        public_nodes,
    )
    entity_keys = {item.key for item in candidate_refs if item.ref_type == "NODE"}
    explicit_region_keys = {item.key for item in candidate_refs if item.ref_type == "REGION"}
    scope_keys = {*entity_keys, *explicit_region_keys}
    locality = definition.metadata.locality
    endpoint_type = locality.transport_endpoint_relation_type_key
    if locality.enabled and endpoint_type is not None:
        scope_keys.update(
            str(item["target_node_key"])
            for item in public_relations
            if (
                item.get("source_node_key") in entity_keys
                and item.get("relation_type_key") == endpoint_type
                and item.get("target_node_key") in public_regions
            )
        )
    return _DynamicGoalGrounding(
        status="RESOLVED",
        candidate_refs=tuple(sorted(candidate_refs, key=lambda item: (item.ref_type, item.key))),
        entity_keys=tuple(sorted(entity_keys)),
        scope_keys=tuple(sorted(scope_keys)),
        source=source,
    )


def _dynamic_goal_grounding_from_keys(
    entity_keys: tuple[str, ...],
    source: str,
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
) -> _DynamicGoalGrounding:
    """Compatibility wrapper for callers that only have NODE keys."""

    return _dynamic_goal_grounding_from_refs(
        tuple(DynamicGoalCandidateReference(ref_type="NODE", key=key) for key in entity_keys),
        source,
        db,
        scope,
        definition,
    )


def _deterministic_dynamic_goal_grounding(
    goal: str,
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
) -> _DynamicGoalGrounding:
    """Ground public references using stable names, keys, and topology.

    This stage only returns public references.  It never chooses a typed Goal
    requirement or a target value, even when a reference name happens to be an
    authored Goal example.
    """

    public_nodes, _goal_addressable_facts, public_regions = _dynamic_goal_public_keys(
        db,
        scope,
        definition,
    )
    public_action_keys = _dynamic_goal_public_action_keys(db, scope, definition)
    public_actor_keys = _dynamic_goal_public_actor_keys(db, scope, definition)
    lookup = _dynamic_goal_public_reference_lookup(
        goal,
        definition,
        public_nodes,
        public_action_keys,
        public_actor_keys,
    )
    ambiguous_identities = {
        identity for match in lookup.ambiguous_matches for identity in match.identities
    }
    if ambiguous_identities:
        ambiguous_refs = tuple(
            DynamicGoalCandidateReference(
                ref_type=identity.ref_type.value,
                key=identity.ref_key,
                provenance="EXACT_USER_MENTION",
                match_semantics="EXACT_OR_AUTHORED",
            )
            for identity in sorted(
                ambiguous_identities,
                key=lambda item: (item.ref_type.value, item.ref_key),
            )
        )
        return _DynamicGoalGrounding(
            status="NEEDS_CLARIFICATION",
            candidate_refs=ambiguous_refs,
            source="DETERMINISTIC_PUBLIC_REFERENCE_AMBIGUOUS",
            clarification_prompt=definition.goal_resolution.clarification_prompt,
        )
    reference_matches = tuple(
        DynamicGoalCandidateReference(
            ref_type=identity.ref_type.value,
            key=identity.ref_key,
            provenance="EXACT_USER_MENTION",
            match_semantics="EXACT_OR_AUTHORED",
        )
        for identity in lookup.identities
    )
    direct_entity_matches = tuple(item.key for item in reference_matches if item.ref_type == "NODE")
    direct_region_matches = tuple(
        item.key for item in reference_matches if item.ref_type == "REGION"
    )

    mentioned_regions = direct_region_matches
    public_relations = _dynamic_goal_public_relations(
        db,
        scope,
        definition,
        public_nodes,
    )
    topology_matches = _dynamic_goal_topology_matches(
        mentioned_regions,
        definition,
        public_nodes,
        public_regions,
        public_relations,
    )
    non_node_matches = tuple(item for item in reference_matches if item.ref_type != "NODE")
    if not direct_entity_matches and topology_matches:
        topology_refs = tuple(
            DynamicGoalCandidateReference(ref_type="NODE", key=key, provenance="TOPOLOGY_ENRICHED")
            for key in topology_matches
        )
        return _dynamic_goal_grounding_from_refs(
            (*topology_refs, *non_node_matches),
            "DETERMINISTIC_PUBLIC_TOPOLOGY",
            db,
            scope,
            definition,
        )
    if reference_matches:
        source = (
            "DETERMINISTIC_ENTITY_GROUNDING"
            if direct_entity_matches or mentioned_regions
            else "DETERMINISTIC_PUBLIC_REFERENCE"
        )
        return _dynamic_goal_grounding_from_refs(
            reference_matches,
            source,
            db,
            scope,
            definition,
        )
    return _DynamicGoalGrounding(status="NONE")


def _llm_all_dynamic_goal_grounding(
    goal: str,
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
    provider: object,
) -> _DynamicGoalGrounding:
    """Ground the whole public Goal before canonical routing when supported.

    The canonical routing path uses this experimental whole-goal pass so the
    backend does not decide that an exact hit has completed entity grounding.
    Providers without the optional capability retain the compatibility path;
    production GenericProvider implements the capability and therefore sends
    an empty deterministic seed together with the full public catalog.
    """

    grounder = getattr(provider, "ground_dynamic_goal_entities", None)
    if not callable(grounder):
        return _deterministic_dynamic_goal_grounding(goal, db, scope, definition)

    request = DynamicGoalEntityGroundingRequest(
        goal=goal,
        public_catalog=_dynamic_goal_entity_catalog(db, scope, definition),
        deterministic_candidate_refs=(),
    )
    raw: object | None = None
    try:
        raw = grounder(request)
        grounded = DynamicGoalEntityGrounding.model_validate(raw)
    except GenericProviderError:
        raise
    except ValidationError as exc:
        raise GenericProviderError(
            "MODEL_PROVIDER_RESPONSE_INVALID",
            "The model provider returned an invalid whole-goal Entity Grounding",
            validation_diagnostics=provider_validation_diagnostics(exc),
        ) from exc
    except (TypeError, ValueError) as exc:
        raise GenericProviderError(
            "MODEL_PROVIDER_RESPONSE_INVALID",
            "The model provider returned an invalid whole-goal Entity Grounding",
        ) from exc

    if grounded.status != "RESOLVED":
        return _DynamicGoalGrounding(
            status=grounded.status,
            source="MODEL_ENTITY_GROUNDING",
            clarification_prompt=grounded.clarification_prompt,
        )

    candidate_refs = _merge_dynamic_goal_candidate_refs(
        (),
        (
            *(
                item
                for item in grounded.candidate_refs
                if item.match_semantics != "RELATED_ONLY"
            ),
            *_dynamic_goal_intent_candidate_refs(grounded.intent),
        ),
    )
    candidate_refs = _validate_dynamic_goal_candidate_refs(
        definition,
        db,
        scope,
        candidate_refs,
    )
    return _dynamic_goal_grounding_from_refs(
        candidate_refs,
        "MODEL_ENTITY_GROUNDING",
        db,
        scope,
        definition,
    )


def _vnext_explicit_role_evidence(
    intent: DynamicGoalIntentDraft | None,
) -> dict[str, object] | None:
    """Retain explicit WHAT evidence while discarding Stage-1 family/Action guesses."""

    if intent is None:
        return None
    return {
        "actor": intent.actor.model_dump(mode="json"),
        "source": intent.source.model_dump(mode="json"),
        "target": intent.target.model_dump(mode="json"),
        "resource": intent.resource.model_dump(mode="json"),
        "amount": intent.amount.model_dump(mode="json"),
    }


_EXPLICIT_ROLE_PROVENANCE = frozenset(
    {
        "EXPLICIT_USER_MENTION",
        "DETERMINISTIC_EXACT",
        "SEMANTIC_ROLE_EVIDENCE",
    }
)
_NON_EXPLICIT_ROLE_PROVENANCE = frozenset({"INFERRED", "NOT_SPECIFIED"})


def _vnext_public_reference_terms(
    public_catalog: dict[str, object] | None,
    *,
    ref_type: str | None,
    key: str | None,
) -> tuple[str, ...] | None:
    """Return public names/aliases for one candidate, if the catalog has it."""

    if public_catalog is None or not isinstance(ref_type, str) or not isinstance(key, str):
        return None
    references = public_catalog.get("references")
    if not isinstance(references, (list, tuple)):
        return None
    expected_type = "NODE" if ref_type == "FACILITY" else ref_type
    reference = next(
        (
            item
            for item in references
            if isinstance(item, dict)
            and item.get("ref_type") == expected_type
            and item.get("key") == key
        ),
        None,
    )
    if reference is None:
        return None
    terms: list[str] = [key]
    name = reference.get("name")
    if isinstance(name, str) and name.strip():
        terms.append(name)
    aliases = reference.get("public_references")
    if isinstance(aliases, (list, tuple)):
        terms.extend(item for item in aliases if isinstance(item, str) and item.strip())
    return tuple(dict.fromkeys(terms))


def _vnext_reference_is_named_in_goal(
    goal: str,
    reference: DynamicGoalCandidateReference,
    public_catalog: dict[str, object] | None,
) -> bool:
    terms = _vnext_public_reference_terms(
        public_catalog,
        ref_type=reference.ref_type,
        key=reference.key,
    )
    if not terms:
        return False
    normalized_goal = _normalize(goal)
    return any(_contains_public_term(normalized_goal, _normalize(term)) for term in terms)


def _vnext_surface_is_in_goal(goal: str, surface: object) -> bool:
    return isinstance(surface, str) and bool(surface.strip()) and _contains_public_term(
        _normalize(goal),
        _normalize(surface),
    )


def _vnext_surface_matches_public_identity(
    surface: object,
    *,
    ref_type: str | None,
    key: str | None,
    public_catalog: dict[str, object] | None,
) -> bool | None:
    """Prove or disprove that a player surface names the returned identity.

    ``None`` means no catalog proof is available (the compatibility path keeps
    accepting old providers that omitted role surfaces).
    """

    if not isinstance(surface, str) or not surface.strip():
        return None
    terms = _vnext_public_reference_terms(
        public_catalog,
        ref_type=ref_type,
        key=key,
    )
    if terms is None:
        return None
    normalized_surface = _normalize(surface)
    return any(
        normalized_surface == _normalize(term)
        for term in terms
    )


def _vnext_role_has_explicit_evidence(
    goal: str,
    slot: DynamicGoalMentionSlot | DynamicGoalScalarMentionSlot,
    deterministic_refs: tuple[DynamicGoalCandidateReference, ...],
    public_catalog: dict[str, object] | None = None,
) -> bool:
    """Determine whether a role was actually expressed by the player.

    In the production path (where a public catalog is present), provider
    provenance is untrusted input.  Only backend-provable deterministic
    identity, a canonical public term actually present in the Goal, or the
    raw Goal surface itself can establish explicit evidence.  The
    ``public_catalog is None`` branch is retained solely for old compatibility
    providers and unit fixtures.
    """

    provenance = getattr(slot, "provenance", None)
    strict_catalog_path = public_catalog is not None
    if not strict_catalog_path:
        if provenance in _EXPLICIT_ROLE_PROVENANCE:
            return True
        if provenance in _NON_EXPLICIT_ROLE_PROVENANCE:
            return False
    if _vnext_surface_is_in_goal(goal, getattr(slot, "surface", None)):
        return True
    key = getattr(slot, "key", None)
    ref_type = getattr(slot, "ref_type", None)
    if isinstance(key, str) and isinstance(ref_type, str):
        terms = _vnext_public_reference_terms(
            public_catalog,
            ref_type=ref_type,
            key=key,
        )
        if terms:
            normalized_goal = _normalize(goal)
            if any(
                _contains_public_term(normalized_goal, _normalize(term)) for term in terms
            ):
                return True
        if any(
            item.ref_type == ref_type
            and item.key == key
            and item.provenance == "EXACT_USER_MENTION"
            for item in deterministic_refs
        ):
            return True
    # Keep old unit/test providers usable when no public catalog is available.
    return slot.status == "GROUNDED" and not strict_catalog_path


def _vnext_semantic_grounding_recheck_feedback(
    goal: str,
    intent: DynamicGoalIntentDraft | None,
    deterministic_refs: tuple[DynamicGoalCandidateReference, ...],
    public_catalog: dict[str, object] | None,
) -> tuple[dict[str, object], ...]:
    """Build one bounded semantic recheck for an explicit entity role.

    Stage 1 remains the semantic owner. This feedback only asks it to revisit
    a role whose typed claim is internally inconsistent (an invalid exact
    claim) or whose explicit surface was left unresolved. It deliberately
    carries no replacement identity, so a related public candidate cannot be
    promoted by the backend.
    """

    if intent is None:
        return ()
    slots: tuple[tuple[str, DynamicGoalMentionSlot], ...] = (
        ("actor", intent.actor),
        ("source", intent.source),
        ("target", intent.target),
        ("resource", intent.resource),
    )
    invalid_exact: list[dict[str, object]] = []
    unresolved_surface: list[dict[str, object]] = []
    disputed_roles: set[str] = set()
    for role, slot in slots:
        if not _vnext_surface_is_in_goal(goal, slot.surface):
            continue
        if (
            slot.status == "GROUNDED"
            and slot.match_semantics == "EXACT_OR_AUTHORED"
            and _vnext_surface_matches_public_identity(
                slot.surface,
                ref_type=slot.ref_type,
                key=slot.key,
                public_catalog=public_catalog,
            )
            is False
        ):
            invalid_exact.append(
                {
                    "role": role,
                    "surface": slot.surface,
                    "candidate_ref_type": slot.ref_type,
                    "candidate_key": slot.key,
                    "claimed_match_semantics": slot.match_semantics,
                }
            )
            disputed_roles.add(role)
        elif slot.status == "UNRESOLVED":
            unresolved_surface.append(
                {
                    "role": role,
                    "surface": slot.surface,
                    "ref_type": slot.ref_type,
                }
            )
            disputed_roles.add(role)

    if not disputed_roles:
        return ()

    preserve: list[dict[str, object]] = []
    for role, slot in slots:
        if role in disputed_roles:
            if isinstance(slot.surface, str) and slot.surface.strip():
                preserve.append(
                    {
                        "path": f"intent.{role}.surface",
                        "surface": slot.surface[:400],
                    }
                )
            continue
        if slot.status != "GROUNDED" or slot.ref_type is None or slot.key is None:
            continue
        preserved: dict[str, object] = {
            "path": f"intent.{role}",
            "ref_type": slot.ref_type,
            "key": slot.key,
        }
        if slot.match_semantics is not None:
            preserved["match_semantics"] = slot.match_semantics
        if isinstance(slot.surface, str) and slot.surface.strip():
            preserved["surface"] = slot.surface[:400]
        preserve.append(preserved)
    disputed_identities = {
        (item.get("candidate_ref_type"), item.get("candidate_key"))
        for item in invalid_exact
    }
    for index, reference in enumerate(deterministic_refs):
        if reference.ref_type == "ACTION" or (
            reference.ref_type,
            reference.key,
        ) in disputed_identities:
            continue
        preserve.append(
            {
                "path": f"deterministic_candidate_refs[{index}]",
                "ref_type": reference.ref_type,
                "key": reference.key,
                "match_semantics": reference.match_semantics,
            }
        )

    code = (
        "ROLE_MATCH_SEMANTICS_RECHECK"
        if invalid_exact
        else "ROLE_SEMANTIC_GROUNDING_RECHECK"
    )
    return (
        {
            "code": code,
            "expected": (
                "Re-evaluate the named explicit role against the complete public catalog "
                "and raw player Goal; do not force the disputed canonical key."
            ),
            "disputed_roles": [*invalid_exact, *unresolved_surface],
            "preserve": preserve,
            "fix_only": [f"intent.{role}" for role in sorted(disputed_roles)],
        },
    )


def _vnext_trusted_role_provenance(
    goal: str,
    slot: DynamicGoalMentionSlot,
    deterministic_refs: tuple[DynamicGoalCandidateReference, ...],
    public_catalog: dict[str, object] | None,
) -> str | None:
    """Assign backend-owned provenance after role evidence has been checked."""

    if slot.status == "NOT_SPECIFIED":
        return "NOT_SPECIFIED"
    explicit_surface = _vnext_surface_is_in_goal(goal, slot.surface)
    deterministic_exact = any(
        item.ref_type == slot.ref_type
        and item.key == slot.key
        and item.provenance == "EXACT_USER_MENTION"
        for item in deterministic_refs
    )
    named_public_identity = bool(
        isinstance(slot.ref_type, str)
        and isinstance(slot.key, str)
        and _vnext_reference_is_named_in_goal(
            goal,
            DynamicGoalCandidateReference(ref_type=slot.ref_type, key=slot.key),
            public_catalog,
        )
    )
    if deterministic_exact or named_public_identity:
        return "DETERMINISTIC_EXACT"
    if slot.match_semantics == "SEMANTIC_EQUIVALENT" and explicit_surface:
        return "SEMANTIC_ROLE_EVIDENCE"
    if explicit_surface:
        return "EXPLICIT_USER_MENTION"
    if public_catalog is None and slot.provenance in {
        "EXPLICIT_USER_MENTION",
        "DETERMINISTIC_EXACT",
        "SEMANTIC_ROLE_EVIDENCE",
        "INFERRED",
        "NOT_SPECIFIED",
    }:
        return slot.provenance
    if slot.status == "UNRESOLVED":
        return "EXPLICIT_USER_MENTION"
    return None


def _vnext_normalize_role_slot(
    goal: str,
    slot: DynamicGoalMentionSlot,
    deterministic_refs: tuple[DynamicGoalCandidateReference, ...],
    public_catalog: dict[str, object] | None,
    *,
    check_surface_identity: bool = True,
) -> DynamicGoalMentionSlot:
    strict_catalog_path = public_catalog is not None
    explicit = _vnext_role_has_explicit_evidence(
        goal,
        slot,
        deterministic_refs,
        public_catalog,
    )
    semantics = slot.match_semantics
    # Action is a semantic operation hint, not a player-frozen HOW choice.
    # Keep a valid public Action hint even when the model did not copy its
    # literal name into ``surface``; all other roles require trusted evidence.
    action_hint = bool(
        slot.ref_type == "ACTION"
        and slot.status == "GROUNDED"
        and isinstance(slot.key, str)
        and semantics != "RELATED_ONLY"
        and _vnext_public_reference_terms(
            public_catalog,
            ref_type="ACTION",
            key=slot.key,
        )
    )
    if slot.status == "UNRESOLVED" and not explicit:
        return DynamicGoalMentionSlot(status="NOT_SPECIFIED", provenance="NOT_SPECIFIED")
    if slot.status == "GROUNDED":
        if semantics == "RELATED_ONLY":
            return (
                slot.model_copy(update={"status": "UNRESOLVED", "key": None})
                if explicit
                else DynamicGoalMentionSlot(status="NOT_SPECIFIED", provenance="NOT_SPECIFIED")
            )
        if semantics == "SEMANTIC_EQUIVALENT" and not explicit and not action_hint:
            return DynamicGoalMentionSlot(status="NOT_SPECIFIED", provenance="NOT_SPECIFIED")
        if strict_catalog_path and not explicit and not action_hint:
            return DynamicGoalMentionSlot(status="NOT_SPECIFIED", provenance="NOT_SPECIFIED")
        if (
            not action_hint
            and not strict_catalog_path
            and slot.provenance in _NON_EXPLICIT_ROLE_PROVENANCE
        ):
            return DynamicGoalMentionSlot(status="NOT_SPECIFIED", provenance="NOT_SPECIFIED")
        if (
            check_surface_identity
            # An explicit EXACT_OR_AUTHORED claim is safe to compare against
            # the public catalog.  Older VNext providers omitted the typed
            # match marker while still returning a semantically grounded
            # role (for example, "north depot" -> a public region).  Treating
            # an unmarked surface as an exact catalog name would demote those
            # valid semantic bindings and make downstream freeze recovery
            # spuriously re-run.  The typed RELATED_ONLY/SEMANTIC_EQUIVALENT
            # paths are handled above; an unmarked role remains on the
            # compatibility semantic-grounding path.
            and semantics == "EXACT_OR_AUTHORED"
            and _vnext_surface_is_in_goal(goal, slot.surface)
        ):
            deterministic_exact_identity = any(
                item.ref_type == slot.ref_type
                and item.key == slot.key
                and item.provenance == "EXACT_USER_MENTION"
                for item in deterministic_refs
            )
            identity_match = (
                True
                if deterministic_exact_identity
                else _vnext_surface_matches_public_identity(
                    slot.surface,
                    ref_type=slot.ref_type,
                    key=slot.key,
                    public_catalog=public_catalog,
                )
            )
            if identity_match is False:
                # The player named a real surface, but it does not name the
                # returned canonical object.  Keep the explicit mention for a
                # clarification instead of accepting a nearest substitute.
                return slot.model_copy(update={"status": "UNRESOLVED", "key": None})
    trusted_provenance = _vnext_trusted_role_provenance(
        goal,
        slot,
        deterministic_refs,
        public_catalog,
    )
    if trusted_provenance is not None:
        return slot.model_copy(update={"provenance": trusted_provenance})
    return slot.model_copy(update={"provenance": None})


def _vnext_unresolved_roles_without_evidence(
    goal: str,
    intent: DynamicGoalIntentDraft | None,
    deterministic_refs: tuple[DynamicGoalCandidateReference, ...],
    public_catalog: dict[str, object] | None,
) -> tuple[str, ...]:
    """Find explicit ``UNRESOLVED`` slots that still lack an identity.

    ``UNRESOLVED`` is a player-facing semantic distinction only when the
    provider carries an explicit surface/provenance signal.  A bare unresolved
    slot has no evidence that the player supplied that role and is normalized
    to ``NOT_SPECIFIED`` so the generic Goal-required gate can decide whether
    clarification is needed.  Explicit unresolved evidence still receives one
    bounded provider recovery before the normal provider-failure fallback.
    """

    if intent is None:
        return ()
    slots: tuple[
        tuple[str, DynamicGoalMentionSlot | DynamicGoalScalarMentionSlot], ...
    ] = (
        ("action", intent.action),
        ("actor", intent.actor),
        ("source", intent.source),
        ("target", intent.target),
        ("resource", intent.resource),
        ("amount", intent.amount),
    )
    return tuple(
        role
        for role, slot in slots
        if (
            (
                slot.status == "UNRESOLVED"
                or (
                    slot.status == "GROUNDED"
                    and getattr(slot, "match_semantics", None) == "SEMANTIC_EQUIVALENT"
                )
            )
            and not _vnext_role_has_explicit_evidence(
                goal,
                slot,
                deterministic_refs,
                public_catalog,
            )
            and (
                _vnext_surface_is_in_goal(goal, getattr(slot, "surface", None))
                or getattr(slot, "provenance", None) in _EXPLICIT_ROLE_PROVENANCE
            )
            and role != "action"
        )
    )


def _vnext_normalize_scalar_slot(
    goal: str,
    slot: DynamicGoalScalarMentionSlot,
    *,
    strict_catalog_path: bool = False,
) -> DynamicGoalScalarMentionSlot:
    provenance = slot.provenance
    explicit = (
        (not strict_catalog_path and provenance in _EXPLICIT_ROLE_PROVENANCE)
        or _vnext_surface_is_in_goal(goal, slot.surface)
    )
    if not explicit and slot.status == "GROUNDED":
        explicit = _vnext_scalar_value_is_explicit(goal, slot.value)
    if not strict_catalog_path and provenance in _NON_EXPLICIT_ROLE_PROVENANCE:
        explicit = False
    if slot.status == "UNRESOLVED" and not explicit:
        return DynamicGoalScalarMentionSlot(status="NOT_SPECIFIED", provenance="NOT_SPECIFIED")
    if slot.status == "GROUNDED" and not explicit:
        return DynamicGoalScalarMentionSlot(status="NOT_SPECIFIED", provenance="NOT_SPECIFIED")
    trusted = (
        "EXPLICIT_USER_MENTION"
        if _vnext_surface_is_in_goal(goal, slot.surface)
        or _vnext_scalar_value_is_explicit(goal, slot.value)
        else slot.provenance
        if not strict_catalog_path and slot.provenance in _EXPLICIT_ROLE_PROVENANCE
        else None
    )
    return slot.model_copy(update={"provenance": trusted})


def _vnext_rejected_role_identities(
    original: DynamicGoalIntentDraft | None,
    normalized: DynamicGoalIntentDraft | None,
) -> frozenset[tuple[str, str]]:
    """Return identities invalidated by role-evidence normalization."""

    if original is None or normalized is None:
        return frozenset()
    pairs: list[tuple[DynamicGoalMentionSlot, DynamicGoalMentionSlot]] = [
        (original.action, normalized.action),
        (original.actor, normalized.actor),
        (original.source, normalized.source),
        (original.target, normalized.target),
        (original.resource, normalized.resource),
    ]
    rejected = {
        (before.ref_type, before.key)
        for before, after in pairs
        if before.status == "GROUNDED"
        and before.ref_type is not None
        and before.key is not None
        and (
            after.status != "GROUNDED"
            or after.ref_type != before.ref_type
            or after.key != before.key
        )
    }
    return frozenset(rejected)


def _vnext_operation_terminal_facts(
    definition: ScenarioDefinitionV2,
    intent: DynamicGoalIntentDraft | None,
) -> tuple[tuple[str, str, StrictScalar | None], ...]:
    """Return authored terminal Fact effects for an explicit operation.

    This is deliberately a metadata check, not an inference engine.  It lets
    frozen STATE interpretation accept one direct operation-equivalent Fact
    while rejecting added consequences or an unrelated sibling state.
    """

    if intent is None or intent.intent_kind != "OPERATION":
        return ()
    if intent.action.status != "GROUNDED" or intent.action.key is None:
        return ()
    action = next((item for item in definition.actions if item.key == intent.action.key), None)
    if action is None:
        return ()
    target_key = intent.target.key if intent.target.status == "GROUNDED" else None
    return action_goal_terminal_effects(definition, action, target_key)


def _vnext_semantic_family_evidence(
    evidence: _FrozenDynamicGoalEvidence,
    definition: ScenarioDefinitionV2,
) -> DynamicGoalSemanticFamilyEvidence:
    """Build advisory operation/state-equivalence evidence for Family routing."""

    intent = evidence.frozen_intent
    if (
        intent is None
        or intent.intent_kind != "OPERATION"
        or intent.action.status != "GROUNDED"
        or intent.action.key is None
        or intent.action.match_semantics == "RELATED_ONLY"
    ):
        return DynamicGoalSemanticFamilyEvidence(
            operation_expressed=False,
            state_equivalent_available=None,
        )
    action = next((item for item in definition.actions if item.key == intent.action.key), None)
    if action is None:
        return DynamicGoalSemanticFamilyEvidence(
            operation_expressed=False,
            state_equivalent_available=None,
        )
    target_key = intent.target.key if intent.target.status == "GROUNDED" else None
    terminal_effects = action_goal_terminal_effects(definition, action, target_key)
    return DynamicGoalSemanticFamilyEvidence(
        operation_expressed=True,
        state_equivalent_available=bool(terminal_effects),
    )


def _vnext_semantic_action_evidence(
    goal: str,
    evidence: _FrozenDynamicGoalEvidence,
    public_action_keys: set[str],
    public_catalog: dict[str, object] | None = None,
) -> DynamicGoalSemanticActionEvidence | None:
    """Project one public Stage-1 Action hint for the Action Router.

    The hint is intentionally narrower than the frozen role evidence.  A
    provider's compatibility Action guess with no player surface or exact
    public mention is not treated as semantic evidence, while a typed
    non-contextual Action surface is retained as advisory input.
    """

    intent = evidence.frozen_intent
    if intent is None or intent.intent_kind != "OPERATION":
        return None
    action = intent.action
    if (
        action.status != "GROUNDED"
        or action.ref_type != "ACTION"
        or action.key is None
        or action.key not in public_action_keys
        or action.match_semantics == "RELATED_ONLY"
    ):
        return None
    surface_in_goal = _vnext_surface_is_in_goal(goal, action.surface)
    exact_mention = any(
        item.ref_type == "ACTION"
        and item.key == action.key
        and item.provenance == "EXACT_USER_MENTION"
        for item in evidence.deterministic_exact_refs
    )
    named_public_action = _vnext_reference_is_named_in_goal(
        goal,
        DynamicGoalCandidateReference(ref_type="ACTION", key=action.key),
        public_catalog,
    )
    # A semantic-equivalent match must carry a real player surface.  For an
    # unmarked compatibility response, require an independently observable
    # player mention so an arbitrary Stage-1 Action guess remains advisory-
    # absent rather than becoming accidental authority.
    if action.match_semantics == "SEMANTIC_EQUIVALENT" and not surface_in_goal:
        return None
    if action.match_semantics is None and not (
        surface_in_goal or exact_mention or named_public_action
    ):
        return None
    return DynamicGoalSemanticActionEvidence(
        action_key=action.key,
        surface=action.surface,
        match_semantics=action.match_semantics,
    )


def _validate_vnext_state_minimality(
    definition: ScenarioDefinitionV2,
    evidence: _FrozenDynamicGoalEvidence,
    candidates: AdHocGoalCandidateSetV2,
) -> None:
    """Enforce minimal terminal-WHAT semantics for an operation-as-STATE.

    Ordinary STATE Goals may contain multiple explicitly requested
    requirements.  The stricter rule applies only when Stage 1 supplied one
    explicit operation intent and the family router selected STATE: at most
    one requirement may represent that operation, and it must match one of the
    authored terminal Fact effects.
    """

    intent = evidence.frozen_intent
    if intent is None or intent.intent_kind != "OPERATION":
        return
    if intent.action.status != "GROUNDED" or intent.action.key is None:
        return
    effects = _vnext_operation_terminal_facts(definition, intent)
    if len(candidates.requirements) != 1:
        raise FormalGoalError(
            "STATE_REQUIREMENT_NOT_MINIMAL",
            "An operation-equivalent STATE Goal must contain one terminal requirement",
            details={"requirement_count": len(candidates.requirements)},
        )
    requirement = candidates.requirements[0]
    if not isinstance(requirement, AdHocFactRequirementCandidateV1):
        raise FormalGoalError(
            "STATE_OPERATION_EQUIVALENCE_CONFLICT",
            "An operation-equivalent STATE Goal must use a terminal Fact",
            details={"kind": getattr(requirement, "kind", None)},
        )
    matching = [
        (node_key, fact_key, value)
        for node_key, fact_key, value in effects
        if requirement.node_key == node_key and requirement.fact_key == fact_key
    ]
    if not matching:
        raise FormalGoalError(
            "STATE_OPERATION_EQUIVALENCE_CONFLICT",
            "The STATE requirement is not a terminal effect of the explicit operation",
            details={
                "node_key": requirement.node_key,
                "fact_key": requirement.fact_key,
            },
        )
    declared_values = tuple(requirement.accepted_values)
    concrete_values = tuple(value for _, _, value in matching if value is not None)
    if concrete_values and set(declared_values) != set(concrete_values):
        raise FormalGoalError(
            "STATE_OPERATION_EQUIVALENCE_CONFLICT",
            "The STATE value does not match the explicit operation terminal effect",
            details={
                "node_key": requirement.node_key,
                "fact_key": requirement.fact_key,
            },
        )


def _vnext_scalar_value_is_explicit(goal: str, value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return _contains_public_term(_normalize(goal), _normalize(str(value)))
    if isinstance(value, int):
        return re.search(rf"(?<![0-9]){re.escape(str(value))}(?![0-9])", goal) is not None
    return _contains_public_term(_normalize(goal), _normalize(str(value)))


def _vnext_normalize_frozen_intent(
    goal: str,
    intent: DynamicGoalIntentDraft | None,
    deterministic_refs: tuple[DynamicGoalCandidateReference, ...],
    public_catalog: dict[str, object] | None = None,
) -> DynamicGoalIntentDraft | None:
    """Normalize semantic role provenance before the immutable evidence freeze.

    Semantic Grounding owns identity and role binding. Public-catalog validation
    rejects invented keys before this freeze; deterministic lookup and optional
    copied surfaces have no authority to add, remove, or replace a role.  The
    only normalization is semantic evidence hygiene: an omitted role cannot be
    represented as ``UNRESOLVED``/``GROUNDED`` merely because Runtime needs a
    value, while a real explicit but unresolved mention remains unresolved.
    """

    if intent is None:
        return None
    normalized_actor = _vnext_normalize_role_slot(
        goal,
        intent.actor,
        deterministic_refs,
        public_catalog,
    )
    normalized_source = _vnext_normalize_role_slot(
        goal,
        intent.source,
        deterministic_refs,
        public_catalog,
    )
    normalized_target = _vnext_normalize_role_slot(
        goal,
        intent.target,
        deterministic_refs,
        public_catalog,
    )
    normalized_resource = _vnext_normalize_role_slot(
        goal,
        intent.resource,
        deterministic_refs,
        public_catalog,
    )
    # A provider sometimes copies a substring of its own Action description
    # into a target role (for example, treating the words that describe
    # generation as the facility being generated).  A role surface that is
    # only contained in the Action surface is not independent player evidence
    # unless deterministic lookup also found that exact public identity.  Keep
    # this generic: role ownership is established from typed surfaces and
    # deterministic refs, never from a Scenario/action-name special case.
    normalized_roles = {
        "actor": normalized_actor,
        "source": normalized_source,
        "target": normalized_target,
        "resource": normalized_resource,
    }
    for role, normalized in tuple(normalized_roles.items()):
        if _vnext_role_surface_is_action_only(
            goal,
            intent.action,
            normalized,
            deterministic_refs,
        ):
            normalized_roles[role] = DynamicGoalMentionSlot(
                status="NOT_SPECIFIED",
                provenance="NOT_SPECIFIED",
            )
    return intent.model_copy(
        update={
            "action": _vnext_normalize_role_slot(
                goal,
                intent.action,
                deterministic_refs,
                public_catalog,
                check_surface_identity=False,
            ),
            **normalized_roles,
            "amount": _vnext_normalize_scalar_slot(
                goal,
                intent.amount,
                strict_catalog_path=public_catalog is not None,
            ),
        }
    )


def _vnext_role_surface_is_action_only(
    goal: str,
    action: DynamicGoalMentionSlot,
    role: DynamicGoalMentionSlot,
    deterministic_refs: tuple[DynamicGoalCandidateReference, ...],
) -> bool:
    """Return whether a role surface is merely copied from the Action phrase.

    Stage-1 semantic evidence is authoritative for explicit role binding, but
    a model can still label an Action's descriptive substring as another role.
    When no deterministic exact public reference supports that role, keeping
    it would turn Action wording into a player constraint.  The conservative
    result is ``NOT_SPECIFIED`` so a data-driven ``goal_required`` gate can ask
    for the missing slot.
    """

    if (
        action.status != "GROUNDED"
        or role.status != "GROUNDED"
        or action.match_semantics == "RELATED_ONLY"
        or not _vnext_surface_is_in_goal(goal, action.surface)
        or not _vnext_surface_is_in_goal(goal, role.surface)
        or not isinstance(action.surface, str)
        or not isinstance(role.surface, str)
    ):
        return False
    action_surface = _normalize(action.surface)
    role_surface = _normalize(role.surface)
    if not action_surface or not role_surface or role_surface not in action_surface:
        return False
    if role.ref_type is None or role.key is None:
        return False
    return not any(
        item.ref_type == role.ref_type
        and item.key == role.key
        and item.provenance == "EXACT_USER_MENTION"
        for item in deterministic_refs
    )


def _vnext_role_candidate_refs(
    intent: DynamicGoalIntentDraft | None,
) -> tuple[DynamicGoalCandidateReference, ...]:
    if intent is None:
        return ()
    refs = []
    for slot in (intent.actor, intent.source, intent.target, intent.resource):
        if (
            slot.status == "GROUNDED"
            and slot.match_semantics != "RELATED_ONLY"
            and slot.ref_type is not None
            and slot.key is not None
        ):
            refs.append(
                DynamicGoalCandidateReference(
                    ref_type=slot.ref_type,
                    key=slot.key,
                    provenance="LLM_SUPPLEMENTED",
                    match_semantics=slot.match_semantics,
                )
            )
    unique = {(item.ref_type, item.key): item for item in refs}
    return tuple(unique[key] for key in sorted(unique))


def _vnext_frozen_dynamic_goal_evidence(
    goal: str,
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
    provider: object,
    deterministic: _DynamicGoalGrounding,
) -> _FrozenDynamicGoalEvidence:
    """Run evidence-only semantic grounding after deterministic public lookup."""

    exact_refs = deterministic.candidate_refs if deterministic.status == "RESOLVED" else ()
    ambiguous_refs = (
        deterministic.candidate_refs if deterministic.status == "NEEDS_CLARIFICATION" else ()
    )
    grounder = getattr(provider, "ground_dynamic_goal_entities", None)
    if not callable(grounder):
        return _FrozenDynamicGoalEvidence(
            deterministic_exact_refs=exact_refs,
            deterministic_ambiguous_refs=ambiguous_refs,
        )

    public_catalog = _dynamic_goal_entity_catalog(db, scope, definition)
    recovery_feedback: tuple[dict[str, object], ...] = ()
    last_error: GenericProviderError | None = None
    for recovery_attempt in range(2):
        request = DynamicGoalEntityGroundingRequest(
            goal=goal,
            public_catalog=public_catalog,
            deterministic_candidate_refs=exact_refs,
            deterministic_ambiguous_refs=ambiguous_refs,
            recovery_attempt=recovery_attempt,
            recovery_feedback=recovery_feedback,
        )
        raw: object | None = None
        try:
            raw = grounder(request)
            grounded = DynamicGoalEntityGrounding.model_validate(raw)

            semantic_refs: tuple[DynamicGoalCandidateReference, ...] = ()
            role_evidence: dict[str, object] | None = None
            frozen_intent: DynamicGoalIntentDraft | None = None
            if grounded.status == "RESOLVED":
                stage1_intent = grounded.intent
                if recovery_attempt == 0:
                    semantic_recheck = _vnext_semantic_grounding_recheck_feedback(
                        goal,
                        stage1_intent,
                        exact_refs,
                        public_catalog,
                    )
                    if semantic_recheck:
                        recovery_feedback = semantic_recheck
                        continue
                invalid_unresolved_roles = _vnext_unresolved_roles_without_evidence(
                    goal,
                    stage1_intent,
                    exact_refs,
                    public_catalog,
                )
                if invalid_unresolved_roles:
                    raise FormalGoalError(
                        "ROLE_EVIDENCE_INVALID",
                        "An UNRESOLVED semantic role has no explicit player evidence",
                        details={"roles": list(invalid_unresolved_roles)},
                    )
                frozen_intent = _vnext_normalize_frozen_intent(
                    goal,
                    stage1_intent,
                    exact_refs,
                    public_catalog,
                )
                rejected_role_identities = _vnext_rejected_role_identities(
                    stage1_intent,
                    frozen_intent,
                )
                raw_semantic_refs = (
                    *(
                        item
                        for item in grounded.candidate_refs
                        if item.ref_type != "ACTION"
                        and item.match_semantics != "RELATED_ONLY"
                        and (item.ref_type, item.key) not in rejected_role_identities
                    ),
                    *_vnext_role_candidate_refs(frozen_intent),
                )
                semantic_refs = tuple(
                    DynamicGoalCandidateReference(
                        ref_type=item.ref_type,
                        key=item.key,
                        provenance=item.provenance,
                        match_semantics=item.match_semantics,
                    )
                    for item in raw_semantic_refs
                )
                semantic_refs = _merge_dynamic_goal_candidate_refs((), semantic_refs)
                if semantic_refs:
                    semantic_refs = _validate_dynamic_goal_candidate_refs(
                        definition,
                        db,
                        scope,
                        semantic_refs,
                    )
                role_evidence = _vnext_explicit_role_evidence(frozen_intent)
        except GenericProviderError as exc:
            last_error = exc
            if recovery_attempt == 0 and exc.code in {
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "PROVIDER_SCHEMA_INVALID",
            }:
                recovery_feedback = exc.grounding_recovery_feedback or tuple(
                    exc.validation_diagnostics
                )
                continue
            raise
        except ValidationError as exc:
            diagnostics = provider_validation_diagnostics(exc)
            last_error = GenericProviderError(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "The model provider returned invalid VNext semantic evidence",
                validation_diagnostics=diagnostics,
            )
            if recovery_attempt == 0:
                recovery_feedback = tuple(diagnostics)
                continue
            raise last_error from exc
        except FormalGoalError as exc:
            last_error = GenericProviderError(
                "PROVIDER_SCHEMA_INVALID",
                "Semantic evidence failed exact-Version validation",
                validation_diagnostics=({"code": exc.code, **dict(exc.details)},),
            )
            if recovery_attempt == 0:
                recovery_feedback = last_error.validation_diagnostics
                continue
            raise last_error from exc

        merged = semantic_refs
        frozen = _FrozenDynamicGoalEvidence(
            deterministic_exact_refs=exact_refs,
            deterministic_ambiguous_refs=ambiguous_refs,
            semantic_refs=semantic_refs,
            merged_refs=merged,
            frozen_intent=frozen_intent,
            explicit_role_evidence=role_evidence,
            semantic_status=grounded.status,
        )
        return replace(
            frozen,
            semantic_action_evidence=_vnext_semantic_action_evidence(
                goal,
                frozen,
                _dynamic_goal_public_action_keys(db, scope, definition),
                public_catalog,
            ),
        )
    assert last_error is not None
    raise last_error


def _vnext_goal_observation(
    evidence: _FrozenDynamicGoalEvidence,
    *,
    frozen_family: Literal["STATE", "OPERATION", "AMBIGUOUS"],
    terminal_stage: str,
    result: str,
    attempt: int = 0,
    rejection_code: str | None = None,
    intermediate_stages: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    stages: list[dict[str, object]] = [
        {
            "stage": "DETERMINISTIC_GROUNDING",
            "provider_calls": 0,
            "exact_refs": [
                item.model_dump(mode="json") for item in evidence.deterministic_exact_refs
            ],
            "ambiguous_refs": [
                item.model_dump(mode="json") for item in evidence.deterministic_ambiguous_refs
            ],
        },
        {
            "stage": "SEMANTIC_GROUNDING",
            "status": evidence.semantic_status,
            "semantic_refs": [item.model_dump(mode="json") for item in evidence.semantic_refs],
            "explicit_role_evidence": evidence.explicit_role_evidence or {},
        },
        {
            "stage": "EVIDENCE_FREEZE",
            "merged_refs": [item.model_dump(mode="json") for item in evidence.merged_refs],
        },
        {"stage": "FAMILY_ROUTING", "frozen_family": frozen_family},
        *intermediate_stages,
        {"stage": terminal_stage, "attempt": attempt, "result": result},
    ]
    observation: dict[str, object] = {
        "pipeline": "GOAL_RESOLVER_VNEXT",
        "stage": terminal_stage,
        "frozen_family": frozen_family,
        "result": result,
        "stages": stages,
    }
    if rejection_code is not None:
        observation["rejection_code"] = rejection_code
    return observation


def _validate_dynamic_entity_grounding_keys(
    definition: ScenarioDefinitionV2,
    db: Session | None,
    scope: RuntimeScope | None,
    candidate_keys: tuple[str, ...],
) -> tuple[str, ...]:
    candidate_refs = tuple(
        DynamicGoalCandidateReference(ref_type="NODE", key=key) for key in candidate_keys
    )
    validated = _validate_dynamic_goal_candidate_refs(definition, db, scope, candidate_refs)
    return tuple(item.key for item in validated)


def _validate_dynamic_goal_candidate_refs(
    definition: ScenarioDefinitionV2,
    db: Session | None,
    scope: RuntimeScope | None,
    candidate_refs: tuple[DynamicGoalCandidateReference, ...],
) -> tuple[DynamicGoalCandidateReference, ...]:
    """Validate Stage 1 references against public exact-Version metadata."""

    public_nodes, _goal_addressable_facts, public_regions = _dynamic_goal_public_keys(
        db,
        scope,
        definition,
    )
    if not candidate_refs:
        raise FormalGoalError(
            "FORMAL_GOAL_DYNAMIC_ENTITY_GROUNDING_INVALID",
            "Dynamic Goal Entity Grounding returned no usable public reference",
        )
    identities = tuple((item.ref_type, item.key) for item in candidate_refs)
    if len(set(identities)) != len(identities):
        raise FormalGoalError(
            "FORMAL_GOAL_DYNAMIC_ENTITY_GROUNDING_INVALID",
            "Dynamic Goal Entity Grounding returned duplicate references",
        )
    public_resources = {item.key for item in definition.world.resources}
    public_actions = _dynamic_goal_public_action_keys(db, scope, definition)
    public_actors = _dynamic_goal_public_actor_keys(db, scope, definition)
    public_derived = {item.key for item in definition.derived_states if item.goal_addressable}
    for reference in candidate_refs:
        if reference.ref_type == "NODE":
            if reference.key not in public_nodes or reference.key in public_regions:
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_ENTITY_NOT_PUBLIC",
                    "Dynamic Goal Entity Grounding returned a non-public Node",
                )
        elif reference.ref_type == "REGION":
            if reference.key not in public_regions:
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_REGION_NOT_PUBLIC",
                    "Dynamic Goal Entity Grounding returned a non-public Region",
                )
        elif reference.ref_type == "RESOURCE":
            if reference.key not in public_resources:
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_RESOURCE_NOT_PUBLIC",
                    "Dynamic Goal Grounding returned a non-public Resource",
                )
        elif reference.ref_type == "ACTION":
            if reference.key not in public_actions:
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_ACTION_NOT_PUBLIC",
                    "Dynamic Goal Grounding returned a non-public Action",
                )
        elif reference.ref_type == "ACTOR":
            if reference.key not in public_actors:
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_ACTOR_NOT_PUBLIC",
                    "Dynamic Goal Grounding returned a non-public Actor",
                )
        elif reference.key not in public_derived:
            raise FormalGoalError(
                "FORMAL_GOAL_DYNAMIC_DERIVED_STATE_NOT_PUBLIC",
                "Dynamic Goal Grounding returned a non-public Derived State",
            )
    return tuple(sorted(candidate_refs, key=lambda item: (item.ref_type, item.key)))


def _dynamic_goal_grounding_keys(grounding: _DynamicGoalGrounding) -> tuple[str, ...]:
    return tuple(item.key for item in grounding.candidate_refs)


def _dynamic_goal_projection(
    *,
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
    grounding: _DynamicGoalGrounding,
) -> _DynamicGoalProjection:
    """Build the allow-list shared by ontology and Stage 2 validation."""

    public_nodes, goal_addressable_facts, public_regions = _dynamic_goal_public_keys(
        db,
        scope,
        definition,
    )
    public_action_keys = _dynamic_goal_public_action_keys(db, scope, definition)
    public_actor_keys = _dynamic_goal_public_actor_keys(db, scope, definition)
    public_relations = _dynamic_goal_public_relations(
        db,
        scope,
        definition,
        public_nodes,
    )
    allowed_entity_keys = tuple(
        sorted(item.key for item in grounding.candidate_refs if item.ref_type == "NODE")
    )
    allowed_action_keys = tuple(
        sorted(
            item.key
            for item in grounding.candidate_refs
            if item.ref_type == "ACTION" and item.key in public_action_keys
        )
    )
    allowed_actor_keys = tuple(
        sorted(
            item.key
            for item in grounding.candidate_refs
            if item.ref_type == "ACTOR" and item.key in public_actor_keys
        )
    )
    allowed_region_keys = {
        item.key for item in grounding.candidate_refs if item.ref_type == "REGION"
    }
    endpoint_type = definition.metadata.locality.transport_endpoint_relation_type_key
    if definition.metadata.locality.enabled and endpoint_type is not None:
        allowed_region_keys.update(
            str(item["target_node_key"])
            for item in public_relations
            if (
                item.get("source_node_key") in allowed_entity_keys
                and item.get("relation_type_key") == endpoint_type
                and item.get("target_node_key") in public_regions
            )
        )
    fact_nodes = {
        *allowed_entity_keys,
        *(item.key for item in grounding.candidate_refs if item.ref_type == "REGION"),
    }
    allowed_fact_keys = tuple(
        sorted(
            (node_key, fact_key)
            for node_key, fact_key in goal_addressable_facts
            if node_key in fact_nodes
        )
    )
    return _DynamicGoalProjection(
        allowed_entity_keys=allowed_entity_keys,
        allowed_region_keys=tuple(sorted(allowed_region_keys)),
        allowed_resource_keys=tuple(
            sorted(item.key for item in grounding.candidate_refs if item.ref_type == "RESOURCE")
        ),
        allowed_derived_state_keys=tuple(
            sorted(
                item.key for item in grounding.candidate_refs if item.ref_type == "DERIVED_STATE"
            )
        ),
        allowed_fact_keys=allowed_fact_keys,
        allowed_action_keys=allowed_action_keys,
        allowed_actor_keys=allowed_actor_keys,
    )


def _dynamic_goal_grounding_has_semantics(projection: _DynamicGoalProjection) -> bool:
    return bool(
        projection.allowed_fact_keys
        or (projection.allowed_region_keys and projection.allowed_resource_keys)
        or projection.allowed_derived_state_keys
        or projection.allowed_action_keys
    )


def _dynamic_goal_grounding_observation(
    grounding: _DynamicGoalGrounding,
    projection: _DynamicGoalProjection | None = None,
) -> dict[str, object]:
    observation: dict[str, object] = {
        "source": grounding.source,
        "candidate_refs": [item.model_dump(mode="json") for item in grounding.candidate_refs],
        "entity_keys": list(grounding.entity_keys),
        "scope_keys": list(grounding.scope_keys),
    }
    if projection is not None:
        observation["projection"] = _dynamic_goal_projection_observation(projection)
    return observation


def _dynamic_goal_projection_observation(
    projection: _DynamicGoalProjection,
) -> dict[str, object]:
    observation: dict[str, object] = {
        "allowed_entity_keys": list(projection.allowed_entity_keys),
        "allowed_region_keys": list(projection.allowed_region_keys),
        "allowed_resource_keys": list(projection.allowed_resource_keys),
        "allowed_derived_state_keys": list(projection.allowed_derived_state_keys),
        "allowed_fact_keys": [
            f"{node_key}.{fact_key}" for node_key, fact_key in projection.allowed_fact_keys
        ],
    }
    if projection.allowed_action_keys:
        observation["allowed_action_keys"] = list(projection.allowed_action_keys)
    if projection.allowed_actor_keys:
        observation["allowed_actor_keys"] = list(projection.allowed_actor_keys)
    return observation


def _dynamic_goal_payload_hash(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _dynamic_goal_response_observation(
    response: object,
    projection: _DynamicGoalProjection | None,
) -> object:
    """Redact rejected private identities before they enter Goal telemetry."""

    if projection is None or isinstance(response, DynamicGoalInterpretation):
        payload = (
            response.model_dump(mode="json")
            if isinstance(response, DynamicGoalInterpretation)
            else response
        )
    else:
        payload = response
    if not isinstance(payload, dict):
        return payload
    requirements = payload.get("requirements")
    if not isinstance(requirements, list):
        return payload
    assert projection is not None
    safe_requirements: list[object] = []
    public_target_keys = {
        *projection.allowed_entity_keys,
        *projection.allowed_region_keys,
        *projection.allowed_actor_keys,
    }
    for raw in requirements:
        if not isinstance(raw, dict):
            safe_requirements.append(raw)
            continue
        item = dict(raw)
        kind = item.get("kind")
        is_public = True
        if kind == ObjectiveRequirementKind.FACT:
            identity = (item.get("node_key"), item.get("fact_key"))
            is_public = identity in projection.allowed_fact_keys
            if not is_public:
                item["node_key"] = {"json_type": "string", "value_omitted": True}
                item["fact_key"] = {"json_type": "string", "value_omitted": True}
        elif kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST:
            is_public = (
                item.get("region_key") in projection.allowed_region_keys
                and item.get("resource_key") in projection.allowed_resource_keys
            )
            if not is_public:
                item["region_key"] = {"json_type": "string", "value_omitted": True}
                item["resource_key"] = {"json_type": "string", "value_omitted": True}
        elif kind == ObjectiveRequirementKind.DERIVED_STATE:
            is_public = item.get("derived_key") in projection.allowed_derived_state_keys
            if not is_public:
                item["derived_key"] = {"json_type": "string", "value_omitted": True}
        elif kind == "ACTION_COMPLETED":
            is_public = (
                item.get("action_key") in projection.allowed_action_keys
                and (
                    item.get("actor_key") is None
                    or item.get("actor_key") in projection.allowed_actor_keys
                )
                and (item.get("target_key") is None or item.get("target_key") in public_target_keys)
            )
            raw_bindings = item.get("binding_constraints", [])
            if isinstance(raw_bindings, list):
                is_public = is_public and all(
                    isinstance(binding, dict)
                    and binding.get("role") in {"source_region", "destination_region"}
                    and binding.get("value") in projection.allowed_region_keys
                    for binding in raw_bindings
                )
            else:
                is_public = False
            if not is_public:
                for field in (
                    "action_key",
                    "actor_key",
                    "target_key",
                    "binding_constraints",
                    "parameter_constraints",
                ):
                    if field in item:
                        item[field] = {"json_type": "value", "value_omitted": True}
        if not is_public and "accepted_values" in item:
            item["accepted_values"] = {"json_type": "array", "value_omitted": True}
        safe_requirements.append(item)
    return {**payload, "requirements": safe_requirements}


def _dynamic_goal_ontology(
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
    *,
    grounding: _DynamicGoalGrounding | None = None,
    projection: _DynamicGoalProjection | None = None,
    grounded_operation: DynamicGoalGroundedOperation | None = None,
) -> dict[str, object]:
    """Build the interpreter's public ontology, excluding Truth and planning."""

    public_nodes, goal_addressable_facts, public_regions = _dynamic_goal_public_keys(
        db,
        scope,
        definition,
    )
    public_action_keys = _dynamic_goal_public_action_keys(db, scope, definition)
    public_actor_keys = _dynamic_goal_public_actor_keys(db, scope, definition)
    public_relations = _dynamic_goal_public_relations(
        db,
        scope,
        definition,
        public_nodes,
    )
    if grounding is not None and projection is None:
        projection = _dynamic_goal_projection(
            db=db,
            scope=scope,
            definition=definition,
            grounding=grounding,
        )
    focused_nodes = set(grounding.scope_keys) if grounding is not None else public_nodes
    allowed_fact_keys = (
        set(projection.allowed_fact_keys) if projection is not None else goal_addressable_facts
    )
    allowed_resource_keys = (
        set(projection.allowed_resource_keys)
        if projection is not None and grounding is not None
        else {item.key for item in definition.world.resources}
    )
    allowed_derived_state_keys = (
        set(projection.allowed_derived_state_keys)
        if projection is not None and grounding is not None
        else {item.key for item in definition.derived_states if item.goal_addressable}
    )
    allowed_action_keys = (
        set(projection.allowed_action_keys)
        if projection is not None and grounding is not None
        else public_action_keys
    )
    allowed_actor_keys = (
        set(projection.allowed_actor_keys)
        if projection is not None and grounding is not None
        else public_actor_keys
    )
    nodes = [
        {
            "key": node.key,
            "name": node.name,
            "description": node.description,
            "node_type_key": node.node_type_key,
        }
        for node in sorted(definition.world.nodes, key=lambda item: item.key)
        if node.key in public_nodes and node.key in focused_nodes
    ]
    facts = [
        {
            "node_key": node.key,
            "fact_key": fact.key,
            "name": fact.name,
            "description": fact.description,
            "value_type": fact.value_type.value,
            **({"goal_aliases": list(fact.goal_aliases)} if fact.goal_aliases else {}),
            **({"goal_examples": list(fact.goal_examples)} if fact.goal_examples else {}),
            **(
                {"goal_target_values": list(fact.goal_target_values)}
                if fact.goal_target_values
                else {}
            ),
            **({"allowed_values": list(fact.allowed_values)} if fact.allowed_values else {}),
        }
        for node in sorted(definition.world.nodes, key=lambda item: item.key)
        if node.key in public_nodes and node.key in focused_nodes
        for fact in sorted(node.facts, key=lambda item: item.key)
        if (node.key, fact.key) in allowed_fact_keys
    ]
    resources = [
        {"key": resource.key, "name": resource.name, "description": resource.description}
        for resource in sorted(definition.world.resources, key=lambda item: item.key)
        if resource.key in allowed_resource_keys
    ]
    derived_states = [
        {
            "key": state.key,
            "name": state.name,
            "description": state.description,
            "value_type": state.value_type.value,
            "target_value": state.available_value,
            "non_target_value": state.unavailable_value,
            **({"goal_aliases": list(state.goal_aliases)} if state.goal_aliases else {}),
            **({"goal_examples": list(state.goal_examples)} if state.goal_examples else {}),
            **({"allowed_values": list(state.allowed_values)} if state.allowed_values else {}),
        }
        for state in sorted(definition.derived_states, key=lambda item: item.key)
        if state.key in allowed_derived_state_keys and state.goal_addressable
    ]
    actions = [
        {
            "key": action.key,
            "name": action.name,
            "description": action.description,
            "target_kind": action.target_kind.value,
            "behavior": action.behavior.value,
            "locality": action.locality.value,
            **(
                {"required_actor_role_key": action.required_actor_role_key}
                if action.required_actor_role_key is not None
                else {}
            ),
            **(
                {
                    "target_actor_roles": [
                        {
                            "target_key": item.target_key,
                            "required_actor_role_key": item.required_actor_role_key,
                        }
                        for item in action.target_actor_roles
                    ]
                }
                if action.target_actor_roles
                else {}
            ),
            "parameters": [item.model_dump(mode="json") for item in action.parameters],
            **(
                {"operation_binding_contract": operation_contract}
                if (operation_contract := action_operation_binding_contract(action))
                else {}
            ),
        }
        for action in sorted(definition.actions, key=lambda item: item.key)
        if action.key in public_action_keys and action.key in allowed_action_keys
    ]
    actors = [
        {
            "key": actor.key,
            "name": actor.name,
            "role_key": actor.role_key,
        }
        for actor in sorted(definition.actors.actor_profiles, key=lambda item: item.key)
        if actor.key in public_actor_keys and actor.key in allowed_actor_keys
    ]
    requirement_kinds: list[str] = []
    if facts:
        requirement_kinds.append("FACT")
    if resources and (projection is None or projection.allowed_region_keys):
        requirement_kinds.append("RESOURCE_AT_LEAST")
    if derived_states:
        requirement_kinds.append("DERIVED_STATE")
    if actions:
        requirement_kinds.append("ACTION_COMPLETED")
    grounding_payload: dict[str, object]
    if grounding is not None:
        grounding_payload = {
            "mode": "FOCUSED_PUBLIC",
            "source": grounding.source,
            "candidate_refs": [item.model_dump(mode="json") for item in grounding.candidate_refs],
            "entity_keys": list(grounding.entity_keys),
            "scope_keys": list(grounding.scope_keys),
            "projection": (
                _dynamic_goal_projection_observation(projection) if projection is not None else {}
            ),
        }
    else:
        grounding_payload = {"mode": "FULL_PUBLIC"}
    ontology: dict[str, object] = {
        "schema_version": 1,
        "grounding": grounding_payload,
        "scenario": {
            "key": definition.metadata.key,
            "name": definition.metadata.name,
            "description": definition.metadata.description,
        },
        "world": {
            "nodes": nodes,
            "regions": [
                node_key
                for node_key in sorted(public_regions)
                if node_key in focused_nodes
                and (projection is None or node_key in projection.allowed_region_keys)
            ],
            "facts": facts,
            "resources": resources,
            "derived_states": derived_states,
            "actions": actions,
            "actors": actors,
            "topology": [
                relation
                for relation in public_relations
                if (
                    relation.get("source_node_key") in focused_nodes
                    and relation.get("target_node_key") in focused_nodes
                )
            ],
        },
        "goal_language": {
            "requirement_kinds": requirement_kinds,
            "combination": "IMPLICIT_AND",
            "comparison": "AT_LEAST_FOR_RESOURCE",
        },
    }
    if grounded_operation is not None:
        ontology["grounded_operation"] = grounded_operation.model_dump(mode="json")
    return ontology


def _action_parameter_resource_keys(parameters: Mapping[str, object]) -> set[str]:
    """Extract only public Resource identities from an Action parameter value."""

    resource_keys: set[str] = set()
    raw_resources = parameters.get("resources")
    if isinstance(raw_resources, (list, tuple)):
        for item in raw_resources:
            if isinstance(item, Mapping) and isinstance(item.get("resource_key"), str):
                resource_keys.add(item["resource_key"])
    raw_resource_key = parameters.get("resource_key")
    if isinstance(raw_resource_key, str):
        resource_keys.add(raw_resource_key)
    return resource_keys


def _validate_dynamic_goal_publicity(
    db: Session | None,
    scope: RuntimeScope | None,
    definition: ScenarioDefinitionV2,
    candidates: AdHocGoalCandidateSetV1 | AdHocGoalCandidateSetV2,
    *,
    projection: _DynamicGoalProjection | None = None,
) -> None:
    """Reject a candidate that names a currently non-public ontology item."""

    if isinstance(candidates, AdHocGoalCandidateSetV2):
        validate_ad_hoc_dynamic_candidates_v2(definition, candidates)
    else:
        validate_ad_hoc_dynamic_candidates(definition, candidates)
    public_nodes, goal_addressable_facts, public_regions = _dynamic_goal_public_keys(
        db,
        scope,
        definition,
    )
    public_resources = {item.key for item in definition.world.resources}
    public_actions = _dynamic_goal_public_action_keys(db, scope, definition)
    public_actors = _dynamic_goal_public_actor_keys(db, scope, definition)
    public_target_keys = {*public_nodes, *public_regions, *public_actors}
    for candidate in candidates.requirements:
        if isinstance(candidate, AdHocFactRequirementCandidateV1):
            if candidate.node_key not in public_nodes:
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_NODE_NOT_PUBLIC",
                    f"Dynamic Goal references a non-public Node {candidate.node_key}",
                )
            if (candidate.node_key, candidate.fact_key) not in goal_addressable_facts:
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_FACT_NOT_PUBLIC",
                    "Dynamic Goal references a non-public Fact "
                    f"{candidate.node_key}.{candidate.fact_key}",
                )
            if (
                projection is not None
                and (
                    candidate.node_key,
                    candidate.fact_key,
                )
                not in projection.allowed_fact_keys
            ):
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_GROUNDING_MISMATCH",
                    "Dynamic Goal Fact is outside the grounded public projection",
                )
            continue
        if isinstance(candidate, AdHocDerivedStateRequirementCandidateV1):
            state = definition.derived_state_definitions.get(candidate.derived_key)
            if state is None or not state.goal_addressable:
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_DERIVED_STATE_NOT_PUBLIC",
                    "Dynamic Goal references a non-public Derived State",
                )
            if projection is not None and candidate.derived_key not in set(
                projection.allowed_derived_state_keys
            ):
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_GROUNDING_MISMATCH",
                    "Dynamic Goal Derived State is outside the grounded public projection",
                )
            continue
        if isinstance(candidate, AdHocActionCompletedRequirementCandidateV1):
            action = next(
                (item for item in definition.actions if item.key == candidate.action_key),
                None,
            )
            if action is None or candidate.action_key not in public_actions:
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_ACTION_NOT_PUBLIC",
                    "Dynamic Goal references a non-public Action",
                )
            if candidate.actor_key is not None and candidate.actor_key not in public_actors:
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_ACTOR_NOT_PUBLIC",
                    "Dynamic Goal references a non-public Actor",
                )
            if candidate.target_key is not None:
                if action.target_kind == ActionTargetKind.ACTOR:
                    target_is_public = candidate.target_key in public_actors
                else:
                    target_is_public = candidate.target_key in public_target_keys
                if not target_is_public:
                    raise FormalGoalError(
                        "FORMAL_GOAL_DYNAMIC_TARGET_NOT_PUBLIC",
                        "Dynamic Goal references a non-public Action target",
                    )
            if any(item.value not in public_regions for item in candidate.binding_constraints):
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_BINDING_NOT_PUBLIC",
                    "Dynamic Goal references a non-public Action binding",
                )
            raw_parameters = candidate.parameter_constraints
            parameter_resources: set[str] = set()
            if raw_parameters is not None:
                parameter_resources = _action_parameter_resource_keys(raw_parameters)
                if not parameter_resources.issubset(public_resources):
                    raise FormalGoalError(
                        "FORMAL_GOAL_DYNAMIC_RESOURCE_NOT_PUBLIC",
                        "Dynamic Goal references a non-public Action Resource",
                    )
            if projection is not None and (
                candidate.action_key not in projection.allowed_action_keys
                or (
                    candidate.actor_key is not None
                    and candidate.actor_key not in projection.allowed_actor_keys
                )
                or (
                    candidate.target_key is not None
                    and candidate.target_key
                    not in {
                        *projection.allowed_entity_keys,
                        *projection.allowed_region_keys,
                        *projection.allowed_actor_keys,
                    }
                )
                or any(
                    item.value not in projection.allowed_region_keys
                    for item in candidate.binding_constraints
                )
                or not parameter_resources.issubset(projection.allowed_resource_keys)
            ):
                raise FormalGoalError(
                    "FORMAL_GOAL_DYNAMIC_GROUNDING_MISMATCH",
                    "Dynamic Goal Action is outside the grounded public projection",
                )
            continue
        assert isinstance(candidate, AdHocResourceAtLeastRequirementCandidateV1)
        if candidate.region_key not in public_regions:
            raise FormalGoalError(
                "FORMAL_GOAL_DYNAMIC_REGION_NOT_PUBLIC",
                f"Dynamic Goal references a non-public Region {candidate.region_key}",
            )
        if candidate.resource_key not in public_resources:
            raise FormalGoalError(
                "FORMAL_GOAL_DYNAMIC_RESOURCE_NOT_PUBLIC",
                f"Dynamic Goal references a non-public Resource {candidate.resource_key}",
            )
        if projection is not None and (
            candidate.region_key not in projection.allowed_region_keys
            or candidate.resource_key not in projection.allowed_resource_keys
        ):
            raise FormalGoalError(
                "FORMAL_GOAL_DYNAMIC_GROUNDING_MISMATCH",
                "Dynamic Goal Resource is outside the grounded public projection",
            )


def normalize_objective_keys(
    definition: ScenarioDefinitionV2,
    objective_keys: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    """Remove objectives fully subsumed by another selected Objective.

    This is applied before ObjectiveScope is frozen.  It follows the authored
    generic ``subsumes`` graph and does not alter the immutable definitions or
    an already persisted scope.
    """

    objectives = {objective.key: objective for objective in definition.objectives}
    selected = set(objective_keys)
    removed: set[str] = set()

    def descendants(key: str, visiting: set[str]) -> set[str]:
        if key in visiting:
            return set()
        visiting.add(key)
        objective = objectives.get(key)
        if objective is None:
            return set()
        result: set[str] = set()
        for child in objective.subsumes:
            result.add(child)
            result.update(descendants(child, visiting))
        visiting.remove(key)
        return result

    for parent in selected:
        removed.update(descendants(parent, set()) & selected)
    return tuple(sorted(selected - removed))


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


def _actor_command_reachability(actor: GameInstanceActor) -> CommandReachability:
    try:
        return CommandReachability(actor.command_reachability)
    except ValueError as exc:
        raise GenericAgentError(
            "RUNTIME_ACTOR_REACHABILITY_INVALID",
            "The Actor command reachability value is invalid",
        ) from exc


def _structured_plan_diagnostic(
    exc: GenericAgentError,
    *,
    action: ActionDefinitionV2,
    step_id: str,
    actor_key: str,
    target_key: str,
    projected_command_reachability: dict[str, CommandReachability],
) -> dict[str, object]:
    """Keep repair diagnostics actionable without exposing hidden Truth."""

    diagnostic: dict[str, object] = {
        "code": _safe_provider_diagnostic(exc.code),
        "failure_code": exc.code,
        "step_id": step_id,
        "action_key": action.key,
        "actor_key": actor_key,
        "target_key": target_key,
    }
    typed_fields = (
        "dimension",
        "required",
        "actual",
        "reason_code",
        "required_interaction_key",
        "actual_interactions",
        "transport_key",
        "source_region",
        "target_region",
        "resource_key",
        "scope_region",
        "required_amount",
        "projected_known_available_amount",
        "deficit",
        "parameter_key",
        "parameter_error",
        "validation_error",
        "actual_parameters",
        "blocking_condition",
        "known_predicate",
    )
    diagnostic.update({key: exc.details[key] for key in typed_fields if key in exc.details})
    if (
        diagnostic.get("code") == "PROPOSAL_INVALID"
        and diagnostic.get("dimension") == "ACTION_PRECONDITION"
    ):
        diagnostic["code"] = "ACTION_PRECONDITION_FAILED"
    if "dimension" not in diagnostic:
        blocker = _diagnostic_blocker(exc.code, action)
        if blocker:
            dimension = blocker.get("type", "ACTION_PRECONDITION")
            diagnostic["dimension"] = dimension
            if "required_value" in blocker:
                diagnostic["required"] = blocker["required_value"]
            if dimension == "COMMAND_REACHABILITY":
                reachability = projected_command_reachability.get(actor_key)
                if reachability is not None:
                    diagnostic["actual"] = reachability.value
    return diagnostic


def _planning_failure_details(
    definition: ScenarioDefinitionV2,
    action: ActionDefinitionV2,
    actor: GameInstanceActor,
    target_key: str,
    failure_code: str,
) -> dict[str, object]:
    """Project only public static assignment facts for a planning failure."""

    if failure_code == "TARGET_INTERACTION_INVALID":
        target = definition.world.node(target_key)
        actual_interactions = tuple(target.interaction_keys) if target is not None else ()
        return {
            "dimension": "TARGET_INTERACTION",
            "required": action.required_interaction_key,
            "actual": list(actual_interactions),
            "required_interaction_key": action.required_interaction_key,
            "actual_interactions": actual_interactions,
        }
    if failure_code == "ACTOR_NOT_ALLOWED":
        return {
            "dimension": "ACTOR_ACTION_ELIGIBILITY",
            "required": action.key,
            "actual": list(actor.allowed_action_keys),
        }
    if failure_code == "ACTOR_CAPABILITY_MISSING":
        return {
            "dimension": "ACTOR_CAPABILITY",
            "required": [item.value for item in action.allowed_actor_capabilities],
            "actual": list(actor.capabilities),
        }
    if failure_code == "ACTOR_ROLE_MISSING":
        return {
            "dimension": "ACTOR_ROLE",
            "required": action.required_actor_role_for_target(target_key),
            "actual": actor.role_key,
        }
    if failure_code == "ACTOR_BINDING_INVALID":
        return {
            "dimension": "ACTOR_BINDING",
            "required": "VALID_VERSION_BINDING",
            "actual": "INVALID",
        }
    if failure_code == "ACTOR_NOT_AVAILABLE":
        return {
            "dimension": "ACTOR_AVAILABILITY",
            "required": "ACTIVE",
            "actual": actor.status,
        }
    if failure_code == "TARGET_NOT_VISIBLE":
        return {"dimension": "TARGET_VISIBILITY", "required": "KNOWN", "actual": "HIDDEN"}
    return {"dimension": "TARGET", "required": "VALID_TARGET", "actual": "INVALID"}


def _diagnostic_with_step_id(
    diagnostic: dict[str, object], proposed_steps: tuple[object, ...]
) -> dict[str, object]:
    result = dict(diagnostic)
    raw_step = result.pop("step", None)
    if isinstance(raw_step, int) and 1 <= raw_step <= len(proposed_steps):
        result["step_id"] = str(getattr(proposed_steps[raw_step - 1], "step_id", ""))
    return result


def _diagnostic_blocker(
    failure_code: str,
    action: ActionDefinitionV2,
) -> dict[str, object] | None:
    if failure_code == "ACTOR_COMMAND_DISCONNECTED":
        return {
            "type": "COMMAND_REACHABILITY",
            "current_value": CommandReachability.DISCONNECTED.value,
            "required_value": CommandReachability.ONLINE.value,
        }
    if failure_code == "RESOURCE_SOURCE_UNKNOWN":
        return {
            "type": "RESOURCE_SOURCE",
            "required_value": "KNOWN_PUBLIC_SOURCE",
            "unknown_value": "UNKNOWN",
        }
    if failure_code in {
        "RESOURCE_INVENTORY_UNKNOWN",
        "TRANSPORT_RESOURCE_KNOWLEDGE_UNKNOWN",
    }:
        return {
            "type": "RESOURCE_KNOWLEDGE",
            "required_value": "KNOWN_VISIBLE_AVAILABLE",
            "unknown_value": "NOT_USABLE",
        }
    if failure_code in {"KNOWN_RESOURCE_INSUFFICIENT", "TRANSPORT_RESOURCE_INSUFFICIENT"}:
        return {
            "type": "RESOURCE_QUANTITY",
            "current_value": "KNOWN_INSUFFICIENT",
            "required_value": "REQUESTED_AMOUNT",
        }
    if failure_code in {"KNOWN_TRANSPORT_BLOCKED", "TRAVEL_BLOCKED", "TRANSPORT_BLOCKED"}:
        return {
            "type": "TRANSPORT_PASSABILITY",
            "current_value": "KNOWN_BLOCKED",
            "required_value": "PASSABLE",
            "unknown_value": "MAY_ATTEMPT",
        }
    if failure_code.startswith("LOCALITY_"):
        return {
            "type": "LOCALITY",
            "contract": action.locality.value,
        }
    if failure_code == "ACTOR_ROLE_MISSING":
        return {
            "type": "ACTOR_ROLE",
            "required_value": action.required_actor_role_key or "TARGET_SPECIFIC_ROLE",
        }
    if failure_code in {
        "SUPPLY_POWER_RELATION_UNKNOWN",
        "SUPPLY_POWER_SOURCE_NOT_OPERATIONAL",
        "SUPPLY_POWER_SOURCE_UNAVAILABLE",
    }:
        return {
            "type": "POWER_SOURCE_REQUIREMENT",
            "required_value": "KNOWN_VALID_SOURCE",
        }
    return {"type": "ACTION_PRECONDITION", "failure_code": failure_code}


def _validate_plan_segment_contract(
    segment: PlanProposal,
    planner_input: PlannerInput,
) -> tuple[PlanViolation, ...]:
    """Validate segment termination without planning a recovery path."""

    step_ids = [step.step_id for step in segment.steps]
    if any(not step_id.strip() for step_id in step_ids):
        return (
            PlanViolation(
                code="STEP_ID_INVALID",
                failure_code="STEP_ID_INVALID",
                dimension="STEP_ID",
                required="NON_BLANK",
                actual="BLANK",
            ),
        )
    if len(step_ids) != len(set(step_ids)):
        return (
            PlanViolation(
                code="STEP_ID_DUPLICATE",
                failure_code="STEP_ID_DUPLICATE",
                dimension="STEP_ID",
                required="UNIQUE",
                actual="DUPLICATE",
                step_ids=tuple(step_ids),
            ),
        )
    if segment.stop_reason in {"OBJECTIVE_COMPLETION", "SEGMENT_COMPLETE"}:
        if segment.boundary_dependency_id is not None:
            return (
                PlanViolation(
                    code="BOUNDARY_DEPENDENCY_NOT_ALLOWED",
                    failure_code="BOUNDARY_DEPENDENCY_NOT_ALLOWED",
                    dimension="SEGMENT_TERMINATION",
                    required="NO_BOUNDARY_DEPENDENCY",
                    actual=segment.boundary_dependency_id,
                    dependency_id=segment.boundary_dependency_id,
                ),
            )
        boundary_violation = _objective_completion_boundary_violation(segment, planner_input)
        if boundary_violation is not None:
            return (boundary_violation,)
        if not segment.steps:
            return (
                PlanViolation(
                    code="NO_STEPS",
                    failure_code="NO_STEPS",
                    dimension="SEGMENT_STEPS",
                    required="AT_LEAST_ONE_STEP",
                    actual=0,
                ),
            )
        return ()
    if segment.stop_reason == "BLOCKED":
        if segment.boundary_dependency_id is not None:
            return (
                PlanViolation(
                    code="BOUNDARY_DEPENDENCY_NOT_ALLOWED",
                    failure_code="BOUNDARY_DEPENDENCY_NOT_ALLOWED",
                    dimension="SEGMENT_TERMINATION",
                    required="NO_BOUNDARY_DEPENDENCY",
                    actual=segment.boundary_dependency_id,
                    dependency_id=segment.boundary_dependency_id,
                ),
            )
        if segment.steps:
            return (
                PlanViolation(
                    code="BLOCKED_SEGMENT_HAS_STEPS",
                    failure_code="BLOCKED_SEGMENT_HAS_STEPS",
                    dimension="SEGMENT_STEPS",
                    required="EMPTY",
                    actual=len(segment.steps),
                    step_ids=tuple(step_ids),
                ),
            )
        if _has_direct_known_progress_option(planner_input):
            return (
                PlanViolation(
                    code="BLOCKED_SEGMENT_HAS_PROGRESS_OPTIONS",
                    failure_code="BLOCKED_SEGMENT_HAS_PROGRESS_OPTIONS",
                    dimension="SEGMENT_TERMINATION",
                    required="NO_DIRECT_KNOWN_LEGAL_PROGRESS_OPTION",
                    actual="DIRECT_KNOWN_LEGAL_PROGRESS_OPTION_EXISTS",
                ),
            )
        return ()

    dependency_id = segment.boundary_dependency_id
    if not isinstance(dependency_id, str) or not dependency_id.strip():
        return (
            PlanViolation(
                code="INFORMATION_BOUNDARY_DEPENDENCY_MISSING",
                failure_code="INFORMATION_BOUNDARY_DEPENDENCY_MISSING",
                dimension="INFORMATION_BOUNDARY",
                required="REGISTERED_UNKNOWN_DEPENDENCY_ID",
                actual="MISSING",
            ),
        )
    matching = next(
        (
            item
            for item in planner_input.known_world.unknown_dependencies
            if item.get("dependency_id") == dependency_id
        ),
        None,
    )
    if matching is None or matching.get("status") != "UNKNOWN" or not matching.get("blocks"):
        return (
            PlanViolation(
                code="INFORMATION_BOUNDARY_NOT_RELEVANT",
                failure_code="INFORMATION_BOUNDARY_NOT_RELEVANT",
                dimension="INFORMATION_BOUNDARY",
                required="ACTIVE_UNKNOWN_BLOCKING_DEPENDENCY",
                actual=dependency_id,
                dependency_id=dependency_id,
            ),
        )
    if not segment.steps:
        return (
            PlanViolation(
                code="NO_STEPS",
                failure_code="NO_STEPS",
                dimension="SEGMENT_STEPS",
                required="AT_LEAST_ONE_STEP",
                actual=0,
            ),
        )
    resolvers = matching.get("resolvable_by_effect_types")
    resolver_types = (
        {str(item) for item in resolvers}
        if isinstance(resolvers, list) and all(isinstance(item, str) for item in resolvers)
        else set()
    )
    action_contracts = {item.action_key: item for item in planner_input.action_contracts}
    target_bindings = {
        (item.action_key, item.target_key): item for item in planner_input.target_bindings
    }
    acquisition_indices: list[int] = []
    dependent_indices: list[int] = []
    for index, step in enumerate(segment.steps):
        contract = action_contracts.get(str(step.action_key))
        binding = target_bindings.get((str(step.action_key), str(step.target_key)))
        effects = (
            *(contract.deterministic_effects if contract is not None else ()),
            *(binding.deterministic_effects if binding is not None else ()),
        )
        if _submitted_step_matches_dependency(
            step,
            matching,
            resolver_types,
            effects,
            planner_input=planner_input,
        ):
            acquisition_indices.append(index)
        if _step_consumes_unknown_dependency(
            step,
            matching,
            contract=contract,
            binding=binding,
        ):
            dependent_indices.append(index)
    if not resolver_types or not acquisition_indices:
        return (
            PlanViolation(
                code="INFORMATION_BOUNDARY_ACQUISITION_MISSING",
                failure_code="INFORMATION_BOUNDARY_ACQUISITION_MISSING",
                dimension="INFORMATION_BOUNDARY_ACQUISITION",
                required="MATCHING_SUBMITTED_KNOWLEDGE_ACQUISITION",
                actual="NO_MATCHING_SUBMITTED_STEP",
                dependency_id=dependency_id,
                required_effect_types=tuple(sorted(resolver_types)),
            ),
        )
    if dependent_indices:
        return (
            PlanViolation(
                code="INFORMATION_BOUNDARY_DEPENDENT_ACTION_INCLUDED",
                failure_code="INFORMATION_BOUNDARY_DEPENDENT_ACTION_INCLUDED",
                dimension="INFORMATION_BOUNDARY_DEPENDENCY",
                required="END_SEGMENT_BEFORE_FIRST_RESULT_DEPENDENT_ACTION",
                actual={
                    "matching_step_indices": acquisition_indices,
                    "dependent_step_indices": dependent_indices,
                    "segment_step_count": len(segment.steps),
                },
                dependency_id=dependency_id,
                required_effect_types=tuple(sorted(resolver_types)),
            ),
        )
    return ()


def _objective_completion_boundary_violation(
    segment: PlanProposal,
    planner_input: PlannerInput,
) -> PlanViolation | None:
    """Prevent a dependent Step from disguising an information boundary.

    The Validator does not choose an acquisition or a recovery path. It only
    observes whether a submitted Step consumes an active blocking UNKNOWN
    dependency; if so, the segment must end before that Step. Independent
    Steps after an observation remain valid.
    """

    contracts = {item.action_key: item for item in planner_input.action_contracts}
    bindings = {(item.action_key, item.target_key): item for item in planner_input.target_bindings}
    for dependency in planner_input.known_world.unknown_dependencies:
        dependency_id = dependency.get("dependency_id")
        if (
            not isinstance(dependency_id, str)
            or dependency.get("status") != "UNKNOWN"
            or not dependency.get("blocks")
            or dependency.get("attempt_policy") == "MAY_ATTEMPT"
        ):
            continue
        raw_types = dependency.get("resolvable_by_effect_types")
        resolver_types = (
            {str(item) for item in raw_types}
            if isinstance(raw_types, list) and all(isinstance(item, str) for item in raw_types)
            else set()
        )
        if not resolver_types:
            continue
        acquisition_indices: list[int] = []
        dependent_indices: list[int] = []
        for index, step in enumerate(segment.steps):
            contract = contracts.get(str(step.action_key))
            binding = bindings.get((str(step.action_key), str(step.target_key)))
            effects = (
                *(contract.deterministic_effects if contract is not None else ()),
                *(binding.deterministic_effects if binding is not None else ()),
            )
            if _submitted_step_matches_dependency(
                step,
                dependency,
                resolver_types,
                effects,
                planner_input=planner_input,
            ):
                acquisition_indices.append(index)
            if _step_consumes_unknown_dependency(
                step,
                dependency,
                contract=contract,
                binding=binding,
            ):
                dependent_indices.append(index)
        first_acquisition_index = min(acquisition_indices, default=None)
        dependent_after_acquisition = [
            index
            for index in dependent_indices
            if first_acquisition_index is not None and index > first_acquisition_index
        ]
        if dependent_after_acquisition:
            return PlanViolation(
                code="INFORMATION_BOUNDARY_REQUIRED",
                failure_code="INFORMATION_BOUNDARY_REQUIRED",
                dimension="INFORMATION_BOUNDARY",
                required="INFORMATION_BOUNDARY_BEFORE_FIRST_RESULT_DEPENDENT_ACTION",
                actual={
                    "stop_reason": segment.stop_reason,
                    "matching_step_indices": acquisition_indices,
                    "dependent_step_indices": dependent_after_acquisition,
                },
                dependency_id=dependency_id,
                required_effect_types=tuple(sorted(resolver_types)),
            )
    return None


def _has_direct_known_progress_option(planner_input: PlannerInput) -> bool:
    """Prove one immediately legal option without planning or suggesting one."""

    nodes = [
        item
        for item in planner_input.known_world.nodes
        if item.get("access") in {"AVAILABLE", "ENTERED"}
    ]
    resource_knowledge = {
        item.get("region_key"): item for item in planner_input.known_world.resource_knowledge
    }
    bindings = {(item.action_key, item.target_key): item for item in planner_input.target_bindings}
    for contract in planner_input.action_contracts:
        if not _direct_known_parameters(contract) or not _direct_known_preconditions(contract):
            continue
        requirements = contract.executor_requirements
        required_role = requirements.get("required_role_key")
        required_capabilities = requirements.get("required_capabilities", [])
        if not isinstance(required_capabilities, list):
            continue
        for actor in planner_input.actors:
            if (
                actor.availability != "ACTIVE"
                or contract.action_key not in actor.allowed_action_keys
                or not set(required_capabilities).issubset(actor.capabilities)
                or (isinstance(required_role, str) and actor.role_key != required_role)
                or (
                    requirements.get("command_reachability") == "ONLINE"
                    and actor.command_reachability != "ONLINE"
                )
            ):
                continue
            if _actor_has_direct_known_target(
                actor,
                contract,
                planner_input,
                nodes,
                bindings,
                resource_knowledge,
            ):
                return True
    return False


def _actor_has_direct_known_target(
    actor: PlannerActorState,
    contract: PlannerActionContract,
    planner_input: PlannerInput,
    nodes: list[dict[str, object]],
    bindings: dict[tuple[str, str], PlannerTargetBinding],
    resource_knowledge: dict[object, dict[str, object]],
) -> bool:
    """Check direct target legality only; never search a recovery sequence."""

    target_kind = contract.target_contract.get("kind")
    locality = contract.locality.get("type")
    if target_kind == "ACTOR":
        required_reachability = contract.target_contract.get("command_reachability")
        return any(
            target.actor_key != actor.actor_key
            and target.availability == "ACTIVE"
            and (
                not isinstance(required_reachability, str)
                or target.command_reachability == required_reachability
            )
            and (
                locality != "ACTOR_SAME_REGION"
                or (
                    actor.current_region is not None
                    and target.current_region == actor.current_region
                )
            )
            for target in planner_input.actors
        )
    required_interaction = contract.target_contract.get("required_interaction_key")
    target_node_types = contract.target_contract.get("node_type_keys")
    for node in nodes:
        target_key = node.get("key")
        interactions = node.get("interactions")
        if not isinstance(target_key, str) or not isinstance(interactions, list):
            continue
        if isinstance(required_interaction, str) and required_interaction not in interactions:
            continue
        if (
            isinstance(target_node_types, list)
            and target_node_types
            and node.get("type") not in target_node_types
        ):
            continue
        binding = bindings.get((contract.action_key, target_key))
        if binding is not None and not _direct_known_binding_requirements(
            binding,
            planner_input,
            actor.current_region,
        ):
            continue
        if not _direct_known_locality(
            actor,
            contract,
            target_key,
            planner_input,
        ):
            continue
        if (
            any(
                effect.get("type") == "RESOURCE_SURVEY_COMPLETED"
                for effect in contract.deterministic_effects
            )
            and resource_knowledge.get(target_key, {}).get("resource_survey_completed") is not False
        ):
            continue
        if not _direct_known_resource_option(
            actor,
            contract,
            binding,
            planner_input,
        ):
            continue
        return True
    return False


def _direct_known_locality(
    actor: PlannerActorState,
    contract: PlannerActionContract,
    target_key: str,
    planner_input: PlannerInput,
) -> bool:
    """Prove one-step locality from the public V2 graph only.

    This intentionally checks existence, not a route or a choice.  Relation
    rows are already part of the canonical Known-world slice, so the helper
    never consults Scenario Truth or constructs a multi-step recovery plan.
    """

    actor_region = actor.current_region
    locality = contract.locality.get("type")
    if not isinstance(actor_region, str):
        return False
    if contract.target_contract.get("kind") == "ACTOR":
        actor_target = next(
            (item for item in planner_input.actors if item.actor_key == target_key),
            None,
        )
        return actor_target is not None and (
            locality not in {"ACTOR_SAME_REGION", "ACTOR_REGION"}
            or actor_target.current_region == actor_region
        )

    nodes = {str(item.get("key")): item for item in planner_input.known_world.nodes}
    target_node = nodes.get(target_key)
    if target_node is None:
        return False
    if locality in {None, "NONE"}:
        return True
    locality_metadata = contract.locality
    region_type = locality_metadata.get("region_node_type_key", "region")
    facility_type = locality_metadata.get("facility_node_type_key", "facility")
    transport_type = locality_metadata.get("transport_node_type_key", "transport")
    located_in_type = locality_metadata.get("located_in_relation_type_key")
    endpoint_type = locality_metadata.get("transport_endpoint_relation_type_key")
    target_type = target_node.get("type")
    if locality in {"REGION", "ACTOR_SAME_REGION"}:
        return target_type == region_type and target_key == actor_region
    if locality in {"FACILITY_REGION", "LOCAL_TARGET", "LOCAL_TARGET_FACILITY_OR_TRANSPORT"}:
        if target_type == facility_type:
            return any(
                relation.get("source_node_key") == target_key
                and relation.get("target_node_key") == actor_region
                and (
                    not isinstance(located_in_type, str)
                    or relation.get("relation_type_key") == located_in_type
                )
                for relation in planner_input.known_world.relations
            )
        if (
            locality in {"LOCAL_TARGET", "LOCAL_TARGET_FACILITY_OR_TRANSPORT"}
            and target_type == transport_type
        ):
            return any(
                relation.get("source_node_key") == target_key
                and relation.get("target_node_key") == actor_region
                and (
                    not isinstance(endpoint_type, str)
                    or relation.get("relation_type_key") == endpoint_type
                )
                for relation in planner_input.known_world.relations
            )
        return False
    if locality == "TRANSPORT_ENDPOINT":
        return any(
            relation.get("source_node_key") == target_key
            and relation.get("target_node_key") == actor_region
            and target_type == transport_type
            and (
                not isinstance(endpoint_type, str)
                or relation.get("relation_type_key") == endpoint_type
            )
            for relation in planner_input.known_world.relations
        )
    if locality == "ONE_HOP_TRANSPORT":
        if target_type != region_type or target_key == actor_region:
            return False
        for transport_key in {
            str(item.get("key"))
            for item in planner_input.known_world.nodes
            if item.get("type") == transport_type and isinstance(item.get("key"), str)
        }:
            endpoints = {
                str(relation.get("target_node_key"))
                for relation in planner_input.known_world.relations
                if relation.get("source_node_key") == transport_key
                and (
                    not isinstance(endpoint_type, str)
                    or relation.get("relation_type_key") == endpoint_type
                )
            }
            if {actor_region, target_key}.issubset(endpoints):
                return _known_transport_route_is_not_blocked(
                    planner_input,
                    transport_key,
                    locality_metadata,
                )
        return False
    return False


def _direct_known_parameters(contract: PlannerActionContract) -> bool:
    for parameter in contract.parameters:
        if parameter.get("required") is not True or "default" in parameter:
            continue
        allowed_values = parameter.get("allowed_values")
        if isinstance(allowed_values, list) and allowed_values:
            continue
        if parameter.get("value_type") in {"BOOLEAN", "INTEGER"}:
            # A numeric/boolean required parameter without a bounded domain is
            # not an immediately provable choice.
            return False
        return False
    return True


def _direct_known_preconditions(contract: PlannerActionContract) -> bool:
    """Accept only preconditions that are publicly proven non-blocking."""

    for precondition in contract.known_preconditions:
        current = precondition.get("current_value")
        failure = precondition.get("failure_condition")
        if current is None or not isinstance(failure, dict):
            return False
        kind = failure.get("kind")
        expected = failure.get("value")
        if kind not in {"FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN"}:
            return False
        blocked = (
            (kind == "FACT_EQUALS" and current == expected)
            or (kind == "FACT_NOT_EQUALS" and current != expected)
            or (
                kind == "FACT_IN"
                and isinstance(failure.get("values"), list)
                and current in failure["values"]
            )
        )
        if blocked:
            return False
    return True


def _known_transport_route_is_not_blocked(
    planner_input: PlannerInput,
    transport_key: str,
    locality: dict[str, object],
) -> bool:
    passability_key = locality.get("passability_fact_key")
    if not isinstance(passability_key, str):
        return True
    identity = f"{transport_key}.{passability_key}"
    value = planner_input.known_world.facts.get(identity)
    return value is not False


def _direct_known_resource_option(
    actor: PlannerActorState,
    contract: PlannerActionContract,
    binding: PlannerTargetBinding | None,
    planner_input: PlannerInput,
) -> bool:
    """Reject a direct proof when a negative Resource effect is unknown/short."""

    effects = (*contract.deterministic_effects, *(binding.deterministic_effects if binding else ()))
    requirements: list[dict[str, object]] = []
    if binding is not None:
        requirements.extend(item for item in binding.requirements if isinstance(item, dict))
    for requirement in requirements:
        cost = requirement.get("cost")
        if isinstance(cost, dict):
            for resource_key, amount in cost.items():
                if not isinstance(resource_key, str) or not isinstance(amount, int) or amount <= 0:
                    continue
                if not _known_resource_sufficient(
                    planner_input.known_world.resources.get(resource_key),
                    actor.current_region,
                    amount,
                ):
                    return False
    for effect in effects:
        if effect.get("type") not in {"RESOURCE_DELTA", "RESOURCE_CONSUMPTION"}:
            continue
        resource_key = effect.get("resource_key")
        amount = effect.get("amount")
        if not isinstance(resource_key, str):
            continue
        if isinstance(amount, bool) or not isinstance(amount, int):
            return False
        if amount >= 0:
            continue
        scope = effect.get("scope")
        scope_region = actor.current_region if scope in {None, "ACTOR_CURRENT_REGION"} else None
        if not _known_resource_sufficient(
            planner_input.known_world.resources.get(resource_key),
            scope_region,
            -amount,
        ):
            return False
    return True


def _direct_known_binding_requirements(
    binding: PlannerTargetBinding,
    planner_input: PlannerInput,
    actor_region: str | None,
) -> bool:
    """Prove sparse target requirements from the public Known projection.

    A target binding may carry a known resource cost and/or known Fact
    predicates.  This helper only evaluates those already-projected values;
    it does not infer hidden Truth or construct a recovery sequence.
    """

    for requirement in binding.requirements:
        if not isinstance(requirement, dict):
            return False
        cost = requirement.get("cost")
        if isinstance(cost, dict):
            for resource_key, amount in cost.items():
                if (
                    not isinstance(resource_key, str)
                    or isinstance(amount, bool)
                    or not isinstance(amount, int)
                    or amount <= 0
                    or not _known_resource_sufficient(
                        planner_input.known_world.resources.get(resource_key),
                        actor_region,
                        amount,
                    )
                ):
                    return False
        special_requirements = requirement.get("special_requirements", [])
        if not isinstance(special_requirements, list):
            return False
        for special in special_requirements:
            if not isinstance(special, dict):
                return False
            node_key = special.get("node_key")
            fact_key = special.get("fact_key")
            operator = special.get("operator")
            expected = special.get("value")
            if not isinstance(node_key, str) or not isinstance(fact_key, str):
                return False
            actual = planner_input.known_world.facts.get(f"{node_key}.{fact_key}")
            if actual is None or not _direct_known_predicate_holds(actual, operator, expected):
                return False
    return True


def _direct_known_predicate_holds(actual: object, operator: object, expected: object) -> bool:
    if not isinstance(operator, str):
        return False
    try:
        if operator == "EQ":
            return actual == expected
        if operator == "NE":
            return actual != expected
        if operator == "IN":
            return isinstance(expected, list) and actual in expected
        if operator == "NOT_IN":
            return isinstance(expected, list) and actual not in expected
        if operator in {"LT", "LTE", "GT", "GTE"}:
            if not isinstance(actual, (bool, int, str)) or not isinstance(
                expected, (bool, int, str)
            ):
                return False
            actual_value = cast(Any, actual)
            expected_value = cast(Any, expected)
            if operator == "LT":
                return bool(actual_value < expected_value)
            if operator == "LTE":
                return bool(actual_value <= expected_value)
            if operator == "GT":
                return bool(actual_value > expected_value)
            return bool(actual_value >= expected_value)
        if operator.startswith("NOT_"):
            return not _direct_known_predicate_holds(actual, operator[4:], expected)
    except TypeError:
        return False
    return False


def _known_resource_sufficient(raw: object, region_key: str | None, amount: int) -> bool:
    if not isinstance(raw, dict):
        return False
    scopes = raw.get("scopes")
    if isinstance(scopes, dict) and region_key is not None:
        value = scopes.get(region_key)
        return (
            isinstance(value, dict)
            and isinstance(value.get("known_available"), int)
            and value["known_available"] >= amount
        )
    known_available = raw.get("known_available")
    return isinstance(known_available, int) and known_available >= amount


def _submitted_step_matches_dependency(
    step: object,
    dependency: dict[str, object],
    resolver_types: set[str],
    effects: Sequence[dict[str, object]],
    *,
    planner_input: PlannerInput | None = None,
) -> bool:
    """Judge the submitted acquisition binding; never choose one for the Planner."""

    target_key = getattr(step, "target_key", None)
    dimension = dependency.get("dimension")
    for effect in effects:
        effect_type = effect.get("type")
        if effect_type not in resolver_types:
            continue
        if dimension == "RESOURCE_SOURCE":
            if not isinstance(target_key, str):
                continue
            known_nodes = planner_input.known_world.nodes if planner_input else ()
            public_region = next(
                (item for item in known_nodes if item.get("key") == target_key),
                None,
            )
            if planner_input is not None and known_nodes and public_region is None:
                continue
            if (
                planner_input is not None
                and public_region is not None
                and str(public_region.get("type", "")).casefold() != "region"
            ):
                continue
            if planner_input is not None and not known_nodes:
                scope_region = dependency.get("scope_region")
                if isinstance(scope_region, str) and target_key != scope_region:
                    continue
            knowledge = next(
                (
                    item
                    for item in (
                        planner_input.known_world.resource_knowledge if planner_input else ()
                    )
                    if item.get("region_key") == target_key
                ),
                None,
            )
            if knowledge is not None and knowledge.get("resource_survey_completed") is True:
                continue
            if effect.get("target") == "target_region":
                return True
            if effect.get("region_key") == target_key:
                return True
            continue
        subject_key = dependency.get("subject_key")
        if isinstance(subject_key, str) and target_key == subject_key:
            return True
    return False


def _step_consumes_unknown_dependency(
    step: object,
    dependency: dict[str, object],
    *,
    contract: PlannerActionContract | None,
    binding: PlannerTargetBinding | None,
) -> bool:
    """Return whether a proposed Step needs the dependency's future result.

    This is deliberately a symbolic, public-contract check.  It does not
    inspect Runtime Truth and it does not infer a recovery Action.  Action
    contracts expose the public preconditions and knowledge semantics needed
    to distinguish an independent Step from one whose legality or binding
    consumes an unresolved observation.
    """

    if contract is None:
        return False
    dimension = dependency.get("dimension")
    if dimension == "RESOURCE_SOURCE":
        resource_key = dependency.get("resource_key")
        if not isinstance(resource_key, str):
            return False
        if _step_parameters_contain_resource(step, resource_key):
            return True
        if _requirements_contain_resource(
            binding.requirements if binding is not None else (), resource_key
        ):
            return True
        return any(
            _is_negative_resource_effect(effect, resource_key)
            for effect in contract.deterministic_effects
        )

    subject_key = dependency.get("subject_key")
    fact_key = dependency.get("fact_key")
    if not isinstance(subject_key, str) or not isinstance(fact_key, str):
        return False

    for requirement in contract.known_preconditions:
        if (
            requirement.get("node_key") == subject_key
            and requirement.get("fact_key") == fact_key
            and requirement.get("knowledge_status") == "UNKNOWN"
        ):
            return True
    raw_parameters = getattr(step, "parameters", {})
    source_key = raw_parameters.get("source_key") if isinstance(raw_parameters, dict) else None
    if source_key == subject_key and any(
        requirement.get("fact_key") == fact_key
        for requirement in _source_precondition_entries(contract.source_preconditions)
    ):
        return True
    return _requirements_contain_fact(
        binding.requirements if binding is not None else (),
        subject_key,
        fact_key,
    )


def _step_parameters_contain_resource(step: object, resource_key: str) -> bool:
    parameters = getattr(step, "parameters", {})
    if not isinstance(parameters, dict):
        return False
    if parameters.get("resource_key") == resource_key:
        return True
    resources = parameters.get("resources")
    return isinstance(resources, list) and any(
        isinstance(item, dict) and item.get("resource_key") == resource_key for item in resources
    )


def _is_negative_resource_effect(effect: dict[str, object], resource_key: str) -> bool:
    amount = effect.get("amount")
    return (
        effect.get("resource_key") == resource_key
        and effect.get("type") in {"RESOURCE_DELTA", "RESOURCE_CONSUMPTION"}
        and isinstance(amount, int)
        and not isinstance(amount, bool)
        and amount < 0
    )


def _requirements_contain_resource(requirements: Sequence[object], resource_key: str) -> bool:
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        cost = requirement.get("cost")
        if isinstance(cost, dict) and resource_key in cost:
            return True
    return False


def _requirements_contain_fact(
    requirements: Sequence[object], subject_key: str, fact_key: str
) -> bool:
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        special = requirement.get("special_requirements")
        if not isinstance(special, (list, tuple)):
            continue
        if any(
            isinstance(item, dict)
            and item.get("node_key") == subject_key
            and item.get("fact_key") == fact_key
            for item in special
        ):
            return True
    return False


def _source_precondition_entries(
    requirements: Sequence[object],
) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        condition = requirement.get("failure_condition")
        if isinstance(condition, dict):
            result.append(condition)
    return tuple(result)


_ANTI_REGRESSION_LOCATION_FIELDS: set[str] = {
    "step_id",
    "sequence",
    "message",
    "cascade_from_step_id",
    "step_ids",
}
_ANTI_REGRESSION_OCCURRENCE_FIELDS: set[str] = {
    "first_seen_attempt",
    "last_seen_attempt",
    "seen_count",
}


def _anti_regression_evidence(violation: PlanViolation) -> dict[str, object]:
    return violation.model_dump(
        mode="json",
        exclude_none=True,
        exclude_defaults=True,
        exclude=_ANTI_REGRESSION_LOCATION_FIELDS,
    )


def _anti_regression_fingerprint(evidence: dict[str, object]) -> str:
    return json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _remember_prior_contradictions(
    memory: tuple[AntiRegressionMemoryItem, ...],
    violations: tuple[PlanViolation, ...],
    *,
    seen_attempt: int,
) -> tuple[AntiRegressionMemoryItem, ...]:
    result = list(memory)
    indexes = {
        _anti_regression_fingerprint(
            item.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
                exclude=(_ANTI_REGRESSION_LOCATION_FIELDS | _ANTI_REGRESSION_OCCURRENCE_FIELDS),
            )
        ): index
        for index, item in enumerate(result)
    }
    seen_in_proposal: set[str] = set()
    for violation in violations:
        evidence = _anti_regression_evidence(violation)
        fingerprint = _anti_regression_fingerprint(evidence)
        if fingerprint in seen_in_proposal:
            continue
        seen_in_proposal.add(fingerprint)
        existing_index = indexes.get(fingerprint)
        if existing_index is None:
            indexes[fingerprint] = len(result)
            result.append(
                AntiRegressionMemoryItem.model_validate(
                    {
                        **evidence,
                        "first_seen_attempt": seen_attempt,
                        "last_seen_attempt": seen_attempt,
                        "seen_count": 1,
                    }
                )
            )
            continue
        existing = result[existing_index]
        result[existing_index] = existing.model_copy(
            update={
                "last_seen_attempt": seen_attempt,
                "seen_count": existing.seen_count + 1,
            }
        )
    return tuple(result)


def _safe_provider_diagnostic(code: str) -> str:
    if code in {
        "KNOWN_TRANSPORT_BLOCKED",
        "OBJECTIVE_IRRELEVANT",
        "TARGET_INTERACTION_INVALID",
        "TARGET_INVALID",
        "TARGET_NOT_VISIBLE",
        "ACTOR_NOT_ALLOWED",
        "ACTOR_CAPABILITY_MISSING",
        "ACTOR_ROLE_MISSING",
        "ACTOR_BINDING_INVALID",
        "ACTOR_NOT_AVAILABLE",
        "RESOURCE_SURVEY_ALREADY_COMPLETED",
        "RESOURCE_SOURCE_UNKNOWN",
        "RESOURCE_INVENTORY_UNKNOWN",
        "KNOWN_RESOURCE_INSUFFICIENT",
    }:
        return code
    if code.startswith("LOCALITY_"):
        return "LOCALITY_INVALID"
    return {
        "ACTION_PARAMETERS_INVALID": "PARAMETER_INVALID",
        "GENERIC_PLAN_PARAMETER_INVALID": "PARAMETER_INVALID",
        "TRANSPORT_PARAMETERS_INVALID": "PARAMETER_INVALID",
        "RESOURCE_AMOUNT_INVALID": "PARAMETER_INVALID",
        "AUTHORITY_PARAMETER_INVALID": "PARAMETER_INVALID",
        "ACTION_APPROVAL_REQUIRED": "AUTHORITY_REQUIRED",
        "ACTION_NOT_ALLOWED": "ACTOR_NOT_ALLOWED",
        "ACTOR_COMMAND_DISCONNECTED": "ACTOR_COMMAND_DISCONNECTED",
        "RELAY_TARGET_NOT_DISCONNECTED": "RELAY_TARGET_NOT_DISCONNECTED",
        "RELAY_TARGET_INVALID": "TARGET_INVALID",
        "SUPPLY_POWER_RELATION_UNKNOWN": "SUPPLY_POWER_REQUIREMENT_UNKNOWN",
        "SUPPLY_POWER_SOURCE_NOT_OPERATIONAL": "SUPPLY_POWER_SOURCE_INVALID",
        "SUPPLY_POWER_SOURCE_UNAVAILABLE": "SUPPLY_POWER_SOURCE_INVALID",
        "GENERIC_PROVIDER_PLAN_INVALID": "PROPOSAL_INVALID",
    }.get(code, "PROPOSAL_INVALID")


def _provider_error_category(error: GenericProviderError) -> str:
    cause = error.__cause__
    return type(cause).__name__ if cause is not None else type(error).__name__


def _objective_resource_refs(
    objectives: tuple[ObjectiveDefinitionV2, ...],
    *,
    definition: ScenarioDefinitionV2 | None = None,
    known_facts: Mapping[tuple[str, str], object] | None = None,
) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    visited: set[str] = set()
    public_known_facts = known_facts or {}

    def add_derived(derived_key: str) -> None:
        if definition is None or derived_key in visited:
            return
        state = definition.derived_state_definitions.get(derived_key)
        if state is None:
            return
        visited.add(derived_key)
        for dependency in state.dependencies:
            gate = dependency.knowledge_gate
            if not knowledge_gate_is_revealed(
                gate,
                public_known_facts.get((gate.node_key, gate.fact_key))
                if gate is not None
                else None,
            ):
                continue
            if dependency.kind.value == "RESOURCE_AT_LEAST":
                assert dependency.region_key is not None and dependency.resource_key is not None
                result.add((dependency.region_key, dependency.resource_key))
            elif dependency.kind.value == "DERIVED_STATE":
                assert dependency.derived_key is not None
                add_derived(dependency.derived_key)

    for objective in objectives:
        for requirement in (
            *objective.completion_requirements,
            *(item for group in objective.prerequisites for item in group.requirements),
        ):
            gate = requirement.knowledge_gate
            if not knowledge_gate_is_revealed(
                gate,
                public_known_facts.get((gate.node_key, gate.fact_key))
                if gate is not None
                else None,
            ):
                continue
            if (
                requirement.kind.value == "RESOURCE_AT_LEAST"
                and requirement.region_key is not None
                and requirement.resource_key is not None
            ):
                result.add((requirement.region_key, requirement.resource_key))
            elif requirement.derived_ref is not None:
                add_derived(requirement.derived_ref)
    return result


def _action_resource_goal_effects(
    definition: ScenarioDefinitionV2,
    action: ActionDefinitionV2,
    target_key: str,
    parameters: ActionParameters,
    objective_resource_refs: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Return typed resource obligations an Action/Target can advance.

    This is a coverage/relevance proof only.  It never evaluates the resulting
    quantity; the Runtime Truth evaluator remains authoritative for completion.
    """

    if not objective_resource_refs:
        return set()
    try:
        target_region = region_for_node(definition, target_key)
    except LocalityEngineError:
        target_region = target_key
    if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
        try:
            cargo = transport_resource_entries(parameters)
        except ValueError:
            cargo = ()
        return {
            (target_region, resource_key)
            for resource_key, amount in cargo
            if amount > 0 and (target_region, resource_key) in objective_resource_refs
        }

    result: set[tuple[str, str]] = set()
    for rule in definition.rules:
        if rule.action_key != action.key or getattr(rule.phase, "value", rule.phase) != "RESOLVE":
            continue
        for effect in rule.effects:
            if getattr(effect.kind, "value", effect.kind) != "ADJUST_RESOURCE":
                continue
            resource_key = effect.resource_key
            scope = effect.resource_scope
            if resource_key is None or scope is None:
                continue
            scope_kind = getattr(scope.kind, "value", scope.kind)
            if scope_kind == "CURRENT_TARGET_REGION":
                region_key = target_region
            elif scope_kind == "EXPLICIT" and scope.node_key is not None:
                try:
                    region_key = region_for_node(definition, scope.node_key)
                except LocalityEngineError:
                    region_key = scope.node_key
            else:
                continue
            amount = _static_integer_effect_amount(effect.amount, parameters)
            if (
                amount is not None
                and amount > 0
                and (region_key, resource_key) in objective_resource_refs
            ):
                result.add((region_key, resource_key))
    return result


def _static_integer_effect_amount(expression: object, parameters: ActionParameters) -> int | None:
    source = getattr(expression, "source", None)
    multiplier = getattr(expression, "multiplier", 1)
    if getattr(source, "value", source) == "LITERAL":
        literal = getattr(expression, "literal", None)
        return (
            literal * multiplier
            if isinstance(literal, int) and not isinstance(literal, bool)
            else None
        )
    parameter_key = getattr(expression, "parameter_key", None)
    value = parameters.get(parameter_key) if isinstance(parameter_key, str) else None
    return value * multiplier if isinstance(value, int) and not isinstance(value, bool) else None


def _duration_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def proposal_signature(
    actor_key: str,
    action_key: str,
    target_key: str,
    parameters: ActionParameters,
    *,
    action: ActionDefinitionV2 | None = None,
) -> str:
    return canonical_action_invocation(
        action,
        action_key=action_key,
        actor_key=actor_key,
        target_key=target_key,
        parameters=parameters,
    ).signature


__all__ = [
    "PLAN_INVALIDATED_BY_NEW_KNOWLEDGE",
    "GenericAgentError",
    "GenericAgentService",
    "GenericGoalResolution",
    "GenericGoalResolver",
    "GenericObjectiveEvaluation",
    "PlanRevalidationResult",
    "normalize_objective_keys",
    "proposal_signature",
]
