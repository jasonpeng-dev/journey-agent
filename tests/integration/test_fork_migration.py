from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import get_settings


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
