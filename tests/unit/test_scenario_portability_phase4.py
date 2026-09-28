"""Phase 4 official artifact and explicit fresh lifecycle regression."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import GameInstance, Scenario, ScenarioDraft, ScenarioVersion
from app.infrastructure.db.session import configure_sqlite_foreign_keys
from app.scenarios.portability import ScenarioArtifactCodec, serialize_scenario_artifact
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_lifecycle import GameLifecycleService
from app.services.player_projection import PlayerProjectionService
from app.services.scenario_portability import ScenarioPortabilityService

REPO_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_ARTIFACT = (
    REPO_ROOT / "scenarios" / "examples" / "linjiang_infrastructure_recovery.scenario.json"
)
OFFICIAL_KEY = "linjiang_infrastructure_recovery_v2_0_final_local"
OFFICIAL_HASH = "7ed0b279093724f131d4156372972cde9b7db5c0791d75dd7ee41ff8945689b0"


@pytest.fixture
def phase4_factory() -> Any:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    configure_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_official_artifact_is_strict_deterministic_and_source_free() -> None:
    raw = OFFICIAL_ARTIFACT.read_bytes().replace(b"\r\n", b"\n")
    candidate = ScenarioArtifactCodec.parse(raw)
    assert candidate.artifact.artifact_type == "journey_scenario"
    assert candidate.artifact.artifact_version == 1
    assert candidate.artifact.content_type == "release"
    assert candidate.artifact.schema_version == 3
    assert candidate.scenario_key == OFFICIAL_KEY
    assert candidate.content_hash == OFFICIAL_HASH
    assert serialize_scenario_artifact(candidate).encode("utf-8") == raw
    assert len(candidate.definition.goal_resolution.quick_inputs) == 6
    assert "0028444e-bb3e-42ea-9a5b-98cd924865a2" not in raw.decode("utf-8")
    assert "c3a2c01578714c9989cf50e8d6d77a84" not in raw.decode("utf-8")
    payload = json.loads(raw)
    assert "published_at" not in json.dumps(payload["definition"])
    assert "scenario_version_id" not in json.dumps(payload["definition"])


def test_official_release_import_creates_v1_and_exact_runtime_game(
    phase4_factory: Any,
) -> None:
    with phase4_factory() as db:
        before = {
            "scenarios": db.scalar(select(func.count()).select_from(Scenario)),
            "drafts": db.scalar(select(func.count()).select_from(ScenarioDraft)),
            "versions": db.scalar(select(func.count()).select_from(ScenarioVersion)),
            "games": db.scalar(select(func.count()).select_from(GameInstance)),
        }
        assert before == {"scenarios": 0, "drafts": 0, "versions": 0, "games": 0}

        result = ScenarioPortabilityService(db).import_as_new(OFFICIAL_ARTIFACT.read_bytes())
        assert result.scenario.key == OFFICIAL_KEY
        assert result.published_version is not None
        assert result.published_version.version_number == 1
        assert result.published_version.schema_version == 3
        assert result.content_hash == OFFICIAL_HASH
        db.commit()

        counts = {
            "scenarios": db.scalar(select(func.count()).select_from(Scenario)),
            "drafts": db.scalar(select(func.count()).select_from(ScenarioDraft)),
            "versions": db.scalar(select(func.count()).select_from(ScenarioVersion)),
            "games": db.scalar(select(func.count()).select_from(GameInstance)),
        }
        assert counts == {"scenarios": 1, "drafts": 1, "versions": 1, "games": 0}

        snapshot = ScenarioVersionRepository(db).load(result.published_version.id)
        assert snapshot.version_number == 1
        assert snapshot.content_hash == OFFICIAL_HASH
        runtime = GameLifecycleService(db).create(
            scenario_id=result.scenario.id,
            scenario_version_id=result.published_version.id,
            idempotency_key="phase4-official-runtime-game",
        )
        db.commit()
        play = PlayerProjectionService(db).game_state(GameInstanceId(runtime.instance.id))
        assert play.scenario_metadata.quick_inputs is not None
        assert len(play.scenario_metadata.quick_inputs) == 6
        assert db.scalar(select(func.count()).select_from(GameInstance)) == 1


def test_production_bootstrap_is_migration_only_and_carries_the_official_example() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY scenarios ./scenarios" in dockerfile
    assert "python -m app.seed" not in dockerfile
    assert "alembic upgrade head && exec uv run uvicorn" in dockerfile
