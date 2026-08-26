from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import GameInstance
from tests.scenario_fixtures import GENERIC_TEST, create_test_scenario


def _create_example(client: TestClient, *, example_key: str, suffix: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/scenarios",
        json={
            "mode": "EXAMPLE",
            "key": f"sandbox_{suffix}_{uuid4().hex[:8]}",
            "name": f"Sandbox {suffix}",
            "example_key": example_key,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_incomplete_draft_returns_diagnostics_without_starting_runtime(
    client: TestClient, session: Session
) -> None:
    created = client.post(
        "/api/v1/scenarios",
        json={"mode": "BLANK", "key": "sandbox_blank", "name": "Sandbox Blank"},
    ).json()
    scenario_id = created["id"]
    games_before = session.scalar(select(func.count()).select_from(GameInstance))

    response = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/sandbox",
        json={"expected_revision": 1, "goal": "test this draft"},
    )

    assert response.status_code == 200
    assert response.json()["sandbox_started"] is False
    assert response.json()["issues"]
    assert session.scalar(select(func.count()).select_from(GameInstance)) == games_before


def test_medical_uses_same_disposable_sandbox_and_formal_game_rejects_draft_identity(
    client: TestClient, session: Session
) -> None:
    created = create_test_scenario(
        session,
        GENERIC_TEST,
        key=f"sandbox_medical_{uuid4().hex[:8]}",
        name="Sandbox Generic Contract",
    )
    scenario_id = str(created.id)
    games_before = session.scalar(select(func.count()).select_from(GameInstance))
    response = client.post(
        f"/api/v1/scenarios/{scenario_id}/draft/sandbox",
        json={"expected_revision": 1, "goal": "stabilize the patient"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["sandbox_started"] is True
    assert response.json()["goal_status"] == "SUCCEEDED"
    assert session.scalar(select(func.count()).select_from(GameInstance)) == games_before

    formal = client.post(
        "/api/v1/games",
        json={"scenario_version_id": scenario_id, "idempotency_key": str(uuid4())},
    )
    assert formal.status_code == 404
    assert formal.json()["error"]["code"] == "SCENARIO_VERSION_NOT_FOUND"
    assert session.get(GameInstance, UUID(scenario_id)) is None
