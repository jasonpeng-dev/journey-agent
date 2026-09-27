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
from app.services.game_lifecycle import GameLifecycleError, GameLifecycleService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioLifecycleError, ScenarioService
from tests.unit.test_scenario_definition_v2 import _contract_scenario_document


def _scenario(session: Session):  # type: ignore[no-untyped-def]
    definition = ScenarioDefinitionV2.model_validate(_contract_scenario_document())
    return ScenarioDefinitionRepository(session).persist_initial_draft(definition)


def test_draft_revision_conflict_and_invalid_publish_diagnostics(session: Session) -> None:
    scenario = _scenario(session)
    service = ScenarioService(session)
    invalid = _contract_scenario_document()
    invalid["world"]["nodes"][0]["interaction_keys"].append("missing")
    draft = service.replace_draft(scenario.id, expected_revision=1, definition_document=invalid)
    with pytest.raises(ScenarioLifecycleError) as stale:
        service.replace_draft(
            scenario.id,
            expected_revision=1,
            definition_document=_contract_scenario_document(),
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


def test_generic_draft_replace_rejects_action_derived_and_relation_identity_mutations(
    session: Session,
) -> None:
    scenario = _scenario(session)
    service = ScenarioService(session)
    original = service.get_draft(scenario.id).definition_document

    changed_action = deepcopy(original)
    action = next(item for item in changed_action["actions"] if item["key"] == "treat_patient")
    action["key"] = "treat_patient_v2"
    with pytest.raises(ScenarioLifecycleError) as action_error:
        service.replace_draft(scenario.id, expected_revision=1, definition_document=changed_action)
    assert action_error.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"

    blank_action_key = deepcopy(original)
    next(item for item in blank_action_key["actions"] if item["key"] == "treat_patient")["key"] = ""
    with pytest.raises(ScenarioLifecycleError) as blank_action_error:
        service.replace_draft(
            scenario.id, expected_revision=1, definition_document=blank_action_key
        )
    assert blank_action_error.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"

    changed_relation = deepcopy(original)
    changed_relation["world"]["relations"][0]["target_node_key"] = "triage_room"
    with pytest.raises(ScenarioLifecycleError) as relation_error:
        service.replace_draft(
            scenario.id, expected_revision=1, definition_document=changed_relation
        )
    assert relation_error.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"

    definition_document = _contract_scenario_document()
    definition_document["metadata"]["key"] = "generic_contract_derived_identity"
    definition_document["world"]["key"] = "generic_contract_derived_identity"
    definition_document["derived_states"] = [
        {
            "key": "status_summary",
            "name": "Status summary",
            "description": "",
            "value_type": "BOOLEAN",
            "available_value": True,
            "unavailable_value": False,
            "dependencies": [
                {
                    "kind": "FACT",
                    "node_key": "patient_one",
                    "fact_key": "stable",
                    "accepted_values": [True],
                }
            ],
        }
    ]
    derived_scenario = ScenarioDefinitionRepository(session).persist_initial_draft(
        ScenarioDefinitionV2.model_validate(definition_document)
    )
    derived_document = ScenarioService(session).get_draft(derived_scenario.id).definition_document
    renamed_derived = deepcopy(derived_document)
    renamed_derived["derived_states"][0]["key"] = "status_summary_v2"
    with pytest.raises(ScenarioLifecycleError) as derived_error:
        ScenarioService(session).replace_draft(
            derived_scenario.id,
            expected_revision=1,
            definition_document=renamed_derived,
        )
    assert derived_error.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"


def test_safe_draft_action_rename_persists_and_rewrites_all_inbound_references(
    session: Session,
) -> None:
    scenario = _scenario(session)
    service = ScenarioService(session)

    renamed = service.rename_draft_key(
        scenario.id,
        expected_revision=1,
        object_kind="action",
        old_key="treat_patient",
        new_key="treat_patient_v2",
    )

    assert renamed.revision == 2
    actions = {item["key"] for item in renamed.definition_document["actions"]}
    assert "treat_patient_v2" in actions and "treat_patient" not in actions
    actor = next(
        item
        for item in renamed.definition_document["actors"]["actor_profiles"]
        if item["key"] == "doctor_lee"
    )
    assert "treat_patient_v2" in actor["allowed_action_keys"]
    assert "treat_patient" not in actor["allowed_action_keys"]
    assert all(
        edge.target.object_key != "treat_patient"
        for edge in service.references(scenario.id)
        if edge.target.object_kind == "action"
    )


def test_generic_draft_replace_requires_complete_relation_identity_at_creation(
    session: Session,
) -> None:
    scenario = _scenario(session)
    service = ScenarioService(session)
    incomplete = service.get_draft(scenario.id).definition_document
    incomplete["world"]["relations"].append(
        {
            "key": "new_relation",
            "source_node_key": "",
            "relation_type_key": "",
            "target_node_key": "",
        }
    )
    with pytest.raises(ScenarioLifecycleError) as incomplete_creation:
        service.replace_draft(scenario.id, expected_revision=1, definition_document=incomplete)
    assert incomplete_creation.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"
    persisted = service.get_draft(scenario.id)
    assert persisted.revision == 1
    assert all(
        item.get("key") != "new_relation"
        for item in persisted.definition_document["world"]["relations"]
    )

    complete = deepcopy(persisted.definition_document)
    complete["world"]["relations"].append(
        {
            "key": "new_relation",
            "source_node_key": "patient_one",
            "relation_type_key": "contains",
            "target_node_key": "triage_room",
        }
    )
    created = service.replace_draft(scenario.id, expected_revision=1, definition_document=complete)

    changed = deepcopy(created.definition_document)
    relation = next(
        item for item in changed["world"]["relations"] if item.get("key") == "new_relation"
    )
    relation["target_node_key"] = "patient_one"
    with pytest.raises(ScenarioLifecycleError) as blocked:
        service.replace_draft(
            scenario.id, expected_revision=created.revision, definition_document=changed
        )
    assert blocked.value.code == "SCENARIO_IDENTITY_MUTATION_REQUIRES_OPERATION"
    persisted_after_change = service.get_draft(scenario.id)
    assert persisted_after_change.revision == created.revision
    assert (
        next(
            item
            for item in persisted_after_change.definition_document["world"]["relations"]
            if item.get("key") == "new_relation"
        )["target_node_key"]
        == "triage_room"
    )


def test_publish_increments_versions_and_semantic_no_change_is_rejected(session: Session) -> None:
    scenario = _scenario(session)
    service = ScenarioService(session)
    first = service.publish_draft(scenario.id, expected_revision=1)
    reordered = deepcopy(first.version.snapshot_document)
    reordered["world"]["nodes"].reverse()
    service.replace_draft(scenario.id, expected_revision=1, definition_document=reordered)
    with pytest.raises(ScenarioLifecycleError) as unchanged:
        service.publish_draft(scenario.id, expected_revision=2)
    assert unchanged.value.code == "SCENARIO_PUBLISH_NO_CHANGES"
    changed = deepcopy(first.version.snapshot_document)
    changed["metadata"]["name"] = "Generic Contract Revised"
    changed["world"]["name"] = "Generic Contract Revised"
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
    with pytest.raises(GameLifecycleError) as blocked:
        GameLifecycleService(session).create(
            scenario_version_id=version.id,
            idempotency_key=f"corrupt-version-{code}",
        )
    assert blocked.value.code == "LEGACY_SCENARIO_VERSION_READ_ONLY"


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
