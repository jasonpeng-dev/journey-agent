"""Minimal contracts for scenario-specific planning and completion behavior."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, Protocol
from uuid import UUID

from app.domain.world import AccessState, RelationDefinition

type ObjectiveKey = str


class ScenarioRuntimeState(Protocol):
    def fact_value(self, node_key: str, fact_key: str) -> str: ...

    def node_known(self, node_key: str) -> bool: ...

    def fact_known(self, node_key: str, fact_key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class ScenarioPlanIssue:
    code: str
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class ObjectiveEvaluation:
    completed: bool
    details: Mapping[str, object] = field(default_factory=dict)
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


class ObjectiveContractError(ValueError):
    """Fail-closed validation error for objective catalog and scope values."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ObjectiveResolutionStatus(StrEnum):
    """Lifecycle states needed before a task may freeze an objective scope."""

    UNRESOLVED = "UNRESOLVED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    RESOLVED = "RESOLVED"
    CONFIRMED = "CONFIRMED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class ObjectiveVerificationRequirement:
    """One public terminal fact contract, not a path or expression language."""

    key: str
    node_key: str
    fact_key: str
    accepted_values: frozenset[str]
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "accepted_values", frozenset(self.accepted_values))
        if not self.key or not self.node_key or not self.fact_key:
            raise ObjectiveContractError(
                "OBJECTIVE_REQUIREMENT_INVALID",
                "Objective verification requirements need stable keys and fact references",
            )
        if not self.accepted_values:
            raise ObjectiveContractError(
                "OBJECTIVE_REQUIREMENT_INVALID",
                "Objective verification requirements need at least one accepted value",
            )


@dataclass(frozen=True, slots=True)
class ObjectivePrerequisite:
    """A known state constraint; it never becomes a player objective implicitly."""

    key: str
    description: str
    requirements: tuple[ObjectiveVerificationRequirement, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirements", tuple(self.requirements))
        if not self.key or not self.description.strip() or not self.requirements:
            raise ObjectiveContractError(
                "OBJECTIVE_PREREQUISITE_INVALID",
                "Objective prerequisites need a key, description, and state requirement",
            )


@dataclass(frozen=True, slots=True)
class ObjectiveDefinition:
    """A desired terminal state with optional public prerequisite constraints."""

    key: ObjectiveKey
    name: str
    description: str
    completion_requirements: tuple[ObjectiveVerificationRequirement, ...]
    prerequisites: tuple[ObjectivePrerequisite, ...] = ()
    subsumes: frozenset[ObjectiveKey] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "completion_requirements", tuple(self.completion_requirements))
        object.__setattr__(self, "prerequisites", tuple(self.prerequisites))
        object.__setattr__(self, "subsumes", frozenset(self.subsumes))
        if not self.key or not self.name.strip() or not self.description.strip():
            raise ObjectiveContractError(
                "OBJECTIVE_DEFINITION_INVALID",
                "Objective definitions need a key, name, and description",
            )
        if not self.completion_requirements:
            raise ObjectiveContractError(
                "OBJECTIVE_DEFINITION_INVALID",
                "Objective definitions need at least one terminal requirement",
            )
        requirement_keys = [item.key for item in self.completion_requirements]
        prerequisite_keys = [item.key for item in self.prerequisites]
        if len(set(requirement_keys)) != len(requirement_keys):
            raise ObjectiveContractError(
                "OBJECTIVE_DEFINITION_INVALID",
                "Objective completion requirements must use unique keys",
            )
        if len(set(prerequisite_keys)) != len(prerequisite_keys):
            raise ObjectiveContractError(
                "OBJECTIVE_DEFINITION_INVALID",
                "Objective prerequisites must use unique keys",
            )


@dataclass(frozen=True, slots=True)
class ObjectiveScope:
    """Immutable, task-owned set of explicit player objectives."""

    scenario_key: str
    catalog_version: str
    objective_keys: tuple[ObjectiveKey, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "objective_keys", tuple(self.objective_keys))
        if not self.scenario_key or not self.catalog_version:
            raise ObjectiveContractError(
                "OBJECTIVE_SCOPE_INVALID",
                "Objective scope needs a scenario key and catalog version",
            )
        if not self.objective_keys:
            raise ObjectiveContractError(
                "OBJECTIVE_SCOPE_EMPTY",
                "Objective scope must contain at least one explicit objective",
            )
        canonical = tuple(sorted(set(self.objective_keys)))
        if self.objective_keys != canonical:
            raise ObjectiveContractError(
                "OBJECTIVE_SCOPE_NOT_CANONICAL",
                "Objective scope keys must be unique and canonically ordered",
            )


@dataclass(frozen=True, slots=True)
class ObjectiveRequirementEvaluation:
    requirement: ObjectiveVerificationRequirement
    actual_value: str | None
    satisfied: bool


@dataclass(frozen=True, slots=True)
class PerObjectiveEvaluation:
    objective_key: ObjectiveKey
    requirements: tuple[ObjectiveRequirementEvaluation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirements", tuple(self.requirements))

    @property
    def completed(self) -> bool:
        return bool(self.requirements) and all(item.satisfied for item in self.requirements)


@dataclass(frozen=True, slots=True)
class ScopedObjectiveEvaluation:
    scope: ObjectiveScope
    objectives: tuple[PerObjectiveEvaluation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "objectives", tuple(self.objectives))
        evaluated_keys = tuple(item.objective_key for item in self.objectives)
        if evaluated_keys != self.scope.objective_keys:
            raise ObjectiveContractError(
                "OBJECTIVE_EVALUATION_SCOPE_MISMATCH",
                "Per-objective evaluations must match the canonical task scope",
            )

    @property
    def completed(self) -> bool:
        """Multiple explicit objectives use AND completion semantics."""

        return bool(self.objectives) and all(item.completed for item in self.objectives)


@dataclass(frozen=True, slots=True)
class GoalResolutionResult:
    """Pure resolution result; persistence and scope freezing belong to later phases."""

    status: ObjectiveResolutionStatus
    scope: ObjectiveScope | None = None
    candidate_scopes: tuple[ObjectiveScope, ...] = ()
    clarification_prompt: str | None = None
    resolver_source: str | None = None
    resolver_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_scopes", tuple(self.candidate_scopes))
        if self.status in {
            ObjectiveResolutionStatus.RESOLVED,
            ObjectiveResolutionStatus.CONFIRMED,
        }:
            if self.scope is None:
                raise ObjectiveContractError(
                    "GOAL_RESOLUTION_SCOPE_REQUIRED",
                    "Resolved or confirmed goals require an objective scope",
                )
        elif self.scope is not None:
            raise ObjectiveContractError(
                "GOAL_RESOLUTION_SCOPE_INVALID",
                "Unresolved, ambiguous, or unsupported goals cannot own a resolved scope",
            )
        if self.status == ObjectiveResolutionStatus.NEEDS_CLARIFICATION:
            if not self.candidate_scopes or not (self.clarification_prompt or "").strip():
                raise ObjectiveContractError(
                    "GOAL_CLARIFICATION_INVALID",
                    "Clarification requires candidate scopes and a player-facing prompt",
                )
        elif self.candidate_scopes or self.clarification_prompt is not None:
            raise ObjectiveContractError(
                "GOAL_CLARIFICATION_INVALID",
                "Clarification candidates and prompts belong only to ambiguous goals",
            )


class ScenarioObjectiveCatalog(Protocol):
    scenario_key: str
    catalog_version: str
    definitions: Mapping[ObjectiveKey, ObjectiveDefinition]

    def scope(self, objective_keys: Iterable[ObjectiveKey]) -> ObjectiveScope: ...

    def evaluate(
        self,
        scope: ObjectiveScope,
        state: ScenarioRuntimeState,
    ) -> ScopedObjectiveEvaluation: ...

    def verification_requirements(
        self,
        scope: ObjectiveScope,
    ) -> tuple[ObjectiveVerificationRequirement, ...]: ...

    def prerequisites(self, scope: ObjectiveScope) -> tuple[ObjectivePrerequisite, ...]: ...


def project_known_relations(
    relations: Iterable[RelationDefinition],
    known_node_access: Mapping[str, AccessState],
) -> tuple[RelationDefinition, ...]:
    """Expose a relation only when both endpoints are known, regardless of access."""

    known_keys = frozenset(known_node_access)
    return tuple(
        relation
        for relation in relations
        if relation.source_node_key in known_keys and relation.target_node_key in known_keys
    )


def project_known_relation_payloads(
    relations: Iterable[RelationDefinition],
    known_node_access: Mapping[str, AccessState],
) -> tuple[Mapping[str, str], ...]:
    """Serialize only safe relation semantics; access remains information, not permission."""

    return tuple(
        MappingProxyType(
            {
                "source_node_key": relation.source_node_key,
                "relation_type": relation.relation_type.value,
                "target_node_key": relation.target_node_key,
                "source_access": known_node_access[relation.source_node_key].value,
                "target_access": known_node_access[relation.target_node_key].value,
            }
        )
        for relation in project_known_relations(relations, known_node_access)
    )


class ScenarioPlanningPolicy(Protocol):
    execution_tools: frozenset[str]
    idempotent_tools: frozenset[str]
    operation_tools: frozenset[str]
    expected_outcome_fields: Mapping[str, frozenset[str]]
    fixed_tool_expected_outcomes: Mapping[str, Mapping[str, str]]
    world_operation_success_outcomes: Mapping[str, tuple[str, ...]]
    allowed_player_action_facts: frozenset[str]
    recoverable_failures: frozenset[str]

    def validate_candidate_plan(
        self,
        steps: Sequence[Mapping[str, Any]],
        selected_tools: Sequence[str],
        wait_count: int,
        *,
        is_replan: bool,
        state: ScenarioRuntimeState,
        scope: ObjectiveScope,
    ) -> tuple[ScenarioPlanIssue, ...]: ...

    def effect_satisfied(
        self,
        tool_name: str,
        tool_arguments: Mapping[str, Any],
        state: ScenarioRuntimeState,
    ) -> bool: ...

    def build_planning_constraints(
        self,
        kind: Literal["PLAN", "REPLAN"],
        reason: str | None,
        state: ScenarioRuntimeState,
        scope: ObjectiveScope,
    ) -> Mapping[str, object]: ...

    def replan_guidance(self, reason: str | None) -> str | None: ...

    def planner_instruction(self, kind: Literal["PLAN", "REPLAN"]) -> str: ...


class ScenarioObjectiveEvaluator(Protocol):
    def evaluate(self, state: ScenarioRuntimeState) -> ObjectiveEvaluation: ...


class ScenarioFallbackPlans(Protocol):
    def supports_state_aware_recovery(self, reason: str) -> bool: ...

    def initial(self, task_id: UUID, scope: ObjectiveScope) -> dict[str, Any]: ...

    def recovery(
        self,
        task_id: UUID,
        next_version: int,
        reason: str,
        scope: ObjectiveScope,
    ) -> dict[str, Any]: ...

    def state_aware_recovery(
        self,
        task_id: UUID,
        next_version: int,
        reason: str,
        state: ScenarioRuntimeState,
        scope: ObjectiveScope,
    ) -> dict[str, Any]: ...
