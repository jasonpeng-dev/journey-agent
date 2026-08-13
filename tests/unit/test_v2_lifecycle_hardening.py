from copy import deepcopy

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import (
    GameInstance,
    GameInstanceBindingImmutableError,
    Player,
    ScenarioVersion,
    ScenarioVersionImmutableError,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.serialization import scenario_content_hash
from app.scenarios.versions import ScenarioVersionError, ScenarioVersionRepository
from app.services.game_instances import GameInstanceService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioLifecycleError, ScenarioService
from tests.unit.test_scenario_definition_v2 import _medical_scenario_document


def _scenario(session: Session):  # type: ignore[no-untyped-def]
    definition = ScenarioDefinitionV2.model_validate(_medical_scenario_document())
    return ScenarioDefinitionRepository(session).persist_initial_draft(definition)


def test_draft_revision_conflict_and_invalid_publish_diagnostics(session: Session) -> None:
    scenario = _scenario(session)
    service = ScenarioService(session)
    invalid = _medical_scenario_document()
    invalid["world"]["nodes"][0]["interaction_keys"].append("missing")
    draft = service.replace_draft(scenario.id, expected_revision=1, definition_document=invalid)
    with pytest.raises(ScenarioLifecycleError) as stale:
        service.replace_draft(
            scenario.id,
            expected_revision=1,
            definition_document=_medical_scenario_document(),
        )
    assert stale.value.code == "SCENARIO_DRAFT_CONFLICT"
    result = service.validate_draft(scenario.id)
    assert not result.passed and draft.validation_errors
    with pytest.raises(ScenarioLifecycleError) as blocked:
        service.publish_draft(scenario.id, expected_revision=2)
    assert blocked.value.code == "SCENARIO_DRAFT_INVALID"
    assert (
        session.scalar(
            select(func.count())
            .select_from(ScenarioVersion)
            .where(ScenarioVersion.scenario_id == scenario.id)
        )
        == 0
    )


def test_publish_increments_versions_and_semantic_no_change_is_reused(session: Session) -> None:
    scenario = _scenario(session)
    service = ScenarioService(session)
    first = service.publish_draft(scenario.id, expected_revision=1)
    reordered = deepcopy(first.version.snapshot_document)
    reordered["world"]["nodes"].reverse()
    service.replace_draft(scenario.id, expected_revision=1, definition_document=reordered)
    same = service.publish_draft(scenario.id, expected_revision=2)
    assert same.status == "NO_CHANGES" and same.version.id == first.version.id
    changed = deepcopy(first.version.snapshot_document)
    changed["metadata"]["name"] = "Medical Emergency Revised"
    changed["world"]["name"] = "Medical Emergency Revised"
    service.replace_draft(scenario.id, expected_revision=2, definition_document=changed)
    second = service.publish_draft(scenario.id, expected_revision=3)
    assert second.version.version_number == 2
    assert second.version.id != first.version.id


@pytest.mark.parametrize(
    ("values", "code"),
    [
        ({"content_hash": "0" * 64}, "SCENARIO_VERSION_HASH_MISMATCH"),
        ({"schema_version": 999}, "SCENARIO_VERSION_SCHEMA_UNSUPPORTED"),
        ({"engine_contract_version": "corrupt"}, "SCENARIO_VERSION_BEHAVIOR_MISMATCH"),
    ],
)
def test_exact_version_loader_fails_closed_for_corrupt_metadata(
    session: Session, values: dict[str, object], code: str
) -> None:
    scenario = _scenario(session)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
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


def test_noncanonical_snapshot_and_published_mutation_are_rejected(session: Session) -> None:
    scenario = _scenario(session)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    version_id = version.id
    session.commit()
    changed = deepcopy(version.snapshot_document)
    changed["world"]["nodes"].reverse()
    version.snapshot_document = changed
    with pytest.raises(ScenarioVersionImmutableError):
        session.flush()
    session.rollback()
    persisted = session.get(ScenarioVersion, version_id)
    assert persisted is not None
    session.execute(
        update(ScenarioVersion)
        .where(ScenarioVersion.id == version_id)
        .values(snapshot_document=changed, content_hash=scenario_content_hash(changed))
        .execution_options(synchronize_session=False)
    )
    session.expire_all()
    with pytest.raises(ScenarioVersionError) as caught:
        ScenarioVersionRepository(session).load(version_id)
    assert caught.value.code == "SCENARIO_VERSION_SNAPSHOT_NOT_CANONICAL"


def test_instance_binding_immutable_and_initialization_rolls_back(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = _scenario(session)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name="binding-hardening")
    session.add(player)
    session.flush()
    initializer = RuntimeInitializationService(session)
    original = initializer._initialize

    def fail_after_materialization(**kwargs):  # type: ignore[no-untyped-def]
        original(**kwargs)
        raise RuntimeError("injected initialization failure")

    monkeypatch.setattr(initializer, "_initialize", fail_after_materialization)
    with pytest.raises(RuntimeError):
        initializer.create(
            player_id=player.id,
            scenario_version_id=version.id,
            creation_key="rollback-hardening",
        )
    assert (
        session.scalar(
            select(func.count())
            .select_from(GameInstance)
            .where(GameInstance.creation_key == "rollback-hardening")
        )
        == 0
    )

    monkeypatch.setattr(initializer, "_initialize", original)
    runtime = initializer.create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="immutable-hardening",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    assert scope.scenario_version_id == version.id
    session.commit()
    runtime.instance.scenario_version_id = scenario.id
    with pytest.raises(GameInstanceBindingImmutableError):
        session.flush()
