"""Runtime helpers for frozen Formal Goal contracts."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agent.objective_scope import ObjectiveScope, ObjectiveScopeError
from app.domain.formal_goal import (
    FormalGoalContractV1,
    FormalGoalError,
    compile_predefined_formal_goal,
)
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario import ScenarioVersionSnapshot
from app.infrastructure.db.models import AgentTask
from app.scenarios.versions import ScenarioVersionRepository
from app.services.objective_requirements import truth_requirement_satisfied


@dataclass(frozen=True, slots=True)
class FormalGoalRequirementEvaluation:
    identity: str
    value: object
    satisfied: bool


@dataclass(frozen=True, slots=True)
class FormalGoalEvaluation:
    completed: bool
    requirements: tuple[FormalGoalRequirementEvaluation, ...]


class FormalGoalCompletionEvaluator:
    """Evaluate every frozen typed requirement against authoritative Truth."""

    def __init__(self, db: Session, scope: RuntimeScope) -> None:
        self.db = db
        self.scope = scope

    def evaluate(self, contract: FormalGoalContractV1) -> FormalGoalEvaluation:
        evaluations: list[FormalGoalRequirementEvaluation] = []
        for item in contract.completion_requirements:
            value, satisfied = truth_requirement_satisfied(
                self.db,
                self.scope,
                item.requirement,
            )
            evaluations.append(
                FormalGoalRequirementEvaluation(
                    identity=item.identity,
                    value=value,
                    satisfied=satisfied,
                )
            )
        return FormalGoalEvaluation(
            completed=bool(evaluations) and all(item.satisfied for item in evaluations),
            requirements=tuple(evaluations),
        )


class FormalGoalPersistenceError(ValueError):
    """A persisted Task cannot be converted into its frozen Formal Goal."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def load_formal_goal_for_task(
    db: Session,
    scope: RuntimeScope,
    task: AgentTask,
) -> FormalGoalContractV1:
    """Load a stored contract or compile a legacy PREDEFINED Task transiently.

    The compatibility path is deliberately read-only. In particular, an
    archived GameInstance is never modified merely because an old Task is
    inspected.
    """

    if task.game_instance_id != scope.game_instance_id or task.player_id != scope.player_id:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_TASK_SCOPE_INVALID",
            "Formal Goal Task does not belong to the requested runtime scope",
        )
    if task.objective_frozen_at is None:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_TASK_NOT_FROZEN",
            "A Task Formal Goal must be frozen before it can be evaluated",
        )

    snapshot = ScenarioVersionRepository(db).load(scope.scenario_version_id)

    if task.formal_goal_contract_json is not None:
        return _load_persisted_formal_goal(task, snapshot)

    formal_fields = (
        task.formal_goal_contract_schema_version,
        task.formal_goal_source_kind,
        task.formal_goal_contract_hash,
        task.formal_goal_scenario_version_id,
        task.formal_goal_scenario_content_hash,
        task.formal_goal_compiler_version,
    )
    if any(value is not None for value in formal_fields):
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_PERSISTENCE_INCOMPLETE",
            "Persisted Formal Goal fields are incomplete",
        )
    return _compile_legacy_predefined_task(task, snapshot)


def _load_persisted_formal_goal(
    task: AgentTask,
    snapshot: ScenarioVersionSnapshot,
) -> FormalGoalContractV1:
    payload = task.formal_goal_contract_json
    if not isinstance(payload, dict):
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_PERSISTENCE_INVALID",
            "Persisted Formal Goal contract must be a JSON object",
        )
    try:
        contract = FormalGoalContractV1.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_PERSISTENCE_INVALID",
            "Persisted Formal Goal contract is invalid",
        ) from exc
    if task.formal_goal_contract_hash != contract.content_hash:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_CONTRACT_HASH_MISMATCH",
            "Persisted Formal Goal contract hash does not match its canonical semantics",
        )
    if task.formal_goal_contract_schema_version != contract.schema_version:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_CONTRACT_VERSION_MISMATCH",
            "Persisted Formal Goal schema version does not match its contract",
        )
    if task.formal_goal_source_kind != contract.source_kind.value:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_SOURCE_KIND_MISMATCH",
            "Persisted Formal Goal source kind does not match its contract",
        )
    if task.formal_goal_scenario_version_id != snapshot.id:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_SCENARIO_VERSION_MISMATCH",
            "Persisted Formal Goal points to a different ScenarioVersion",
        )
    if task.formal_goal_scenario_content_hash != snapshot.content_hash:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_SCENARIO_HASH_MISMATCH",
            "Persisted Formal Goal Scenario proof does not match the exact Version",
        )
    try:
        contract.assert_bound_to(snapshot)
    except FormalGoalError as exc:
        raise FormalGoalPersistenceError(exc.code, exc.message) from exc
    return contract


def _compile_legacy_predefined_task(
    task: AgentTask,
    snapshot: ScenarioVersionSnapshot,
) -> FormalGoalContractV1:
    if task.objective_frozen_at is None:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_TASK_NOT_FROZEN",
            "A legacy Task must have a frozen ObjectiveScope",
        )
    expected_catalog = f"scenario-version:{snapshot.id}"
    try:
        scope = ObjectiveScope.create(
            task.objective_scope_keys or [], task.objective_catalog_version or ""
        )
    except ObjectiveScopeError as exc:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_LEGACY_SCOPE_INVALID",
            str(exc),
        ) from exc
    if scope.catalog_version != expected_catalog or scope.content_hash != task.objective_scope_hash:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_LEGACY_SCOPE_INVALID",
            "Legacy Task ObjectiveScope is not bound to the exact ScenarioVersion",
        )
    catalog = snapshot.definition.objective_definitions
    try:
        objectives = tuple(catalog[key] for key in scope.objective_keys)
    except KeyError as exc:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_LEGACY_OBJECTIVE_INVALID",
            "Legacy Task references an Objective absent from the exact Version",
        ) from exc
    try:
        return compile_predefined_formal_goal(snapshot, objectives)
    except FormalGoalError as exc:
        raise FormalGoalPersistenceError(exc.code, exc.message) from exc


__all__ = [
    "FormalGoalCompletionEvaluator",
    "FormalGoalEvaluation",
    "FormalGoalPersistenceError",
    "FormalGoalRequirementEvaluation",
    "load_formal_goal_for_task",
]
