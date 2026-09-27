"""Phase 2 HTTP and CLI adapter contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.cli import EXIT_ARTIFACT, EXIT_CONFLICT, EXIT_SUCCESS, main
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import GameInstance, Scenario, ScenarioVersion
from app.infrastructure.db.session import configure_sqlite_foreign_keys
from app.main import app
from app.scenarios.migration import preview_v2_to_v3
from app.scenarios.portability import ScenarioArtifactCodec, serialize_scenario_artifact
from app.services.scenario_portability import ScenarioPortabilityService
from tests.scenario_fixtures import GENERIC_TEST


@pytest.fixture
def phase2_factory() -> Any:
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


def _v3_document(key: str) -> dict[str, Any]:
    document = preview_v2_to_v3(GENERIC_TEST.model_dump(mode="json")).target_document
    document["metadata"]["key"] = key
    document["metadata"]["name"] = key.replace("_", " ").title()
    document["world"]["key"] = key
    document["world"]["name"] = document["metadata"]["name"]
    return document


def _seed_draft(factory: Any, key: str = "phase2_source") -> tuple[UUID, Any]:
    with factory() as db:
        service = ScenarioPortabilityService(db)
        scenario = service.scenarios.create_from_authored_document(
            key=key,
            name=key,
            definition_document=_v3_document(key),
        )
        db.commit()
        return scenario.id, service.export_draft(scenario.id)


def _seed_published(factory: Any, key: str = "phase2_release") -> tuple[UUID, Any]:
    with factory() as db:
        service = ScenarioPortabilityService(db)
        scenario = service.scenarios.create_from_authored_document(
            key=key,
            name=key,
            definition_document=_v3_document(key),
        )
        draft = service.scenarios.get_draft(scenario.id)
        validation = service.scenarios.validate_draft(scenario.id, expected_revision=draft.revision)
        assert validation.passed
        published = service.scenarios.publish_draft(
            scenario.id,
            expected_revision=draft.revision,
            expected_content_hash=draft.content_hash,
        )
        db.commit()
        return scenario.id, service.export_release(scenario.id, published.version.id)


@pytest.fixture
def phase2_client(phase2_factory: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    def override() -> Any:
        with phase2_factory() as db:
            yield db

    from app.infrastructure.db.session import get_db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_http_new_preview_is_typed_and_does_not_mutate(
    phase2_factory: Any,
    phase2_client: TestClient,
) -> None:
    source_id, artifact = _seed_draft(phase2_factory)
    del source_id
    before = phase2_factory()
    try:
        before_count = before.scalar(select(func.count()).select_from(Scenario))
    finally:
        before.close()
    response = phase2_client.post(
        "/api/v1/scenarios/artifacts/preview",
        json={
            "artifact": artifact.model_dump(mode="json"),
            "target_scenario_key": "phase2_preview_target",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["artifact"]["content_type"] == "draft"
    assert payload["candidate_target_key"] == "phase2_preview_target"
    assert payload["key_conflict"] is False
    assert payload["what_import_will_create"]["records"] == ["Scenario", "Draft"]
    assert payload["what_import_will_create"]["game_created"] is False
    after = phase2_factory()
    try:
        assert after.scalar(select(func.count()).select_from(Scenario)) == before_count
    finally:
        after.close()


def test_http_new_import_draft_and_release_have_expected_lifecycle(
    phase2_factory: Any,
    phase2_client: TestClient,
) -> None:
    _, draft_artifact = _seed_draft(phase2_factory, "phase2_draft_source")
    draft_response = phase2_client.post(
        "/api/v1/scenarios/artifacts/import",
        json={
            "artifact": draft_artifact.model_dump(mode="json"),
            "target_scenario_key": "phase2_draft_target",
        },
    )
    assert draft_response.status_code == 201, draft_response.text
    draft_payload = draft_response.json()
    assert draft_payload["scenario"]["key"] == "phase2_draft_target"
    assert draft_payload["published_version"] is None
    assert draft_payload["game_created"] is False

    _, release_artifact = _seed_published(phase2_factory)
    release_response = phase2_client.post(
        "/api/v1/scenarios/artifacts/import",
        json={
            "artifact": release_artifact.model_dump(mode="json"),
            "target_scenario_key": "phase2_release_target",
        },
    )
    assert release_response.status_code == 201, release_response.text
    release_payload = release_response.json()
    assert release_payload["published_version"]["version_number"] == 1
    assert release_payload["published_version"]["schema_version"] == 3

    with phase2_factory() as db:
        target = db.scalar(select(Scenario).where(Scenario.key == "phase2_release_target"))
        assert target is not None
        assert db.scalar(select(func.count()).select_from(GameInstance)) == 0


def test_http_release_preview_reports_publish_v1_without_mutation(
    phase2_factory: Any,
    phase2_client: TestClient,
) -> None:
    _, artifact = _seed_published(phase2_factory, "phase2_release_preview_source")
    with phase2_factory() as db:
        before = db.scalar(select(func.count()).select_from(Scenario))
    response = phase2_client.post(
        "/api/v1/scenarios/artifacts/preview",
        json={
            "artifact": artifact.model_dump(mode="json"),
            "target_scenario_key": "phase2_release_preview_target",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["artifact"]["content_type"] == "release"
    assert payload["what_import_will_create"]["records"] == [
        "Scenario",
        "Draft",
        "Published Version v1",
    ]
    assert payload["what_import_will_create"]["published_version_number"] == 1
    with phase2_factory() as db:
        assert db.scalar(select(func.count()).select_from(Scenario)) == before


def test_http_existing_draft_preview_endpoint_is_removed(
    phase2_factory: Any,
    phase2_client: TestClient,
) -> None:
    scenario_id, _ = _seed_draft(phase2_factory, "phase2_existing_target")
    response = phase2_client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/artifacts/preview",
        json={"artifact": {}},
    )
    assert response.status_code == 405


def test_http_exports_are_deterministic_downloads_with_safe_filenames(
    phase2_factory: Any,
    phase2_client: TestClient,
) -> None:
    draft_id, draft_artifact = _seed_draft(phase2_factory, "phase2_download")
    draft_response = phase2_client.get(f"/api/v1/scenarios/{draft_id}/draft/artifact")
    assert draft_response.status_code == 200, draft_response.text
    assert (
        'filename="phase2_download.draft.scenario.json"'
        in draft_response.headers["content-disposition"]
    )
    assert draft_response.content.decode("utf-8") == serialize_scenario_artifact(draft_artifact)

    release_id, release_artifact = _seed_published(phase2_factory, "phase2_download_release")
    with phase2_factory() as db:
        scenario = db.get(Scenario, release_id)
        assert scenario is not None
        version = db.scalar(
            select(ScenarioVersion).where(ScenarioVersion.scenario_id == release_id)
        )
        assert version is not None
        version_id = version.id
    release_response = phase2_client.get(
        f"/api/v1/scenarios/{release_id}/versions/{version_id}/artifact"
    )
    assert release_response.status_code == 200, release_response.text
    assert (
        'filename="phase2_download_release.v1.scenario.json"'
        in release_response.headers["content-disposition"]
    )
    assert release_response.content.decode("utf-8") == serialize_scenario_artifact(release_artifact)


def test_http_latest_export_is_exact_current_published_version_without_fallback(
    phase2_factory: Any,
    phase2_client: TestClient,
) -> None:
    draft_id, _ = _seed_draft(phase2_factory, "phase2_latest_without_release")
    unavailable = phase2_client.get(f"/api/v1/scenarios/{draft_id}/latest/artifact")
    assert unavailable.status_code == 409, unavailable.text
    assert unavailable.json()["error"]["code"] == "NO_PUBLISHED_VERSION"

    release_id, release_artifact = _seed_published(phase2_factory, "phase2_latest_release")
    latest = phase2_client.get(f"/api/v1/scenarios/{release_id}/latest/artifact")
    assert latest.status_code == 200, latest.text
    assert latest.content.decode("utf-8") == serialize_scenario_artifact(release_artifact)


def test_http_typed_errors_cover_hash_conflict_invalid_key_and_stale_revision(
    phase2_factory: Any,
    phase2_client: TestClient,
) -> None:
    scenario_id, artifact = _seed_draft(phase2_factory, "phase2_error_source")
    tampered = artifact.model_dump(mode="json")
    tampered["content_hash"] = "0" * 64
    bad_hash = phase2_client.post(
        "/api/v1/scenarios/artifacts/preview",
        json={"artifact": tampered},
    )
    assert bad_hash.status_code == 422
    assert bad_hash.json()["error"]["code"] == "ARTIFACT_INTEGRITY_MISMATCH"

    invalid_key = phase2_client.post(
        "/api/v1/scenarios/artifacts/preview",
        json={"artifact": artifact.model_dump(mode="json"), "target_scenario_key": "../bad"},
    )
    assert invalid_key.status_code == 422
    assert invalid_key.json()["error"]["code"] == "INVALID_TARGET_SCENARIO_KEY"

    conflict = phase2_client.post(
        "/api/v1/scenarios/artifacts/import",
        json={
            "artifact": artifact.model_dump(mode="json"),
            "target_scenario_key": "phase2_error_source",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "SCENARIO_KEY_CONFLICT"

    stale = phase2_client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/artifacts/preview",
        json={"artifact": artifact.model_dump(mode="json")},
    )
    assert stale.status_code == 405


def test_http_v2_release_export_is_rejected_with_typed_error(
    phase2_factory: Any,
    phase2_client: TestClient,
) -> None:
    with phase2_factory() as db:
        service = ScenarioPortabilityService(db)
        scenario = service.scenarios.create_from_definition(
            key="phase2_v2_source",
            name="phase2_v2_source",
            definition=GENERIC_TEST,
        )
        draft = service.scenarios.get_draft(scenario.id)
        validation = service.scenarios.validate_draft(scenario.id, expected_revision=draft.revision)
        assert validation.passed
        published = service.scenarios.publish_draft(
            scenario.id,
            expected_revision=draft.revision,
            expected_content_hash=draft.content_hash,
        )
        db.commit()
        version_id = published.version.id
        scenario_id = scenario.id
    response = phase2_client.get(f"/api/v1/scenarios/{scenario_id}/versions/{version_id}/artifact")
    assert response.status_code == 422, response.text
    payload = response.json()
    assert payload["error"]["code"] == "UNSUPPORTED_SCENARIO_SCHEMA_VERSION"
    assert set(payload["error"]) == {"code", "message", "details", "request_id"}


def test_http_input_limits_are_enforced_before_import(
    phase2_client: TestClient,
) -> None:
    oversized = {
        "artifact_type": "journey_scenario",
        "artifact_version": 1,
        "content_type": "draft",
        "schema_version": 3,
        "scenario": {"key": "oversized", "name": "Oversized", "description": ""},
        "definition": {
            "metadata": {"key": "oversized", "name": "Oversized", "description": "x" * 20_000}
        },
    }
    response = phase2_client.post(
        "/api/v1/scenarios/artifacts/preview",
        json={"artifact": oversized},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "ARTIFACT_INPUT_LIMIT"


def test_http_and_cli_exports_have_identical_artifact_bytes(
    phase2_factory: Any,
    phase2_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario_id, artifact = _seed_draft(phase2_factory, "phase2_parity")
    http_response = phase2_client.get(f"/api/v1/scenarios/{scenario_id}/draft/artifact")
    assert http_response.status_code == 200, http_response.text

    from app.infrastructure.db import session as db_session

    monkeypatch.setattr(db_session, "SessionLocal", phase2_factory)
    output_path = tmp_path / "parity.scenario.json"
    assert (
        main(
            [
                "scenario",
                "export",
                "phase2_parity",
                "--draft",
                "--output",
                str(output_path),
            ]
        )
        == EXIT_SUCCESS
    )
    capsys.readouterr()
    assert output_path.read_bytes() == http_response.content
    assert output_path.read_text(encoding="utf-8") == serialize_scenario_artifact(artifact)


def test_cli_validate_import_and_draft_export_use_same_service(
    phase2_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.infrastructure.db import session as db_session

    _, artifact = _seed_draft(phase2_factory, "phase2_cli_source")
    input_path = tmp_path / "source.scenario.json"
    input_path.write_text(serialize_scenario_artifact(artifact), encoding="utf-8")
    monkeypatch.setattr(db_session, "SessionLocal", phase2_factory)

    assert main(["scenario", "validate-file", str(input_path)]) == EXIT_SUCCESS
    assert capsys.readouterr().out

    imported = main(["scenario", "import", str(input_path), "--target-key", "phase2_cli_target"])
    assert imported == EXIT_SUCCESS
    import_payload = capsys.readouterr().out
    assert '"phase2_cli_target"' in import_payload

    output_path = tmp_path / "exported.scenario.json"
    exported = main(
        [
            "scenario",
            "export",
            "phase2_cli_target",
            "--draft",
            "--output",
            str(output_path),
        ]
    )
    assert exported == EXIT_SUCCESS
    assert output_path.exists()
    assert ScenarioArtifactCodec.parse(output_path.read_bytes()).scenario_key == "phase2_cli_target"

    monkeypatch.chdir(tmp_path)
    default_export = main(["scenario", "export", "phase2_cli_target", "--draft"])
    assert default_export == EXIT_SUCCESS
    assert (tmp_path / "scenarios" / "exports" / "phase2_cli_target.draft.scenario.json").exists()
    capsys.readouterr()

    denied = main(
        [
            "scenario",
            "export",
            "phase2_cli_target",
            "--draft",
            "--output",
            str(output_path),
        ]
    )
    assert denied == EXIT_ARTIFACT
    assert "CLI_OUTPUT_EXISTS" in capsys.readouterr().err

    overwritten = main(
        [
            "scenario",
            "export",
            "phase2_cli_target",
            "--draft",
            "--output",
            str(output_path),
            "--force",
        ]
    )
    assert overwritten == EXIT_SUCCESS


def test_cli_release_export_import_and_version_number(
    phase2_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.infrastructure.db import session as db_session

    _, artifact = _seed_published(phase2_factory, "phase2_cli_release_source")
    input_path = tmp_path / "release.scenario.json"
    input_path.write_text(serialize_scenario_artifact(artifact), encoding="utf-8")
    monkeypatch.setattr(db_session, "SessionLocal", phase2_factory)

    imported = main(
        ["scenario", "import", str(input_path), "--target-key", "phase2_cli_release_target"]
    )
    assert imported == EXIT_SUCCESS
    assert '"version_number": 1' in capsys.readouterr().out

    output_path = tmp_path / "release_export.scenario.json"
    exported = main(
        [
            "scenario",
            "export",
            "phase2_cli_release_source",
            "--version",
            "1",
            "--output",
            str(output_path),
        ]
    )
    assert exported == EXIT_SUCCESS
    parsed = ScenarioArtifactCodec.parse(output_path.read_bytes())
    assert parsed.content_type == "release"
    assert parsed.scenario_key == "phase2_cli_release_source"


def test_cli_conflict_is_explicit_and_does_not_overwrite(
    phase2_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.infrastructure.db import session as db_session

    _, artifact = _seed_draft(phase2_factory, "phase2_cli_conflict_source")
    input_path = tmp_path / "conflict.scenario.json"
    input_path.write_text(serialize_scenario_artifact(artifact), encoding="utf-8")
    monkeypatch.setattr(db_session, "SessionLocal", phase2_factory)
    assert (
        main(
            [
                "scenario",
                "import",
                str(input_path),
                "--target-key",
                "phase2_cli_conflict_source",
            ]
        )
        == EXIT_CONFLICT
    )
    assert "SCENARIO_KEY_CONFLICT" in capsys.readouterr().err


def test_cli_invalid_and_unsupported_files_have_nonzero_typed_exit_codes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{", encoding="utf-8")
    assert main(["scenario", "validate-file", str(invalid_path)]) == EXIT_ARTIFACT
    assert "INVALID_ARTIFACT" in capsys.readouterr().err

    unsupported_path = tmp_path / "unsupported.json"
    unsupported_path.write_text(
        '{"artifact_type":"other","artifact_version":1,"content_type":"draft",'
        '"schema_version":3,"scenario":{"key":"unsupported","name":"Unsupported",'
        '"description":""},"definition":{}}',
        encoding="utf-8",
    )
    assert main(["scenario", "validate-file", str(unsupported_path)]) == EXIT_ARTIFACT
    assert "UNSUPPORTED_ARTIFACT_TYPE" in capsys.readouterr().err
