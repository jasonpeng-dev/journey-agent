from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.db.models import Player, ScenarioDraft
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.starfire.scenario import STARFIRE_SCENARIO_DEFINITION
from app.services.game_instances import GameInstanceService
from app.services.scenarios import ScenarioService
from app.services.seed import seed_scenario_definitions

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _upgrade(monkeypatch: pytest.MonkeyPatch, database_url: str, revision: str) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.upgrade(config, revision)
    get_settings.cache_clear()


def test_fresh_database_persists_and_loads_starfire_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'scenario-c1.db').as_posix()}"
    _upgrade(monkeypatch, database_url, "head")
    engine = create_engine(database_url)

    assert {
        "scenarios",
        "scenario_drafts",
        "scenario_versions",
    }.issubset(inspect(engine).get_table_names())
    inspector = inspect(engine)
    assert "current_published_version_id" in {
        column["name"] for column in inspector.get_columns("scenarios")
    }
    assert {"content_hash", "base_scenario_version_id"}.issubset(
        {column["name"] for column in inspector.get_columns("scenario_drafts")}
    )
    with Session(engine) as db:
        seed_scenario_definitions(db)
        db.commit()
        draft = db.query(ScenarioDraft).one()
        loaded = ScenarioDefinitionRepository(db).load_draft(draft.scenario_id)
        assert loaded == STARFIRE_SCENARIO_DEFINITION

    engine.dispose()


def test_migrated_game_instance_binding_is_database_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'scenario-c4.db').as_posix()}"
    _upgrade(monkeypatch, database_url, "head")
    engine = create_engine(database_url)
    assert "game_instances" in inspect(engine).get_table_names()
    assert "creation_key" in {
        column["name"] for column in inspect(engine).get_columns("game_instances")
    }
    runtime_tables = {
        "game_instance_node_states",
        "game_instance_fact_states",
        "game_instance_resource_states",
        "game_instance_world_facts",
        "game_instance_officer_appointments",
    }
    assert runtime_tables.issubset(inspect(engine).get_table_names())
    for table_name in (
        "conversation_sessions",
        "memories",
        "agent_tasks",
        "world_operations",
        "player_decision_requests",
    ):
        instance_column = next(
            column
            for column in inspect(engine).get_columns(table_name)
            if column["name"] == "game_instance_id"
        )
        assert instance_column["nullable"] is True
    with Session(engine) as db:
        seed_scenario_definitions(db)
        scenario = ScenarioDefinitionRepository(db).find_scenario("starfire_command")
        assert scenario is not None
        version = (
            ScenarioService(db)
            .publish_draft(
                scenario.id,
                expected_revision=1,
            )
            .version
        )
        player = Player(name="migration-player")
        db.add(player)
        db.flush()
        instance = GameInstanceService(db).create(
            player_id=player.id,
            scenario_version_id=version.id,
        )
        db.commit()

        with pytest.raises(DBAPIError):
            db.execute(
                text("UPDATE game_instances SET scenario_version_id = :id WHERE id = :instance"),
                {"id": uuid4(), "instance": instance.id},
            )
        db.rollback()

    engine.dispose()


def test_migrated_database_rejects_raw_scenario_version_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'scenario-c3.db').as_posix()}"
    _upgrade(monkeypatch, database_url, "head")
    engine = create_engine(database_url)
    with Session(engine) as db:
        seed_scenario_definitions(db)
        scenario = ScenarioDefinitionRepository(db).find_scenario("starfire_command")
        assert scenario is not None
        version = (
            ScenarioService(db)
            .publish_draft(
                scenario.id,
                expected_revision=1,
            )
            .version
        )
        db.commit()

        with pytest.raises(DBAPIError):
            db.execute(
                text("UPDATE scenario_versions SET version_number = 2 WHERE id = :id"),
                {"id": version.id},
            )
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(
                text("DELETE FROM scenario_versions WHERE id = :id"),
                {"id": version.id},
            )
        db.rollback()

    engine.dispose()
