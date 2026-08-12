from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, insert, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.db.models import AgentTask, ConversationSession
from app.services.game import GameService, seed_id
from app.services.seed import seed_demo_world
from app.services.tasks import TaskService

PRE_SCOPE_REVISION = "9f3c2d8a4b71"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _upgrade(monkeypatch: pytest.MonkeyPatch, database_url: str, revision: str) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.upgrade(config, revision)
    get_settings.cache_clear()


def test_scope_migration_backfills_only_preexisting_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'task-scope.db').as_posix()}"
    _upgrade(monkeypatch, database_url, PRE_SCOPE_REVISION)
    engine = create_engine(database_url)
    with Session(engine) as db:
        seed_demo_world(db)
        player = GameService(db).create_player("Legacy Task Player")
        conversation_id = uuid4()
        officer_id = seed_id("npc:shen_ce")
        now = datetime.now(UTC)
        conversations = Table("conversation_sessions", MetaData(), autoload_with=engine)
        db.execute(
            insert(conversations).values(
                id=conversation_id.hex,
                player_id=player.id.hex,
                npc_id=officer_id.hex,
                status="ACTIVE",
                summary="",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
        player_id = player.id
        metadata = MetaData()
        tasks = Table("agent_tasks", metadata, autoload_with=engine)
        legacy_task_id = uuid4()
        db.execute(
            insert(tasks).values(
                id=legacy_task_id.hex,
                player_id=player_id.hex,
                owner_npc_id=officer_id.hex,
                origin_session_id=conversation_id.hex,
                last_session_id=conversation_id.hex,
                goal_description="Legacy strategic command",
                scenario_key="starfire_command",
                planning_mode="PROVIDER",
                status="ACTIVE",
                current_plan_version=0,
                replan_count=0,
                last_error_code=None,
                version=1,
                completed_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    engine.dispose()

    _upgrade(monkeypatch, database_url, "head")
    engine = create_engine(database_url)
    with Session(engine) as db:
        legacy = db.get(AgentTask, legacy_task_id)
        assert legacy is not None
        assert legacy.objective_resolution_status == "CONFIRMED"
        assert legacy.objective_scope_keys == ["FULL_NORTHERN_RECOVERY"]
        assert legacy.objective_catalog_version == "starfire-objectives-v1"
        assert legacy.objective_resolver_source == "LEGACY_MIGRATION"
        assert legacy.objective_confirmation_source == "LEGACY_MIGRATION"
        assert legacy.objective_freeze_source == "LEGACY_MIGRATION"
        assert TaskService(db).require_frozen_scope(legacy).objective_keys == (
            "FULL_NORTHERN_RECOVERY",
        )

        conversation = db.scalar(select(ConversationSession).limit(1))
        assert conversation is not None
        legacy.status = "SUCCEEDED"
        db.flush()
        fresh = TaskService(db).create_task(
            conversation,
            "A newly issued unsupported command",
            "starfire_command",
        )
        assert fresh.objective_resolution_status == "UNRESOLVED"
        assert fresh.objective_scope_keys is None
        assert fresh.objective_catalog_version is None
    engine.dispose()


def test_fresh_database_has_unresolved_task_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'fresh-task-scope.db').as_posix()}"
    _upgrade(monkeypatch, database_url, "head")
    engine = create_engine(database_url)
    with Session(engine) as db:
        seed_demo_world(db)
        player = GameService(db).create_player("Fresh Task Player")
        conversation = ConversationSession(
            player_id=player.id,
            npc_id=seed_id("npc:shen_ce"),
        )
        db.add(conversation)
        db.flush()
        task = TaskService(db).create_task(
            conversation,
            "A fresh ambiguous northern command",
            "starfire_command",
        )
        db.commit()

        loaded = db.get(AgentTask, task.id)
        assert loaded is not None
        assert loaded.objective_resolution_status == "UNRESOLVED"
        assert loaded.objective_scope_keys is None
        assert loaded.objective_frozen_at is None
    engine.dispose()
