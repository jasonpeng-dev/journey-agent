from __future__ import annotations

from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, GetJsonSchemaHandler, model_validator
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema
from sqlalchemy.orm import Session

from app.agent.types import ToolContext
from app.core.errors import AppError
from app.services.game import GameService
from app.services.tasks import TaskService
from app.tools.interaction_validation import (
    MILITARY_INTERACTION,
    RECON_INTERACTION,
    REPAIR_INTERACTION,
    TRADE_ROUTE_INTERACTION,
    resolve_tool_interaction,
)


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyArgs(StrictArgs):
    pass


class IdempotentArgs(StrictArgs):
    idempotency_key: str = Field(min_length=8, max_length=160)


class ReconOperationArgs(IdempotentArgs):
    target_key: str = Field(min_length=1, max_length=80)
    troop_count: int = Field(ge=1, le=300)
    approach: Literal["CAUTIOUS", "STANDARD", "AGGRESSIVE"]


class MilitaryOperationArgs(IdempotentArgs):
    target_key: str = Field(min_length=1, max_length=80)
    troop_count: int = Field(ge=1, le=300)
    mission_type: Literal["CLEAR_VALLEY", "DISRUPT_SUPPLY", "ESCORT", "DEFEND"]
    strategy: Literal["CAUTIOUS", "STANDARD", "AGGRESSIVE"]


class VillageSupportArgs(IdempotentArgs):
    food_offer: int = Field(ge=0, le=100)
    requested_support: Literal["INTELLIGENCE", "GUIDE", "SUPPLIES"]


class PreferredTargetArgs(IdempotentArgs):
    """Expose target_key to new callers while accepting narrow legacy inputs."""

    compatibility_input_fields: ClassVar[frozenset[str]] = frozenset()
    target_key: str | None = Field(default=None, min_length=1, max_length=80)

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        core_schema: CoreSchema,
        handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        schema = handler(core_schema)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for field_name in cls.compatibility_input_fields:
                properties.pop(field_name, None)
            target_schema = properties.get("target_key")
            if isinstance(target_schema, dict):
                variants = target_schema.get("anyOf")
                if isinstance(variants, list):
                    string_schema = next(
                        (
                            variant
                            for variant in variants
                            if isinstance(variant, dict) and variant.get("type") == "string"
                        ),
                        None,
                    )
                    if string_schema is not None:
                        properties["target_key"] = {
                            **string_schema,
                            "title": target_schema.get("title", "Target Key"),
                        }
                properties["target_key"].pop("default", None)
        required = [
            field_name
            for field_name in schema.get("required", [])
            if field_name not in cls.compatibility_input_fields
        ]
        if "target_key" not in required:
            required.append("target_key")
        schema["required"] = required
        return schema


class OutpostRepairArgs(PreferredTargetArgs):
    repair_level: Literal["TEMPORARY", "FULL"]
    food_commitment: int = Field(ge=0, le=100)
    gold_commitment: int = Field(ge=0, le=80)


class TradeRouteTestArgs(PreferredTargetArgs):
    compatibility_input_fields: ClassVar[frozenset[str]] = frozenset({"route_key"})
    route_key: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def require_target_input(self) -> TradeRouteTestArgs:
        if self.target_key is None and self.route_key is None:
            raise ValueError("target_key is required")
        return self


class PlanStepArgs(StrictArgs):
    description: str = Field(min_length=3, max_length=500)
    execution_type: Literal["TOOL", "WAIT_FOR_PLAYER_ACTION", "WAIT_FOR_WORLD_EVENT"]
    assigned_officer_key: str | None = Field(default=None, min_length=2, max_length=80)
    action_intent: str | None = Field(default=None, min_length=2, max_length=100)
    constraints: dict[str, Any] = Field(default_factory=dict)
    allowed_tool_names: list[str] = Field(default_factory=list, max_length=8)
    selected_tool_name: str | None = None
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    expected_outcome: dict[str, Any] = Field(default_factory=dict)
    resume_condition: dict[str, Any] | None = None


class PlanObjectiveScopeArgs(StrictArgs):
    scenario_key: str = Field(min_length=1, max_length=100)
    catalog_version: str = Field(min_length=1, max_length=100)
    objective_keys: list[str] = Field(min_length=1, max_length=20)


class CreateTaskPlanArgs(StrictArgs):
    task_id: UUID
    objective_scope: PlanObjectiveScopeArgs
    strategy_summary: str = Field(min_length=3, max_length=1000)
    steps: list[PlanStepArgs] = Field(min_length=1, max_length=12)
    idempotency_key: str = Field(min_length=8, max_length=160)


class ReplanTaskArgs(CreateTaskPlanArgs):
    replan_reason: str = Field(min_length=3, max_length=160)


def inspect_command_state(db: Session, context: ToolContext, _args: BaseModel) -> dict[str, Any]:
    return GameService(db).inspect_command_state(context.player_id)


def preflight_recon_operation(db: Session, context: ToolContext, args: BaseModel) -> None:
    parsed = ReconOperationArgs.model_validate(args)
    target = resolve_tool_interaction(context.scenario_key, RECON_INTERACTION, parsed)
    GameService(db).preflight_recon_operation(
        player_id=context.player_id,
        troop_count=parsed.troop_count,
        target_key=target.key,
        approach=parsed.approach,
    )


def start_recon_operation(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = ReconOperationArgs.model_validate(args)
    target = resolve_tool_interaction(context.scenario_key, RECON_INTERACTION, parsed)
    operation = GameService(db).start_recon_operation(
        player_id=context.player_id,
        officer_npc_id=context.npc_id,
        task_id=context.task_id,
        source_step_id=context.step_id,
        target_key=target.key,
        troop_count=parsed.troop_count,
        approach=parsed.approach,
        idempotency_key=parsed.idempotency_key,
    )
    return _operation_result(operation)


def preflight_military_operation(db: Session, context: ToolContext, args: BaseModel) -> None:
    parsed = MilitaryOperationArgs.model_validate(args)
    target = resolve_tool_interaction(context.scenario_key, MILITARY_INTERACTION, parsed)
    GameService(db).preflight_military_operation(
        player_id=context.player_id,
        troop_count=parsed.troop_count,
        mission_type=parsed.mission_type,
        target_key=target.key,
        strategy=parsed.strategy,
    )


def start_military_operation(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = MilitaryOperationArgs.model_validate(args)
    target = resolve_tool_interaction(context.scenario_key, MILITARY_INTERACTION, parsed)
    operation = GameService(db).start_military_operation(
        player_id=context.player_id,
        officer_npc_id=context.npc_id,
        task_id=context.task_id,
        source_step_id=context.step_id,
        target_key=target.key,
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
        requested_support=parsed.requested_support,
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
    target = resolve_tool_interaction(context.scenario_key, REPAIR_INTERACTION, parsed)
    GameService(db).preflight_outpost_repair(
        player_id=context.player_id,
        food_commitment=parsed.food_commitment,
        gold_commitment=parsed.gold_commitment,
        target_key=target.key,
        repair_level=parsed.repair_level,
    )


def start_outpost_repair(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = OutpostRepairArgs.model_validate(args)
    target = resolve_tool_interaction(context.scenario_key, REPAIR_INTERACTION, parsed)
    operation = GameService(db).start_outpost_repair(
        player_id=context.player_id,
        officer_npc_id=context.npc_id,
        task_id=context.task_id,
        source_step_id=context.step_id,
        target_key=target.key,
        repair_level=parsed.repair_level,
        food_commitment=parsed.food_commitment,
        gold_commitment=parsed.gold_commitment,
        idempotency_key=parsed.idempotency_key,
    )
    return _operation_result(operation)


def preflight_trade_route_test(db: Session, context: ToolContext, args: BaseModel) -> None:
    parsed = TradeRouteTestArgs.model_validate(args)
    target = resolve_tool_interaction(context.scenario_key, TRADE_ROUTE_INTERACTION, parsed)
    GameService(db).preflight_trade_route_test(
        player_id=context.player_id,
        route_key=target.key,
    )


def start_trade_route_test(db: Session, context: ToolContext, args: BaseModel) -> dict[str, Any]:
    parsed = TradeRouteTestArgs.model_validate(args)
    target = resolve_tool_interaction(context.scenario_key, TRADE_ROUTE_INTERACTION, parsed)
    operation = GameService(db).start_trade_route_test(
        player_id=context.player_id,
        officer_npc_id=context.npc_id,
        task_id=context.task_id,
        source_step_id=context.step_id,
        route_key=target.key,
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
        "STEP_ACTOR_MISMATCH",
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
    return {
        "player": {"id": str(player.id), "version": player.version},
        "strategic_command": GameService(db).inspect_command_state(context.player_id),
    }
