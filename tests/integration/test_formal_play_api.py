from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.scenarios.builtin import MEDICAL_EMERGENCY_V2, require_builtin_v2_version


def _new_game(client: TestClient, version_id: str) -> str:
    response = client.post(
        "/api/v1/games",
        json={"scenario_version_id": version_id, "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def test_starfire_formal_play_auto_settles_and_never_exposes_hidden_truth(
    client: TestClient,
) -> None:
    scenario = next(
        item for item in client.get("/api/v1/scenarios").json() if item["key"] == "starfire_command"
    )
    game_id = _new_game(client, scenario["current_published_version_id"])
    initial = client.get(f"/api/v1/games/{game_id}/play")
    assert initial.status_code == 200
    assert "supply_status" not in initial.text
    assert "truth_value" not in initial.text
    denied = client.get(f"/api/v1/developer/games/{game_id}/snapshot")
    assert denied.status_code == 403
    developer = client.get(
        f"/api/v1/developer/games/{game_id}/snapshot",
        headers={"x-developer-token": "test-developer"},
    )
    assert developer.status_code == 200
    hidden_key = "enemy_north_supply_route.supply_status"
    assert hidden_key in developer.json()["truth"]["facts"]
    assert hidden_key not in developer.json()["knowledge"]["facts"]

    goal_key = str(uuid4())
    goal = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "gather valley intelligence", "idempotency_key": goal_key},
    )
    assert goal.status_code == 200, goal.text
    assert goal.json()["status"] == "ACCEPTED"
    assert goal.json()["task"]["status"] == "COMPLETED"
    assert [step["status"] for step in goal.json()["task"]["plan"]["steps"]] == [
        "COMPLETED",
        "COMPLETED",
    ]
    replay = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "gather valley intelligence", "idempotency_key": goal_key},
    )
    assert replay.json()["task"]["id"] == goal.json()["task"]["id"]
    conflict = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "secure the northern valley", "idempotency_key": goal_key},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "GOAL_IDEMPOTENCY_CONFLICT"


def test_medical_uses_same_play_api_and_game_remains_active_after_goal(
    client: TestClient, session: Session
) -> None:
    version = require_builtin_v2_version(session, MEDICAL_EMERGENCY_V2)
    session.commit()
    game_id = _new_game(client, str(version.id))
    first = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    )
    assert first.status_code == 200, first.text
    assert first.json()["task"]["status"] == "COMPLETED"
    state = client.get(f"/api/v1/games/{game_id}/play").json()
    assert state["game"]["status"] == "ACTIVE"
    assert state["game"]["active_task_id"] is None
    second = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    )
    assert second.status_code == 200
    assert second.json()["task"]["status"] == "COMPLETED"


def test_starfire_failure_updates_knowledge_and_replans_in_formal_play(
    client: TestClient,
) -> None:
    scenario = next(
        item for item in client.get("/api/v1/scenarios").json() if item["key"] == "starfire_command"
    )
    game_id = _new_game(client, scenario["current_published_version_id"])
    response = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "secure the northern valley", "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["status"] == "COMPLETED"
    assert task["plan"]["updated"] is True
    state = client.get(f"/api/v1/games/{game_id}/play").json()
    knowledge = {item["fact_key"]: item["value"] for item in state["known_facts"]}
    assert knowledge["ambush_status"] == "CLEARED"
    assert knowledge["valley_security"] == "SAFE"


def test_starfire_trade_goal_completes_prerequisites_in_one_task(client: TestClient) -> None:
    scenario = next(
        item for item in client.get("/api/v1/scenarios").json() if item["key"] == "starfire_command"
    )
    game_id = _new_game(client, scenario["current_published_version_id"])

    response = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "open the northern trade route", "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["status"] == "COMPLETED"
    assert task["plan"]["updated"] is True
    state = client.get(f"/api/v1/games/{game_id}/play").json()
    knowledge = {item["fact_key"]: item["value"] for item in state["known_facts"]}
    assert knowledge["supply_status"] == "DISRUPTED"
    assert knowledge["valley_security"] == "SAFE"
    assert knowledge["village_support"] == "GUIDE"
    assert knowledge["outpost_status"] == "RESTORED"
    assert knowledge["trade_route_status"] == "OPEN"


def test_unsupported_goal_does_not_create_a_task(client: TestClient) -> None:
    scenario = next(
        item for item in client.get("/api/v1/scenarios").json() if item["key"] == "starfire_command"
    )
    game_id = _new_game(client, scenario["current_published_version_id"])
    response = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "invent warp travel", "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "UNSUPPORTED"
    assert response.json()["task"] is None
    no_task = client.post(f"/api/v1/games/{game_id}/continue")
    assert no_task.status_code == 409
    assert no_task.json()["error"]["code"] == "AGENT_TASK_NOT_ACTIVE"
    assert client.post(f"/api/v1/games/{game_id}/archive").status_code == 200
    archived_goal = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "gather valley intelligence", "idempotency_key": str(uuid4())},
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
