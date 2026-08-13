"""Player-safe Runtime projection; hidden Truth is never selected."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas.phase_d import (
    GameSummaryResponse,
    PlayerGameStateResponse,
    PublicFactResponse,
    PublicGameStatus,
    PublicNodeResponse,
    PublicPlanResponse,
    PublicPlanStepResponse,
    PublicResourceResponse,
    PublicStepStatus,
    PublicTaskResponse,
    PublicTaskStatus,
)
from app.domain.enums import AgentStepStatus, AgentTaskStatus, DecisionStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentPlan,
    AgentStep,
    AgentTask,
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceResourceState,
    ScenarioVersion,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import GameLifecycleService


class PlayerProjectionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def game_state(self, game_instance_id: GameInstanceId) -> PlayerGameStateResponse:
        scope = GameInstanceService(self.db).load(game_instance_id)
        game = GameLifecycleService(self.db).get(game_instance_id)
        definition = ScenarioVersionRepository(self.db).load(scope.scenario_version_id).definition
        assert isinstance(definition, ScenarioDefinitionV2)
        node_definitions = {item.key: item for item in definition.world.nodes}
        resource_definitions = {item.key: item for item in definition.world.resources}
        visible_nodes = tuple(
            self.db.scalars(
                select(GameInstanceNodeState).where(
                    GameInstanceNodeState.game_instance_id == game.id,
                    GameInstanceNodeState.visibility == Visibility.KNOWN,
                )
            )
        )
        visible_node_keys = {item.node_key for item in visible_nodes}
        known_facts = tuple(
            self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == game.id,
                    GameInstanceFactState.visibility == Visibility.KNOWN,
                    GameInstanceFactState.node_key.in_(visible_node_keys),
                )
            )
        )
        resources = tuple(
            self.db.scalars(
                select(GameInstanceResourceState).where(
                    GameInstanceResourceState.game_instance_id == game.id
                )
            )
        )
        task = self.db.scalar(
            select(AgentTask)
            .where(AgentTask.game_instance_id == game.id)
            .order_by(AgentTask.created_at.desc())
        )
        pending = self.db.scalar(
            select(ActionDecisionRequest.id).where(
                ActionDecisionRequest.game_instance_id == game.id,
                ActionDecisionRequest.status == DecisionStatus.PENDING,
            )
        )
        return PlayerGameStateResponse(
            game=self._game_summary(game, task),
            visible_nodes=[
                PublicNodeResponse(
                    key=item.node_key,
                    name=node_definitions[item.node_key].name,
                    accessible=item.status.value != "LOCKED",
                )
                for item in visible_nodes
            ],
            known_facts=[
                PublicFactResponse(
                    node_key=item.node_key,
                    fact_key=item.fact_key,
                    name=next(
                        fact.name
                        for fact in node_definitions[item.node_key].facts
                        if fact.key == item.fact_key
                    ),
                    value=item.truth_value,
                )
                for item in known_facts
            ],
            resources=[
                PublicResourceResponse(
                    key=item.resource_key,
                    name=resource_definitions[item.resource_key].name,
                    value=item.value,
                    reserved_value=item.reserved_value,
                )
                for item in resources
            ],
            current_task=self.task(task, definition) if task is not None else None,
            pending_approval_id=pending,
        )

    def _game_summary(self, game: GameInstance, task: AgentTask | None) -> GameSummaryResponse:
        version = self.db.get(ScenarioVersion, game.scenario_version_id)
        assert version is not None
        active_task_id = (
            task.id if task is not None and task.status in _PUBLIC_ACTIVE_TASKS else None
        )
        return GameSummaryResponse(
            id=game.id,
            scenario_id=version.scenario_id,
            scenario_version_id=version.id,
            scenario_version_number=version.version_number,
            scenario_content_hash=version.content_hash,
            status=PublicGameStatus(game.status.value),
            active_task_id=active_task_id,
            created_at=game.created_at,
            updated_at=game.updated_at,
        )

    def task(self, task: AgentTask, definition: ScenarioDefinitionV2) -> PublicTaskResponse:
        plan = self.db.scalar(
            select(AgentPlan).where(AgentPlan.task_id == task.id).order_by(AgentPlan.version.desc())
        )
        actors = {
            item.actor_key: item.name
            for item in self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == task.game_instance_id
                )
            )
        }
        public_plan = None
        if plan is not None:
            steps = tuple(
                self.db.scalars(
                    select(AgentStep)
                    .where(AgentStep.plan_id == plan.id)
                    .order_by(AgentStep.sequence)
                )
            )
            public_plan = PublicPlanResponse(
                version=plan.version,
                strategy_summary=plan.strategy_summary,
                updated=plan.version > 1,
                steps=[
                    PublicPlanStepResponse(
                        id=step.id,
                        sequence=step.sequence,
                        description=step.description,
                        assigned_actor_name=actors.get(
                            step.assigned_actor_key, step.assigned_actor_key
                        ),
                        status=_step_status(step.status),
                        result_summary=_result_summary(step),
                    )
                    for step in steps
                ],
            )
        objective_names = {item.key: item.name for item in definition.objectives}
        return PublicTaskResponse(
            id=task.id,
            version=task.version,
            goal=task.goal_description,
            status=_task_status(task.status, task.last_error_code),
            objective_names=[
                objective_names[key]
                for key in task.objective_scope_keys or ()
                if key in objective_names
            ],
            plan=public_plan,
            explanation=task.last_error_code,
        )


def _task_status(status: AgentTaskStatus, error_code: str | None) -> PublicTaskStatus:
    if status == AgentTaskStatus.BLOCKED:
        return (
            PublicTaskStatus.BLOCKED_BY_PLAYER_DECISION
            if error_code == "BLOCKED_BY_PLAYER_DECISION"
            else PublicTaskStatus.UNREACHABLE_IN_CURRENT_STATE
        )
    return {
        AgentTaskStatus.ACTIVE: PublicTaskStatus.ACTIVE,
        AgentTaskStatus.WAITING_FOR_WORLD_EVENT: PublicTaskStatus.ACTIVE,
        AgentTaskStatus.WAITING_FOR_PLAYER_ACTION: PublicTaskStatus.NEEDS_PLAYER_INPUT,
        AgentTaskStatus.REQUIRES_PLAYER_DECISION: PublicTaskStatus.NEEDS_PLAYER_INPUT,
        AgentTaskStatus.SUCCEEDED: PublicTaskStatus.COMPLETED,
        AgentTaskStatus.FAILED: PublicTaskStatus.UNREACHABLE_IN_CURRENT_STATE,
        AgentTaskStatus.ABORTED: PublicTaskStatus.ABORTED,
    }[status]


_PUBLIC_ACTIVE_TASKS = (
    AgentTaskStatus.ACTIVE,
    AgentTaskStatus.REQUIRES_PLAYER_DECISION,
    AgentTaskStatus.WAITING_FOR_PLAYER_ACTION,
    AgentTaskStatus.WAITING_FOR_WORLD_EVENT,
)


def _step_status(status: AgentStepStatus) -> PublicStepStatus:
    if status == AgentStepStatus.SUCCEEDED or status == AgentStepStatus.SKIPPED:
        return PublicStepStatus.COMPLETED
    if status == AgentStepStatus.FAILED:
        return PublicStepStatus.FAILED
    if status == AgentStepStatus.BLOCKED:
        return PublicStepStatus.BLOCKED
    if status == AgentStepStatus.PENDING:
        return PublicStepStatus.PENDING
    return PublicStepStatus.CURRENT


def _result_summary(step: AgentStep) -> str | None:
    if step.failure_code:
        return step.failure_code.replace("_", " ").title()
    if step.status != AgentStepStatus.SUCCEEDED:
        return None
    result = step.actual_result or {}
    outcome = result.get("outcome")
    if isinstance(outcome, dict) and outcome.get("outcome_code"):
        return str(outcome["outcome_code"]).replace("_", " ").title()
    return "Completed"


__all__ = ["PlayerProjectionService"]
