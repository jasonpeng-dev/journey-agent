import json
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from app.agent.planning import PlanValidator, build_planning_request
from app.core.config import Settings
from app.core.errors import AppError
from app.debug.snapshot_service import StrategicSnapshotService
from app.infrastructure.db.models import AgentTask, ConversationSession
from app.scenarios.starfire.fallback_plans import initial_strategic_starfire_plan
from app.scenarios.starfire.objective_catalog import FULL_STARFIRE_SCOPE
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService
from app.tools.catalog import build_registry


def _planning_context(session: Session):  # type: ignore[no-untyped-def]
    game = GameService(session)
    player = game.create_player("Truth Knowledge Lord")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    tasks = TaskService(session)
    task = tasks.create_task(
        conversation,
        "修复星火前哨并重新打通北方商路。",
        "starfire_command",
    )
    tasks.resolve_and_freeze_scope(
        task,
        FULL_STARFIRE_SCOPE,
        resolver_source="TEST",
        resolver_version="v1",
        confirmation_source="TEST",
        freeze_source="TEST",
    )
    return game, player, conversation, task


def _request(
    session: Session,
    conversation: ConversationSession,
    task: AgentTask,
    kind: Literal["PLAN", "REPLAN"] = "PLAN",
) -> dict[str, object]:
    return build_planning_request(
        db=session,
        registry=build_registry(),
        settings=Settings(database_url="sqlite+pysqlite:///:memory:"),
        task=task,
        session=conversation,
        kind=kind,
        replan_reason="ENCOUNTER_DEFEAT" if kind == "REPLAN" else None,
    )


def _player_nodes(session: Session, conversation: ConversationSession) -> dict[str, object]:
    snapshot = StrategicSnapshotService(
        session,
        Settings(database_url="sqlite+pysqlite:///:memory:"),
    ).build(
        conversation.id,
        include_trace=False,
        include_hidden_truth=False,
    )
    projection = snapshot["player_world_state"]
    assert isinstance(projection, dict)
    nodes = projection["nodes"]
    assert isinstance(nodes, list)
    return {str(node["key"]): node for node in nodes if isinstance(node, dict)}


def _resolve_recon(game: GameService, player_id: UUID) -> None:
    operation = game.start_recon_operation(
        player_id=player_id,
        officer_npc_id=seed_id("npc:han_lie"),
        task_id=None,
        source_step_id=None,
        target_key="northern_valley",
        troop_count=60,
        approach="CAUTIOUS",
        idempotency_key="truth-knowledge-recon",
    )
    game.resolve_world_operation(operation.id, "truth-knowledge-recon-resolution")


def _resolve_military(
    game: GameService,
    player_id: UUID,
    *,
    target_key: str,
    mission_type: str,
    key: str,
) -> None:
    operation = game.start_military_operation(
        player_id=player_id,
        officer_npc_id=seed_id("npc:han_lie"),
        task_id=None,
        source_step_id=None,
        target_key=target_key,
        troop_count=160,
        mission_type=mission_type,
        strategy="CAUTIOUS",
        idempotency_key=key,
    )
    game.resolve_world_operation(operation.id, f"{key}-resolution")


def test_truth_and_knowledge_lifecycle_does_not_leak_to_planner(session: Session) -> None:
    game, player, conversation, task = _planning_context(session)

    truth = game.scenario_truth_state(player.id)
    known = game.scenario_known_state(player.id)
    assert truth.fact_value("northern_valley", "ambush_status") == "ACTIVE"
    assert truth.fact_value("enemy_north_supply_route", "supply_status") == "ACTIVE"
    assert not known.fact_known("northern_valley", "ambush_status")
    assert not known.node_known("enemy_north_supply_route")
    assert "enemy_north_supply_route" not in _player_nodes(session, conversation)

    initial = _request(session, conversation, task)
    initial_json = json.dumps(initial, ensure_ascii=False)
    assert "northern_valley.ambush_status" not in initial_json
    assert "enemy_north_supply_route" not in initial_json
    assert "supply_status" not in initial_json

    _resolve_recon(game, player.id)
    truth_after_recon = game.scenario_truth_state(player.id)
    known_after_recon = game.scenario_known_state(player.id)
    assert truth_after_recon.fact_value("northern_valley", "ambush_status") == "ACTIVE"
    assert known_after_recon.fact_value("northern_valley", "ambush_status") == "ACTIVE"
    assert not known_after_recon.node_known("enemy_north_supply_route")
    recon_nodes = _player_nodes(session, conversation)
    assert recon_nodes["northern_valley"]["facts"]["ambush_status"] == "ACTIVE"  # type: ignore[index]
    assert "enemy_north_supply_route" not in recon_nodes
    recon_request = _request(session, conversation, task)
    recon_json = json.dumps(recon_request, ensure_ascii=False)
    assert '"northern_valley.ambush_status": "ACTIVE"' in recon_json
    assert "enemy_north_supply_route" not in recon_json

    game.set_world_fact(player.id, "village_support", {"status": "GUIDE"})
    _resolve_military(
        game,
        player.id,
        target_key="northern_valley",
        mission_type="CLEAR_VALLEY",
        key="truth-knowledge-first-clear",
    )
    known_after_failure = game.scenario_known_state(player.id)
    assert known_after_failure.node_known("enemy_north_supply_route")
    assert known_after_failure.fact_value("enemy_north_supply_route", "supply_status") == "ACTIVE"
    failure_nodes = _player_nodes(session, conversation)
    assert failure_nodes["enemy_north_supply_route"]["facts"]["supply_status"] == "ACTIVE"  # type: ignore[index]
    replan = _request(session, conversation, task, "REPLAN")
    replan_json = json.dumps(replan, ensure_ascii=False)
    assert '"enemy_north_supply_route.supply_status": "ACTIVE"' in replan_json
    military_description = next(
        item["description"]
        for item in replan["allowed_tools"]
        if item["name"] == "start_military_operation"
    )
    assert "enemy_north_supply_route [AVAILABLE]" in military_description

    _resolve_military(
        game,
        player.id,
        target_key="enemy_north_supply_route",
        mission_type="DISRUPT_SUPPLY",
        key="truth-knowledge-disrupt",
    )
    assert (
        game.scenario_known_state(player.id).fact_value("enemy_north_supply_route", "supply_status")
        == "DISRUPTED"
    )
    disrupted_nodes = _player_nodes(session, conversation)
    assert (
        disrupted_nodes["enemy_north_supply_route"]["facts"]["supply_status"]  # type: ignore[index]
        == "DISRUPTED"
    )

    _resolve_military(
        game,
        player.id,
        target_key="northern_valley",
        mission_type="CLEAR_VALLEY",
        key="truth-knowledge-second-clear",
    )
    final_truth = game.scenario_truth_state(player.id)
    final_known = game.scenario_known_state(player.id)
    assert final_truth.fact_value("northern_valley", "ambush_status") == "CLEARED"
    assert final_known.fact_value("northern_valley", "ambush_status") == "CLEARED"
    assert final_truth.fact_value("northern_valley", "valley_security") == "SAFE"
    final_nodes = _player_nodes(session, conversation)
    assert final_nodes["northern_valley"]["facts"]["ambush_status"] == "CLEARED"  # type: ignore[index]


def test_hidden_supply_target_is_rejected_before_discovery(session: Session) -> None:
    game, player, conversation, task = _planning_context(session)

    with pytest.raises(AppError) as caught:
        game.preflight_military_operation(
            player_id=player.id,
            troop_count=80,
            target_key="enemy_north_supply_route",
            mission_type="DISRUPT_SUPPLY",
            strategy="CAUTIOUS",
        )

    assert caught.value.code == "INTERACTION_TARGET_HIDDEN"

    proposal = initial_strategic_starfire_plan(task.id, FULL_STARFIRE_SCOPE)
    military = next(
        step
        for step in proposal["steps"]
        if step["selected_tool_name"] == "start_military_operation"
    )
    military["tool_arguments"].update(
        {
            "target_key": "enemy_north_supply_route",
            "mission_type": "DISRUPT_SUPPLY",
        }
    )
    military["expected_outcome"]["target_key"] = "enemy_north_supply_route"
    result = PlanValidator(
        session,
        build_registry(),
        Settings(database_url="sqlite+pysqlite:///:memory:"),
    ).validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )
    assert "PLAN_INTERACTION_TARGET_HIDDEN" in {issue.code for issue in result.errors}


@pytest.mark.parametrize(
    "preflight",
    [
        lambda game, player_id: game.preflight_outpost_repair(
            player_id=player_id,
            food_commitment=20,
            gold_commitment=15,
        ),
        lambda game, player_id: game.preflight_trade_route_test(player_id=player_id),
    ],
)
def test_known_but_locked_targets_fail_closed(
    session: Session,
    preflight,  # type: ignore[no-untyped-def]
) -> None:
    game, player, _conversation, _task = _planning_context(session)

    with pytest.raises(AppError) as caught:
        preflight(game, player.id)

    assert caught.value.code == "INTERACTION_TARGET_LOCKED"
