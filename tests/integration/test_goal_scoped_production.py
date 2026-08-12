from collections.abc import Iterable

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    ("goal", "expected_scope", "forbidden_tools"),
    [
        (
            "Gather intelligence about the Northern Valley",
            ["GATHER_VALLEY_INTELLIGENCE"],
            {"start_military_operation", "start_outpost_repair", "start_trade_route_test"},
        ),
        (
            "Secure the Northern Valley",
            ["SECURE_NORTHERN_VALLEY"],
            {"start_outpost_repair", "start_trade_route_test"},
        ),
        (
            "Restore Starfire Outpost",
            ["RESTORE_STARFIRE_OUTPOST"],
            {"start_trade_route_test"},
        ),
        (
            "Gather intelligence and restore Starfire Outpost",
            ["GATHER_VALLEY_INTELLIGENCE", "RESTORE_STARFIRE_OUTPOST"],
            {"start_trade_route_test"},
        ),
        (
            "Full northern recovery",
            ["FULL_NORTHERN_RECOVERY"],
            set(),
        ),
    ],
)
def test_goal_scoped_workflows_stop_without_extra_objectives(
    client: TestClient,
    goal: str,
    expected_scope: list[str],
    forbidden_tools: set[str],
) -> None:
    session_id, task_id = _issue(client, goal)
    final = _drive_to_terminal(client, session_id, task_id)
    task = final["task"]

    assert task["status"] == "SUCCEEDED", task
    assert task["objective_scope"]["objective_keys"] == expected_scope
    selected_tools = {
        step["selected_tool_name"]
        for plan in task["plans"]
        for step in plan["steps"]
        if step["selected_tool_name"] is not None
    }
    assert "inspect_command_state" not in selected_tools
    assert selected_tools.isdisjoint(forbidden_tools)
    assert all(
        plan_scope == expected_scope
        for plan_scope in _trace_plan_scopes(final.get("recent_traces", []))
    )


def test_open_trade_scope_keeps_prerequisites_out_of_objective_keys(
    client: TestClient,
) -> None:
    session_id, task_id = _issue(client, "Open the Northern Trade Route")
    final = _drive_to_terminal(client, session_id, task_id)
    task = final["task"]

    assert task["status"] == "SUCCEEDED"
    assert task["objective_scope"]["objective_keys"] == ["OPEN_NORTHERN_TRADE_ROUTE"]
    assert final["known_world_state"]["starfire_outpost_status"] == "OPERATIONAL"
    assert final["known_world_state"]["northern_trade_route_status"] == "OPEN"
    assert all(
        step["selected_tool_name"] != "inspect_command_state"
        for plan in task["plans"]
        for step in plan["steps"]
    )


def _issue(client: TestClient, goal: str) -> tuple[str, str]:
    reset = client.post("/api/v1/debug/strategic/reset", json={})
    assert reset.status_code == 201, reset.text
    session_id = str(reset.json()["session_id"])
    command = client.post(
        "/api/v1/debug/strategic/commands",
        json={
            "session_id": session_id,
            "command": goal,
            "idempotency_key": f"scoped-command-{len(goal):03d}-0001",
        },
    )
    assert command.status_code == 201, command.text
    assert command.json()["event"] == "PLANNED"
    return session_id, str(command.json()["task_id"])


def _drive_to_terminal(
    client: TestClient,
    session_id: str,
    task_id: str,
) -> dict[str, object]:
    for index in range(30):
        response = client.get(
            "/api/v1/debug/strategic/snapshot",
            params={"session_id": session_id, "include_trace": "true"},
        )
        assert response.status_code == 200, response.text
        snapshot = response.json()
        task = snapshot["task"]
        if task["status"] in {"SUCCEEDED", "BLOCKED", "FAILED"}:
            return snapshot
        decision = snapshot["active_decision"]
        if decision is not None:
            resolved = client.post(
                f"/api/v1/debug/strategic/tasks/{task_id}/decisions/{decision['id']}/resolve",
                json={"session_id": session_id, "option_id": "APPROVE"},
            )
            assert resolved.status_code == 200, resolved.text
            continue
        operation = snapshot["pending_world_event"]
        assert operation is not None, snapshot
        resolved = client.post(
            f"/api/v1/debug/strategic/tasks/{task_id}/world-events/{operation['id']}/resolve",
            json={
                "session_id": session_id,
                "idempotency_key": f"scoped-resolution-{index:03d}",
            },
        )
        assert resolved.status_code == 200, resolved.text
    raise AssertionError("Goal-scoped workflow did not terminate")


def _trace_plan_scopes(traces: Iterable[object]) -> list[list[str]]:
    scopes: list[list[str]] = []
    for item in traces:
        if not isinstance(item, dict) or item.get("purpose") not in {"PLAN", "REPLAN"}:
            continue
        scope = item.get("objective_scope")
        if isinstance(scope, dict) and isinstance(scope.get("objective_keys"), list):
            scopes.append(scope["objective_keys"])
    return scopes
