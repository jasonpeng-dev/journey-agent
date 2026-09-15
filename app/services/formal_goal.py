"""Runtime helpers for frozen Formal Goal contracts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.agent.objective_scope import ObjectiveScope, ObjectiveScopeError
from app.domain.completion import (
    CompletionEvidence,
    CompletionStatus,
    OperationMatchConstraint,
)
from app.domain.formal_goal import (
    FormalGoalActionCompletedRequirementV1,
    FormalGoalContract,
    FormalGoalContractV1,
    FormalGoalContractV2,
    FormalGoalError,
    compile_predefined_formal_goal,
)
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario import ScenarioVersionSnapshot
from app.domain.scenario_v2 import ObjectiveRequirementV2, ScenarioDefinitionV2
from app.infrastructure.db.models import AgentTask, ResolvedGoalDraft
from app.scenarios.versions import ScenarioVersionRepository
from app.services.completion_kernel import (
    evaluate_operation_requirement,
    evaluate_state_requirement,
)
from app.services.derived_state import evaluate_derived_states
from app.services.objective_requirements import (
    known_requirement_satisfied,
    truth_requirement_satisfied,
)


@dataclass(frozen=True, slots=True)
class FormalGoalRequirementEvaluation:
    identity: str
    value: object
    satisfied: bool
    player_visible_satisfied: bool | None = None
    kind: str = "STATE"
    status: CompletionStatus = CompletionStatus.UNSATISFIED
    authoritative_evidence: CompletionEvidence | None = None


@dataclass(frozen=True, slots=True)
class FormalGoalEvaluation:
    """Expose authoritative and player-visible completion separately.

    ``completed`` is deliberately Truth-based: it answers whether the frozen
    contract is satisfied in the authoritative Runtime.  A player-facing
    lifecycle must use ``player_visible_completed`` when a Scenario
    definition is available, because a hidden value cannot be used as an
    observable completion signal.
    """

    completed: bool
    requirements: tuple[FormalGoalRequirementEvaluation, ...]
    player_visible_completed: bool | None = None


class FormalGoalCompletionEvaluator:
    """Evaluate every frozen typed requirement against authoritative Truth."""

    def __init__(self, db: Session, scope: RuntimeScope) -> None:
        self.db = db
        self.scope = scope

    def evaluate(
        self,
        contract: FormalGoalContract,
        *,
        definition: ScenarioDefinitionV2 | None = None,
        task: AgentTask | None = None,
    ) -> FormalGoalEvaluation:
        derived_evaluation = (
            evaluate_derived_states(self.db, self.scope, definition)
            if definition is not None and definition.derived_states
            else None
        )
        evaluations: list[FormalGoalRequirementEvaluation] = []
        for item in contract.completion_requirements:
            if isinstance(item.requirement, FormalGoalActionCompletedRequirementV1):
                if task is None or definition is None:
                    raise ValueError(
                        "ACTION_COMPLETED evaluation requires its Task and Scenario definition"
                    )
                constraint = OperationMatchConstraint(
                    action_key=item.requirement.action_key,
                    actor_key=item.requirement.actor_key,
                    target_key=item.requirement.target_key,
                    bindings=item.requirement.binding_constraints,
                    parameters=item.requirement.parameter_constraints,
                )
                completion = evaluate_operation_requirement(
                    self.db,
                    self.scope,
                    task,
                    definition,
                    requirement_identity=item.identity,
                    constraint=constraint,
                )
                evaluations.append(
                    FormalGoalRequirementEvaluation(
                        identity=item.identity,
                        value=completion.value,
                        satisfied=completion.satisfied,
                        player_visible_satisfied=completion.player_visible_satisfied,
                        kind=completion.requirement_kind,
                        status=completion.status,
                        authoritative_evidence=completion.authoritative_evidence,
                    )
                )
                continue
            if not isinstance(item.requirement, ObjectiveRequirementV2):
                raise ValueError("Unsupported Formal Goal requirement kind")
            requirement = item.requirement
            value, satisfied = truth_requirement_satisfied(
                self.db,
                self.scope,
                requirement,
                derived_evaluation=derived_evaluation,
            )
            player_visible_satisfied = (
                known_requirement_satisfied(
                    self.db,
                    self.scope,
                    definition,
                    requirement,
                    derived_evaluation=derived_evaluation,
                )
                if definition is not None
                else None
            )
            completion = evaluate_state_requirement(
                item.identity,
                value=value,
                satisfied=satisfied,
                player_visible_satisfied=player_visible_satisfied,
            )
            evaluations.append(
                FormalGoalRequirementEvaluation(
                    identity=item.identity,
                    value=value,
                    satisfied=satisfied,
                    player_visible_satisfied=player_visible_satisfied,
                    kind=completion.requirement_kind,
                    status=completion.status,
                    authoritative_evidence=completion.authoritative_evidence,
                )
            )
        authoritative_completed = bool(evaluations) and all(item.satisfied for item in evaluations)
        player_visible_completed = (
            authoritative_completed
            and all(item.player_visible_satisfied is True for item in evaluations)
            if definition is not None
            else None
        )
        return FormalGoalEvaluation(
            completed=authoritative_completed,
            requirements=tuple(evaluations),
            player_visible_completed=player_visible_completed,
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
    *,
    for_execution: bool = False,
) -> FormalGoalContract:
    """Load a stored contract or compile a legacy Task for historical READ.

    Legacy ObjectiveScope data may still be converted into a historical view,
    but it is never admitted to the current Planner/Runtime execution path.
    In particular, an archived GameInstance is never modified merely because
    an old Task is inspected.
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
    if for_execution:
        raise FormalGoalPersistenceError(
            "LEGACY_TASK_EXECUTION_UNSUPPORTED",
            "This historical Task has no current Formal Goal contract and is read-only",
        )
    return _compile_legacy_predefined_task(task, snapshot)


def load_formal_goal_for_draft(
    db: Session,
    scope: RuntimeScope,
    draft: ResolvedGoalDraft,
) -> FormalGoalContract:
    """Load and prove a Draft contract without reinterpreting its original text."""

    if draft.game_instance_id != scope.game_instance_id:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_DRAFT_SCOPE_INVALID",
            "Formal Goal Draft does not belong to the requested runtime scope",
        )
    snapshot = ScenarioVersionRepository(db).load(scope.scenario_version_id)
    return load_and_validate_formal_goal_contract(
        payload=draft.formal_goal_contract_json,
        contract_hash=draft.formal_goal_contract_hash,
        schema_version=draft.formal_goal_contract_schema_version,
        source_kind=draft.formal_goal_source_kind,
        scenario_version_id=draft.scenario_version_id,
        scenario_content_hash=draft.scenario_content_hash,
        compiler_version=draft.formal_goal_compiler_version,
        snapshot=snapshot,
    )


def _load_persisted_formal_goal(
    task: AgentTask,
    snapshot: ScenarioVersionSnapshot,
) -> FormalGoalContract:
    return load_and_validate_formal_goal_contract(
        payload=task.formal_goal_contract_json,
        contract_hash=task.formal_goal_contract_hash,
        schema_version=task.formal_goal_contract_schema_version,
        source_kind=task.formal_goal_source_kind,
        scenario_version_id=task.formal_goal_scenario_version_id,
        scenario_content_hash=task.formal_goal_scenario_content_hash,
        compiler_version=task.formal_goal_compiler_version,
        snapshot=snapshot,
    )


def load_and_validate_formal_goal_contract(
    *,
    payload: object,
    contract_hash: str | None,
    schema_version: int | None,
    source_kind: str | None,
    scenario_version_id: UUID | None,
    scenario_content_hash: str | None,
    compiler_version: str | None,
    snapshot: ScenarioVersionSnapshot,
) -> FormalGoalContract:
    """Deserialize and prove one persisted FormalGoal envelope without semantic inference."""

    if not isinstance(payload, dict):
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_PERSISTENCE_INVALID",
            "Persisted Formal Goal contract must be a JSON object",
        )
    try:
        contract_model = (
            FormalGoalContractV2 if payload.get("schema_version") == 2 else FormalGoalContractV1
        )
        contract = contract_model.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_PERSISTENCE_INVALID",
            "Persisted Formal Goal contract is invalid",
        ) from exc
    if contract_hash != contract.content_hash:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_CONTRACT_HASH_MISMATCH",
            "Persisted Formal Goal contract hash does not match its canonical semantics",
        )
    if schema_version != contract.schema_version:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_CONTRACT_VERSION_MISMATCH",
            "Persisted Formal Goal schema version does not match its contract",
        )
    if source_kind != contract.source_kind.value:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_SOURCE_KIND_MISMATCH",
            "Persisted Formal Goal source kind does not match its contract",
        )
    if scenario_version_id != snapshot.id:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_SCENARIO_VERSION_MISMATCH",
            "Persisted Formal Goal points to a different ScenarioVersion",
        )
    if scenario_content_hash != snapshot.content_hash:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_SCENARIO_HASH_MISMATCH",
            "Persisted Formal Goal Scenario proof does not match the exact Version",
        )
    if compiler_version != contract.compiler_version:
        raise FormalGoalPersistenceError(
            "FORMAL_GOAL_COMPILER_VERSION_MISMATCH",
            "Persisted Formal Goal compiler version does not match its contract",
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
    "load_and_validate_formal_goal_contract",
    "load_formal_goal_for_draft",
    "load_formal_goal_for_task",
]
