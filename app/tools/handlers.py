from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.types import ToolContext
from app.core.errors import AppError
from app.infrastructure.db.models import (
    EncounterRun,
    InventoryItem,
    ItemDefinition,
    Memory,
    PlayerNPCRelationship,
    Quest,
    WorldNode,
)
from app.schemas.game import GameAction, QuestCreate, RelationshipUpdate
from app.services.game import GameService
from app.services.tasks import TaskService


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyArgs(StrictArgs):
    pass


class InventoryArgs(StrictArgs):
    item_type: str | None = None


class EncounterStateArgs(StrictArgs):
    run_id: UUID | None = None


class CreateQuestArgs(QuestCreate):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=160)


class UpdateRelationshipArgs(RelationshipUpdate):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=160)


class GameActionArgs(GameAction):
    model_config = ConfigDict(extra="forbid")


class IdempotentArgs(StrictArgs):
    idempotency_key: str = Field(min_length=8, max_length=160)


class ReconOperationArgs(IdempotentArgs):
    target_key: Literal["valley_entrance"]
    troop_count: int = Field(ge=1, le=300)
    approach: Literal["CAUTIOUS", "STANDARD", "AGGRESSIVE"]


class MilitaryOperationArgs(IdempotentArgs):
    target_key: Literal["ambush_valley", "enemy_north_supply_route"]
    troop_count: int = Field(ge=1, le=300)
    mission_type: Literal["CLEAR_VALLEY", "DISRUPT_SUPPLY", "ESCORT", "DEFEND"]
    strategy: Literal["CAUTIOUS", "STANDARD", "AGGRESSIVE"]


class VillageSupportArgs(IdempotentArgs):
    food_offer: int = Field(ge=0, le=100)
    requested_support: Literal["INTELLIGENCE", "GUIDE", "SUPPLIES"]


class OutpostRepairArgs(IdempotentArgs):
    repair_level: Literal["TEMPORARY", "FULL"]
    food_commitment: int = Field(ge=0, le=100)
    gold_commitment: int = Field(ge=0, le=80)


class TradeRouteTestArgs(IdempotentArgs):
    route_key: Literal["northern_trade_route"]


class PlanStepArgs(StrictArgs):
    description: str = Field(min_length=3, max_length=500)
    execution_type: Literal[
        "TOOL",
        "WAIT_FOR_USER",
        "WAIT_FOR_PLAYER_ACTION",
        "WAIT_FOR_WORLD_EVENT",
    ]
    assigned_officer_key: str | None = Field(default=None, min_length=2, max_length=80)
    action_intent: str | None = Field(default=None, min_length=2, max_length=100)
    constraints: dict[str, Any] = Field(default_factory=dict)
    allowed_tool_names: list[str] = Field(default_factory=list, max_length=8)
    selected_tool_name: str | None = None
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    expected_outcome: dict[str, Any] = Field(default_factory=dict)
    resume_condition: dict[str, Any] | None = None


class CreateTaskPlanArgs(StrictArgs):
    task_id: UUID
    strategy_summary: str = Field(min_length=3, max_length=1000)
    steps: list[PlanStepArgs] = Field(min_length=1, max_length=12)
    idempotency_key: str = Field(min_length=8, max_length=160)


class ReplanTaskArgs(CreateTaskPlanArgs):
    replan_reason: str = Field(min_length=3, max_length=160)


def player_state(db: Session, context: ToolContext, _args: BaseModel) -> dict[str, Any]:
    player = GameService(db).get_player(context.player_id)
    active_quests = db.scalars(
        select(Quest).where(
            Quest.player_id == player.id,
            Quest.status.in_(["AVAILABLE", "ACTIVE", "COMPLETED"]),
        )
    ).all()
    encounter = db.scalar(
        select(EncounterRun).where(
            EncounterRun.player_id == player.id,
            EncounterRun.status == "ACTIVE",
        )
    )
    return {
        "level": player.level,
        "gold": player.gold,
        "current_node_id": str(player.current_node_id),
        "active_quests": [quest.narrative_title for quest in active_quests],
        "has_active_encounter": encounter is not None,
        "version": player.version,
    }


def inventory(db: Session, context: ToolContext, args: BaseModel) -> list[Any]:
    parsed = InventoryArgs.model_validate(args)
    query = (
        select(InventoryItem, ItemDefinition)
        .join(ItemDefinition)
        .where(InventoryItem.player_id == context.player_id, InventoryItem.quantity > 0)
    )
    if parsed.item_type:
        query = query.where(ItemDefinition.type == parsed.item_type)
    return [
        {"key": definition.key, "name": definition.name, "quantity": item.quantity}
        for item, definition in db.execute(query).all()
    ]


def world_state(db: Session, context: ToolContext, _args: BaseModel) -> dict[str, Any]:
    player = GameService(db).get_player(context.player_id)
    current = db.get(WorldNode, player.current_node_id)
    nodes = GameService(db).list_nodes(context.player_id)
    return {
        "chapter": 1,
        "current_node": current.key if current else None,
        "available_nodes": [
            {"key": node.key, "name": node.name}
            for node, state in nodes
            if state.status.value in {"AVAILABLE", "ENTERED"}
        ],
    }


def available_nodes(db: Session, context: ToolContext, _args: BaseModel) -> list[Any]:
    nodes = world_state(db, context, _args)["available_nodes"]
    assert isinstance(nodes, list)
    return nodes


def active_quests(db: Session, context: ToolContext, _args: BaseModel) -> list[Any]:
    quests = db.scalars(
        select(Quest).where(
            Quest.player_id == context.player_id,
            Quest.status.in_(["AVAILABLE", "ACTIVE", "COMPLETED"]),
        )
    ).all()
    return [
        {
            "id": str(quest.id),
            "template_key": quest.template.key,
            "status": quest.status.value,
            "progress": quest.progress,
            "target": quest.template.objective_quantity,
            "reward_status": quest.reward_status.value,
        }
        for quest in quests
    ]


def encounter_state(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = EncounterStateArgs.model_validate(args)
    query = select(EncounterRun).where(EncounterRun.player_id == context.player_id)
    if parsed.run_id:
        query = query.where(EncounterRun.id == parsed.run_id)
    else:
        query = query.order_by(EncounterRun.started_at.desc())
    run = db.scalar(query)
    return (
        {"active": False}
        if not run
        else {"active": run.status.value == "ACTIVE", "id": str(run.id), "status": run.status.value}
    )


def relationship_update(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = UpdateRelationshipArgs.model_validate(args)
    rel = GameService(db).update_relationship(
        context.player_id, context.npc_id, parsed.delta, parsed.reason_code
    )
    return {"score": rel.score, "attitude": rel.attitude.value, "version": rel.version}


def quest_create(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = CreateQuestArgs.model_validate(args)
    quest = GameService(db).create_quest(
        context.player_id,
        context.npc_id,
        parsed.template_key,
        parsed.narrative_title,
        parsed.narrative_description,
        parsed.idempotency_key,
    )
    return {"quest_id": str(quest.id), "status": quest.status.value}


def game_action(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = GameActionArgs.model_validate(args)
    if parsed.action == "STORE_MEMORY":
        content = parsed.parameters.get("content")
        importance = parsed.parameters.get("importance", 5)
        if not isinstance(content, str) or not content or not isinstance(importance, int):
            raise AppError("INVALID_TOOL_ARGUMENTS", "Invalid memory parameters")
        memory = Memory(
            player_id=context.player_id,
            npc_id=context.npc_id,
            type="PLAYER_PREFERENCE",
            content=content[:1000],
            importance=max(1, min(10, importance)),
            source_session_id=context.session_id,
            source_event_id=parsed.source_id,
        )
        db.add(memory)
        db.flush()
        return {"memory_id": str(memory.id)}
    if parsed.action == "UNLOCK_NODE":
        if parsed.source_type not in {"QUEST", "ENCOUNTER"}:
            raise AppError("ACTION_SOURCE_INVALID", "Unlock requires a quest or encounter")
        node_key = parsed.parameters.get("node_key")
        if not isinstance(node_key, str):
            raise AppError("INVALID_TOOL_ARGUMENTS", "node_key is required")
        state = GameService(db).unlock_node(context.player_id, node_key)
        return {"node_id": str(state.node_id), "status": state.status.value}
    raise AppError(
        "ACTION_REQUIRES_REWARD_SERVICE",
        "Grant and completion actions must use the validated reward workflow",
    )


def inspect_task_requirements(
    db: Session, context: ToolContext, _args: BaseModel
) -> dict[str, Any]:
    return GameService(db).inspect_starfire_requirements(context.player_id)


def inspect_command_state(db: Session, context: ToolContext, _args: BaseModel) -> dict[str, Any]:
    return GameService(db).inspect_command_state(context.player_id)


def preflight_recon_operation(db: Session, context: ToolContext, args: BaseModel) -> None:
    parsed = ReconOperationArgs.model_validate(args)
    GameService(db).preflight_recon_operation(
        player_id=context.player_id,
        troop_count=parsed.troop_count,
    )


def start_recon_operation(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = ReconOperationArgs.model_validate(args)
    operation = GameService(db).start_recon_operation(
        player_id=context.player_id,
        officer_npc_id=context.npc_id,
        task_id=context.task_id,
        source_step_id=context.step_id,
        target_key=parsed.target_key,
        troop_count=parsed.troop_count,
        approach=parsed.approach,
        idempotency_key=parsed.idempotency_key,
    )
    return _operation_result(operation)


def preflight_military_operation(db: Session, context: ToolContext, args: BaseModel) -> None:
    parsed = MilitaryOperationArgs.model_validate(args)
    GameService(db).preflight_military_operation(
        player_id=context.player_id,
        troop_count=parsed.troop_count,
        mission_type=parsed.mission_type,
    )


def start_military_operation(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = MilitaryOperationArgs.model_validate(args)
    operation = GameService(db).start_military_operation(
        player_id=context.player_id,
        officer_npc_id=context.npc_id,
        task_id=context.task_id,
        source_step_id=context.step_id,
        target_key=parsed.target_key,
        troop_count=parsed.troop_count,
        mission_type=parsed.mission_type,
        strategy=parsed.strategy,
        idempotency_key=parsed.idempotency_key,
    )
    return _operation_result(operation)


def preflight_village_support(db: Session, context: ToolContext, args: BaseModel) -> None:
    parsed = VillageSupportArgs.model_validate(args)
    GameService(db).preflight_village_support(
        player_id=context.player_id,
        food_offer=parsed.food_offer,
    )


def negotiate_village_support(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = VillageSupportArgs.model_validate(args)
    return GameService(db).negotiate_village_support(
        player_id=context.player_id,
        food_offer=parsed.food_offer,
        requested_support=parsed.requested_support,
    )


def preflight_outpost_repair(db: Session, context: ToolContext, args: BaseModel) -> None:
    parsed = OutpostRepairArgs.model_validate(args)
    GameService(db).preflight_outpost_repair(
        player_id=context.player_id,
        food_commitment=parsed.food_commitment,
        gold_commitment=parsed.gold_commitment,
    )


def start_outpost_repair(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = OutpostRepairArgs.model_validate(args)
    operation = GameService(db).start_outpost_repair(
        player_id=context.player_id,
        officer_npc_id=context.npc_id,
        task_id=context.task_id,
        source_step_id=context.step_id,
        repair_level=parsed.repair_level,
        food_commitment=parsed.food_commitment,
        gold_commitment=parsed.gold_commitment,
        idempotency_key=parsed.idempotency_key,
    )
    return _operation_result(operation)


def preflight_trade_route_test(db: Session, context: ToolContext, args: BaseModel) -> None:
    TradeRouteTestArgs.model_validate(args)
    GameService(db).preflight_trade_route_test(player_id=context.player_id)


def start_trade_route_test(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = TradeRouteTestArgs.model_validate(args)
    operation = GameService(db).start_trade_route_test(
        player_id=context.player_id,
        officer_npc_id=context.npc_id,
        task_id=context.task_id,
        source_step_id=context.step_id,
        route_key=parsed.route_key,
        idempotency_key=parsed.idempotency_key,
    )
    return _operation_result(operation)


def _operation_result(operation: Any) -> dict[str, Any]:
    return {
        "operation_id": str(operation.id),
        "operation_type": operation.operation_type,
        "status": operation.status.value,
        "target_key": operation.target_key,
    }


def prepare_starfire_route(db: Session, context: ToolContext, _args: BaseModel) -> dict[str, Any]:
    state = GameService(db).prepare_starfire_route(context.player_id)
    return {"node_id": str(state.node_id), "status": state.status.value}


def request_npc_assistance(db: Session, context: ToolContext, _args: BaseModel) -> dict[str, Any]:
    fact = GameService(db).request_starfire_assistance(context.player_id)
    return {"assistance_active": bool(fact.value.get("value")), "version": fact.version}


def restore_outpost(db: Session, context: ToolContext, _args: BaseModel) -> dict[str, Any]:
    fact = GameService(db).restore_starfire_outpost(context.player_id)
    return {"outpost_operational": bool(fact.value.get("value")), "version": fact.version}


def grant_access(db: Session, context: ToolContext, _args: BaseModel) -> dict[str, Any]:
    state = GameService(db).grant_starfire_access(context.player_id)
    return {
        "node_id": str(state.node_id),
        "status": state.status.value,
        "access_granted": True,
    }


def create_task_plan(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = CreateTaskPlanArgs.model_validate(args)
    if context.task_id != parsed.task_id:
        raise AppError(
            "TASK_CONTEXT_MISMATCH",
            "The plan target does not match the active task",
            status_code=403,
        )
    plan = TaskService(db).create_plan(
        parsed.task_id,
        parsed.strategy_summary,
        [step.model_dump(mode="json") for step in parsed.steps],
        created_by_run_id=context.agent_run_id,
        source=context.plan_source or "MANUAL",
        planner_model=context.planner_model,
        validation_status=context.plan_validation_status or "PASSED",
        validation_errors=context.plan_validation_errors,
    )
    return {"task_id": str(parsed.task_id), "plan_id": str(plan.id), "version": plan.version}


def replan_task(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = ReplanTaskArgs.model_validate(args)
    if context.task_id != parsed.task_id:
        raise AppError(
            "TASK_CONTEXT_MISMATCH",
            "The replan target does not match the active task",
            status_code=403,
        )
    if parsed.replan_reason in {
        "NPC_PERMISSION_DENIED",
        "STEP_TOOL_MISMATCH",
        "TASK_PLAYER_MISMATCH",
        "TASK_NPC_MISMATCH",
    }:
        raise AppError(
            "SECURITY_FAILURE_NOT_REPLANNABLE",
            "Authorization failures cannot trigger replanning",
            status_code=403,
        )
    plan = TaskService(db).create_plan(
        parsed.task_id,
        parsed.strategy_summary,
        [step.model_dump(mode="json") for step in parsed.steps],
        created_by_run_id=context.agent_run_id,
        replan_reason=parsed.replan_reason,
        source=context.plan_source or "MANUAL",
        planner_model=context.planner_model,
        validation_status=context.plan_validation_status or "PASSED",
        validation_errors=context.plan_validation_errors,
    )
    return {"task_id": str(parsed.task_id), "plan_id": str(plan.id), "version": plan.version}


def snapshot(db: Session, context: ToolContext) -> dict[str, Any]:
    player = GameService(db).get_player(context.player_id)
    relationship = db.get(PlayerNPCRelationship, (context.player_id, context.npc_id))
    return {
        "player": {
            "gold": player.gold,
            "level": player.level,
            "current_node_id": str(player.current_node_id),
            "version": player.version,
        },
        "relationship": None
        if not relationship
        else {"score": relationship.score, "version": relationship.version},
        "starfire": GameService(db).inspect_starfire_requirements(context.player_id),
        "strategic_command": GameService(db).inspect_command_state(context.player_id),
    }
