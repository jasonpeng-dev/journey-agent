from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from app.infrastructure.db.models import AgentTask
from app.services.play import PlayOrchestrator


def parse_and_confirm_api(
    client: TestClient,
    game_id: str,
    goal: str,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    parsed = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={
            "goal": goal,
            "idempotency_key": idempotency_key or str(uuid4()),
        },
    )
    assert parsed.status_code == 200, parsed.text
    payload = parsed.json()
    assert payload["status"] == "READY_FOR_CONFIRMATION"
    assert "task" not in payload
    confirmed = client.post(
        f"/api/v1/games/{game_id}/goal-drafts/{payload['draft_id']}/confirm"
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def submit_and_confirm(
    orchestrator: PlayOrchestrator,
    goal: str,
    *,
    idempotency_key: str | None = None,
) -> AgentTask:
    submitted = orchestrator.submit_goal(
        goal,
        idempotency_key=idempotency_key or str(uuid4()),
    )
    assert submitted.draft is not None
    return orchestrator.confirm_goal_draft(submitted.draft.id)
