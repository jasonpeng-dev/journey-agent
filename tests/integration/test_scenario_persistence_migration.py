from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.db.models import ScenarioDraft
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.starfire.scenario import STARFIRE_SCENARIO_DEFINITION
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
    with Session(engine) as db:
        seed_scenario_definitions(db)
        db.commit()
        draft = db.query(ScenarioDraft).one()
        loaded = ScenarioDefinitionRepository(db).load_draft(draft.scenario_id)
        assert loaded == STARFIRE_SCENARIO_DEFINITION

    engine.dispose()
