from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import get_settings


def test_goal_resolution_attempt_migration_recovers_cleanly(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'goal-resolution.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")

    try:
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        inspector = inspect(engine)
        assert "goal_resolution_attempts" in inspector.get_table_names()
        assert {
            "game_instance_id",
            "scenario_version_id",
            "original_goal_text",
            "normalized_goal_text",
            "goal_hash",
            "resolution_status",
            "grounded_public_entity_keys",
            "public_catalog_hash",
            "focused_ontology_hash",
            "interpretation_attempts",
            "value_type_diagnostics",
            "provider_metadata",
        } <= {str(item["name"]) for item in inspector.get_columns("goal_resolution_attempts")}
        engine.dispose()

        command.downgrade(config, "r99000000001")
        engine = create_engine(database_url)
        assert "goal_resolution_attempts" not in inspect(engine).get_table_names()
        engine.dispose()

        command.upgrade(config, "head")
        engine = create_engine(database_url)
        assert "goal_resolution_attempts" in inspect(engine).get_table_names()
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_resolved_goal_draft_migration_upgrade_and_downgrade(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'goal-draft.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")

    try:
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        inspector = inspect(engine)
        assert "resolved_goal_drafts" in inspector.get_table_names()
        assert {
            "resolution_attempt_id",
            "original_goal_text",
            "scenario_content_hash",
            "formal_goal_contract_json",
            "formal_goal_contract_hash",
            "confirmed_task_id",
            "confirmed_at",
        } <= {str(item["name"]) for item in inspector.get_columns("resolved_goal_drafts")}
        indexes = {item["name"]: item for item in inspector.get_indexes("resolved_goal_drafts")}
        assert indexes["uq_resolved_goal_drafts_instance_ready"]["unique"] == 1
        engine.dispose()

        command.downgrade(config, "r99100000003")
        engine = create_engine(database_url)
        assert "resolved_goal_drafts" not in inspect(engine).get_table_names()
        engine.dispose()
    finally:
        get_settings.cache_clear()
