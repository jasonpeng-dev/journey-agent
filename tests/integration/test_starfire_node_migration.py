from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.enums import NodeStatus, NodeType, NPCRole, WorldOperationStatus
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    NPC,
    Player,
    PlayerDomainState,
    PlayerNodeState,
    PlayerWorldFact,
    PlayerWorldFactState,
    World,
    WorldNode,
    WorldOperation,
)
from app.services.game import GameService, seed_id
from app.services.seed import seed_demo_world

INITIAL_REVISION = "d0b3e5ceb9a2"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _config(database_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _upgrade(monkeypatch: pytest.MonkeyPatch, database_url: str, revision: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.upgrade(_config(database_url), revision)
    get_settings.cache_clear()


def _downgrade(monkeypatch: pytest.MonkeyPatch, database_url: str, revision: str) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.downgrade(_config(database_url), revision)
    get_settings.cache_clear()


def test_fresh_database_migrates_and_seeds_canonical_nodes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'fresh.db').as_posix()}"
    _upgrade(monkeypatch, database_url, "head")
    engine = create_engine(database_url)
    with Session(engine) as db, db.begin():
        seed_demo_world(db)
    with Session(engine) as db:
        with db.begin():
            player = GameService(db).create_player("Fresh Migration Player")
        keys = set(db.scalars(select(WorldNode.key)).all())
        truth = GameService(db).scenario_truth_state(player.id)
        known = GameService(db).scenario_known_state(player.id)
        domain = db.get(PlayerDomainState, player.id)

    assert keys == {
        "capital_council",
        "north_village",
        "northern_valley",
        "enemy_north_supply_route",
        "starfire_outpost",
        "northern_trade_route",
    }
    assert set(known.node_access) == {
        "capital_council",
        "north_village",
        "northern_valley",
        "starfire_outpost",
        "northern_trade_route",
    }
    assert known.node_access["starfire_outpost"].value == "LOCKED"
    assert known.node_access["northern_trade_route"].value == "LOCKED"
    assert not known.node_known("enemy_north_supply_route")
    assert not known.fact_known("northern_valley", "ambush_status")
    assert truth.fact_value("northern_valley", "ambush_status") == "ACTIVE"
    assert truth.fact_value("enemy_north_supply_route", "supply_status") == "ACTIVE"
    assert truth.fact_value("starfire_outpost", "outpost_status") == "DAMAGED"
    assert truth.fact_value("northern_trade_route", "trade_route_status") == "CLOSED"
    assert domain is not None
    assert (domain.soldiers_total, domain.food, player.gold, domain.morale) == (300, 100, 80, 60)


def test_upgrade_merges_legacy_nodes_without_rewriting_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'upgrade.db').as_posix()}"
    _upgrade(monkeypatch, database_url, INITIAL_REVISION)
    engine = create_engine(database_url)
    world_id = seed_id("world:starfire_command")
    entrance_id = seed_id("node:valley_entrance")
    ambush_id = seed_id("node:ambush_valley")
    with Session(engine) as db:
        world = World(id=world_id, key="starfire_command", name="Legacy Starfire", chapter=1)
        node_specs = [
            ("capital_council", NodeType.START, NodeStatus.AVAILABLE),
            ("north_village", NodeType.NPC, NodeStatus.AVAILABLE),
            ("valley_entrance", NodeType.EVENT, NodeStatus.AVAILABLE),
            ("ambush_valley", NodeType.ENCOUNTER, NodeStatus.LOCKED),
            ("starfire_outpost", NodeType.EVENT, NodeStatus.LOCKED),
            ("northern_trade_route", NodeType.EVENT, NodeStatus.LOCKED),
        ]
        nodes = {
            key: WorldNode(
                id=seed_id(f"node:{key}"),
                world_id=world_id,
                key=key,
                name=key,
                description=key,
                type=node_type,
                default_status=status,
            )
            for key, node_type, status in node_specs
        }
        db.add(world)
        db.add_all(nodes.values())
        db.flush()
        unknown_player = Player(name="Unknown Supply", current_node_id=ambush_id)
        known_player = Player(name="Known Supply", current_node_id=entrance_id)
        disrupted_player = Player(name="Disrupted Supply", current_node_id=entrance_id)
        officer = NPC(
            key="legacy_general",
            name="Legacy General",
            persona="Migration fixture",
            doctrine={},
            authority_limits={},
            current_node_id=entrance_id,
            role=NPCRole.GENERAL,
            permission_profile={},
        )
        db.add_all([unknown_player, known_player, disrupted_player, officer])
        db.flush()
        legacy_node_states = Table(
            "player_node_states",
            MetaData(),
            autoload_with=db.get_bind(),
        )
        now = datetime.now(UTC)
        db.execute(
            legacy_node_states.insert(),
            [
                {
                    "player_id": unknown_player.id.hex,
                    "node_id": entrance_id.hex,
                    "status": NodeStatus.ENTERED.value,
                    "version": 2,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "player_id": unknown_player.id.hex,
                    "node_id": ambush_id.hex,
                    "status": NodeStatus.AVAILABLE.value,
                    "version": 4,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "player_id": known_player.id.hex,
                    "node_id": entrance_id.hex,
                    "status": NodeStatus.AVAILABLE.value,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "player_id": disrupted_player.id.hex,
                    "node_id": entrance_id.hex,
                    "status": NodeStatus.AVAILABLE.value,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "player_id": disrupted_player.id.hex,
                    "node_id": ambush_id.hex,
                    "status": NodeStatus.LOCKED.value,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "player_id": known_player.id.hex,
                    "node_id": ambush_id.hex,
                    "status": NodeStatus.LOCKED.value,
                    "version": 1,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
        db.add_all(
            [
                PlayerWorldFact(
                    player_id=unknown_player.id,
                    key="enemy_supply_route",
                    value={"status": "UNKNOWN"},
                ),
                PlayerWorldFact(
                    player_id=known_player.id,
                    key="enemy_supply_route",
                    value={"status": "ACTIVE"},
                ),
                PlayerWorldFact(
                    player_id=known_player.id,
                    key="valley_intelligence",
                    value={"status": "COMPLETE"},
                ),
                PlayerWorldFact(
                    player_id=known_player.id,
                    key="valley_security",
                    value={"status": "SAFE"},
                ),
                PlayerWorldFact(
                    player_id=known_player.id,
                    key="village_support",
                    value={"status": "GUIDE"},
                ),
                PlayerWorldFact(
                    player_id=known_player.id,
                    key="starfire_outpost_status",
                    value={"status": "OPERATIONAL"},
                ),
                PlayerWorldFact(
                    player_id=known_player.id,
                    key="northern_trade_route_status",
                    value={"status": "OPEN"},
                ),
                PlayerWorldFact(
                    player_id=disrupted_player.id,
                    key="enemy_supply_route",
                    value={"status": "DISRUPTED"},
                ),
            ]
        )
        operation_id = uuid4()
        legacy_operations = Table(
            "world_operations",
            MetaData(),
            autoload_with=db.get_bind(),
        )
        db.execute(
            legacy_operations.insert().values(
                id=operation_id.hex,
                player_id=known_player.id.hex,
                officer_npc_id=officer.id.hex,
                operation_type="MILITARY",
                target_key="ambush_valley",
                status=WorldOperationStatus.PENDING.value,
                parameters={"mission_type": "CLEAR_VALLEY"},
                idempotency_key=f"legacy-operation-{uuid4()}",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
        unknown_player_id = unknown_player.id
        known_player_id = known_player.id
        disrupted_player_id = disrupted_player.id
        officer_id = officer.id
    engine.dispose()

    _upgrade(monkeypatch, database_url, "head")
    engine = create_engine(database_url)
    with Session(engine) as db:
        nodes = {node.key: node for node in db.scalars(select(WorldNode)).all()}
        northern_valley = nodes["northern_valley"]
        supply_route = nodes["enemy_north_supply_route"]
        loaded_unknown_player = db.get(Player, unknown_player_id)
        loaded_known_player = db.get(Player, known_player_id)
        loaded_officer = db.get(NPC, officer_id)
        loaded_operation = db.get(WorldOperation, operation_id)
        assert loaded_unknown_player is not None
        assert loaded_known_player is not None
        assert loaded_officer is not None
        assert loaded_operation is not None

        assert "valley_entrance" not in nodes
        assert "ambush_valley" not in nodes
        assert loaded_unknown_player.current_node_id == northern_valley.id
        assert loaded_known_player.current_node_id == northern_valley.id
        assert loaded_officer.current_node_id == northern_valley.id
        assert db.get(PlayerNodeState, (unknown_player_id, northern_valley.id)) is not None
        assert db.get(PlayerNodeState, (known_player_id, northern_valley.id)) is not None
        unknown_supply_state = db.get(
            PlayerNodeState,
            (unknown_player_id, supply_route.id),
        )
        known_supply_state = db.get(
            PlayerNodeState,
            (known_player_id, supply_route.id),
        )
        disrupted_supply_state = db.get(
            PlayerNodeState,
            (disrupted_player_id, supply_route.id),
        )
        legacy_fact = db.get(PlayerWorldFact, (unknown_player_id, "enemy_supply_route"))
        assert unknown_supply_state is not None
        assert known_supply_state is not None
        assert disrupted_supply_state is not None
        assert legacy_fact is not None
        assert unknown_supply_state.status == NodeStatus.LOCKED
        assert known_supply_state.status == NodeStatus.AVAILABLE
        assert unknown_supply_state.visibility == Visibility.HIDDEN
        assert known_supply_state.visibility == Visibility.KNOWN
        assert disrupted_supply_state.status == NodeStatus.AVAILABLE
        assert disrupted_supply_state.visibility == Visibility.KNOWN
        unknown_supply_fact = db.get(
            PlayerWorldFactState,
            (unknown_player_id, supply_route.id, "supply_status"),
        )
        known_supply_fact = db.get(
            PlayerWorldFactState,
            (known_player_id, supply_route.id, "supply_status"),
        )
        disrupted_supply_fact = db.get(
            PlayerWorldFactState,
            (disrupted_player_id, supply_route.id, "supply_status"),
        )
        assert unknown_supply_fact is not None
        assert known_supply_fact is not None
        assert disrupted_supply_fact is not None
        assert unknown_supply_fact.truth_value == "ACTIVE"
        assert unknown_supply_fact.visibility == Visibility.HIDDEN
        assert known_supply_fact.truth_value == "ACTIVE"
        assert known_supply_fact.visibility == Visibility.KNOWN
        assert disrupted_supply_fact.truth_value == "DISRUPTED"
        assert disrupted_supply_fact.visibility == Visibility.KNOWN
        migrated_facts = {
            (node.key, fact.fact_key): fact
            for fact, node in db.execute(
                select(PlayerWorldFactState, WorldNode)
                .join(WorldNode, WorldNode.id == PlayerWorldFactState.node_id)
                .where(PlayerWorldFactState.player_id == known_player_id)
            ).all()
        }
        assert migrated_facts[("north_village", "village_support")].truth_value == "GUIDE"
        assert migrated_facts[("northern_valley", "valley_intelligence")].truth_value == "COMPLETE"
        assert migrated_facts[("northern_valley", "valley_security")].truth_value == "SAFE"
        assert migrated_facts[("northern_valley", "ambush_status")].truth_value == "CLEARED"
        assert migrated_facts[("northern_valley", "ambush_status")].visibility == Visibility.KNOWN
        assert migrated_facts[("starfire_outpost", "outpost_status")].truth_value == "OPERATIONAL"
        assert migrated_facts[("northern_trade_route", "trade_route_status")].truth_value == "OPEN"
        assert loaded_operation.target_key == "ambush_valley"
        assert legacy_fact.value == {"status": "UNKNOWN"}
    engine.dispose()

    _downgrade(monkeypatch, database_url, INITIAL_REVISION)
    engine = create_engine(database_url)
    with Session(engine) as db:
        downgraded_keys = set(db.scalars(select(WorldNode.key)).all())
        downgraded_supply = db.get(
            PlayerWorldFact,
            (unknown_player_id, "enemy_supply_route"),
        )

    assert "valley_entrance" in downgraded_keys
    assert "ambush_valley" in downgraded_keys
    assert "northern_valley" not in downgraded_keys
    assert "enemy_north_supply_route" not in downgraded_keys
    assert downgraded_supply is not None
    assert downgraded_supply.value == {"status": "UNKNOWN"}
    engine.dispose()

    _upgrade(monkeypatch, database_url, "head")
    engine = create_engine(database_url)
    with Session(engine) as db:
        round_trip_keys = set(db.scalars(select(WorldNode.key)).all())
        round_trip_operation = db.get(WorldOperation, operation_id)

    assert "northern_valley" in round_trip_keys
    assert "enemy_north_supply_route" in round_trip_keys
    assert "valley_entrance" not in round_trip_keys
    assert "ambush_valley" not in round_trip_keys
    assert round_trip_operation is not None
    assert round_trip_operation.target_key == "ambush_valley"
    engine.dispose()
