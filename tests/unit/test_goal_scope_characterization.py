import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.agent.planning import PlanValidator, build_planning_request
from app.agent.providers import MockModelProvider
from app.agent.types import Message, ToolDefinition
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.enums import AgentTaskStatus
from app.infrastructure.db.models import AgentTask, ConversationSession
from app.scenarios.starfire.fallback_plans import initial_strategic_starfire_plan
from app.scenarios.starfire.objective_catalog import FULL_STARFIRE_SCOPE
from app.scenarios.starfire.objectives import STARFIRE_OBJECTIVES
from app.scenarios.starfire.planning_policy import STARFIRE_PLANNING_POLICY
from app.scenarios.starfire.ruleset import (
    StarfireFactState,
    StarfireResources,
    StarfireRuleState,
)
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService
from app.tools.catalog import build_registry


def test_legal_short_horizon_plan_is_not_rejected_by_a_full_route_blueprint(
    session: Session,
) -> None:
    conversation, task = _task_context(session, "只侦察北境山谷, 不执行清剿、修复或商路行动。")
    proposal = initial_strategic_starfire_plan(task.id, FULL_STARFIRE_SCOPE)
    proposal["steps"] = proposal["steps"][:2]

    result = _validator(session).validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert result.passed


def test_restore_only_completion_currently_fails_while_trade_is_closed() -> None:
    state = _state(
        valley_security="SAFE",
        outpost_status="OPERATIONAL",
        trade_route_status="CLOSED",
    )

    evaluation = STARFIRE_OBJECTIVES.evaluate(state)

    assert not evaluation.completed
    assert evaluation.details["starfire_outpost.outpost_status"] == "OPERATIONAL"
    assert evaluation.details["northern_trade_route.trade_route_status"] == "CLOSED"


def test_backend_final_verification_is_derived_from_the_frozen_scope() -> None:
    state = _state(
        valley_security="SAFE",
        outpost_status="DAMAGED",
        trade_route_status="OPEN",
    )

    constraints = STARFIRE_PLANNING_POLICY.build_planning_constraints(
        "PLAN",
        None,
        state,
        FULL_STARFIRE_SCOPE,
    )
    assert constraints["final_verification"] == "BACKEND_SCOPED_OBJECTIVE_EVALUATOR"
    assert "required_final_step" not in constraints
    assert not STARFIRE_OBJECTIVES.evaluate(state).completed


@pytest.mark.parametrize(
    "goal",
    [
        "把北方处理妥当, 但我还没有决定要侦察还是全面恢复。",
        "在南海建立一支目前场景完全不支持的舰队。",
    ],
)
def test_new_goals_enter_the_unresolved_scope_lifecycle_without_implicit_full(
    session: Session,
    goal: str,
) -> None:
    _conversation, task = _task_context(session, goal, freeze=False)

    assert task.goal_description == goal
    assert task.status == AgentTaskStatus.ACTIVE
    assert task.current_plan_version == 0
    assert "objective_scope_keys" in AgentTask.__table__.columns
    assert task.objective_resolution_status == "UNRESOLVED"
    assert task.objective_scope_keys is None


def test_mock_planner_uses_the_supplied_scope_not_goal_text() -> None:
    task_id = uuid4()
    tool = ToolDefinition(name="create_task_plan", description="", parameters={})

    first = asyncio.run(
        MockModelProvider().complete(
            [_planner_message(task_id, "只侦察北境山谷")],
            [tool],
        )
    )
    second = asyncio.run(
        MockModelProvider().complete(
            [_planner_message(task_id, "恢复整个北方地区")],
            [tool],
        )
    )

    assert first.tool_calls[0].arguments == second.tool_calls[0].arguments
    assert first.tool_calls[0].arguments == initial_strategic_starfire_plan(
        task_id, FULL_STARFIRE_SCOPE
    )


def test_replan_context_serializes_the_same_frozen_scope(
    session: Session,
) -> None:
    conversation, task = _task_context(session, "重建星火驿站。")

    request = build_planning_request(
        db=session,
        registry=build_registry(),
        settings=_settings(),
        task=task,
        session=conversation,
        kind="REPLAN",
        replan_reason="WORLD_STATE_CHANGED",
    )
    serialized = TaskService(session).serialize(task)

    assert request["goal"] == task.goal_description
    assert request["objective_scope"]["objective_keys"] == ["FULL_NORTHERN_RECOVERY"]
    assert serialized["objective_scope"]["objective_keys"] == ["FULL_NORTHERN_RECOVERY"]


def test_planning_request_contains_only_relations_between_known_nodes(
    session: Session,
) -> None:
    conversation, task = _task_context(session, "修复星火前哨并重新打通北方商路。")

    request = build_planning_request(
        db=session,
        registry=build_registry(),
        settings=_settings(),
        task=task,
        session=conversation,
        kind="PLAN",
    )

    encoded = json.dumps(request["known_relations"], ensure_ascii=False)
    assert "UNLOCKS" in encoded
    assert "ENABLES" in encoded
    assert "enemy_north_supply_route" not in encoded


def test_known_locked_targets_are_advertised_but_execution_fails_closed(
    session: Session,
) -> None:
    conversation, task = _task_context(session, "重建星火驿站。")
    request = build_planning_request(
        db=session,
        registry=build_registry(),
        settings=_settings(),
        task=task,
        session=conversation,
        kind="PLAN",
    )
    tools = {item["name"]: item for item in request["allowed_tools"]}

    assert "starfire_outpost [LOCKED]" in tools["start_outpost_repair"]["description"]
    with pytest.raises(AppError) as caught:
        GameService(session).preflight_outpost_repair(
            player_id=task.player_id,
            target_key="starfire_outpost",
            repair_level="TEMPORARY",
            food_commitment=20,
            gold_commitment=20,
        )
    assert caught.value.code == "INTERACTION_TARGET_LOCKED"


def _task_context(
    session: Session,
    goal: str,
    *,
    freeze: bool = True,
) -> tuple[ConversationSession, AgentTask]:
    player = GameService(session).create_player(f"Goal Scope Characterization {uuid4()}")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    tasks = TaskService(session)
    task = tasks.create_task(conversation, goal, "starfire_command")
    if freeze:
        tasks.resolve_and_freeze_scope(
            task,
            FULL_STARFIRE_SCOPE,
            resolver_source="CHARACTERIZATION",
            resolver_version="v1",
            confirmation_source="TEST",
            freeze_source="TEST",
        )
    return conversation, task


def _validator(session: Session) -> PlanValidator:
    return PlanValidator(session, build_registry(), _settings())


def _settings() -> Settings:
    return Settings(database_url="sqlite+pysqlite:///:memory:")


def _planner_message(task_id: object, goal: str) -> Message:
    request = {
        "kind": "PLAN",
        "task_id": str(task_id),
        "scenario_key": "starfire_command",
        "goal": goal,
        "objective_scope": {
            "scenario_key": FULL_STARFIRE_SCOPE.scenario_key,
            "catalog_version": FULL_STARFIRE_SCOPE.catalog_version,
            "objective_keys": list(FULL_STARFIRE_SCOPE.objective_keys),
        },
    }
    return Message(
        role="system",
        content=f"PLANNER_REQUEST_JSON:{json.dumps(request, ensure_ascii=False)}",
    )


def _state(
    *,
    valley_security: str = "UNSAFE",
    outpost_status: str = "DAMAGED",
    trade_route_status: str = "CLOSED",
) -> StarfireRuleState:
    return StarfireRuleState(
        facts={
            ("north_village", "village_support"): StarfireFactState("NONE"),
            ("northern_valley", "valley_intelligence"): StarfireFactState("INCOMPLETE"),
            ("northern_valley", "valley_security"): StarfireFactState(valley_security),
            ("northern_valley", "ambush_status"): StarfireFactState("ACTIVE"),
            ("enemy_north_supply_route", "supply_status"): StarfireFactState("ACTIVE"),
            ("starfire_outpost", "outpost_status"): StarfireFactState(outpost_status),
            ("northern_trade_route", "trade_route_status"): StarfireFactState(trade_route_status),
        },
        resources=StarfireResources(soldiers_available=300, food=100, gold=80, morale=60),
    )
