"""Explicit compatibility bootstrap for the built-in Starfire snapshot."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import ScenarioVersion
from app.scenarios.documents import SCENARIO_DOCUMENT_SCHEMA_VERSION, ScenarioDefinitionDocument
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.serialization import canonical_document, scenario_content_hash
from app.scenarios.starfire.scenario import STARFIRE_SCENARIO_DEFINITION
from app.services.scenarios import ScenarioService


def require_builtin_starfire_version(db: Session) -> ScenarioVersion:
    """Return the canonical built-in version by hash; never resolve latest/current."""

    repository = ScenarioDefinitionRepository(db)
    scenario = repository.find_scenario("starfire_command")
    if scenario is None:
        scenario = repository.persist_initial_draft(STARFIRE_SCENARIO_DEFINITION)
        return ScenarioService(db).publish_draft(scenario.id, expected_revision=1).version

    document = canonical_document(
        ScenarioDefinitionDocument.from_domain(STARFIRE_SCENARIO_DEFINITION).model_dump(mode="json")
    )
    payload = document.model_dump(mode="json")
    content_hash = scenario_content_hash(payload)
    existing = db.scalar(
        select(ScenarioVersion).where(
            ScenarioVersion.scenario_id == scenario.id,
            ScenarioVersion.content_hash == content_hash,
        )
    )
    if existing is not None:
        return existing

    # Compatibility publication intentionally does not rewrite an author's Draft
    # or current-published pointer. It materializes the exact historical built-in
    # definition needed by legacy runtimes and debug fixtures.
    next_number = (
        db.scalar(
            select(func.max(ScenarioVersion.version_number)).where(
                ScenarioVersion.scenario_id == scenario.id
            )
        )
        or 0
    ) + 1
    version = ScenarioVersion(
        scenario_id=scenario.id,
        version_number=next_number,
        schema_version=SCENARIO_DOCUMENT_SCHEMA_VERSION,
        snapshot_document=payload,
        content_hash=content_hash,
        behavior_bundle_key=document.behavior_bundle.key,
        behavior_bundle_version=document.behavior_bundle.version,
        published_at=datetime.now(UTC),
    )
    db.add(version)
    db.flush()
    return version


__all__ = ["require_builtin_starfire_version"]
