from fastapi.testclient import TestClient


def test_ambiguous_goal_waits_for_dedicated_clarification_before_planning(
    client: TestClient,
) -> None:
    reset = client.post("/api/v1/debug/strategic/reset", json={})
    session_id = str(reset.json()["session_id"])
    command = client.post(
        "/api/v1/debug/strategic/commands",
        json={
            "session_id": session_id,
            "command": "Handle the northern situation",
            "idempotency_key": "ambiguous-goal-command-001",
        },
    )

    assert command.status_code == 201, command.text
    assert command.json()["event"] == "GOAL_CLARIFICATION_REQUIRED"
    assert command.json()["transitions"] == []
    task_id = str(command.json()["task_id"])
    before = client.get(
        "/api/v1/debug/strategic/snapshot",
        params={"session_id": session_id, "include_trace": "true"},
    ).json()
    assert before["task"]["objective_resolution"]["status"] == "NEEDS_CLARIFICATION"
    assert before["task"]["objective_scope"] is None
    assert before["active_plan"] is None
    assert before["active_decision"] is None
    assert before["capabilities"]["requires_goal_clarification"] is True
    assert before["polling"]["recommended"] is False

    clarified = client.post(
        f"/api/v1/debug/strategic/tasks/{task_id}/goal-clarification",
        json={
            "session_id": session_id,
            "objective_keys": ["RESTORE_STARFIRE_OUTPOST"],
        },
    )

    assert clarified.status_code == 200, clarified.text
    assert clarified.json()["event"] == "GOAL_CONFIRMED"
    after = client.get(
        "/api/v1/debug/strategic/snapshot",
        params={"session_id": session_id},
    ).json()
    assert after["task"]["objective_scope"]["objective_keys"] == ["RESTORE_STARFIRE_OUTPOST"]
    assert after["task"]["objective_scope"]["frozen"] is True
    assert after["active_plan"] is not None
    assert after["active_decision"] is None


def test_unsupported_goal_does_not_create_a_plan_or_full_scope(client: TestClient) -> None:
    reset = client.post("/api/v1/debug/strategic/reset", json={})
    session_id = str(reset.json()["session_id"])
    command = client.post(
        "/api/v1/debug/strategic/commands",
        json={
            "session_id": session_id,
            "command": "Build a fleet in the southern seas",
            "idempotency_key": "unsupported-goal-command-001",
        },
    )

    assert command.status_code == 201, command.text
    assert command.json()["event"] == "GOAL_UNSUPPORTED"
    snapshot = client.get(
        "/api/v1/debug/strategic/snapshot",
        params={"session_id": session_id, "include_trace": "true"},
    ).json()
    assert snapshot["task"]["objective_resolution"]["status"] == "UNSUPPORTED"
    assert snapshot["task"]["objective_scope"] is None
    assert snapshot["active_plan"] is None
    assert snapshot["recent_traces"] == []
