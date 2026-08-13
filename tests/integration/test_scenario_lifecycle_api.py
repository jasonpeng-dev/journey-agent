from copy import deepcopy
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import Scenario, ScenarioVersion


def _create_medical(client: TestClient, *, key: str = "clinic_one") -> dict[str, object]:
    response = client.post(
        "/api/v1/scenarios",
        json={
            "mode": "EXAMPLE",
            "key": key,
            "name": "Clinic One",
            "example_key": "medical_emergency",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_blank_draft_is_editable_but_cannot_publish(client: TestClient) -> None:
    created = client.post(
        "/api/v1/scenarios",
        json={"mode": "BLANK", "key": "blank_case", "name": "Blank Case"},
    )
    assert created.status_code == 201
    scenario_id = created.json()["id"]

    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft")
    assert draft.status_code == 200
    assert draft.json()["revision"] == 1
    assert draft.json()["definition_document"]["world"]["nodes"] == []

    incomplete = {"metadata": {"key": "blank_case", "name": "Still Draft"}}
    saved = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={"expected_revision": 1, "definition_document": incomplete},
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == 2

    stale = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={"expected_revision": 1, "definition_document": incomplete},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "SCENARIO_DRAFT_CONFLICT"

    validation = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/validate",
        json={"expected_revision": 2},
    )
    assert validation.status_code == 200
    assert validation.json()["publish_ready"] is False
    assert validation.json()["issues"][0]["severity"] == "ERROR"

    publish = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/publish",
        json={"expected_revision": 2},
    )
    assert publish.status_code == 409
    assert publish.json()["error"]["code"] == "SCENARIO_DRAFT_INVALID"


def test_publish_versions_restore_clone_and_archive_are_isolated(
    client: TestClient,
    session: Session,
) -> None:
    created = _create_medical(client)
    scenario_id = created["id"]
    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()

    validation = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/validate",
        json={"expected_revision": draft["revision"]},
    )
    assert validation.status_code == 200
    assert validation.json()["publish_ready"] is True
    content_hash = validation.json()["content_hash"]

    published_v1 = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/publish",
        json={"expected_revision": 1, "expected_content_hash": content_hash},
    )
    assert published_v1.status_code == 200, published_v1.text
    version_one = published_v1.json()["version"]
    assert version_one["version_number"] == 1

    no_change = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/publish",
        json={"expected_revision": 1},
    )
    assert no_change.status_code == 409
    assert no_change.json()["error"]["code"] == "SCENARIO_PUBLISH_NO_CHANGES"

    changed_document = deepcopy(draft["definition_document"])
    changed_document["metadata"]["name"] = "Clinic Two"
    changed_document["world"]["name"] = "Clinic Two"
    saved = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={"expected_revision": 1, "definition_document": changed_document},
    )
    assert saved.status_code == 200

    published_v2 = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/publish",
        json={"expected_revision": 2},
    )
    assert published_v2.status_code == 200
    version_two = published_v2.json()["version"]
    assert version_two["version_number"] == 2

    old_version = client.get(f"/api/v1/scenarios/{scenario_id}/versions/{version_one['id']}").json()
    assert old_version["definition_document"]["metadata"]["name"] == "Clinic One"

    versions = client.get(f"/api/v1/scenarios/{scenario_id}/versions")
    assert [item["version_number"] for item in versions.json()] == [2, 1]

    restored = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/restore",
        json={"expected_revision": 2, "version_id": version_one["id"]},
    )
    assert restored.status_code == 200
    assert restored.json()["revision"] == 3
    assert restored.json()["base_scenario_version_id"] == version_one["id"]
    assert restored.json()["definition_document"]["metadata"]["name"] == "Clinic One"

    clone = client.post(
        "/api/v1/scenarios",
        json={
            "mode": "CLONE_VERSION",
            "key": "clinic_clone",
            "name": "Clinic Clone",
            "source_version_id": version_one["id"],
        },
    )
    assert clone.status_code == 201
    clone_draft = client.get(f"/api/v1/scenarios/{clone.json()['id']}/draft").json()
    assert clone_draft["definition_document"]["metadata"]["key"] == "clinic_clone"
    assert clone_draft["base_scenario_version_id"] == version_one["id"]

    archived = client.post(f"/api/v1/scenarios/{scenario_id}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    blocked = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={
            "expected_revision": 3,
            "definition_document": restored.json()["definition_document"],
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SCENARIO_ARCHIVED"

    assert (
        session.scalar(select(ScenarioVersion).where(ScenarioVersion.id == UUID(version_one["id"])))
        is not None
    )


def test_scenario_library_detail_identity_and_not_found_contract(
    client: TestClient,
    session: Session,
) -> None:
    created = _create_medical(client, key="library_case")
    scenario_id = created["id"]

    listing = client.get("/api/v1/scenarios")
    assert listing.status_code == 200
    assert any(item["id"] == scenario_id for item in listing.json())

    detail = client.get(f"/api/v1/scenarios/{scenario_id}")
    assert detail.status_code == 200
    assert detail.json()["key"] == "library_case"

    draft = client.get(f"/api/v1/scenarios/{scenario_id}/draft").json()
    document = draft["definition_document"]
    document["metadata"]["key"] = "renamed_identity"
    immutable = client.put(
        f"/api/v1/scenarios/{scenario_id}/draft",
        json={"expected_revision": 1, "definition_document": document},
    )
    assert immutable.status_code == 409
    assert immutable.json()["error"]["code"] == "SCENARIO_KEY_IMMUTABLE"

    missing = client.get(f"/api/v1/scenarios/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SCENARIO_NOT_FOUND"

    assert session.scalar(select(Scenario).where(Scenario.key == "library_case")) is not None
