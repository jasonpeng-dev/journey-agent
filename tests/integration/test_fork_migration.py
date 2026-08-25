from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.domain.enums import GameInstanceStatus
from app.infrastructure.db.models import GameInstance, Player, Scenario, ScenarioVersion
from app.infrastructure.db.session import configure_sqlite_foreign_keys


def test_fork_provenance_migration_upgrade_and_downgrade(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'fork-provenance.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert "forked_from_game_instance_id" in {
        str(item["name"]) for item in inspector.get_columns("game_instances")
    }
    assert any(
        item.get("name") == "fk_game_instances_forked_from_game_instance"
        for item in inspector.get_foreign_keys("game_instances")
    )
    engine.dispose()

    command.downgrade(config, "r97000000001")
    engine = create_engine(database_url)
    assert "forked_from_game_instance_id" not in {
        str(item["name"]) for item in inspect(engine).get_columns("game_instances")
    }
    engine.dispose()
    get_settings.cache_clear()


def test_checkpoint_provenance_schema_and_source_revision_uniqueness(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'checkpoint-provenance.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    configure_sqlite_foreign_keys(engine)
    inspector = inspect(engine)
    columns = {str(item["name"]) for item in inspector.get_columns("game_instances")}
    assert {
        "checkpointed_from_game_instance_id",
        "checkpoint_source_runtime_revision",
        "inherited_task_count",
    } <= columns
    assert any(
        item.get("name") == "fk_game_instances_checkpointed_from_game_instance"
        for item in inspector.get_foreign_keys("game_instances")
    )
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        player = Player(name="checkpoint test player")
        scenario = Scenario(key="checkpoint_test", name="Checkpoint Test", status="PUBLISHED")
        db.add_all([player, scenario])
        db.flush()
        version = ScenarioVersion(
            scenario_id=scenario.id,
            version_number=1,
            schema_version=2,
            snapshot_document={},
            content_hash="a" * 64,
            engine_contract_key="v2",
            engine_contract_version="1",
            published_at=datetime.now(UTC),
        )
        db.add(version)
        db.flush()
        source = GameInstance(
            player_id=player.id,
            scenario_version_id=version.id,
            status=GameInstanceStatus.ACTIVE,
            creation_key="source",
            runtime_revision=10,
        )
        db.add(source)
        db.flush()
        first = GameInstance(
            player_id=player.id,
            scenario_version_id=version.id,
            status=GameInstanceStatus.ARCHIVED,
            creation_key="checkpoint-one",
            runtime_revision=1,
            checkpointed_from_game_instance_id=source.id,
            checkpoint_source_runtime_revision=10,
        )
        db.add(first)
        db.commit()
        duplicate = GameInstance(
            player_id=player.id,
            scenario_version_id=version.id,
            status=GameInstanceStatus.ARCHIVED,
            creation_key="checkpoint-two",
            runtime_revision=1,
            checkpointed_from_game_instance_id=source.id,
            checkpoint_source_runtime_revision=10,
        )
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    engine.dispose()
    get_settings.cache_clear()
