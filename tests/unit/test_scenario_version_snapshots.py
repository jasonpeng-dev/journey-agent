from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.infrastructure.db.models import (
    ScenarioVersion,
    ScenarioVersionImmutableError,
)
from app.scenarios.documents import ScenarioDefinitionDocument
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.serialization import canonical_document
from app.scenarios.starfire.scenario import STARFIRE_SCENARIO_DEFINITION
from app.scenarios.versions import ScenarioVersionError, ScenarioVersionRepository
from app.services.scenarios import ScenarioService


def _publish(session: Session) -> ScenarioVersion:
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(
        STARFIRE_SCENARIO_DEFINITION
    )
    return (
        ScenarioService(session)
        .publish_draft(
            scenario.id,
            expected_revision=1,
        )
        .version
    )


def test_exact_published_version_loads_as_frozen_verified_snapshot(session: Session) -> None:
    version = _publish(session)

    snapshot = ScenarioVersionRepository(session).load(version.id)

    assert snapshot.id == version.id
    assert snapshot.version_number == 1
    assert snapshot.definition.world.key == STARFIRE_SCENARIO_DEFINITION.world.key
    assert canonical_document(
        ScenarioDefinitionDocument.from_domain(snapshot.definition).model_dump(mode="json")
    ) == canonical_document(
        ScenarioDefinitionDocument.from_domain(STARFIRE_SCENARIO_DEFINITION).model_dump(mode="json")
    )
    with pytest.raises(AttributeError):
        snapshot.version_number = 2  # type: ignore[misc]
    with pytest.raises(ScenarioVersionError) as missing:
        ScenarioVersionRepository(session).load(uuid4())
    assert missing.value.code == "SCENARIO_VERSION_NOT_FOUND"


def test_publishing_v2_does_not_change_readable_v1_snapshot(session: Session) -> None:
    first = _publish(session)
    original_snapshot = deepcopy(first.snapshot_document)
    scenario = ScenarioDefinitionRepository(session).find_scenario("starfire_command")
    assert scenario is not None
    changed = deepcopy(original_snapshot)
    changed["world"]["name"] = "Starfire v2"
    service = ScenarioService(session)
    service.replace_draft(
        scenario.id,
        expected_revision=1,
        definition_document=changed,
    )
    second = service.publish_draft(scenario.id, expected_revision=2).version

    loaded_v1 = ScenarioVersionRepository(session).load(first.id)
    loaded_v2 = ScenarioVersionRepository(session).load(second.id)

    assert loaded_v1.definition.world.name != loaded_v2.definition.world.name
    assert loaded_v1.version_number == 1
    assert loaded_v2.version_number == 2
    session.refresh(first)
    assert first.snapshot_document == original_snapshot


def test_orm_rejects_update_and_delete_of_published_version(session: Session) -> None:
    version = _publish(session)
    version_id = version.id
    session.commit()
    changed = deepcopy(version.snapshot_document)
    changed["world"]["name"] = "Forbidden mutation"
    version.snapshot_document = changed

    with pytest.raises(ScenarioVersionImmutableError):
        session.flush()
    session.rollback()

    persisted = session.get(ScenarioVersion, version_id)
    assert persisted is not None
    session.delete(persisted)
    with pytest.raises(ScenarioVersionImmutableError):
        session.flush()
    session.rollback()


@pytest.mark.parametrize(
    ("values", "code"),
    [
        ({"schema_version": 999}, "SCENARIO_VERSION_SCHEMA_UNSUPPORTED"),
        ({"content_hash": "0" * 64}, "SCENARIO_VERSION_HASH_MISMATCH"),
        (
            {"behavior_bundle_version": "different"},
            "SCENARIO_VERSION_BEHAVIOR_MISMATCH",
        ),
    ],
)
def test_version_loader_fails_closed_for_corrupt_metadata(
    session: Session,
    values: dict[str, object],
    code: str,
) -> None:
    version = _publish(session)
    session.execute(
        update(ScenarioVersion)
        .where(ScenarioVersion.id == version.id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    session.expire_all()

    with pytest.raises(ScenarioVersionError) as caught:
        ScenarioVersionRepository(session).load(version.id)

    assert caught.value.code == code


def test_version_loader_rejects_noncanonical_snapshot_even_when_hash_is_semantic(
    session: Session,
) -> None:
    version = _publish(session)
    reordered = deepcopy(version.snapshot_document)
    reordered["world"]["nodes"].reverse()
    session.execute(
        update(ScenarioVersion)
        .where(ScenarioVersion.id == version.id)
        .values(snapshot_document=reordered)
        .execution_options(synchronize_session=False)
    )
    session.expire_all()

    with pytest.raises(ScenarioVersionError) as caught:
        ScenarioVersionRepository(session).load(version.id)

    assert caught.value.code == "SCENARIO_VERSION_SNAPSHOT_NOT_CANONICAL"
