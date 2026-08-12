"""Minimal contracts for scenario-specific planning and completion behavior."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol
from uuid import UUID


class ScenarioRuntimeState(Protocol):
    def fact_value(self, node_key: str, fact_key: str) -> str: ...


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
    ) -> Mapping[str, object]: ...

    def replan_guidance(self, reason: str | None) -> str | None: ...

    def planner_instruction(self, kind: Literal["PLAN", "REPLAN"]) -> str: ...


class ScenarioObjectiveEvaluator(Protocol):
    def evaluate(self, state: ScenarioRuntimeState) -> ObjectiveEvaluation: ...


class ScenarioFallbackPlans(Protocol):
    def supports_state_aware_recovery(self, reason: str) -> bool: ...

    def initial(self, task_id: UUID) -> dict[str, Any]: ...

    def recovery(self, task_id: UUID, next_version: int, reason: str) -> dict[str, Any]: ...

    def state_aware_recovery(
        self,
        task_id: UUID,
        next_version: int,
        reason: str,
        state: ScenarioRuntimeState,
    ) -> dict[str, Any]: ...
