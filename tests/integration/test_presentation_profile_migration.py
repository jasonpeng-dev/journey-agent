from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import get_settings


def test_presentation_profile_migration_upgrade_and_downgrade(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'presentation-profile.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")

    try:
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        inspector = inspect(engine)
        assert {
            "scenario_presentation_profiles",
            "scenario_presentation_profile_revisions",
        } <= set(inspector.get_table_names())
        assert {
            "scenario_id",
            "revision",
            "profile_document",
            "created_at",
        } <= {
            str(item["name"])
            for item in inspector.get_columns("scenario_presentation_profile_revisions")
        }
        assert any(
            item["name"] == "ix_scenario_presentation_profile_revisions_scenario_revision"
            for item in inspector.get_indexes("scenario_presentation_profile_revisions")
        )
        engine.dispose()

        command.downgrade(config, "r99100000005")
        engine = create_engine(database_url)
        assert {
            "scenario_presentation_profiles",
            "scenario_presentation_profile_revisions",
        }.isdisjoint(inspect(engine).get_table_names())
        engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(database_url)
        assert "scenario_presentation_profiles" in inspect(engine).get_table_names()
        engine.dispose()
    finally:
        get_settings.cache_clear()
