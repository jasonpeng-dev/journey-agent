from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.enums import NodeStatus
from app.infrastructure.db.models import GameInstanceNodeState
from app.scenarios.builtin import require_builtin_v2_version
from tests.scenario_fixtures import GENERIC_TEST


def _new_game(client: TestClient, session: Session) -> str:
    version = require_builtin_v2_version(session, GENERIC_TEST)
    session.commit()
    response = client.post(
        "/api/v1/games",
        json={"scenario_version_id": str(version.id), "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _start_planning(client: TestClient, game_id: str, task: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/games/{game_id}/play/start-planning",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_task"] is not None
    return response.json()["current_task"]


def _ack_action(client: TestClient, game_id: str, task: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/games/{game_id}/play/acknowledge-action",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_task"] is not None
    return response.json()["current_task"]


def _ack_debrief(client: TestClient, game_id: str, task: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/games/{game_id}/play/acknowledge-debrief",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_task"] is not None
    return response.json()["current_task"]


def _drive_task(
    client: TestClient, game_id: str, task: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    action_results: list[dict[str, Any]] = []
    for _ in range(30):
        if task["execution_phase"] in ("COMPLETED", "BLOCKED", "ABORTED"):
            return task, action_results
        if task["execution_phase"] == "AWAITING_PLAN_START":
            task = _start_planning(client, game_id, task)
        elif task["execution_phase"] == "AWAITING_ACTION_ACK":
            task = _ack_action(client, game_id, task)
            action_results.append(task)
        elif task["execution_phase"] == "AWAITING_REPLAN_ACK":
            response = client.post(
                f"/api/v1/games/{game_id}/play/replan",
                json={"expected_pacing_version": task["pacing_version"]},
            )
            assert response.status_code == 200, response.text
            task = response.json()["current_task"]
        elif task["execution_phase"] == "AWAITING_DEBRIEF_ACK":
            task = _ack_debrief(client, game_id, task)
        else:
            raise AssertionError(f"Unexpected phase: {task['execution_phase']}")
    raise AssertionError("Task did not stop within the test safety bound")


def test_generic_play_stops_at_briefing_and_ack_runs_one_action_cycle(
    client: TestClient, session: Session
) -> None:
    game_id = _new_game(client, session)
    goal = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    )
    assert goal.status_code == 200, goal.text
    task = goal.json()["task"]
    assert task["status"] == "ACTIVE"
    assert task["execution_phase"] == "AWAITING_PLAN_START"
    assert task["briefing"] is None
    task = _start_planning(client, game_id, task)
    assert task["execution_phase"] == "AWAITING_ACTION_ACK"
    assert task["briefing"] is not None
    assert task["plan_history"]
    after_action = _ack_action(client, game_id, task)
    assert after_action["execution_phase"] in {
        "AWAITING_DEBRIEF_ACK",
        "AWAITING_ACTION_ACK",
        "COMPLETED",
    }


def test_generic_scenario_uses_same_stepwise_play_and_game_remains_active(
    client: TestClient, session: Session
) -> None:
    game_id = _new_game(client, session)
    first = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    ).json()["task"]
    completed, action_results = _drive_task(client, game_id, first)

    assert completed["status"] == "COMPLETED"
    assert len(action_results) == 2
    assert all(stage["status"] == "COMPLETED" for stage in completed["roadmap"]["stages"])
    assert completed["plan_history"][-1]["status"] == "COMPLETED"
    game = client.get(f"/api/v1/games/{game_id}").json()
    assert game["status"] == "ACTIVE"
    assert game["active_task_id"] is None


def test_play_state_exposes_task_history_and_scopes_selected_task(
    client: TestClient, session: Session
) -> None:
    game_id = _new_game(client, session)
    first = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "diagnose the patient", "idempotency_key": str(uuid4())},
    ).json()["task"]
    first, _ = _drive_task(client, game_id, first)
    assert first["status"] == "COMPLETED"

    second = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    ).json()["task"]
    latest = client.get(f"/api/v1/games/{game_id}/play")
    assert latest.status_code == 200
    latest_state = latest.json()
    assert [item["id"] for item in latest_state["task_history"]] == [first["id"], second["id"]]
    objective_names = {item.key: item.name for item in GENERIC_TEST.objectives}
    assert latest_state["task_history"][0]["objective_names"] == [
        objective_names["diagnose_patient"]
    ]
    assert latest_state["task_history"][1]["objective_names"] == [
        objective_names["stabilize_patient"]
    ]
    assert latest_state["current_task"]["id"] == second["id"]
    assert latest_state["game"]["active_task_id"] == second["id"]

    historical = client.get(f"/api/v1/games/{game_id}/play?task_id={first['id']}")
    assert historical.status_code == 200
    historical_state = historical.json()
    assert historical_state["current_task"]["id"] == first["id"]
    assert historical_state["current_task"]["timeline"]
    assert historical_state["game"]["active_task_id"] == second["id"]
    assert historical_state["known_facts"] == latest_state["known_facts"]

    missing = client.get(f"/api/v1/games/{game_id}/play?task_id={uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_pacing_version_and_phase_are_server_enforced(client: TestClient, session: Session) -> None:
    game_id = _new_game(client, session)
    task = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    ).json()["task"]
    wrong_phase = client.post(
        f"/api/v1/games/{game_id}/play/acknowledge-debrief",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert wrong_phase.status_code == 409
    assert wrong_phase.json()["error"]["code"] == "PLAYER_PACING_PHASE_INVALID"
    task = _start_planning(client, game_id, task)
    advanced = _ack_action(client, game_id, task)
    stale = client.post(
        f"/api/v1/games/{game_id}/play/acknowledge-debrief",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "PLAYER_PACING_CONFLICT"
    assert advanced["pacing_version"] > task["pacing_version"]


def test_truly_unreachable_generic_goal_stops_reliably(
    client: TestClient, session: Session
) -> None:
    game_id = _new_game(client, session)
    game_uuid = UUID(game_id)
    patient = session.get(GameInstanceNodeState, (game_uuid, "patient_one"))
    assert patient is not None
    patient.status = NodeStatus.LOCKED
    session.flush()
    task = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    ).json()["task"]
    assert task["status"] == "ACTIVE"
    task = _start_planning(client, game_id, task)
    assert task["status"] == "UNREACHABLE_IN_CURRENT_STATE"
    assert task["execution_phase"] == "BLOCKED"


def test_unsupported_goal_does_not_create_a_task(client: TestClient, session: Session) -> None:
    game_id = _new_game(client, session)
    response = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "invent warp travel", "idempotency_key": str(uuid4())},
    )
    assert response.json()["status"] == "UNSUPPORTED"
    no_task = client.post(
        f"/api/v1/games/{game_id}/play/acknowledge-action",
        json={"expected_pacing_version": 1},
    )
    assert no_task.status_code == 409
    assert no_task.json()["error"]["code"] == "AGENT_TASK_NOT_ACTIVE"
    missing_task = client.post(f"/api/v1/games/{game_id}/tasks/{uuid4()}/abandon")
    assert missing_task.status_code in (404, 409)
    game = client.get(f"/api/v1/games/{game_id}").json()
    archived = client.post(
        f"/api/v1/games/{game_id}/archive",
        json={"expected_runtime_revision": game["runtime_revision"]},
    )
    assert archived.status_code == 200
    archived_again = client.post(
        f"/api/v1/games/{game_id}/archive",
        json={"expected_runtime_revision": archived.json()["runtime_revision"]},
    )
    assert archived_again.status_code == 409
    assert archived_again.json()["error"]["code"] == "GAME_INSTANCE_TRANSITION_INVALID"
    archived_goal = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "diagnose the patient", "idempotency_key": str(uuid4())},
    )
    assert archived_goal.status_code == 409
    assert archived_goal.json()["error"]["code"] == "GAME_INSTANCE_READ_ONLY"


def test_play_resources_reject_unknown_identifiers(client: TestClient) -> None:
    missing = uuid4()
    assert client.get(f"/api/v1/games/{missing}/play").status_code == 404
    invalid_version = client.post(
        "/api/v1/games",
        json={"scenario_version_id": str(uuid4()), "idempotency_key": str(uuid4())},
    )
    assert invalid_version.status_code == 404
    assert invalid_version.json()["error"]["code"] == "SCENARIO_VERSION_NOT_FOUND"
