from fastapi.testclient import TestClient


def _create_blank(client: TestClient) -> str:
    response = client.post(
        "/api/v1/scenarios",
        json={"mode": "BLANK", "key": "presentation_api_case", "name": "Presentation API Case"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _profile(template: str = "compact") -> dict[str, object]:
    return {
        "schema_version": 1,
        "template": template,
        "global_display": {"density": "COMPACT", "summary_slot": "HEADER"},
        "goal_execution": {"plan_default": "COMPACT", "timeline_density": "STANDARD"},
    }


def test_presentation_profile_current_history_restore_and_optimistic_conflict(
    client: TestClient,
) -> None:
    scenario_id = _create_blank(client)

    current = client.get(f"/api/v1/scenarios/{scenario_id}/presentation")
    assert current.status_code == 200, current.text
    assert current.json()["revision"] == 1
    assert current.json()["profile"]["template"] == "standard"

    saved = client.put(
        f"/api/v1/scenarios/{scenario_id}/presentation",
        json={"expected_revision": 1, "profile": _profile()},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2

    stale = client.put(
        f"/api/v1/scenarios/{scenario_id}/presentation",
        json={"expected_revision": 1, "profile": _profile("detailed")},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "SCENARIO_PRESENTATION_PROFILE_CONFLICT"

    history = client.get(f"/api/v1/scenarios/{scenario_id}/presentation/revisions")
    assert history.status_code == 200
    assert [item["revision"] for item in history.json()["revisions"]] == [2, 1]

    restored = client.post(
        f"/api/v1/scenarios/{scenario_id}/presentation/restore",
        json={"expected_revision": 2, "revision": 1},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["revision"] == 3
    assert restored.json()["profile"]["template"] == "standard"


def test_presentation_profile_rejects_entity_local_and_invalid_enum_payloads(
    client: TestClient,
) -> None:
    scenario_id = _create_blank(client)
    response = client.put(
        f"/api/v1/scenarios/{scenario_id}/presentation",
        json={
            "expected_revision": 1,
            "profile": {
                "schema_version": 1,
                "template": "standard",
                "entity_overrides": {"patient_one": {"default_open": "FULL"}},
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SCENARIO_PRESENTATION_PROFILE_INVALID"

    invalid_enum = client.put(
        f"/api/v1/scenarios/{scenario_id}/presentation",
        json={
            "expected_revision": 1,
            "profile": {"schema_version": 1, "template": "freeform"},
        },
    )
    assert invalid_enum.status_code == 422
    assert invalid_enum.json()["error"]["code"] == "SCENARIO_PRESENTATION_PROFILE_INVALID"
