import json
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agent.planning import build_planning_request
from app.core.config import Settings
from app.debug.snapshot_service import StrategicSnapshotService
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import ConversationSession
from app.scenarios.starfire.objective_catalog import (
    STARFIRE_OBJECTIVE_CATALOG,
    StarfireObjectiveKey,
)
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService
from app.tools.catalog import build_registry


def test_known_locked_relations_are_hints_in_planner_and_snapshot(session: Session) -> None:
    conversation, task = _context(session)
    request = _request(session, conversation, task)
    snapshot = StrategicSnapshotService(session, _settings()).build(
        conversation.id,
        include_trace=False,
        include_hidden_truth=False,
    )
    planning_relations = request["known_relations"]
    player_relations = snapshot["player_world_state"]["known_relations"]

    assert planning_relations == player_relations
    assert {
        (
            relation["source_node_key"],
            relation["relation_type"],
            relation["target_node_key"],
        )
        for relation in planning_relations
    } == {
        ("north_village", "SUPPORTS", "northern_valley"),
        ("north_village", "SUPPORTS", "northern_trade_route"),
        ("northern_valley", "UNLOCKS", "starfire_outpost"),
        ("northern_valley", "ENABLES", "northern_trade_route"),
        ("starfire_outpost", "ENABLES", "northern_trade_route"),
    }
    unlock = next(
        relation
        for relation in planning_relations
        if relation["target_node_key"] == "starfire_outpost"
    )
    assert unlock["source_access"] == "AVAILABLE"
    assert unlock["target_access"] == "LOCKED"


def test_hidden_relation_endpoints_do_not_leak_then_appear_after_discovery(
    session: Session,
) -> None:
    conversation, task = _context(session)
    before = json.dumps(_request(session, conversation, task), ensure_ascii=False)
    assert "enemy_north_supply_route" not in before
    assert "supply_status" not in before

    game = GameService(session)
    game.set_world_fact(task.player_id, "enemy_supply_route", {"status": "ACTIVE"})
    after_request = _request(session, conversation, task)
    after = json.dumps(after_request["known_relations"], ensure_ascii=False)

    assert {
        "source_node_key": "enemy_north_supply_route",
        "relation_type": "SUPPORTS",
        "target_node_key": "northern_valley",
        "source_access": "AVAILABLE",
        "target_access": "AVAILABLE",
    } in after_request["known_relations"]
    assert "enemy_north_supply_route" in after
    assert "REVEALS" in after


def test_relation_projection_adds_no_persistence_or_dsl() -> None:
    table_names = set(Base.metadata.tables)

    assert not any("relation" in table_name for table_name in table_names)
    assert not any("graph" in table_name for table_name in table_names)


def _context(session: Session):  # type: ignore[no-untyped-def]
    player = GameService(session).create_player(f"Relation planning {uuid4()}")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
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
    return conversation, task


def _request(
    session: Session,
    conversation: ConversationSession,
    task: object,
):  # type: ignore[no-untyped-def]
    return build_planning_request(
        db=session,
        registry=build_registry(),
        settings=_settings(),
        task=task,
        session=conversation,
        kind="PLAN",
    )


def _settings() -> Settings:
    return Settings(database_url="sqlite+pysqlite:///:memory:")
