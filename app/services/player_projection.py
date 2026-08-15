"""Player-safe Runtime projection; hidden Truth is never selected."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas.phase_d import (
    GameSummaryResponse,
    MissionRoadmapResponse,
    MissionRoadmapStageResponse,
    PlayerGameStateResponse,
    PublicActionBriefingResponse,
    PublicActionDebriefResponse,
    PublicActorResponse,
    PublicExecutionPhase,
    PublicFactResponse,
    PublicGameStatus,
    PublicKnowledgeChangeResponse,
    PublicNodeResponse,
    PublicPlanHistoryResponse,
    PublicPlanHistoryStatus,
    PublicPlanHistoryStepResponse,
    PublicPlanHistoryStepStatus,
    PublicPlanResponse,
    PublicPlanStepResponse,
    PublicResourceResponse,
    PublicStepStatus,
    PublicTaskResponse,
    PublicTaskStatus,
    PublicTaskSummaryResponse,
    PublicTimelineEventKind,
    PublicTimelineEventResponse,
)
from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    DecisionStatus,
    StepExecutionType,
)
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ActionDefinitionV2, ScenarioDefinitionV2
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
    PlayerExecutionCheckpoint,
    ScenarioVersion,
    WorldOperation,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceError, GameInstanceService
from app.services.game_lifecycle import GameLifecycleService
from app.services.mission_roadmap import MissionRoadmap, MissionRoadmapProjector
from app.services.player_pacing import PlayerExecutionPhase


class PlayerProjectionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def game_state(
        self,
        game_instance_id: GameInstanceId,
        *,
        selected_task_id: UUID | None = None,
    ) -> PlayerGameStateResponse:
        scope = GameInstanceService(self.db).load(game_instance_id)
        game = GameLifecycleService(self.db).get(game_instance_id)
        definition = ScenarioVersionRepository(self.db).load(scope.scenario_version_id).definition
        assert isinstance(definition, ScenarioDefinitionV2)
        node_definitions = {item.key: item for item in definition.world.nodes}
        resource_definitions = {item.key: item for item in definition.world.resources}
        role_definitions = {item.key: item for item in definition.actors.roles}
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
        actors = tuple(
            self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == game.id,
                    GameInstanceActor.status == "ACTIVE",
                )
            )
        )
        task_query = (
            select(AgentTask)
            .where(AgentTask.game_instance_id == game.id)
            .order_by(AgentTask.created_at)
        )
        task_rows = tuple(self.db.scalars(task_query))
        active_task = next(
            (item for item in reversed(task_rows) if item.status in _PUBLIC_ACTIVE_TASKS),
            None,
        )
        if selected_task_id is None:
            # The active Task is the player-facing default.  A terminal Task
            # can only be the fallback when the Instance has no active work.
            task = active_task or (task_rows[-1] if task_rows else None)
        else:
            task = next((item for item in task_rows if item.id == selected_task_id), None)
            if task is None:
                raise GameInstanceError("TASK_NOT_FOUND", "The requested Task is not in this Game")
        pending = (
            self.db.scalar(
                select(ActionDecisionRequest.id).where(
                    ActionDecisionRequest.game_instance_id == game.id,
                    ActionDecisionRequest.task_id == task.id,
                    ActionDecisionRequest.status == DecisionStatus.PENDING,
                )
            )
            if task is not None
            else None
        )
        return PlayerGameStateResponse(
            game=self._game_summary(game, active_task),
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
            actors=[
                PublicActorResponse(
                    key=item.actor_key,
                    name=item.name,
                    role_name=role_definitions[item.role_key].name,
                    current_node_name=node_definitions[item.current_node_key].name,
                )
                for item in actors
                if item.current_node_key in visible_node_keys
            ],
            current_task=(
                self.task(
                    task,
                    definition,
                    known_facts={
                        (item.node_key, item.fact_key): item.truth_value for item in known_facts
                    },
                )
                if task is not None
                else None
            ),
            task_history=[
                self._task_summary(item, sequence=index)
                for index, item in enumerate(task_rows, start=1)
            ],
            pending_approval_id=pending,
        )

    def _task_summary(self, task: AgentTask, *, sequence: int) -> PublicTaskSummaryResponse:
        return PublicTaskSummaryResponse(
            id=task.id,
            sequence=sequence,
            goal=task.goal_description,
            status=_task_status(task.status, task.last_error_code),
            execution_phase=PublicExecutionPhase(
                _execution_phase(task, self.db.get(PlayerExecutionCheckpoint, task.id)).value
            ),
            created_at=task.created_at,
            completed_at=task.completed_at,
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

    def task(
        self,
        task: AgentTask,
        definition: ScenarioDefinitionV2,
        *,
        known_facts: dict[tuple[str, str], str | int | bool],
    ) -> PublicTaskResponse:
        plans = tuple(
            self.db.scalars(
                select(AgentPlan).where(AgentPlan.task_id == task.id).order_by(AgentPlan.version)
            )
        )
        actors = {
            item.actor_key: item.name
            for item in self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == task.game_instance_id
                )
            )
        }
        steps_by_plan = {
            plan.id: tuple(
                self.db.scalars(
                    select(AgentStep)
                    .where(AgentStep.plan_id == plan.id)
                    .order_by(AgentStep.sequence)
                )
            )
            for plan in plans
        }
        latest = plans[-1] if plans else None
        checkpoint = self.db.get(PlayerExecutionCheckpoint, task.id)
        phase = _execution_phase(task, checkpoint)
        public_plan = (
            PublicPlanResponse(
                strategy_summary=latest.strategy_summary,
                updated=latest.version > 1,
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
                    for step in steps_by_plan[latest.id]
                    if step.execution_type == StepExecutionType.TOOL
                ],
            )
            if latest is not None
            else None
        )
        objective_names = {item.key: item.name for item in definition.objectives}
        roadmap = MissionRoadmapProjector().project(
            definition,
            tuple(task.objective_scope_keys or ()),
            known_facts,
        )
        current_step = _next_tool_step(latest, steps_by_plan)
        if checkpoint is not None and checkpoint.last_action_step_id is not None:
            last_action_step = self.db.get(AgentStep, checkpoint.last_action_step_id)
        else:
            last_action_step = None
        return PublicTaskResponse(
            id=task.id,
            version=task.version,
            goal=task.goal_description,
            status=_task_status(task.status, task.last_error_code),
            execution_phase=PublicExecutionPhase(phase.value),
            pacing_version=checkpoint.version if checkpoint is not None else 1,
            objective_names=[
                objective_names[key]
                for key in task.objective_scope_keys or ()
                if key in objective_names
            ],
            roadmap=MissionRoadmapResponse(
                stages=[
                    MissionRoadmapStageResponse(
                        key=stage.key,
                        name=stage.name,
                        description=stage.description,
                        status=stage.status.value,
                        objective_key=stage.objective_key,
                    )
                    for stage in roadmap.stages
                ]
            ),
            plan=public_plan,
            plan_history=[
                _plan_history_entry(
                    plan,
                    steps_by_plan[plan.id],
                    definition,
                    actors,
                    task_status=task.status,
                    is_latest=plan == latest,
                    current_step_id=(
                        current_step.id
                        if plan == latest
                        and current_step is not None
                        and phase
                        in (
                            PlayerExecutionPhase.AWAITING_ACTION_ACK,
                            PlayerExecutionPhase.APPROVAL_REQUIRED,
                        )
                        else None
                    ),
                )
                for plan in plans
            ],
            timeline=self._timeline(task, definition, plans, steps_by_plan, actors, checkpoint),
            briefing=(
                self._briefing(current_step, definition, actors, roadmap)
                if current_step is not None
                and phase
                in (
                    PlayerExecutionPhase.AWAITING_ACTION_ACK,
                    PlayerExecutionPhase.APPROVAL_REQUIRED,
                )
                else None
            ),
            debrief=(
                self._debrief(last_action_step, definition, plans, steps_by_plan)
                if last_action_step is not None
                and phase
                in (
                    PlayerExecutionPhase.AWAITING_DEBRIEF_ACK,
                    PlayerExecutionPhase.AWAITING_REPLAN_ACK,
                )
                else None
            ),
            explanation=(
                "当前世界状态下没有可继续执行的合法行动"
                if task.status in (AgentTaskStatus.FAILED, AgentTaskStatus.BLOCKED)
                else None
            ),
        )

    def _timeline(
        self,
        task: AgentTask,
        definition: ScenarioDefinitionV2,
        plans: tuple[AgentPlan, ...],
        steps_by_plan: dict[UUID, tuple[AgentStep, ...]],
        actors: dict[str, str],
        checkpoint: PlayerExecutionCheckpoint | None,
    ) -> list[PublicTimelineEventResponse]:
        operations = tuple(
            self.db.scalars(
                select(WorldOperation)
                .where(WorldOperation.task_id == task.id)
                .order_by(WorldOperation.created_at)
            )
        )
        decisions = tuple(
            self.db.scalars(
                select(ActionDecisionRequest)
                .where(ActionDecisionRequest.task_id == task.id)
                .order_by(ActionDecisionRequest.created_at)
            )
        )
        operations_by_step: dict[UUID, list[WorldOperation]] = {}
        for operation in operations:
            if operation.source_step_id is not None:
                operations_by_step.setdefault(operation.source_step_id, []).append(operation)
        decisions_by_step = {
            decision.source_step_id: decision
            for decision in decisions
            if decision.source_step_id is not None
        }
        events = [
            PublicTimelineEventResponse(
                id=f"task:{task.id}:accepted",
                kind=PublicTimelineEventKind.GOAL_ACCEPTED,
                title="任务已接受",
                detail=task.goal_description,
                occurred_at=task.created_at,
                duration_ms=_goal_resolution_duration(task),
            )
        ]
        plan_durations = _plan_durations(task)
        for plan in plans:
            if plan.version == 1:
                events.append(
                    PublicTimelineEventResponse(
                        id=f"plan:{plan.id}:created",
                        kind=PublicTimelineEventKind.PLAN_CREATED,
                        title="Agent 已完成计划",
                        detail=_first_action_name(definition, steps_by_plan[plan.id]),
                        occurred_at=plan.created_at,
                        duration_ms=plan_durations.get(plan.version),
                    )
                )
            else:
                events.append(
                    PublicTimelineEventResponse(
                        id=f"plan:{plan.id}",
                        kind=PublicTimelineEventKind.PLAN_UPDATED,
                        title="Agent 已重新规划",
                        detail=_first_action_name(definition, steps_by_plan[plan.id]),
                        occurred_at=plan.created_at,
                        duration_ms=plan_durations.get(plan.version),
                    )
                )
            plan_steps = steps_by_plan[plan.id]
            for step in plan_steps:
                if step.execution_type != StepExecutionType.TOOL:
                    continue
                decision = decisions_by_step.get(step.id)
                if decision is not None:
                    if decision.status == DecisionStatus.PENDING:
                        continue
                    decision_action = _action_definition(definition, step)
                    decision_kind = {
                        DecisionStatus.APPROVED: PublicTimelineEventKind.APPROVAL_APPROVED,
                        DecisionStatus.CONSUMED: PublicTimelineEventKind.APPROVAL_APPROVED,
                        DecisionStatus.REJECTED: PublicTimelineEventKind.APPROVAL_REJECTED,
                        DecisionStatus.CANCELLED: PublicTimelineEventKind.TASK_BLOCKED,
                    }[decision.status]
                    events.append(
                        PublicTimelineEventResponse(
                            id=f"decision:{decision.id}",
                            kind=decision_kind,
                            title=(
                                decision_action.name
                                if decision_action is not None
                                else step.description
                            ),
                            actor_name=actors.get(step.assigned_actor_key, step.assigned_actor_key),
                            occurred_at=decision.decided_at or decision.created_at,
                        )
                    )
                if _action_cycle_finished(step, plan_steps):
                    action_operation = next(iter(operations_by_step.get(step.id, [])), None)
                    debrief = self._debrief(step, definition, plans, steps_by_plan)
                    assert debrief is not None
                    events.append(
                        PublicTimelineEventResponse(
                            id=f"result:{step.id}",
                            kind=PublicTimelineEventKind.ACTION_RESULT,
                            title=debrief.action_name,
                            actor_name=actors.get(step.assigned_actor_key, step.assigned_actor_key),
                            result_summary=debrief.result_summary,
                            success=debrief.success,
                            knowledge_changes=(
                                _knowledge_changes(action_operation)
                                if action_operation is not None
                                else []
                            ),
                            occurred_at=_action_cycle_completed_at(step, plan_steps),
                        )
                    )
        terminal_kind = {
            AgentTaskStatus.SUCCEEDED: PublicTimelineEventKind.TASK_COMPLETED,
            AgentTaskStatus.FAILED: PublicTimelineEventKind.TASK_BLOCKED,
            AgentTaskStatus.BLOCKED: PublicTimelineEventKind.TASK_BLOCKED,
            AgentTaskStatus.ABORTED: PublicTimelineEventKind.TASK_ABORTED,
        }.get(task.status)
        if terminal_kind is not None:
            events.append(
                PublicTimelineEventResponse(
                    id=f"task:{task.id}:terminal",
                    kind=terminal_kind,
                    title=task.goal_description,
                    detail=None,
                    occurred_at=task.completed_at,
                )
            )
        return events

    def _briefing(
        self,
        step: AgentStep,
        definition: ScenarioDefinitionV2,
        actors: dict[str, str],
        roadmap: MissionRoadmap,
    ) -> PublicActionBriefingResponse:
        action = _action_definition(definition, step)
        target_key = str(step.tool_arguments.get("target_key", ""))
        target = next((item for item in definition.world.nodes if item.key == target_key), None)
        current_stage = next(
            (item for item in roadmap.stages if item.status.value == "CURRENT"),
            None,
        )
        purpose = (
            action.description
            if action is not None and action.description
            else (current_stage.description if current_stage is not None else "")
            or (current_stage.name if current_stage is not None else "")
            or step.description
        )
        return PublicActionBriefingResponse(
            step_id=step.id,
            action_name=action.name if action is not None else step.description,
            actor_name=actors.get(step.assigned_actor_key, step.assigned_actor_key),
            target_name=target.name if target is not None else target_key,
            purpose=purpose,
        )

    def _debrief(
        self,
        step: AgentStep,
        definition: ScenarioDefinitionV2,
        plans: tuple[AgentPlan, ...],
        steps_by_plan: dict[UUID, tuple[AgentStep, ...]],
    ) -> PublicActionDebriefResponse:
        action = _action_definition(definition, step)
        plan = next(item for item in plans if item.id == step.plan_id)
        plan_steps = steps_by_plan[plan.id]
        operation = self.db.scalar(
            select(WorldOperation)
            .where(WorldOperation.source_step_id == step.id)
            .order_by(WorldOperation.created_at.desc())
        )
        failed = step.status == AgentStepStatus.FAILED or bool(
            isinstance(operation.outcome, dict) and operation.outcome.get("failure")
            if operation is not None
            else False
        )
        wait_step = _matching_wait(step, plan_steps)
        if wait_step is not None and wait_step.status == AgentStepStatus.FAILED:
            failed = True
        result_summary = _public_result_summary(action, operation, failed)
        newer_plans = [item for item in plans if item.version > plan.version]
        return PublicActionDebriefResponse(
            step_id=step.id,
            action_name=action.name if action is not None else step.description,
            success=not failed,
            result_summary=result_summary,
            knowledge_changes=_knowledge_changes(operation) if operation is not None else [],
            plan_adjusted=bool(newer_plans),
            plan_adjustment_summary=(
                _next_action_name(definition, steps_by_plan[newer_plans[-1].id])
                if newer_plans
                else None
            ),
        )


def _task_status(status: AgentTaskStatus, error_code: str | None) -> PublicTaskStatus:
    if status == AgentTaskStatus.BLOCKED:
        if error_code == "BLOCKED_BY_PLAYER_DECISION":
            return PublicTaskStatus.BLOCKED_BY_PLAYER_DECISION
        if error_code == "MODEL_PLAN_REJECTED":
            return PublicTaskStatus.MODEL_PLAN_REJECTED
        return PublicTaskStatus.UNREACHABLE_IN_CURRENT_STATE
    return {
        AgentTaskStatus.ACTIVE: PublicTaskStatus.ACTIVE,
        AgentTaskStatus.WAITING_FOR_WORLD_EVENT: PublicTaskStatus.ACTIVE,
        AgentTaskStatus.WAITING_FOR_PLAYER_ACTION: PublicTaskStatus.NEEDS_PLAYER_INPUT,
        AgentTaskStatus.REQUIRES_PLAYER_DECISION: PublicTaskStatus.NEEDS_PLAYER_INPUT,
        AgentTaskStatus.SUCCEEDED: PublicTaskStatus.COMPLETED,
        AgentTaskStatus.FAILED: PublicTaskStatus.UNREACHABLE_IN_CURRENT_STATE,
        AgentTaskStatus.ABORTED: PublicTaskStatus.ABORTED,
    }[status]


def _plan_history_entry(
    plan: AgentPlan,
    steps: tuple[AgentStep, ...],
    definition: ScenarioDefinitionV2,
    actors: dict[str, str],
    *,
    task_status: AgentTaskStatus,
    is_latest: bool,
    current_step_id: UUID | None,
) -> PublicPlanHistoryResponse:
    tool_steps = tuple(step for step in steps if step.execution_type == StepExecutionType.TOOL)
    public_steps = [
        PublicPlanHistoryStepResponse(
            id=step.id,
            sequence=step.sequence,
            action_name=(
                action.name
                if (action := _action_definition(definition, step))
                else step.description
            ),
            assigned_actor_name=actors.get(step.assigned_actor_key, step.assigned_actor_key),
            status=_plan_history_step_status(
                step,
                plan,
                current_step_id,
                steps,
                task_terminal=is_latest
                and task_status
                in (AgentTaskStatus.FAILED, AgentTaskStatus.BLOCKED, AgentTaskStatus.ABORTED),
            ),
            result_summary=(
                "行动未完成" if _plan_action_failed(step, steps) else _result_summary(step)
            ),
        )
        for step in tool_steps
    ]
    failed_step = next(
        (step for step in public_steps if step.status == PublicPlanHistoryStepStatus.FAILED),
        None,
    )
    status = {
        AgentPlanStatus.ACTIVE: PublicPlanHistoryStatus.EXECUTING,
        AgentPlanStatus.SUPERSEDED: PublicPlanHistoryStatus.ADJUSTED,
        AgentPlanStatus.SUCCEEDED: PublicPlanHistoryStatus.COMPLETED,
        AgentPlanStatus.FAILED: PublicPlanHistoryStatus.BLOCKED,
    }[plan.status]
    if (
        is_latest
        and plan.status == AgentPlanStatus.ACTIVE
        and task_status
        in (AgentTaskStatus.FAILED, AgentTaskStatus.BLOCKED, AgentTaskStatus.ABORTED)
    ):
        status = PublicPlanHistoryStatus.BLOCKED
    return PublicPlanHistoryResponse(
        id=plan.id,
        ordinal=plan.version,
        status=status,
        completed_steps=sum(
            step.status == PublicPlanHistoryStepStatus.COMPLETED for step in public_steps
        ),
        total_steps=len(public_steps),
        failed_step_name=failed_step.action_name if failed_step is not None else None,
        steps=public_steps,
    )


def _plan_history_step_status(
    step: AgentStep,
    plan: AgentPlan,
    current_step_id: UUID | None,
    plan_steps: tuple[AgentStep, ...],
    *,
    task_terminal: bool,
) -> PublicPlanHistoryStepStatus:
    if _plan_action_failed(step, plan_steps):
        return PublicPlanHistoryStepStatus.FAILED
    if step.status == AgentStepStatus.SUCCEEDED:
        return PublicPlanHistoryStepStatus.COMPLETED
    if task_terminal or plan.status in (AgentPlanStatus.SUPERSEDED, AgentPlanStatus.SUCCEEDED):
        return PublicPlanHistoryStepStatus.CANCELLED
    if step.id == current_step_id:
        return PublicPlanHistoryStepStatus.CURRENT
    if step.status in (AgentStepStatus.BLOCKED, AgentStepStatus.SKIPPED):
        return PublicPlanHistoryStepStatus.CANCELLED
    return PublicPlanHistoryStepStatus.PLANNED


def _plan_action_failed(step: AgentStep, plan_steps: tuple[AgentStep, ...]) -> bool:
    if step.status == AgentStepStatus.FAILED:
        return True
    wait_step = _matching_wait(step, plan_steps)
    return wait_step is not None and wait_step.status == AgentStepStatus.FAILED


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
        return "行动未完成"
    if step.status != AgentStepStatus.SUCCEEDED:
        return None
    return "行动已完成"


def _execution_phase(
    task: AgentTask, checkpoint: PlayerExecutionCheckpoint | None
) -> PlayerExecutionPhase:
    if task.status == AgentTaskStatus.SUCCEEDED:
        return PlayerExecutionPhase.COMPLETED
    if task.status in (AgentTaskStatus.FAILED, AgentTaskStatus.BLOCKED):
        return PlayerExecutionPhase.BLOCKED
    if task.status == AgentTaskStatus.ABORTED:
        return PlayerExecutionPhase.ABORTED
    if task.status == AgentTaskStatus.REQUIRES_PLAYER_DECISION:
        return PlayerExecutionPhase.APPROVAL_REQUIRED
    if checkpoint is not None:
        return PlayerExecutionPhase(checkpoint.phase)
    return PlayerExecutionPhase.AWAITING_ACTION_ACK


def _next_tool_step(
    plan: AgentPlan | None, steps_by_plan: dict[UUID, tuple[AgentStep, ...]]
) -> AgentStep | None:
    if plan is None:
        return None
    return next(
        (
            step
            for step in steps_by_plan[plan.id]
            if step.execution_type == StepExecutionType.TOOL
            and step.status in (AgentStepStatus.PENDING, AgentStepStatus.REQUIRES_PLAYER_DECISION)
        ),
        None,
    )


def _action_definition(
    definition: ScenarioDefinitionV2, step: AgentStep
) -> ActionDefinitionV2 | None:
    action_key = step.tool_arguments.get("action_key")
    return next((item for item in definition.actions if item.key == action_key), None)


def _matching_wait(action_step: AgentStep, plan_steps: tuple[AgentStep, ...]) -> AgentStep | None:
    return next(
        (
            step
            for step in plan_steps
            if step.sequence > action_step.sequence
            and step.action_intent == action_step.action_intent
            and step.execution_type == StepExecutionType.WAIT_FOR_WORLD_EVENT
        ),
        None,
    )


def _action_cycle_finished(action_step: AgentStep, plan_steps: tuple[AgentStep, ...]) -> bool:
    if action_step.status == AgentStepStatus.FAILED:
        return True
    wait_step = _matching_wait(action_step, plan_steps)
    if wait_step is not None:
        return wait_step.status in (AgentStepStatus.SUCCEEDED, AgentStepStatus.FAILED)
    return action_step.status == AgentStepStatus.SUCCEEDED


def _action_cycle_completed_at(
    action_step: AgentStep, plan_steps: tuple[AgentStep, ...]
) -> datetime | None:
    wait_step = _matching_wait(action_step, plan_steps)
    if wait_step is not None and wait_step.completed_at is not None:
        return wait_step.completed_at
    return action_step.completed_at or action_step.started_at


def _next_action_name(definition: ScenarioDefinitionV2, steps: tuple[AgentStep, ...]) -> str | None:
    step = next(
        (
            item
            for item in steps
            if item.execution_type == StepExecutionType.TOOL
            and item.status in (AgentStepStatus.PENDING, AgentStepStatus.REQUIRES_PLAYER_DECISION)
        ),
        None,
    )
    if step is None:
        return None
    action = _action_definition(definition, step)
    return action.name if action is not None else step.description


def _first_action_name(
    definition: ScenarioDefinitionV2, steps: tuple[AgentStep, ...]
) -> str | None:
    """Return the plan's creation-time first Action without reading step status.

    Mission history is reconstructed from persisted Plan/Step inputs.  Using
    the first tool step instead of the current pending step keeps historical
    PLAN_CREATED/PLAN_UPDATED event text stable after execution advances.
    """

    step = next(
        (item for item in steps if item.execution_type == StepExecutionType.TOOL),
        None,
    )
    if step is None:
        return None
    action = _action_definition(definition, step)
    return action.name if action is not None else step.description


def _public_result_summary(
    action: ActionDefinitionV2 | None,
    operation: WorldOperation | None,
    failed: bool,
) -> str:
    if failed:
        return "行动未能达成预期结果"
    if operation is not None and isinstance(operation.outcome, dict):
        code = operation.outcome.get("outcome_code")
        if action is not None:
            outcome = next((item for item in action.expected_outcomes if item.code == code), None)
            if outcome is not None:
                return outcome.name
    return "行动已完成"


def _knowledge_changes(operation: WorldOperation) -> list[PublicKnowledgeChangeResponse]:
    if not isinstance(operation.outcome, dict):
        return []
    payload = operation.outcome.get("knowledge_changes")
    if not isinstance(payload, list):
        return []
    changes: list[PublicKnowledgeChangeResponse] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            changes.append(PublicKnowledgeChangeResponse.model_validate(item))
        except ValueError:
            continue
    return changes


def _provider_calls(task: AgentTask) -> tuple[dict[str, object], ...]:
    metadata = task.objective_resolution_metadata
    if not isinstance(metadata, dict):
        return ()
    calls = metadata.get("provider_calls")
    if not isinstance(calls, list):
        return ()
    return tuple(item for item in calls if isinstance(item, dict))


def _call_latency_ms(call: dict[str, object]) -> int:
    value = call.get("latency_ms")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _goal_resolution_duration(task: AgentTask) -> int | None:
    """Return the persisted goal-resolution operation duration, if available."""

    for snapshot in _operation_durations(task):
        if snapshot.get("kind") == "GOAL_RESOLUTION":
            duration = snapshot.get("duration_ms")
            if isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0:
                return duration

    duration = sum(
        _call_latency_ms(call)
        for call in _provider_calls(task)
        if call.get("call_type") == "GOAL_SELECTION"
    )
    return duration or None


def _plan_durations(task: AgentTask) -> dict[int, int]:
    """Map persisted plan ordinals to provider operation durations.

    A plan call may be followed by bounded ``REPAIR`` calls.  The metadata is
    append-only and ordered, so grouping each INITIAL_PLAN/REPLAN with its
    repairs gives a stable presentation snapshot without adding a heartbeat or
    timing subsystem.
    """

    operation_durations: dict[int, int] = {}
    for snapshot in _operation_durations(task):
        plan_version = snapshot.get("plan_version")
        duration = snapshot.get("duration_ms")
        if (
            isinstance(plan_version, int)
            and not isinstance(plan_version, bool)
            and isinstance(duration, int)
            and not isinstance(duration, bool)
            and duration >= 0
        ):
            operation_durations[plan_version] = duration
    if operation_durations:
        return operation_durations

    durations: dict[int, int] = {}
    plan_version = 0
    current_duration = 0
    for call in _provider_calls(task):
        call_type = call.get("call_type")
        if call_type in ("INITIAL_PLAN", "REPLAN"):
            if plan_version:
                durations[plan_version] = current_duration or 0
            plan_version += 1
            current_duration = _call_latency_ms(call)
        elif call_type == "REPAIR" and plan_version:
            current_duration += _call_latency_ms(call)
    if plan_version:
        durations[plan_version] = current_duration or 0
    return {key: value for key, value in durations.items() if value > 0}


def _operation_durations(task: AgentTask) -> tuple[dict[str, object], ...]:
    metadata = task.objective_resolution_metadata
    if not isinstance(metadata, dict):
        return ()
    durations = metadata.get("operation_durations")
    if not isinstance(durations, list):
        return ()
    return tuple(item for item in durations if isinstance(item, dict))


__all__ = ["PlayerProjectionService"]
