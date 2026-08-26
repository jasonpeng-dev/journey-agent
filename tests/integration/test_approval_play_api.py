from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import ActionDecisionRequest, AgentTask
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.scenarios import ScenarioService
from tests.unit.test_scenario_definition_v2 import _contract_scenario_document


def _approval_version(session: Session) -> str:
    document = deepcopy(_contract_scenario_document())
    parameter = document["actions"][0]["parameters"][0]
    parameter["required"] = False
    parameter["default"] = 2
    document["actions"][0]["authority_policy"] = {
        "autonomous_limits": [{"parameter_key": "dosage", "maximum": 1}]
    }
    from app.domain.scenario_v2 import ScenarioDefinitionV2

    definition = ScenarioDefinitionV2.model_validate(document)
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    session.commit()
    return str(version.id)


def _start_approval_game(client: TestClient, version_id: str) -> tuple[str, dict[str, Any]]:
    game = client.post(
        "/api/v1/games",
        json={"scenario_version_id": version_id, "idempotency_key": str(uuid4())},
    ).json()
    goal = client.post(
        f"/api/v1/games/{game['id']}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    )
    assert goal.status_code == 200, goal.text
    briefing = goal.json()["task"]
    assert briefing["execution_phase"] == "AWAITING_PLAN_START"
    planning = client.post(
        f"/api/v1/games/{game['id']}/play/start-planning",
        json={"expected_pacing_version": briefing["pacing_version"]},
    )
    assert planning.status_code == 200, planning.text
    briefing = planning.json()["current_task"]
    assert briefing["execution_phase"] == "AWAITING_ACTION_ACK"
    state = client.post(
        f"/api/v1/games/{game['id']}/play/acknowledge-action",
        json={"expected_pacing_version": briefing["pacing_version"]},
    ).json()
    assert state["current_task"]["status"] == "NEEDS_PLAYER_INPUT"
    assert state["current_task"]["execution_phase"] == "APPROVAL_REQUIRED"
    assert state["pending_approval_id"]
    return str(game["id"]), state


def test_approve_executes_one_cycle_and_returns_a_debrief(
    client: TestClient, session: Session
) -> None:
    game_id, state = _start_approval_game(client, _approval_version(session))
    response = client.post(
        f"/api/v1/games/{game_id}/approvals/{state['pending_approval_id']}/approve",
        json={"expected_task_version": state["current_task"]["version"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["current_task"]["execution_phase"] in (
        "AWAITING_DEBRIEF_ACK",
        "COMPLETED",
    )
    assert body["pending_approval_id"] is None
    decisions = session.scalars(
        select(ActionDecisionRequest).where(ActionDecisionRequest.game_instance_id == UUID(game_id))
    ).all()
    assert [item.status.value for item in decisions] == ["CONSUMED"]


def test_reject_persists_exact_constraint_and_stops_without_approval_loop(
    client: TestClient, session: Session
) -> None:
    game_id, state = _start_approval_game(client, _approval_version(session))
    response = client.post(
        f"/api/v1/games/{game_id}/approvals/{state['pending_approval_id']}/reject",
        json={"expected_task_version": state["current_task"]["version"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["current_task"]["status"] == "BLOCKED_BY_PLAYER_DECISION"
    assert body["pending_approval_id"] is None
    task = session.scalar(select(AgentTask).where(AgentTask.game_instance_id == UUID(game_id)))
    assert task is not None
    assert len(task.rejected_proposal_signatures) == 1
    decisions = session.scalars(
        select(ActionDecisionRequest).where(ActionDecisionRequest.task_id == task.id)
    ).all()
    assert [item.status.value for item in decisions] == ["REJECTED"]
    assert task.replan_count == 0
