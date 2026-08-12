import json

from sqlalchemy.orm import Session

from app.agent.planning import build_planning_request
from app.core.config import Settings
from app.infrastructure.db.models import ConversationSession
from app.scenarios.starfire.objective_catalog import (
    STARFIRE_OBJECTIVE_CATALOG,
    StarfireObjectiveKey,
)
from app.scenarios.starfire.planning_policy import STARFIRE_PLANNING_POLICY
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService
from app.tools.catalog import build_registry


def test_planning_constraints_are_guardrails_not_a_route_blueprint(session: Session) -> None:
    player = GameService(session).create_player("Reduced policy")
    conversation = ConversationSession(player_id=player.id, npc_id=seed_id("npc:shen_ce"))
    session.add(conversation)
    session.flush()
    tasks = TaskService(session)
    task = tasks.create_task(conversation, "Restore Starfire Outpost", "starfire_command")
    scope = STARFIRE_OBJECTIVE_CATALOG.scope([StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST])
    tasks.resolve_and_freeze_scope(
        task,
        scope,
        resolver_source="TEST",
        resolver_version="v1",
        confirmation_source="TEST",
        freeze_source="TEST",
    )

    request = build_planning_request(
        db=session,
        registry=build_registry(),
        settings=Settings(database_url="sqlite+pysqlite:///:memory:"),
        task=task,
        session=conversation,
        kind="PLAN",
    )
    constraints = request["constraints"]
    encoded = json.dumps(constraints, ensure_ascii=False)

    assert "strategic_initial_plan_blueprint" not in constraints
    assert "strategic_replan_blueprint" not in constraints
    assert "ordered_phases" not in encoded
    assert "exact_step_count" not in encoded
    assert constraints["guardrails"] == {
        "scope_must_remain_frozen": True,
        "use_only_known_targets": True,
        "respect_tool_and_officer_authority": True,
        "asynchronous_operations_require_adjacent_wait_steps": True,
        "do_not_repeat_completed_effects": True,
        "do_not_pursue_terminal_effects_outside_frozen_scope": True,
    }
    instruction = STARFIRE_PLANNING_POLICY.planner_instruction("PLAN")
    assert "ten steps" not in instruction
    assert "start_military_operation" not in instruction
    assert "start_outpost_repair" not in instruction
    assert "start_trade_route_test" not in instruction
