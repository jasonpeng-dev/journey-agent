"""Load built-in Scenario v2 data files through the public definition contract."""

from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import ScenarioDraft, ScenarioVersion
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.serialization import scenario_content_hash
from app.services.scenarios import ScenarioService

_DATA_DIRECTORY = Path(__file__).with_name("data")


def load_builtin_scenario(filename: str) -> ScenarioDefinitionV2:
    path = _DATA_DIRECTORY / filename
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Built-in Scenario data must contain one aggregate document")
    return ScenarioDefinitionV2.model_validate(payload)


STARFIRE_V2 = load_builtin_scenario("starfire_v2.yaml")
MEDICAL_EMERGENCY_V2 = load_builtin_scenario("medical_emergency_v2.yaml")


def require_builtin_v2_version(
    db: Session,
    definition: ScenarioDefinitionV2,
) -> ScenarioVersion:
    """Persist/publish one exact built-in data definition idempotently by hash."""

    payload = definition.model_dump(mode="json")
    content_hash = scenario_content_hash(payload)
    scenario = ScenarioDefinitionRepository(db).find_scenario(definition.metadata.key)
    if scenario is None:
        scenario = ScenarioDefinitionRepository(db).persist_initial_draft(definition)
    elif scenario.name != definition.metadata.name:
        scenario.name = definition.metadata.name
        db.flush()
    existing = db.scalar(
        select(ScenarioVersion).where(
            ScenarioVersion.scenario_id == scenario.id,
            ScenarioVersion.content_hash == content_hash,
        )
    )
    if existing is not None:
        return existing
    draft = db.get(ScenarioDraft, scenario.id)
    if draft is None:
        raise ValueError("Built-in Scenario is missing its Draft")
    service = ScenarioService(db)
    expected_revision = draft.revision
    service.replace_draft(
        scenario.id,
        expected_revision=expected_revision,
        definition_document=payload,
    )
    return service.publish_draft(
        scenario.id,
        expected_revision=expected_revision + 1,
    ).version


__all__ = [
    "MEDICAL_EMERGENCY_V2",
    "STARFIRE_V2",
    "load_builtin_scenario",
    "require_builtin_v2_version",
]
