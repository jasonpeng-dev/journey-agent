import json
from datetime import UTC, datetime
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import AgentTask, Player
from app.scenarios.builtin import MEDICAL_EMERGENCY_V2, require_builtin_v2_version
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.runtime_recovery import RuntimeRecoveryService


def test_existing_r8_database_migrates_scope_and_recovers(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    url = f"sqlite+pysqlite:///{(tmp_path / 'existing-r8.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    command.upgrade(config, "r80000000001")

    engine = create_engine(url)
    with Session(engine) as db:
        version = require_builtin_v2_version(db, MEDICAL_EMERGENCY_V2)
        player = Player(name="existing-r8-player")
        db.add(player)
        db.flush()
        runtime = RuntimeInitializationService(db).create(
            player_id=player.id,
            scenario_version_id=version.id,
            creation_key="existing-r8-runtime",
        )
        now = datetime.now(UTC).isoformat()
        task_id = uuid4()
        db.execute(
            text(
                """
                INSERT INTO agent_tasks (
                  id, player_id, game_instance_id, owner_actor_key,
                  origin_session_id, last_session_id, goal_description, scenario_key,
                  objective_resolution_status, objective_scope_keys,
                  objective_catalog_version, planning_mode, status,
                  current_plan_version, replan_count, version, created_at, updated_at
                ) VALUES (
                  :id, :player, :instance, :actor, :session, :session, :goal, :scenario,
                  'CONFIRMED', :keys, :catalog, 'GENERIC', 'ACTIVE', 0, 0, 1, :now, :now
                )
                """
            ),
            {
                "id": task_id.hex,
                "player": player.id.hex,
                "instance": runtime.instance.id.hex,
                "actor": runtime.session.actor_key,
                "session": runtime.session.id.hex,
                "goal": "stabilize the patient",
                "scenario": MEDICAL_EMERGENCY_V2.metadata.key,
                "keys": json.dumps(["stabilize_patient"]),
                "catalog": f"scenario-version:{version.id}",
                "now": now,
            },
        )
        ids = runtime.instance.id, task_id
        db.commit()
    engine.dispose()

    get_settings.cache_clear()
    command.upgrade(config, "head")
    restarted = create_engine(url)
    scope_column = next(
        item
        for item in inspect(restarted).get_columns("agent_tasks")
        if item["name"] == "objective_scope_hash"
    )
    assert not scope_column["nullable"]
    with Session(restarted) as db:
        task = db.get(AgentTask, ids[1])
        assert task is not None and task.objective_scope_hash
        recovered = RuntimeRecoveryService(db).recover(GameInstanceId(ids[0]))
        assert recovered.tasks[0].id == task.id
    restarted.dispose()
    get_settings.cache_clear()
