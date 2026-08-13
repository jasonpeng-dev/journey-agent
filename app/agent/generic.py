"""Generic exact-Version goal resolution, planning, validation and execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    NodeStatus,
    StepExecutionType,
    WorldOperationStatus,
)
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import (
    ActionDefinitionV2,
    ActionExecutionMode,
    ObjectiveDefinitionV2,
    ScenarioDefinitionV2,
    StrictScalar,
)
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    AgentPlan,
    AgentStep,
    AgentTask,
    ConversationSession,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    WorldOperation,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionError, GenericActionService

ObjectiveSelector = Callable[[str, tuple[ObjectiveDefinitionV2, ...]], str | None]


class GenericAgentError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class GenericGoalResolution:
    status: str
    objective_key: str | None = None
    candidate_keys: tuple[str, ...] = ()
    clarification_prompt: str | None = None
    source: str = "DETERMINISTIC"


@dataclass(frozen=True, slots=True)
class GenericObjectiveEvaluation:
    objective_key: str
    completed: bool
    requirements: tuple[tuple[str, StrictScalar, bool], ...]


class GenericGoalResolver:
    def __init__(self, selector: ObjectiveSelector | None = None) -> None:
        self.selector = selector

    def resolve(
        self,
        goal: str,
        definition: ScenarioDefinitionV2,
    ) -> GenericGoalResolution:
        normalized = _normalize(goal)
        matches = [
            objective
            for objective in definition.objectives
            if normalized
            in {
                _normalize(objective.key),
                _normalize(objective.name),
                *(_normalize(alias) for alias in objective.goal_aliases),
                *(_normalize(example) for example in objective.goal_examples),
            }
        ]
        if len(matches) == 1:
            return GenericGoalResolution("RESOLVED", matches[0].key)
        if len(matches) > 1:
            return GenericGoalResolution(
                "NEEDS_CLARIFICATION",
                candidate_keys=tuple(sorted(item.key for item in matches)),
                clarification_prompt=definition.goal_resolution.clarification_prompt,
            )
        if definition.goal_resolution.allow_llm_fallback and self.selector is not None:
            selected = self.selector(goal, definition.objectives)
            if selected in {objective.key for objective in definition.objectives}:
                return GenericGoalResolution("RESOLVED", selected, source="MODEL_VALIDATED")
        return GenericGoalResolution("UNSUPPORTED")


class GenericAgentService:
    """A compact persistent Agent loop driven only by exact v2 Version data."""

    def __init__(
        self,
        db: Session,
        scope: RuntimeScope,
        *,
        goal_resolver: GenericGoalResolver | None = None,
    ) -> None:
        self.db = db
        self.scope = scope
        self.goal_resolver = goal_resolver or GenericGoalResolver()

    def create_task(self, session: ConversationSession, goal: str) -> AgentTask:
        definition = self._definition()
        if session.game_instance_id != self.scope.game_instance_id or not session.actor_key:
            raise GenericAgentError(
                "GENERIC_SESSION_SCOPE_INVALID",
                "Generic task creation requires the Instance primary Actor session",
            )
        resolution = self.goal_resolver.resolve(goal, definition)
        if resolution.status != "RESOLVED" or resolution.objective_key is None:
            raise GenericAgentError(
                f"GOAL_{resolution.status}",
                resolution.clarification_prompt or "Goal does not resolve in the exact Version",
            )
        now = datetime.now(UTC)
        task = AgentTask(
            player_id=self.scope.player_id,
            game_instance_id=self.scope.game_instance_id,
            owner_actor_key=session.actor_key,
            origin_session_id=session.id,
            last_session_id=session.id,
            goal_description=goal,
            scenario_key=definition.metadata.key,
            objective_resolution_status="CONFIRMED",
            objective_scope_keys=[resolution.objective_key],
            objective_catalog_version=f"scenario-version:{self.scope.scenario_version_id}",
            objective_resolver_source=resolution.source,
            objective_resolver_version="generic-goal-resolver@1",
            objective_resolution_metadata={"exact_version": str(self.scope.scenario_version_id)},
            objective_resolved_at=now,
            objective_confirmed_at=now,
            objective_confirmation_source="EXACT_MATCH",
            objective_frozen_at=now,
            objective_freeze_source="GENERIC_AGENT",
            planning_mode="GENERIC",
        )
        self.db.add(task)
        self.db.flush()
        self.plan(task)
        return task

    def plan(self, task: AgentTask, *, reason: str | None = None) -> AgentPlan:
        definition = self._definition()
        self._task_scope(task)
        objective = self._objective(task, definition)
        actor = self._actor(task.owner_actor_key)
        old_plan = self.db.scalar(
            select(AgentPlan).where(
                AgentPlan.task_id == task.id,
                AgentPlan.status == AgentPlanStatus.ACTIVE,
            )
        )
        if old_plan is not None:
            old_plan.status = AgentPlanStatus.SUPERSEDED
        next_version = task.current_plan_version + 1
        steps = self._candidate_steps(
            definition,
            objective,
            actor,
            reason=reason,
            plan_version=next_version,
        )
        if not steps and not self.evaluate(task).completed:
            raise GenericAgentError(
                "GENERIC_PLAN_NOT_FOUND",
                "No exact-Version Action can advance the frozen Objective from current Knowledge",
            )
        plan = AgentPlan(
            task_id=task.id,
            version=next_version,
            status=AgentPlanStatus.ACTIVE,
            strategy_summary="Execute exact-Version actions for the frozen objective scope",
            replan_reason=reason,
            supersedes_plan_id=old_plan.id if old_plan else None,
            created_by_actor_key=actor.actor_key,
            source="GENERIC",
            planner_model=None,
            validation_status="PASSED",
            validation_errors=[],
        )
        self.db.add(plan)
        self.db.flush()
        for sequence, candidate in enumerate(steps, start=1):
            self.db.add(
                AgentStep(
                    plan_id=plan.id,
                    sequence=sequence,
                    description=candidate["description"],
                    execution_type=candidate["execution_type"],
                    assigned_actor_key=actor.actor_key,
                    action_intent=candidate["action_intent"],
                    constraints={"scenario_version_id": str(self.scope.scenario_version_id)},
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
        if reason is not None:
            task.replan_count += 1
        self.db.flush()
        return plan

    def execute_next(self, task: AgentTask) -> AgentStep | None:
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
                        AgentStepStatus.WAITING_FOR_WORLD_EVENT,
                    ]
                ),
            )
            .order_by(AgentStep.sequence)
        )
        if step is None:
            self.plan(task, reason="PLAN_EXHAUSTED")
            return self.execute_next(task)
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
                step.status = AgentStepStatus.FAILED
                step.failure_code = failure_code
                task.last_error_code = failure_code
                self.plan(task, reason=failure_code)
                self.db.flush()
                return step
        else:
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
                )
            except GenericActionError as exc:
                step.status = AgentStepStatus.FAILED
                step.failure_code = exc.code
                task.last_error_code = exc.code
                self.plan(task, reason=exc.code)
                self.db.flush()
                return step
            step.actual_result = {
                "operation_id": str(result.operation.id),
                "status": result.operation.status.value,
                "outcome": result.operation.outcome,
            }
            if result.applied is not None and result.applied.outcome.failure is not None:
                failure = result.applied.outcome.failure
                step.status = AgentStepStatus.FAILED
                step.failure_code = failure.code
                task.last_error_code = failure.code
                self.plan(task, reason=failure.code)
                self.db.flush()
                return step
            if result.operation.status == WorldOperationStatus.PENDING:
                step.status = AgentStepStatus.SUCCEEDED
                step.completed_at = datetime.now(UTC)
            else:
                step.status = AgentStepStatus.SUCCEEDED
                step.completed_at = datetime.now(UTC)
        if self.evaluate(task).completed:
            self._complete_task(task)
        self.db.flush()
        return step

    def evaluate(self, task: AgentTask) -> GenericObjectiveEvaluation:
        definition = self._definition()
        objective = self._objective(task, definition)
        evaluations: list[tuple[str, StrictScalar, bool]] = []
        for requirement in objective.completion_requirements:
            row = self.db.get(
                GameInstanceFactState,
                (self.scope.game_instance_id, requirement.node_key, requirement.fact_key),
            )
            if row is None:
                raise GenericAgentError(
                    "OBJECTIVE_TRUTH_MISSING", "Objective Truth is missing from this Instance"
                )
            evaluations.append(
                (
                    requirement.key,
                    row.truth_value,
                    row.truth_value in requirement.accepted_values,
                )
            )
        return GenericObjectiveEvaluation(
            objective.key,
            bool(evaluations) and all(item[2] for item in evaluations),
            tuple(evaluations),
        )

    def _candidate_steps(
        self,
        definition: ScenarioDefinitionV2,
        objective: ObjectiveDefinitionV2,
        actor: GameInstanceActor,
        *,
        reason: str | None,
        plan_version: int,
    ) -> list[dict[str, object]]:
        needed = [
            (requirement.node_key, requirement.fact_key)
            for prerequisite in objective.prerequisites
            for requirement in prerequisite.requirements
            if not self._known_requirement_satisfied(requirement)
        ] + [
            (requirement.node_key, requirement.fact_key)
            for requirement in objective.completion_requirements
            if not self._known_requirement_satisfied(requirement)
        ]
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
        for action in actions:
            effects = {
                (item.node_key, item.fact_key)
                for item in (
                    *action.planning.terminal_effects,
                    *action.planning.supporting_effects,
                )
            }
            matched = [item for item in needed if item in effects and item not in covered]
            if not matched or action.key not in actor.allowed_action_keys:
                continue
            target_key = matched[0][0]
            if not self._validate_known_action(definition, action, actor, target_key):
                continue
            parameters = self._default_parameters(action)
            arguments = {
                "action_key": action.key,
                "target_key": target_key,
                "parameters": parameters,
                "idempotency_key": (
                    f"task-{objective.key}-plan-{plan_version}-{self.scope.game_instance_id}-"
                    f"{action.key}"
                )[:160],
            }
            candidates.append(
                {
                    "description": f"Execute {action.name}",
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
            and node_state.visibility == Visibility.KNOWN
            and node_state.status != NodeStatus.LOCKED
            and action.required_interaction_key in target.interaction_keys
            and action.key in actor.allowed_action_keys
        )

    @staticmethod
    def _default_parameters(action: ActionDefinitionV2) -> dict[str, StrictScalar]:
        parameters: dict[str, StrictScalar] = {}
        for parameter in action.parameters:
            if parameter.default is not None:
                parameters[parameter.key] = parameter.default
            elif parameter.required:
                raise GenericAgentError(
                    "GENERIC_PLAN_PARAMETER_REQUIRED",
                    f"Action {action.key} needs a player/model-selected parameter",
                )
        return parameters

    def _known_requirement_satisfied(self, requirement) -> bool:  # type: ignore[no-untyped-def]
        row = self.db.get(
            GameInstanceFactState,
            (self.scope.game_instance_id, requirement.node_key, requirement.fact_key),
        )
        return bool(
            row
            and row.visibility == Visibility.KNOWN
            and row.truth_value in requirement.accepted_values
        )

    def _definition(self) -> ScenarioDefinitionV2:
        persisted = GameInstanceService(self.db).load(self.scope.game_instance_id)
        self.scope.assert_compatible(persisted)
        definition = (
            ScenarioVersionRepository(self.db).load(self.scope.scenario_version_id).definition
        )
        if not isinstance(definition, ScenarioDefinitionV2):
            raise GenericAgentError(
                "GENERIC_RUNTIME_SCHEMA_REQUIRED", "Generic Agent requires ScenarioDefinition v2"
            )
        return definition

    def _task_scope(self, task: AgentTask) -> None:
        if (
            task.game_instance_id != self.scope.game_instance_id
            or task.player_id != self.scope.player_id
            or task.objective_catalog_version
            != f"scenario-version:{self.scope.scenario_version_id}"
        ):
            raise GenericAgentError(
                "GENERIC_TASK_SCOPE_INVALID", "Task does not belong to this exact Version scope"
            )

    @staticmethod
    def _objective(task: AgentTask, definition: ScenarioDefinitionV2) -> ObjectiveDefinitionV2:
        keys = task.objective_scope_keys or []
        if len(keys) != 1:
            raise GenericAgentError("GENERIC_OBJECTIVE_SCOPE_INVALID", "One Objective is required")
        objective = next((item for item in definition.objectives if item.key == keys[0]), None)
        if objective is None:
            raise GenericAgentError(
                "GENERIC_OBJECTIVE_SCOPE_INVALID", "Objective is absent from exact Version"
            )
        return objective

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
        task.completed_at = datetime.now(UTC)


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


__all__ = [
    "GenericAgentError",
    "GenericAgentService",
    "GenericGoalResolution",
    "GenericGoalResolver",
    "GenericObjectiveEvaluation",
]
