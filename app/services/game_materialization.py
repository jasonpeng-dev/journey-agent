"""Shared, fail-closed materialization of runtime state and player history."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.domain.enums import DecisionStatus, WorldOperationStatus
from app.domain.resources import (
    resource_identity,
    resource_pool_initial_states,
    valid_resource_state_identity,
)
from app.domain.scenario_v2 import ScenarioDefinitionV2, relation_identity
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentPlan,
    AgentStep,
    AgentTask,
    ConversationSession,
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceRelationKnowledge,
    GameInstanceResourceState,
    PlanningAttempt,
    PlanningCycle,
    PlayerExecutionCheckpoint,
    WorldOperation,
)


class MaterializationError(ValueError):
    """A source cannot be copied without losing runtime or history semantics."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    nodes: tuple[GameInstanceNodeState, ...]
    facts: tuple[GameInstanceFactState, ...]
    resources: tuple[GameInstanceResourceState, ...]
    region_knowledge: tuple[GameInstanceRegionResourceKnowledge, ...]
    relation_knowledge: tuple[GameInstanceRelationKnowledge, ...]
    actors: tuple[GameInstanceActor, ...]


@dataclass(frozen=True, slots=True)
class MaterializedHistory:
    session: ConversationSession
    inherited_task_count: int


class GameMaterializer:
    """Copy an exact runtime and its stable history into a new GameInstance."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def load_runtime(
        self, source: GameInstance, definition: ScenarioDefinitionV2
    ) -> RuntimeSnapshot:
        required_tables = (
            "game_instance_region_resource_knowledge",
            "game_instance_relation_knowledge",
        )
        connection = self.db.connection()
        if any(not inspect(connection).has_table(table) for table in required_tables):
            raise MaterializationError(
                "MATERIALIZATION_RUNTIME_SCHEMA_REQUIRED",
                "The database must be upgraded before materialization",
            )

        nodes: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(GameInstanceNodeState)
                .where(GameInstanceNodeState.game_instance_id == source.id)
                .order_by(GameInstanceNodeState.node_key)
            )
        )
        facts: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(GameInstanceFactState)
                .where(GameInstanceFactState.game_instance_id == source.id)
                .order_by(GameInstanceFactState.node_key, GameInstanceFactState.fact_key)
            )
        )
        resources: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(GameInstanceResourceState)
                .where(GameInstanceResourceState.game_instance_id == source.id)
                .order_by(GameInstanceResourceState.resource_identity)
            )
        )
        region_knowledge: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(GameInstanceRegionResourceKnowledge)
                .where(GameInstanceRegionResourceKnowledge.game_instance_id == source.id)
                .order_by(GameInstanceRegionResourceKnowledge.region_key)
            )
        )
        relation_knowledge: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(GameInstanceRelationKnowledge)
                .where(GameInstanceRelationKnowledge.game_instance_id == source.id)
                .order_by(GameInstanceRelationKnowledge.relation_key)
            )
        )
        actors: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(GameInstanceActor)
                .where(GameInstanceActor.game_instance_id == source.id)
                .order_by(GameInstanceActor.actor_key)
            )
        )

        node_definitions = {item.key: item for item in definition.world.nodes}
        fact_keys = {(node.key, fact.key) for node in definition.world.nodes for fact in node.facts}
        expected_regions = {
            node.key
            for node in definition.world.nodes
            if definition.metadata.locality.enabled
            and node.node_type_key == definition.metadata.locality.region_node_type_key
        }
        expected_relations = {relation_identity(item) for item in definition.world.relations}
        expected_actor_keys = {item.key for item in definition.actors.actor_profiles}
        if (
            {item.node_key for item in nodes} != set(node_definitions)
            or {(item.node_key, item.fact_key) for item in facts} != fact_keys
            or {item.region_key for item in region_knowledge} != expected_regions
            or {item.relation_key for item in relation_knowledge} != expected_relations
            or {item.actor_key for item in actors} != expected_actor_keys
        ):
            raise MaterializationError(
                "MATERIALIZATION_RUNTIME_INVALID",
                "The source runtime graph does not match its ScenarioVersion",
            )
        node_keys = set(node_definitions)
        if source.current_node_key not in node_keys:
            raise MaterializationError(
                "MATERIALIZATION_RUNTIME_INVALID",
                "The source GameInstance points at an unknown current node",
            )
        if any(actor.current_node_key not in node_keys for actor in actors):
            raise MaterializationError(
                "MATERIALIZATION_RUNTIME_INVALID",
                "A source actor points to a node absent from its ScenarioVersion",
            )
        if any(resource.reserved_value != 0 for resource in resources):
            raise MaterializationError(
                "MATERIALIZATION_SOURCE_RESERVATION_ACTIVE",
                "A materialization source cannot contain reserved resources",
            )
        if (
            sum(actor.actor_key == definition.initialization.primary_actor_key for actor in actors)
            != 1
        ):
            raise MaterializationError(
                "MATERIALIZATION_RUNTIME_INVALID",
                "The source has no unique primary actor",
            )
        self._validate_resource_rows(resources, definition)
        self._validate_actor_rows(actors, definition)
        return RuntimeSnapshot(
            nodes=nodes,
            facts=facts,
            resources=resources,
            region_knowledge=region_knowledge,
            relation_knowledge=relation_knowledge,
            actors=actors,
        )

    def copy_runtime(
        self,
        *,
        source: GameInstance,
        target: GameInstance,
        snapshot: RuntimeSnapshot,
        definition: ScenarioDefinitionV2,
    ) -> None:
        for node_row in snapshot.nodes:
            self.db.add(
                GameInstanceNodeState(
                    game_instance_id=target.id,
                    node_key=node_row.node_key,
                    status=node_row.status,
                    visibility=node_row.visibility,
                    version=1,
                )
            )
        for fact_row in snapshot.facts:
            self.db.add(
                GameInstanceFactState(
                    game_instance_id=target.id,
                    node_key=fact_row.node_key,
                    fact_key=fact_row.fact_key,
                    truth_value=deepcopy(fact_row.truth_value),
                    visibility=fact_row.visibility,
                    version=1,
                )
            )
        pools = resource_pool_initial_states(definition)
        for resource_row in snapshot.resources:
            static_pool = self._static_pool(resource_row, pools)
            self.db.add(
                GameInstanceResourceState(
                    game_instance_id=target.id,
                    resource_identity=resource_identity(
                        resource_row.resource_key,
                        resource_row.scope_node_key,
                        resource_row.pool_key,
                    ),
                    resource_key=resource_row.resource_key,
                    scope_node_key=resource_row.scope_node_key,
                    pool_key=resource_row.pool_key,
                    facility_key=static_pool.facility_key if static_pool is not None else None,
                    value=resource_row.value,
                    reserved_value=0,
                    visibility=resource_row.visibility,
                    availability=resource_row.availability,
                    survey_discoverable=(
                        static_pool.survey_discoverable if static_pool is not None else False
                    ),
                    availability_requirement=(
                        static_pool.availability_requirement.model_dump(mode="json")
                        if static_pool is not None
                        and static_pool.availability_requirement is not None
                        else None
                    ),
                    version=1,
                )
            )
        for region_row in snapshot.region_knowledge:
            self.db.add(
                GameInstanceRegionResourceKnowledge(
                    game_instance_id=target.id,
                    region_key=region_row.region_key,
                    resource_inventory_visibility=region_row.resource_inventory_visibility,
                    resource_survey_completed=region_row.resource_survey_completed,
                    version=1,
                )
            )
        relation_keys = {relation_identity(item) for item in definition.world.relations}
        for relation_row in snapshot.relation_knowledge:
            if relation_row.relation_key not in relation_keys:
                raise MaterializationError(
                    "MATERIALIZATION_RUNTIME_INVALID",
                    "The source contains an unknown Relation Knowledge key",
                )
            self.db.add(
                GameInstanceRelationKnowledge(
                    game_instance_id=target.id,
                    relation_key=relation_row.relation_key,
                    visibility=relation_row.visibility,
                    version=1,
                )
            )
        profiles = {item.key: item for item in definition.actors.actor_profiles}
        roles = {item.key: item for item in definition.actors.roles}
        primary_key = definition.initialization.primary_actor_key
        for actor_row in snapshot.actors:
            profile = profiles[actor_row.actor_key]
            role = roles.get(profile.role_key)
            if role is None:
                raise MaterializationError(
                    "MATERIALIZATION_SCENARIO_INVALID",
                    "An Actor profile references a missing Role",
                )
            expected_doctrine = {item.key: item.value for item in profile.doctrine}
            expected_authority = profile.authority_policy.model_dump(mode="json")
            expected_capabilities = [item.value for item in role.capabilities]
            self.db.add(
                GameInstanceActor(
                    game_instance_id=target.id,
                    actor_key=actor_row.actor_key,
                    role_key=profile.role_key,
                    name=profile.name,
                    persona=profile.persona,
                    doctrine=deepcopy(expected_doctrine),
                    current_node_key=actor_row.current_node_key,
                    allowed_action_keys=list(profile.allowed_action_keys),
                    authority_policy=deepcopy(expected_authority),
                    capabilities=list(expected_capabilities),
                    command_reachability=actor_row.command_reachability,
                    is_primary=actor_row.actor_key == primary_key,
                    status=actor_row.status,
                    version=1,
                )
            )
        target.current_node_key = source.current_node_key
        self.db.flush()

    def copy_history(self, *, source: GameInstance, target: GameInstance) -> MaterializedHistory:
        sessions: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(ConversationSession)
                .where(ConversationSession.game_instance_id == source.id)
                .order_by(ConversationSession.created_at, ConversationSession.id)
            )
        )
        if not sessions:
            raise MaterializationError(
                "MATERIALIZATION_HISTORY_INVALID",
                "The source has no ConversationSession required by the game runtime",
            )
        copied: Any = None
        session_map: dict[UUID, ConversationSession] = {}
        for row in sessions:
            copied = ConversationSession(
                player_id=target.player_id,
                game_instance_id=target.id,
                actor_key=row.actor_key,
                status=row.status,
                summary=row.summary,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            self.db.add(copied)
            session_map[row.id] = copied
        self.db.flush()

        tasks: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(AgentTask)
                .where(AgentTask.game_instance_id == source.id)
                .order_by(AgentTask.created_at, AgentTask.id)
            )
        )
        task_map: dict[UUID, AgentTask] = {}
        for row in tasks:
            if row.origin_session_id not in session_map or row.last_session_id not in session_map:
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_INVALID",
                    "An AgentTask references a ConversationSession outside the source",
                )
            copied = AgentTask(
                player_id=target.player_id,
                game_instance_id=target.id,
                owner_actor_key=row.owner_actor_key,
                origin_session_id=session_map[row.origin_session_id].id,
                last_session_id=session_map[row.last_session_id].id,
                goal_description=row.goal_description,
                submission_idempotency_key=row.submission_idempotency_key,
                rejected_proposal_signatures=deepcopy(row.rejected_proposal_signatures),
                scenario_key=row.scenario_key,
                objective_resolution_status=row.objective_resolution_status,
                objective_scope_keys=deepcopy(row.objective_scope_keys),
                objective_catalog_version=row.objective_catalog_version,
                objective_scope_hash=row.objective_scope_hash,
                objective_resolver_source=row.objective_resolver_source,
                objective_resolver_version=row.objective_resolver_version,
                objective_resolution_metadata=deepcopy(row.objective_resolution_metadata),
                objective_resolved_at=row.objective_resolved_at,
                objective_confirmed_at=row.objective_confirmed_at,
                objective_confirmation_source=row.objective_confirmation_source,
                objective_frozen_at=row.objective_frozen_at,
                objective_freeze_source=row.objective_freeze_source,
                planning_mode=row.planning_mode,
                status=row.status,
                current_plan_version=row.current_plan_version,
                replan_count=row.replan_count,
                last_error_code=row.last_error_code,
                last_error_detail=row.last_error_detail,
                version=1,
                completed_at=row.completed_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            self.db.add(copied)
            task_map[row.id] = copied
        self.db.flush()

        cycles: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(PlanningCycle)
                .where(PlanningCycle.game_instance_id == source.id)
                .order_by(PlanningCycle.created_at, PlanningCycle.id)
            )
        )
        cycle_map: dict[UUID, PlanningCycle] = {}
        source_cycle_by_id = {row.id: row for row in cycles}
        for row in cycles:
            if row.status == "RUNNING":
                raise MaterializationError(
                    "MATERIALIZATION_PLANNING_IN_FLIGHT",
                    "The source has a RUNNING PlanningCycle",
                )
            if row.task_id not in task_map:
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_INVALID",
                    "A PlanningCycle references a task outside the source",
                )
            copied = PlanningCycle(
                task_id=task_map[row.task_id].id,
                game_instance_id=target.id,
                base_call_type=row.base_call_type,
                replan_reason=row.replan_reason,
                frozen_objective_scope=deepcopy(row.frozen_objective_scope),
                planner_input=deepcopy(row.planner_input),
                planner_input_hash=row.planner_input_hash,
                status=row.status,
                current_attempt=row.current_attempt,
                rejected_segment=deepcopy(row.rejected_segment),
                current_violations=deepcopy(row.current_violations),
                anti_regression_memory=deepcopy(row.anti_regression_memory),
                started_at=row.started_at,
                finished_at=row.finished_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            self.db.add(copied)
            cycle_map[row.id] = copied
        self.db.flush()

        attempts: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(PlanningAttempt)
                .where(PlanningAttempt.task_id.in_(tuple(task_map)))
                .order_by(PlanningAttempt.created_at, PlanningAttempt.id)
            )
            if task_map
            else ()
        )
        for row in attempts:
            if row.status == "RUNNING":
                raise MaterializationError(
                    "MATERIALIZATION_PLANNING_IN_FLIGHT",
                    "The source has a RUNNING PlanningAttempt",
                )
            source_cycle = source_cycle_by_id.get(row.cycle_id)
            if (
                source_cycle is None
                or source_cycle.task_id != row.task_id
                or row.task_id not in task_map
            ):
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_INVALID",
                    "A PlanningAttempt references history outside the source",
                )
            self.db.add(
                PlanningAttempt(
                    cycle_id=cycle_map[row.cycle_id].id,
                    task_id=task_map[row.task_id].id,
                    attempt_index=row.attempt_index,
                    call_type=row.call_type,
                    status=row.status,
                    provider_payload=deepcopy(row.provider_payload),
                    proposal=deepcopy(row.proposal),
                    rejected_segment=deepcopy(row.rejected_segment),
                    validator_violations=deepcopy(row.validator_violations),
                    anti_regression_memory=deepcopy(row.anti_regression_memory),
                    stop_reason=row.stop_reason,
                    started_at=row.started_at,
                    finished_at=row.finished_at,
                    latency_ms=row.latency_ms,
                    usage=deepcopy(row.usage),
                    finish_reason=row.finish_reason,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )
        self.db.flush()

        task_order = {task.id: index for index, task in enumerate(tasks)}
        plans: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(AgentPlan)
                .where(AgentPlan.task_id.in_(tuple(task_map)))
                .order_by(AgentPlan.created_at, AgentPlan.id)
            )
            if task_map
            else ()
        )
        plan_map: dict[UUID, AgentPlan] = {}
        for row in sorted(
            plans, key=lambda item: (task_order[item.task_id], item.version, item.id.hex)
        ):
            if row.task_id not in task_map:
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_INVALID",
                    "An AgentPlan references a task outside the source",
                )
            if row.planning_cycle_id is not None:
                source_cycle = source_cycle_by_id.get(row.planning_cycle_id)
                if source_cycle is None or source_cycle.task_id != row.task_id:
                    raise MaterializationError(
                        "MATERIALIZATION_HISTORY_INVALID",
                        "An AgentPlan references a planning cycle outside its task",
                    )
            copied = AgentPlan(
                task_id=task_map[row.task_id].id,
                version=row.version,
                status=row.status,
                strategy_summary=row.strategy_summary,
                replan_reason=row.replan_reason,
                planning_cycle_id=(
                    cycle_map[row.planning_cycle_id].id
                    if row.planning_cycle_id is not None
                    else None
                ),
                supersedes_plan_id=None,
                created_by_run_id=row.created_by_run_id,
                created_by_actor_key=row.created_by_actor_key,
                source=row.source,
                planner_model=row.planner_model,
                validation_status=row.validation_status,
                validation_errors=deepcopy(row.validation_errors),
                stop_reason=row.stop_reason,
                created_at=row.created_at,
            )
            self.db.add(copied)
            plan_map[row.id] = copied
        self.db.flush()
        for row in plans:
            if row.supersedes_plan_id is not None:
                copied = plan_map.get(row.id)
                superseded = plan_map.get(row.supersedes_plan_id)
                if copied is None or superseded is None:
                    raise MaterializationError(
                        "MATERIALIZATION_HISTORY_INVALID",
                        "An AgentPlan supersedes a plan outside the source",
                    )
                copied.supersedes_plan_id = superseded.id
        self.db.flush()

        steps: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(AgentStep)
                .where(AgentStep.plan_id.in_(tuple(plan_map)))
                .order_by(AgentStep.plan_id, AgentStep.sequence, AgentStep.id)
            )
            if plan_map
            else ()
        )
        step_map: dict[UUID, AgentStep] = {}
        for row in steps:
            if row.plan_id not in plan_map:
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_INVALID",
                    "An AgentStep references a plan outside the source",
                )
            copied = AgentStep(
                plan_id=plan_map[row.plan_id].id,
                sequence=row.sequence,
                planner_step_id=row.planner_step_id,
                description=row.description,
                execution_type=row.execution_type,
                status=row.status,
                assigned_actor_key=row.assigned_actor_key,
                action_intent=row.action_intent,
                constraints=deepcopy(row.constraints),
                allowed_tool_names=deepcopy(row.allowed_tool_names),
                selected_tool_name=row.selected_tool_name,
                tool_arguments=deepcopy(row.tool_arguments),
                expected_outcome=deepcopy(row.expected_outcome),
                actual_result=deepcopy(row.actual_result),
                failure_code=row.failure_code,
                attempts=row.attempts,
                resume_condition=deepcopy(row.resume_condition),
                started_at=row.started_at,
                completed_at=row.completed_at,
            )
            self.db.add(copied)
            step_map[row.id] = copied
        self.db.flush()

        operations: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(WorldOperation)
                .where(WorldOperation.game_instance_id == source.id)
                .order_by(WorldOperation.created_at, WorldOperation.id)
            )
        )
        for row in operations:
            if row.status == WorldOperationStatus.PENDING:
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_UNSTABLE",
                    "The source has a pending WorldOperation",
                )
            if row.task_id is not None and row.task_id not in task_map:
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_INVALID",
                    "A WorldOperation references a task outside the source",
                )
            if row.source_step_id is not None and row.source_step_id not in step_map:
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_INVALID",
                    "A WorldOperation references a step outside the source",
                )
            self.db.add(
                WorldOperation(
                    player_id=target.player_id,
                    game_instance_id=target.id,
                    task_id=task_map[row.task_id].id if row.task_id is not None else None,
                    source_step_id=(
                        step_map[row.source_step_id].id if row.source_step_id is not None else None
                    ),
                    actor_key=row.actor_key,
                    action_key=row.action_key,
                    execution_mode=row.execution_mode,
                    target_key=row.target_key,
                    status=row.status,
                    parameters=deepcopy(row.parameters),
                    outcome=deepcopy(row.outcome),
                    idempotency_key=row.idempotency_key,
                    resolution_key=row.resolution_key,
                    resolved_at=row.resolved_at,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )

        decisions: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(ActionDecisionRequest)
                .where(ActionDecisionRequest.game_instance_id == source.id)
                .order_by(ActionDecisionRequest.created_at, ActionDecisionRequest.id)
            )
        )
        for row in decisions:
            if row.status == DecisionStatus.PENDING:
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_UNSTABLE",
                    "The source has a pending ActionDecisionRequest",
                )
            if row.task_id is not None and row.task_id not in task_map:
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_INVALID",
                    "A decision references a task outside the source",
                )
            if row.source_step_id is not None and row.source_step_id not in step_map:
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_INVALID",
                    "A decision references a step outside the source",
                )
            self.db.add(
                ActionDecisionRequest(
                    player_id=target.player_id,
                    game_instance_id=target.id,
                    task_id=task_map[row.task_id].id if row.task_id is not None else None,
                    source_step_id=(
                        step_map[row.source_step_id].id if row.source_step_id is not None else None
                    ),
                    actor_key=row.actor_key,
                    action_key=row.action_key,
                    target_key=row.target_key,
                    parameters=deepcopy(row.parameters),
                    idempotency_key=row.idempotency_key,
                    status=row.status,
                    reason_code=row.reason_code,
                    policy_details=deepcopy(row.policy_details),
                    decided_at=row.decided_at,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )

        checkpoints: tuple[Any, ...] = tuple(
            self.db.scalars(
                select(PlayerExecutionCheckpoint)
                .where(PlayerExecutionCheckpoint.game_instance_id == source.id)
                .order_by(PlayerExecutionCheckpoint.created_at, PlayerExecutionCheckpoint.task_id)
            )
        )
        for row in checkpoints:
            if row.task_id not in task_map:
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_INVALID",
                    "A PlayerExecutionCheckpoint references a task outside the source",
                )
            if row.last_action_step_id is not None and row.last_action_step_id not in step_map:
                raise MaterializationError(
                    "MATERIALIZATION_HISTORY_INVALID",
                    "A PlayerExecutionCheckpoint references a step outside the source",
                )
            self.db.add(
                PlayerExecutionCheckpoint(
                    task_id=task_map[row.task_id].id,
                    game_instance_id=target.id,
                    phase=row.phase,
                    last_action_step_id=(
                        step_map[row.last_action_step_id].id
                        if row.last_action_step_id is not None
                        else None
                    ),
                    version=1,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )
        self.db.flush()
        first_session = min(sessions, key=lambda item: (item.created_at, item.id.hex))
        return MaterializedHistory(
            session=session_map[first_session.id],
            inherited_task_count=len(tasks),
        )

    @staticmethod
    def _static_pool(row: GameInstanceResourceState, pools: Sequence[Any]) -> Any | None:
        static_pool = next(
            (
                item
                for item in pools
                if item.resource_key == row.resource_key
                and item.pool_key == row.pool_key
                and item.region_key == row.scope_node_key
            ),
            None,
        )
        if static_pool is None:
            static_pool = next(
                (
                    item
                    for item in pools
                    if item.resource_key == row.resource_key
                    and item.pool_key == row.pool_key
                    and item.region_key is None
                ),
                None,
            )
        if static_pool is None and row.pool_key != "default":
            raise MaterializationError(
                "MATERIALIZATION_RUNTIME_INVALID",
                "The source contains an unknown Resource Pool",
            )
        return static_pool

    def _validate_resource_rows(
        self, rows: tuple[GameInstanceResourceState, ...], definition: ScenarioDefinitionV2
    ) -> None:
        pools = resource_pool_initial_states(definition)
        for row in rows:
            if row.resource_identity != resource_identity(
                row.resource_key, row.scope_node_key, row.pool_key
            ) or not valid_resource_state_identity(
                definition, row.resource_key, row.scope_node_key, row.pool_key
            ):
                raise MaterializationError(
                    "MATERIALIZATION_RUNTIME_INVALID",
                    "The source contains an invalid Resource identity",
                )
            self._static_pool(row, pools)

    def _validate_actor_rows(
        self, rows: tuple[GameInstanceActor, ...], definition: ScenarioDefinitionV2
    ) -> None:
        profiles = {item.key: item for item in definition.actors.actor_profiles}
        roles = {item.key: item for item in definition.actors.roles}
        for row in rows:
            profile = profiles.get(row.actor_key)
            if profile is None:
                raise MaterializationError(
                    "MATERIALIZATION_RUNTIME_INVALID",
                    "The source contains an unknown Actor",
                )
            role = roles.get(profile.role_key)
            if role is None:
                raise MaterializationError(
                    "MATERIALIZATION_SCENARIO_INVALID",
                    "An Actor profile references a missing Role",
                )
            expected_doctrine = {item.key: item.value for item in profile.doctrine}
            expected_authority = profile.authority_policy.model_dump(mode="json")
            expected_capabilities = [item.value for item in role.capabilities]
            if (
                row.role_key != profile.role_key
                or row.name != profile.name
                or row.persona != profile.persona
                or row.doctrine != expected_doctrine
                or row.allowed_action_keys != list(profile.allowed_action_keys)
                or row.authority_policy != expected_authority
                or row.capabilities != expected_capabilities
            ):
                raise MaterializationError(
                    "MATERIALIZATION_RUNTIME_INVALID",
                    "The source Actor static metadata does not match its ScenarioVersion",
                )


__all__ = [
    "GameMaterializer",
    "MaterializationError",
    "MaterializedHistory",
    "RuntimeSnapshot",
]
