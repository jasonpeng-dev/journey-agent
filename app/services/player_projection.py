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
    PublicActionLocationResponse,
    PublicActionRequirementResponse,
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
    PublicPlanInterruptionKind,
    PublicPlanInterruptionResponse,
    PublicPlanningAttemptResponse,
    PublicPlanningCycleResponse,
    PublicPlanningDraftStepResponse,
    PublicPlanningViolationResponse,
    PublicPlanResponse,
    PublicPlanStepResponse,
    PublicRelationResponse,
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
    WorldOperationStatus,
)
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ActionBehavior, ActionDefinitionV2, ScenarioDefinitionV2
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentPlan,
    AgentStep,
    AgentTask,
    GameInstance,
    GameInstanceActor,
    GameInstanceResourceState,
    PlanningAttempt,
    PlanningCycle,
    PlayerExecutionCheckpoint,
    ScenarioVersion,
    WorldOperation,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceError, GameInstanceService
from app.services.game_lifecycle import GameLifecycleService
from app.services.knowledge_projection import SharedKnowledgeProjection
from app.services.mission_roadmap import MissionRoadmap, MissionRoadmapProjector
from app.services.player_pacing import PlayerExecutionPhase
from app.services.spatial_projection import SpatialDisplayProjector, SpatialNodeProjection


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
        spatial = SpatialDisplayProjector(definition)
        knowledge_projection = SharedKnowledgeProjection(self.db, scope, definition)
        visible_resource_identities = {
            (item.resource_key, item.region_key, item.pool_key)
            for item in knowledge_projection.visible_resource_pools()
        }
        visible_nodes = knowledge_projection.known_node_rows()
        visible_node_keys = {item.node_key for item in visible_nodes}
        known_facts = knowledge_projection.known_fact_rows()
        known_relations = knowledge_projection.known_relations()
        known_action_requirements = knowledge_projection.known_action_requirements()
        node_projections: dict[str, SpatialNodeProjection] = {}
        for item in visible_nodes:
            projection = spatial.node(item.node_key)
            if projection is not None:
                node_projections[item.node_key] = projection
        resources = tuple(
            self.db.scalars(
                select(GameInstanceResourceState).where(
                    GameInstanceResourceState.game_instance_id == game.id
                )
            )
        )
        resources = tuple(
            item
            for item in resources
            if (item.resource_key, item.scope_node_key, item.pool_key)
            in visible_resource_identities
        )
        actors = knowledge_projection.actor_rows()
        task_query = (
            select(AgentTask)
            .where(AgentTask.game_instance_id == game.id)
            .order_by(AgentTask.created_at)
        )
        task_rows = tuple(self.db.scalars(task_query))
        objective_names_by_key = {item.key: item.name for item in definition.objectives}
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
                    node_type_key=(
                        node_projections[item.node_key].node_type_key
                        if node_projections[item.node_key] is not None
                        else None
                    ),
                    region_key=(
                        node_projections[item.node_key].region_key
                        if node_projections[item.node_key] is not None
                        else None
                    ),
                    region_name=(
                        node_projections[item.node_key].region_name
                        if node_projections[item.node_key] is not None
                        else None
                    ),
                    endpoint_region_keys=list(
                        node_projections[item.node_key].endpoint_region_keys
                        if node_projections[item.node_key] is not None
                        else ()
                    ),
                    endpoint_region_names=list(
                        node_projections[item.node_key].endpoint_region_names
                        if node_projections[item.node_key] is not None
                        else ()
                    ),
                    associated_known_resources=(
                        knowledge_projection.associated_known_resources(item.node_key)
                        if (
                            definition.metadata.locality.enabled
                            and definition.metadata.locality.facility_node_type_key
                            and node_definitions[item.node_key].node_type_key
                            == definition.metadata.locality.facility_node_type_key
                        )
                        else []
                    ),
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
                    node_name=node_definitions[item.node_key].name,
                    node_type_key=(
                        node_projections[item.node_key].node_type_key
                        if node_projections[item.node_key] is not None
                        else None
                    ),
                    region_key=(
                        node_projections[item.node_key].region_key
                        if node_projections[item.node_key] is not None
                        else None
                    ),
                    region_name=(
                        node_projections[item.node_key].region_name
                        if node_projections[item.node_key] is not None
                        else None
                    ),
                    endpoint_region_keys=list(
                        node_projections[item.node_key].endpoint_region_keys
                        if node_projections[item.node_key] is not None
                        else ()
                    ),
                    endpoint_region_names=list(
                        node_projections[item.node_key].endpoint_region_names
                        if node_projections[item.node_key] is not None
                        else ()
                    ),
                )
                for item in known_facts
            ],
            known_relations=[
                PublicRelationResponse(
                    relation_key=(
                        str(item["relation_key"]) if item.get("relation_key") is not None else None
                    ),
                    source_node_key=str(item["source_node_key"]),
                    relation_type_key=str(item["relation_type_key"]),
                    target_node_key=str(item["target_node_key"]),
                    source_node_name=(
                        node_definitions[str(item["source_node_key"])].name
                        if str(item["source_node_key"]) in node_definitions
                        else None
                    ),
                    target_node_name=(
                        node_definitions[str(item["target_node_key"])].name
                        if str(item["target_node_key"]) in node_definitions
                        else None
                    ),
                )
                for item in known_relations
            ],
            known_action_requirements=[
                PublicActionRequirementResponse(**item) for item in known_action_requirements
            ],
            resources=[
                PublicResourceResponse(
                    key=item.resource_key,
                    name=resource_definitions[item.resource_key].name,
                    value=item.value,
                    reserved_value=item.reserved_value,
                    pool_key=item.pool_key,
                    facility_key=item.facility_key,
                    availability=(
                        item.availability.value
                        if hasattr(item.availability, "value")
                        else item.availability
                    ),
                    availability_requirement=knowledge_projection.known_requirement(
                        item.availability_requirement
                    ),
                    availability_requirement_status=knowledge_projection.requirement_status(
                        item.availability_requirement
                    ),
                    scope_node_key=item.scope_node_key,
                    scope_node_name=spatial.resource_scope(item.scope_node_key).scope_node_name,
                    scope_region_key=spatial.resource_scope(item.scope_node_key).scope_region_key,
                    scope_region_name=spatial.resource_scope(item.scope_node_key).scope_region_name,
                )
                for item in resources
            ],
            resource_intelligence=knowledge_projection.resource_intelligence(),
            actors=[
                PublicActorResponse(
                    key=item.actor_key,
                    name=item.name,
                    role_name=role_definitions[item.role_key].name,
                    current_node_name=node_definitions[item.current_node_key].name,
                    command_reachability=(
                        "DISCONNECTED" if item.command_reachability == "DISCONNECTED" else "ONLINE"
                    ),
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
                self._task_summary(
                    item,
                    sequence=index,
                    objective_names_by_key=objective_names_by_key,
                )
                for index, item in enumerate(task_rows, start=1)
            ],
            pending_approval_id=pending,
        )

    def _task_summary(
        self,
        task: AgentTask,
        *,
        sequence: int,
        objective_names_by_key: dict[str, str],
    ) -> PublicTaskSummaryResponse:
        return PublicTaskSummaryResponse(
            id=task.id,
            sequence=sequence,
            goal=task.goal_description,
            objective_names=[
                objective_names_by_key[key]
                for key in task.objective_scope_keys or ()
                if key in objective_names_by_key
            ],
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
        spatial = SpatialDisplayProjector(definition)
        detailed_location_by_step = self._locations_by_step(
            task,
            definition,
            spatial,
            plans,
            steps_by_plan,
        )
        compact_location_by_step = self._locations_by_step(
            task,
            definition,
            spatial,
            plans,
            steps_by_plan,
            compact=True,
        )
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
                        location=compact_location_by_step.get(step.id),
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
        planning_cycle = _public_planning_cycle(self.db, task.id)
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
                    task,
                    plan,
                    steps_by_plan[plan.id],
                    definition,
                    actors,
                    location_by_step=detailed_location_by_step,
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
            planning_cycle=planning_cycle,
            timeline=self._timeline(
                task,
                definition,
                plans,
                steps_by_plan,
                actors,
                checkpoint,
                location_by_step=detailed_location_by_step,
            ),
            briefing=(
                self._briefing(
                    current_step,
                    definition,
                    actors,
                    roadmap,
                    location_by_step=detailed_location_by_step,
                )
                if current_step is not None
                and phase
                in (
                    PlayerExecutionPhase.AWAITING_ACTION_ACK,
                    PlayerExecutionPhase.APPROVAL_REQUIRED,
                )
                else None
            ),
            debrief=(
                self._debrief(
                    task,
                    last_action_step,
                    definition,
                    plans,
                    steps_by_plan,
                    location_by_step=detailed_location_by_step,
                )
                if last_action_step is not None
                and phase
                in (
                    PlayerExecutionPhase.AWAITING_DEBRIEF_ACK,
                    PlayerExecutionPhase.AWAITING_REPLAN_ACK,
                )
                else None
            ),
            explanation=(
                _task_explanation(task)
                if task.status in (AgentTaskStatus.FAILED, AgentTaskStatus.BLOCKED)
                else None
            ),
        )

    def _locations_by_step(
        self,
        task: AgentTask,
        definition: ScenarioDefinitionV2,
        spatial: SpatialDisplayProjector,
        plans: tuple[AgentPlan, ...],
        steps_by_plan: dict[UUID, tuple[AgentStep, ...]],
        compact: bool = False,
    ) -> dict[UUID, PublicActionLocationResponse | None]:
        source_nodes = self._source_nodes_by_step(task, definition, plans, steps_by_plan)
        locations: dict[UUID, PublicActionLocationResponse | None] = {}
        for plan in plans:
            for step in steps_by_plan[plan.id]:
                if step.execution_type != StepExecutionType.TOOL:
                    continue
                action = _action_definition(definition, step)
                target_key = step.tool_arguments.get("target_key")
                if not isinstance(target_key, str):
                    locations[step.id] = None
                    continue
                raw_parameters = step.tool_arguments.get("parameters")
                parameters = raw_parameters if isinstance(raw_parameters, dict) else {}
                projection = spatial.action_location(
                    action,
                    target_node_key=target_key,
                    source_node_key=source_nodes.get(step.id),
                    parameters=parameters,
                    compact=compact,
                )
                locations[step.id] = (
                    PublicActionLocationResponse(
                        kind=projection.kind,
                        summary=projection.summary,
                        detail=projection.detail,
                    )
                    if projection is not None
                    else None
                )
        return locations

    def _source_nodes_by_step(
        self,
        task: AgentTask,
        definition: ScenarioDefinitionV2,
        plans: tuple[AgentPlan, ...],
        steps_by_plan: dict[UUID, tuple[AgentStep, ...]],
    ) -> dict[UUID, str | None]:
        """Replay persisted actor movement for display-only source locations.

        The replay consumes only Scenario initial positions and persisted
        WorldOperation outcomes.  Pending actions in the current Plan are
        advanced virtually so a Player can read the planned route; that
        virtual position is never written back to runtime state.
        """

        initial_positions = {
            profile.key: profile.initial_node_key for profile in definition.actors.actor_profiles
        }
        operations = tuple(
            self.db.scalars(
                select(WorldOperation)
                .where(WorldOperation.game_instance_id == task.game_instance_id)
                .order_by(WorldOperation.created_at, WorldOperation.id)
            )
        )
        source_nodes: dict[UUID, str | None] = {}

        def apply_operation(positions: dict[str, str], operation: WorldOperation) -> None:
            if not _operation_succeeded(operation):
                return
            destination = (
                operation.outcome.get("actor_location_update")
                if isinstance(operation.outcome, dict)
                else None
            )
            if not isinstance(destination, str):
                action = next(
                    (item for item in definition.actions if item.key == operation.action_key),
                    None,
                )
                if action is None or action.behavior not in (
                    ActionBehavior.TRAVEL,
                ):
                    return
                destination = operation.target_key
            positions[operation.actor_key] = destination

        for plan in plans:
            # Every Plan gets an independent projected replay.  The starting
            # position is the real runtime position at Plan creation, while
            # the Plan's own travel sequence remains a display-only intent
            # even when one of its steps later failed or was skipped.
            positions = dict(initial_positions)
            for operation in operations:
                if operation.created_at <= plan.created_at:
                    apply_operation(positions, operation)
            plan_steps = steps_by_plan[plan.id]
            for step in plan_steps:
                if step.execution_type != StepExecutionType.TOOL:
                    continue
                source_nodes[step.id] = positions.get(step.assigned_actor_key)
                action = _action_definition(definition, step)
                target_key = step.tool_arguments.get("target_key")
                if (
                    action is not None
                    and action.behavior
                    == ActionBehavior.TRAVEL
                    and isinstance(target_key, str)
                ):
                    # Use the planned destination for the next step.  This is
                    # intentionally independent of persisted operation success
                    # so a failed Travel does not rewrite the following
                    # transport's historical source into the runtime location.
                    positions[step.assigned_actor_key] = target_key
        return source_nodes

    def _timeline(
        self,
        task: AgentTask,
        definition: ScenarioDefinitionV2,
        plans: tuple[AgentPlan, ...],
        steps_by_plan: dict[UUID, tuple[AgentStep, ...]],
        actors: dict[str, str],
        checkpoint: PlayerExecutionCheckpoint | None,
        *,
        location_by_step: dict[UUID, PublicActionLocationResponse | None],
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
                            location=location_by_step.get(step.id),
                        )
                    )
                if _action_cycle_finished(step, plan_steps):
                    action_operation = next(iter(operations_by_step.get(step.id, [])), None)
                    debrief = self._debrief(
                        task,
                        step,
                        definition,
                        plans,
                        steps_by_plan,
                        location_by_step=location_by_step,
                    )
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
                            location=location_by_step.get(step.id),
                        )
                    )
        terminal_kind = {
            AgentTaskStatus.SUCCEEDED: PublicTimelineEventKind.TASK_COMPLETED,
            AgentTaskStatus.FAILED: PublicTimelineEventKind.TASK_BLOCKED,
            AgentTaskStatus.BLOCKED: PublicTimelineEventKind.TASK_BLOCKED,
            AgentTaskStatus.ABORTED: PublicTimelineEventKind.TASK_ABORTED,
        }.get(task.status)
        if terminal_kind is not None:
            objective_names = tuple(
                objective.name
                for objective in definition.objectives
                if objective.key in (task.objective_scope_keys or ())
            )
            events.append(
                PublicTimelineEventResponse(
                    id=f"task:{task.id}:terminal",
                    kind=terminal_kind,
                    title=(
                        "目标已完成"
                        if task.status == AgentTaskStatus.SUCCEEDED
                        else "规划失败"
                        if _is_provider_failure(task.last_error_code)
                        else task.goal_description
                    ),
                    detail=(
                        " · ".join(objective_names)
                        if task.status == AgentTaskStatus.SUCCEEDED and objective_names
                        else task.last_error_detail
                        if _is_provider_failure(task.last_error_code)
                        else None
                    ),
                    occurred_at=task.completed_at,
                    duration_ms=(
                        _provider_failure_duration(task)
                        if _is_provider_failure(task.last_error_code)
                        else None
                    ),
                )
            )
        return events

    def _briefing(
        self,
        step: AgentStep,
        definition: ScenarioDefinitionV2,
        actors: dict[str, str],
        roadmap: MissionRoadmap,
        *,
        location_by_step: dict[UUID, PublicActionLocationResponse | None],
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
            location=location_by_step.get(step.id),
        )

    def _debrief(
        self,
        task: AgentTask,
        step: AgentStep,
        definition: ScenarioDefinitionV2,
        plans: tuple[AgentPlan, ...],
        steps_by_plan: dict[UUID, tuple[AgentStep, ...]],
        *,
        location_by_step: dict[UUID, PublicActionLocationResponse | None],
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
        invalidation = _plan_invalidation_for(task, plan.version)
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
            plan_invalidated=invalidation is not None,
            plan_invalidation_reason=(
                str(invalidation["reason"])
                if invalidation is not None and isinstance(invalidation.get("reason"), str)
                else None
            ),
            location=location_by_step.get(step.id),
        )


def _operation_succeeded(operation: WorldOperation) -> bool:
    return operation.status == WorldOperationStatus.RESOLVED and not (
        isinstance(operation.outcome, dict) and bool(operation.outcome.get("failure"))
    )


_PROVIDER_FAILURE_CODES = {
    "MODEL_PROVIDER_HTTP_ERROR",
    "MODEL_PROVIDER_RESPONSE_INVALID",
    "MODEL_PROVIDER_CONFIGURATION_INVALID",
    "MODEL_PROVIDER_ERROR",
}


def _is_provider_failure(error_code: str | None) -> bool:
    return error_code == "MODEL_PROVIDER_TIMEOUT" or error_code in _PROVIDER_FAILURE_CODES


def _task_explanation(task: AgentTask) -> str:
    if task.last_error_code == "MODEL_PLAN_REJECTED":
        return "Model failed to produce a validated executable plan within the allowed repairs"
    if _is_provider_failure(task.last_error_code):
        return task.last_error_detail or "模型调用失败"
    return "当前世界状态下没有可继续执行的合法行动"


def _provider_failure_duration(task: AgentTask) -> int | None:
    for call in reversed(_provider_calls(task)):
        if call.get("outcome") not in {"TIMEOUT", "ERROR"}:
            continue
        for key in ("wall_clock_latency_ms", "latency_ms"):
            value = call.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
    return None


def _task_status(status: AgentTaskStatus, error_code: str | None) -> PublicTaskStatus:
    if status == AgentTaskStatus.BLOCKED:
        if error_code == "BLOCKED_BY_PLAYER_DECISION":
            return PublicTaskStatus.BLOCKED_BY_PLAYER_DECISION
        if error_code == "MODEL_PLAN_REJECTED":
            return PublicTaskStatus.MODEL_PLAN_REJECTED
        if error_code == "MODEL_PROVIDER_TIMEOUT":
            return PublicTaskStatus.MODEL_PROVIDER_TIMEOUT
        if _is_provider_failure(error_code):
            return PublicTaskStatus.MODEL_PROVIDER_FAILURE
        return PublicTaskStatus.UNREACHABLE_IN_CURRENT_STATE
    return {
        AgentTaskStatus.ACTIVE: PublicTaskStatus.ACTIVE,
        AgentTaskStatus.WAITING_FOR_WORLD_EVENT: PublicTaskStatus.ACTIVE,
        AgentTaskStatus.WAITING_FOR_PLAYER_ACTION: PublicTaskStatus.NEEDS_PLAYER_INPUT,
        AgentTaskStatus.REQUIRES_PLAYER_DECISION: PublicTaskStatus.NEEDS_PLAYER_INPUT,
        AgentTaskStatus.SUCCEEDED: PublicTaskStatus.COMPLETED,
        AgentTaskStatus.FAILED: (
            PublicTaskStatus.MODEL_PROVIDER_TIMEOUT
            if error_code == "MODEL_PROVIDER_TIMEOUT"
            else PublicTaskStatus.MODEL_PROVIDER_FAILURE
            if _is_provider_failure(error_code)
            else PublicTaskStatus.UNREACHABLE_IN_CURRENT_STATE
        ),
        AgentTaskStatus.ABORTED: PublicTaskStatus.ABORTED,
    }[status]


def _plan_history_entry(
    task: AgentTask,
    plan: AgentPlan,
    steps: tuple[AgentStep, ...],
    definition: ScenarioDefinitionV2,
    actors: dict[str, str],
    *,
    task_status: AgentTaskStatus,
    is_latest: bool,
    current_step_id: UUID | None,
    location_by_step: dict[UUID, PublicActionLocationResponse | None],
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
            location=location_by_step.get(step.id),
        )
        for step in tool_steps
    ]
    failed_step = next(
        (step for step in public_steps if step.status == PublicPlanHistoryStepStatus.FAILED),
        None,
    )
    interruption = _plan_interruption(
        task=task,
        plan=plan,
        tool_steps=tool_steps,
        public_steps=public_steps,
        failed_step=failed_step,
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
        interruption=interruption,
        steps=public_steps,
    )


def _plan_interruption(
    *,
    task: AgentTask,
    plan: AgentPlan,
    tool_steps: tuple[AgentStep, ...],
    public_steps: list[PublicPlanHistoryStepResponse],
    failed_step: PublicPlanHistoryStepResponse | None,
) -> PublicPlanInterruptionResponse | None:
    if failed_step is not None:
        return PublicPlanInterruptionResponse(
            kind=PublicPlanInterruptionKind.FAILURE,
            step_id=failed_step.id,
            sequence=failed_step.sequence,
            step_name=failed_step.action_name,
        )

    marker = _plan_invalidation_for(task, plan.version)
    diagnostics = marker.get("diagnostics") if marker is not None else None
    if not isinstance(diagnostics, list):
        return None
    diagnostic = next(
        (
            item
            for item in diagnostics
            if isinstance(item, dict) and isinstance(item.get("sequence"), int)
        ),
        None,
    )
    if diagnostic is None:
        return None
    sequence = diagnostic["sequence"]
    assert isinstance(sequence, int)
    step = next((item for item in public_steps if item.sequence == sequence), None)
    persisted_step = next((item for item in tool_steps if item.sequence == sequence), None)
    if step is None or persisted_step is None:
        return None
    return PublicPlanInterruptionResponse(
        kind=PublicPlanInterruptionKind.KNOWLEDGE_CONFLICT,
        step_id=step.id,
        sequence=step.sequence,
        step_name=step.action_name,
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


def _plan_invalidation_for(task: AgentTask, plan_version: int) -> dict[str, object] | None:
    metadata = task.objective_resolution_metadata
    if not isinstance(metadata, dict):
        return None
    marker = metadata.get("plan_invalidation")
    if not isinstance(marker, dict) or marker.get("plan_version") != plan_version:
        return None
    return marker


def _provider_calls(task: AgentTask) -> tuple[dict[str, object], ...]:
    metadata = task.objective_resolution_metadata
    if not isinstance(metadata, dict):
        return ()
    calls = metadata.get("provider_calls")
    if not isinstance(calls, list):
        return ()
    return tuple(item for item in calls if isinstance(item, dict))


_PUBLIC_PLANNING_VIOLATION_SUMMARIES = {
    "TARGET_INTERACTION": ("TARGET_INTERACTION", "目标不满足行动所需的公开交互条件。"),
    "LOCALITY": ("LOCALITY", "行动者与目标不在允许的本地范围。"),
    "TRANSPORT_PASSABILITY": ("TRANSPORT", "当前公开交通状态不允许该通行。"),
    "RESOURCE_QUANTITY": ("RESOURCE", "公开已知资源数量不足。"),
    "RESOURCE_KNOWLEDGE": ("RESOURCE_KNOWLEDGE", "所需资源的公开库存信息尚未完整。"),
    "RESOURCE_SOURCE": ("RESOURCE_SOURCE", "所需资源来源尚未由公开信息确定。"),
    "OBJECTIVE_COVERAGE": ("OBJECTIVE", "方案没有覆盖当前目标要求。"),
    "OBJECTIVE": ("OBJECTIVE", "方案没有覆盖当前目标要求。"),
    "ACTOR_COMMAND_REACHABILITY": ("ACTOR", "行动者当前无法接收指令。"),
    "ACTOR_ELIGIBILITY": ("ACTOR", "所选行动者不满足公开资格约束。"),
    "PARAMETER": ("PARAMETER", "行动参数不符合公开约束。"),
    "INFORMATION_BOUNDARY": ("INFORMATION", "需要先获取新的公开信息后再继续规划。"),
    "SEGMENT_TERMINATION": ("TERMINATION", "方案终止方式不符合当前公开约束。"),
}


def _public_planning_violation(raw: object) -> PublicPlanningViolationResponse:
    """Project persisted Validator data without exposing internal evidence."""

    item = raw if isinstance(raw, dict) else {}
    dimension = item.get("dimension")
    dimension_value = dimension if isinstance(dimension, str) else None
    category, summary = _PUBLIC_PLANNING_VIOLATION_SUMMARIES.get(
        dimension_value or "",
        ("PLAN_CONSTRAINT", "方案违反了一个公开约束。"),
    )

    def public_key(name: str) -> str | None:
        value = item.get(name)
        return value if isinstance(value, str) else None

    return PublicPlanningViolationResponse(
        category=category,
        dimension=dimension_value,
        summary=summary,
        step_id=public_key("step_id"),
        actor_key=public_key("actor_key"),
        action_key=public_key("action_key"),
        target_key=public_key("target_key"),
    )


def _public_planning_cycle(
    db: Session,
    task_id: UUID,
) -> PublicPlanningCycleResponse | None:
    cycle = db.scalar(
        select(PlanningCycle)
        .where(PlanningCycle.task_id == task_id)
        .order_by(PlanningCycle.created_at.desc())
    )
    if cycle is None:
        return None
    attempts = tuple(
        db.scalars(
            select(PlanningAttempt)
            .where(PlanningAttempt.cycle_id == cycle.id)
            .order_by(PlanningAttempt.attempt_index)
        )
    )
    public_attempts: list[PublicPlanningAttemptResponse] = []
    for attempt in attempts:
        proposal = attempt.proposal if isinstance(attempt.proposal, dict) else {}
        raw_steps = proposal.get("steps", [])
        steps: list[PublicPlanningDraftStepResponse] = []
        if isinstance(raw_steps, list):
            for raw in raw_steps:
                if not isinstance(raw, dict):
                    continue
                step_id = raw.get("step_id")
                if not isinstance(step_id, str):
                    continue
                parameters = raw.get("parameters")
                steps.append(
                    PublicPlanningDraftStepResponse(
                        step_id=step_id,
                        purpose=str(raw.get("purpose") or ""),
                        action_key=(
                            str(raw["action_key"])
                            if isinstance(raw.get("action_key"), str)
                            else None
                        ),
                        actor_key=(
                            str(raw["actor_key"])
                            if isinstance(raw.get("actor_key"), str)
                            else None
                        ),
                        target_key=(
                            str(raw["target_key"])
                            if isinstance(raw.get("target_key"), str)
                            else None
                        ),
                        parameters=(parameters if isinstance(parameters, dict) else {}),
                    )
                )
        violations = attempt.validator_violations
        public_attempts.append(
            PublicPlanningAttemptResponse(
                id=attempt.id,
                attempt_index=attempt.attempt_index,
                call_type=attempt.call_type,
                status=attempt.status,
                stop_reason=attempt.stop_reason,
                proposal_steps=steps,
                validator_violations=(
                    [_public_planning_violation(item) for item in violations]
                    if isinstance(violations, list)
                    else []
                ),
                started_at=attempt.started_at,
                finished_at=attempt.finished_at,
                latency_ms=attempt.latency_ms,
            )
        )
    return PublicPlanningCycleResponse(
        id=cycle.id,
        base_call_type=cycle.base_call_type,
        status=cycle.status,
        current_attempt=cycle.current_attempt,
        attempts=public_attempts,
    )


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
