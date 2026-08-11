from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app


def _reset(client: TestClient) -> dict[str, object]:
    response = client.post("/api/v1/debug/strategic/reset", json={})
    assert response.status_code == 201, response.text
    return response.json()


def _snapshot(
    client: TestClient,
    session_id: str,
    *,
    trace: bool = False,
    hidden: bool = False,
) -> dict[str, object]:
    response = client.get(
        "/api/v1/debug/strategic/snapshot",
        params={
            "session_id": session_id,
            "include_trace": str(trace).lower(),
            "include_hidden_truth": str(hidden).lower(),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_strategic_snapshot_is_shen_ce_scoped_and_hides_truth_by_default(
    client: TestClient,
) -> None:
    fixture = _reset(client)
    session_id = str(fixture["session_id"])

    snapshot = _snapshot(client, session_id)

    assert snapshot["scenario"]["key"] == "starfire_command"
    assert snapshot["session"]["commanding_officer"]["key"] == "shen_ce"
    assert {officer["key"] for officer in snapshot["officers"]} == {
        "shen_ce",
        "han_lie",
        "lu_ning",
    }
    assert snapshot["known_world_state"]["ambush_status"] == "UNKNOWN"
    assert snapshot["known_world_state"]["enemy_supply_route"] == "UNKNOWN"
    assert snapshot["hidden_world_truth"] is None
    assert snapshot["task"] is None
    assert snapshot["capabilities"]["can_issue_command"] is True

    developer = _snapshot(client, session_id, hidden=True)
    assert developer["hidden_world_truth"]["classification"] == "DEVELOPER_ONLY"
    assert developer["hidden_world_truth"]["ambush_status"] == "ACTIVE"


def test_strategic_facade_auto_drives_to_pauses_and_completion(
    client: TestClient,
) -> None:
    fixture = _reset(client)
    session_id = str(fixture["session_id"])
    command = client.post(
        "/api/v1/debug/strategic/commands",
        json={
            "session_id": session_id,
            "command": "修复星火前哨并重新打通北方商路。",
            "idempotency_key": "command-starfire-001",
        },
    )
    assert command.status_code == 201, command.text
    command_snapshot = _snapshot(client, session_id)
    assert command_snapshot["task"]["goal_description"] == "修复星火前哨并重新打通北方商路。"
    initial_plan = command_snapshot["active_plan"]
    assert "沈策" in initial_plan["strategy_summary"]
    assert all(
        any("\u4e00" <= character <= "\u9fff" for character in step["description"])
        for step in initial_plan["steps"]
    )

    seen_decision = False
    seen_replan = False
    for _ in range(20):
        snapshot = _snapshot(client, session_id, trace=True)
        task = snapshot["task"]
        assert task["owner_officer"]["key"] == "shen_ce"
        assert task["scenario_key"] == "starfire_command"
        if task["current_plan_version"] >= 2:
            seen_replan = True
        if task["status"] == "SUCCEEDED":
            break
        decision = snapshot["active_decision"]
        if decision is not None:
            seen_decision = True
            assert decision["requested_by_officer"]["key"] == "lu_ning"
            assert decision["action_arguments"]["food_offer"] == 35
            response = client.post(
                (f"/api/v1/debug/strategic/tasks/{task['id']}/decisions/{decision['id']}/resolve"),
                json={"session_id": session_id, "option_id": "APPROVE"},
            )
            assert response.status_code == 200, response.text
            continue
        world_event = snapshot["pending_world_event"]
        assert world_event is not None, snapshot
        response = client.post(
            (
                f"/api/v1/debug/strategic/tasks/{task['id']}/"
                f"world-events/{world_event['id']}/resolve"
            ),
            json={"session_id": session_id},
        )
        assert response.status_code == 200, response.text
    else:
        raise AssertionError("Strategic facade did not reach a terminal state")

    assert task["status"] == "SUCCEEDED"
    assert seen_replan is True
    assert seen_decision is True
    assert task["current_plan_version"] == 2
    assert snapshot["known_world_state"]["starfire_outpost_status"] == "OPERATIONAL"
    assert snapshot["known_world_state"]["northern_trade_route_status"] == "OPEN"
    assert any(item["kind"] == "FINAL_REPORT" for item in snapshot["timeline"])
    assert any(run["tools"] for run in snapshot["recent_traces"])


def test_strategic_debug_facade_is_disabled_in_production(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(app_env="production")

    response = client.post("/api/v1/debug/strategic/reset", json={})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DEBUG_FIXTURE_DISABLED"
