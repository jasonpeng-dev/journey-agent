"""Phase 1 Scenario portability artifact and import/export contract tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.infrastructure.db.base import Base
from app.infrastructure.db.models import GameInstance, Scenario, ScenarioDraft, ScenarioVersion
from app.infrastructure.db.session import configure_sqlite_foreign_keys
from app.scenarios.migration import preview_v2_to_v3
from app.scenarios.portability import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_CONTAINER_ENTRIES,
    MAX_ARTIFACT_DEPTH,
    ScenarioArtifactCodec,
    ScenarioPortabilityError,
    parse_scenario_artifact,
    serialize_scenario_artifact,
)
from app.scenarios.serialization import canonical_document_payload
from app.scenarios.versions import ScenarioVersionRepository
from app.services.scenario_portability import ScenarioPortabilityService
from app.services.scenarios import ScenarioService
from tests.scenario_fixtures import GENERIC_TEST, LINJIANG_CURRENT_TEST


@pytest.fixture
def db_factory():  # type: ignore[no-untyped-def]
    engines = []

    def make() -> Session:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        configure_sqlite_foreign_keys(engine)
        Base.metadata.create_all(engine)
        engines.append(engine)
        return sessionmaker(engine, expire_on_commit=False)()

    yield make
    for engine in engines:
        engine.dispose()


def _v3_document(
    *, key: str = "portability_source", name: str = "Portable Source"
) -> dict[str, Any]:
    document = preview_v2_to_v3(GENERIC_TEST.model_dump(mode="json")).target_document
    document["metadata"]["key"] = key
    document["metadata"]["name"] = name
    document["world"]["key"] = key
    document["world"]["name"] = name
    document["planning"]["instructions"] = ["first instruction", "second instruction"]
    document["goal_resolution"]["quick_inputs"] = ["first objective", "second objective"]
    return document


def _create_draft(db: Session, document: dict[str, Any]) -> Scenario:
    service = ScenarioService(db)
    scenario = service.create_from_authored_document(
        key=document["metadata"]["key"],
        name=document["metadata"]["name"],
        definition_document=document,
    )
    db.commit()
    return scenario


def _create_published(db: Session, document: dict[str, Any]) -> tuple[Scenario, ScenarioVersion]:
    service = ScenarioService(db)
    scenario = service.create_from_authored_document(
        key=document["metadata"]["key"],
        name=document["metadata"]["name"],
        definition_document=document,
    )
    db.flush()
    draft = service.get_draft(scenario.id)
    published = service.publish_draft(scenario.id, expected_revision=draft.revision)
    db.commit()
    return scenario, published.version


def _count(db: Session, model: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def test_artifact_draft_and_release_roundtrip_preserves_authored_v3_fields() -> None:
    document = _v3_document()
    artifact = ScenarioArtifactCodec.from_definition(document, content_type="release")
    raw = serialize_scenario_artifact(artifact)
    candidate = parse_scenario_artifact(raw.encode("utf-8"))

    assert candidate.definition.schema_version == 3
    assert candidate.content_type == "release"
    assert candidate.content_hash == artifact.content_hash
    assert candidate.canonical_document == artifact.definition
    assert any(
        effect["kind"] == "BLOCK_ACTION"
        for rule in candidate.canonical_document["rules"]
        for effect in rule["effects"]
    )
    assert "recovery_hints" not in candidate.canonical_document["planning"]
    assert raw.endswith("\n")
    assert "first instruction" in raw
    assert "first objective" in raw


@pytest.mark.parametrize(
    ("field", "value", "code"),
    (
        ("artifact_type", "other", "UNSUPPORTED_ARTIFACT_TYPE"),
        ("artifact_version", 2, "UNSUPPORTED_ARTIFACT_VERSION"),
        ("schema_version", 2, "UNSUPPORTED_SCENARIO_SCHEMA_VERSION"),
        ("content_type", "unknown", "INVALID_CONTENT_TYPE"),
    ),
)
def test_artifact_discriminators_are_strict(field: str, value: object, code: str) -> None:
    document = _v3_document()
    raw = ScenarioArtifactCodec.from_definition(document, content_type="draft").model_dump(
        mode="json"
    )
    raw[field] = value
    with pytest.raises(ScenarioPortabilityError) as error:
        parse_scenario_artifact(raw)
    assert error.value.code == code


def test_artifact_rejects_unknown_envelope_metadata_mismatch_and_hash_tampering() -> None:
    document = _v3_document()
    raw = ScenarioArtifactCodec.from_definition(document, content_type="draft").model_dump(
        mode="json"
    )

    unknown = deepcopy(raw)
    unknown["unexpected"] = True
    with pytest.raises(ScenarioPortabilityError) as error:
        parse_scenario_artifact(unknown)
    assert error.value.code == "INVALID_ARTIFACT"

    mismatch = deepcopy(raw)
    mismatch["scenario"]["name"] = "Different name"
    with pytest.raises(ScenarioPortabilityError) as error:
        parse_scenario_artifact(mismatch)
    assert error.value.code == "ARTIFACT_METADATA_MISMATCH"

    tampered = deepcopy(raw)
    tampered["content_hash"] = "0" * 64
    with pytest.raises(ScenarioPortabilityError) as error:
        parse_scenario_artifact(tampered)
    assert error.value.code == "ARTIFACT_INTEGRITY_MISMATCH"

    with pytest.raises(ScenarioPortabilityError) as error:
        parse_scenario_artifact(b"{not-json")
    assert error.value.code == "INVALID_ARTIFACT"


def test_unicode_json_roundtrip_and_root_rebind_preserve_internal_keys() -> None:
    source = _v3_document(key="unicode_source", name="临江恢复场景")
    artifact = ScenarioArtifactCodec.from_definition(source, content_type="draft")
    candidate = parse_scenario_artifact(serialize_scenario_artifact(artifact))
    assert candidate.artifact.scenario.name == "临江恢复场景"

    from app.scenarios.portability import rebind_root_identity

    rebound = rebind_root_identity(candidate.definition, target_scenario_key="unicode_target")
    assert rebound["metadata"]["key"] == "unicode_target"
    assert rebound["world"]["key"] == "unicode_target"
    assert rebound["world"]["nodes"] == candidate.canonical_document["world"]["nodes"]
    assert rebound["actions"] == candidate.canonical_document["actions"]


def test_identity_collection_order_is_canonical_but_ordered_paths_change_hash() -> None:
    document = _v3_document()
    artifact = ScenarioArtifactCodec.from_definition(document, content_type="draft")
    raw = artifact.model_dump(mode="json")

    identity_reordered = deepcopy(raw)
    identity_reordered["definition"]["world"]["nodes"] = list(
        reversed(identity_reordered["definition"]["world"]["nodes"])
    )
    identity_candidate = parse_scenario_artifact(identity_reordered)
    assert identity_candidate.content_hash == artifact.content_hash
    assert identity_candidate.canonical_document == artifact.definition

    ordered_reordered = deepcopy(raw)
    ordered_reordered["definition"]["planning"]["instructions"] = [
        "second instruction",
        "first instruction",
    ]
    ordered_reordered["content_hash"] = None
    ordered_candidate = parse_scenario_artifact(ordered_reordered)
    assert ordered_candidate.content_hash != artifact.content_hash
    assert ordered_candidate.canonical_document["planning"]["instructions"] == [
        "second instruction",
        "first instruction",
    ]


def test_input_limits_are_typed_and_shared_at_core_boundary() -> None:
    document = _v3_document()
    raw = ScenarioArtifactCodec.from_definition(document, content_type="draft").model_dump(
        mode="json"
    )

    oversized = deepcopy(raw)
    oversized["unknown"] = "x" * MAX_ARTIFACT_BYTES
    with pytest.raises(ScenarioPortabilityError) as error:
        parse_scenario_artifact(oversized)
    assert error.value.code == "ARTIFACT_INPUT_LIMIT"

    deep = deepcopy(raw)
    node: dict[str, Any] = {}
    deep["unknown"] = node
    for _ in range(MAX_ARTIFACT_DEPTH + 1):
        node["next"] = {}
        node = node["next"]
    with pytest.raises(ScenarioPortabilityError) as error:
        parse_scenario_artifact(deep)
    assert error.value.code == "ARTIFACT_INPUT_LIMIT"

    many = deepcopy(raw)
    many["unknown"] = [0] * (MAX_ARTIFACT_CONTAINER_ENTRIES + 1)
    with pytest.raises(ScenarioPortabilityError) as error:
        parse_scenario_artifact(many)
    assert error.value.code == "ARTIFACT_INPUT_LIMIT"


def test_draft_export_and_import_create_only_scenario_and_draft(db_factory) -> None:  # type: ignore[no-untyped-def]
    source_db = db_factory()
    target_db = db_factory()
    try:
        source = _create_draft(source_db, _v3_document(key="draft_source"))
        portability = ScenarioPortabilityService(source_db)
        artifact = portability.export_draft(source.id)
        assert artifact.content_type == "draft"

        imported = ScenarioPortabilityService(target_db).import_draft_as_new(
            artifact,
            target_scenario_key="draft_imported",
        )
        assert imported.scenario.key == "draft_imported"
        assert imported.draft.revision == 1
        assert imported.published_version is None
        assert imported.draft.definition_document["schema_version"] == 3
        assert _count(target_db, Scenario) == 1
        assert _count(target_db, ScenarioDraft) == 1
        assert _count(target_db, ScenarioVersion) == 0
        assert _count(target_db, GameInstance) == 0
    finally:
        source_db.close()
        target_db.close()


def test_structurally_valid_incomplete_draft_is_exportable_and_importable(
    db_factory,
) -> None:  # type: ignore[no-untyped-def]
    source_db = db_factory()
    target_db = db_factory()
    try:
        document = _v3_document(key="incomplete_source")
        document["rules"] = []
        source = _create_draft(source_db, document)
        artifact = ScenarioPortabilityService(source_db).export_draft(source.id)
        imported = ScenarioPortabilityService(target_db).import_draft_as_new(
            artifact,
            target_scenario_key="incomplete_imported",
        )
        assert imported.draft.validation_status == "UNVALIDATED"
        assert (
            not ScenarioPortabilityService(target_db)
            .validator.validate(imported.draft.definition_document)
            .passed
        )
    finally:
        source_db.close()
        target_db.close()


def test_release_v1_import_roundtrip_uses_exact_authored_v3_and_runtime_load(
    db_factory,
) -> None:  # type: ignore[no-untyped-def]
    source_db = db_factory()
    target_db = db_factory()
    try:
        source_document = _v3_document(key="release_source")
        source, source_version = _create_published(source_db, source_document)
        artifact = ScenarioPortabilityService(source_db).export_release(
            source.id,
            source_version.id,
        )
        imported = ScenarioPortabilityService(target_db).import_release_as_new(artifact)
        assert imported.published_version is not None
        assert imported.published_version.version_number == 1
        assert imported.published_version.schema_version == 3
        assert imported.scenario.key == "release_source"
        assert _count(target_db, Scenario) == 1
        assert _count(target_db, ScenarioDraft) == 1
        assert _count(target_db, ScenarioVersion) == 1
        assert _count(target_db, GameInstance) == 0
        assert imported.published_version.id != source_version.id
        assert imported.published_version.snapshot_document == source_version.snapshot_document
        loaded = ScenarioVersionRepository(target_db).load(imported.published_version.id)
        assert loaded.schema_version == 3
        assert loaded.content_hash == imported.content_hash
    finally:
        source_db.close()
        target_db.close()


def test_v2_version_export_is_rejected_without_portable_conversion(db_factory) -> None:  # type: ignore[no-untyped-def]
    db = db_factory()
    try:
        from app.scenarios.persistence import ScenarioDefinitionRepository

        repository = ScenarioDefinitionRepository(db)
        persisted = repository.persist_initial_draft(GENERIC_TEST)
        db.flush()
        version = (
            ScenarioService(db)
            .publish_draft(
                persisted.id,
                expected_revision=1,
            )
            .version
        )
        db.commit()
        with pytest.raises(ScenarioPortabilityError) as error:
            ScenarioPortabilityService(db).export_release(persisted.id, version.id)
        assert error.value.code == "UNSUPPORTED_SCENARIO_SCHEMA_VERSION"
    finally:
        db.close()


def test_existing_draft_import_service_path_is_removed(db_factory) -> None:  # type: ignore[no-untyped-def]
    db = db_factory()
    try:
        document = _v3_document(key="preview_target")
        scenario = _create_draft(db, document)
        service = ScenarioPortabilityService(db)
        assert not hasattr(service, "preview_existing_draft")
        assert service.scenarios.get_draft(scenario.id).revision == 1
    finally:
        db.close()


def test_import_key_conflict_and_explicit_target_key_are_typed(db_factory) -> None:  # type: ignore[no-untyped-def]
    db = db_factory()
    try:
        document = _v3_document(key="conflict_source")
        source = _create_draft(db, document)
        artifact = ScenarioPortabilityService(db).export_draft(source.id)
        service = ScenarioPortabilityService(db)
        with pytest.raises(ScenarioPortabilityError) as error:
            service.import_draft_as_new(artifact)
        assert error.value.code == "SCENARIO_KEY_CONFLICT"
        imported = service.import_draft_as_new(artifact, target_scenario_key="explicit_target")
        assert imported.scenario.key == "explicit_target"
        assert imported.draft.definition_document["metadata"]["key"] == "explicit_target"
        assert imported.draft.definition_document["world"]["key"] == "explicit_target"
        assert imported.draft.definition_document["world"]["nodes"] == document["world"]["nodes"]
    finally:
        db.close()


def test_new_import_rolls_back_after_create_failure(db_factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    db = db_factory()
    try:
        document = _v3_document(key="rollback_source")
        artifact = ScenarioArtifactCodec.from_definition(document, content_type="draft")
        service = ScenarioPortabilityService(db)
        original = service.scenarios.create_from_authored_document

        def fail_after_create(**kwargs: Any) -> Scenario:
            original(**kwargs)
            raise RuntimeError("injected after Scenario and Draft create")

        monkeypatch.setattr(service.scenarios, "create_from_authored_document", fail_after_create)
        with pytest.raises(ScenarioPortabilityError) as error:
            service.import_draft_as_new(artifact)
        assert error.value.code == "INTERNAL_IMPORT_FAILURE"
        assert _count(db, Scenario) == 0
        assert _count(db, ScenarioDraft) == 0
        assert _count(db, ScenarioVersion) == 0
    finally:
        db.close()


def test_release_validation_failure_rolls_back_without_rows(db_factory) -> None:  # type: ignore[no-untyped-def]
    db = db_factory()
    try:
        document = _v3_document(key="invalid_release")
        document["rules"] = []
        artifact = ScenarioArtifactCodec.from_definition(document, content_type="release")
        with pytest.raises(ScenarioPortabilityError) as error:
            ScenarioPortabilityService(db).import_release_as_new(artifact)
        assert error.value.code == "RELEASE_NOT_PUBLISHABLE"
        assert _count(db, Scenario) == 0
        assert _count(db, ScenarioDraft) == 0
        assert _count(db, ScenarioVersion) == 0
    finally:
        db.close()


def test_truth_knowledge_and_resource_initialization_values_roundtrip() -> None:
    document = _v3_document()
    document["initialization"]["resource_initial_states"] = [
        {"resource_key": "medicine", "value": 0, "reserved_value": 0}
    ]
    artifact = ScenarioArtifactCodec.from_definition(document, content_type="draft")
    candidate = parse_scenario_artifact(serialize_scenario_artifact(artifact))
    expected = canonical_document_payload(document)
    assert candidate.canonical_document["initialization"] == expected["initialization"]
    assert (
        candidate.canonical_document["initialization"]["resource_initial_states"][0]["value"] == 0
    )
    assert (
        candidate.canonical_document["initialization"]["resource_initial_states"][0][
            "reserved_value"
        ]
        == 0
    )


def test_resource_pools_region_knowledge_visibility_and_unknown_values_roundtrip() -> None:
    document = preview_v2_to_v3(LINJIANG_CURRENT_TEST.model_dump(mode="json")).target_document
    artifact = ScenarioArtifactCodec.from_definition(document, content_type="draft")
    candidate = parse_scenario_artifact(serialize_scenario_artifact(artifact))
    initialization = candidate.canonical_document["initialization"]
    expected = canonical_document_payload(document)["initialization"]
    assert initialization["resource_pools"] == expected["resource_pools"]
    assert initialization["region_resource_knowledge"] == expected["region_resource_knowledge"]
    assert any(pool["visibility"] == "HIDDEN" for pool in initialization["resource_pools"])
    assert any(
        state["resource_survey_completed"] is False
        for state in initialization["region_resource_knowledge"]
    )
