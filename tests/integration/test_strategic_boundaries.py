from fastapi.testclient import TestClient


def _start(client: TestClient) -> tuple[str, dict[str, object]]:
    reset = client.post("/api/v1/debug/strategic/reset", json={})
    assert reset.status_code == 201, reset.text
    session_id = str(reset.json()["session_id"])
    command = client.post(
        "/api/v1/debug/strategic/commands",
        json={
            "session_id": session_id,
            "command": "修复星火前哨并重新打通北方商路。",
            "idempotency_key": "boundary-command-0001",
        },
    )
    assert command.status_code == 201, command.text
    return session_id, _snapshot(client, session_id)


def _snapshot(client: TestClient, session_id: str) -> dict[str, object]:
    response = client.get(
        "/api/v1/debug/strategic/snapshot",
        params={"session_id": session_id, "include_trace": "true"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _resolve_event(
    client: TestClient,
    session_id: str,
    snapshot: dict[str, object],
    key: str,
):  # type: ignore[no-untyped-def]
    task = snapshot["task"]
    operation = snapshot["pending_world_event"]
    assert isinstance(task, dict)
    assert isinstance(operation, dict)
    return client.post(
        f"/api/v1/debug/strategic/tasks/{task['id']}/world-events/{operation['id']}/resolve",
        json={"session_id": session_id, "idempotency_key": key},
    )


def test_world_event_resolution_is_scoped_and_idempotent(client: TestClient) -> None:
    session_id, snapshot = _start(client)
    event = snapshot["pending_world_event"]
    assert isinstance(event, dict)
    assert event["operation_type"] == "RECONNAISSANCE"
    assert snapshot["resources"]["soldiers_committed"] == 60  # type: ignore[index]

    spoofed = _resolve_event(client, session_id, snapshot, "x" * 161)
    assert spoofed.status_code == 422

    first = _resolve_event(client, session_id, snapshot, "resolve-recon-0001")
    assert first.status_code == 200, first.text
    assert first.json()["outcome"]["result"] == "PARTIAL_SUCCESS"

    replay = client.post(
        f"/api/v1/debug/strategic/tasks/{snapshot['task']['id']}"
        f"/world-events/{event['id']}/resolve",  # type: ignore[index]
        json={"session_id": session_id, "idempotency_key": "resolve-recon-0001"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["outcome"] == first.json()["outcome"]

    conflict = client.post(
        f"/api/v1/debug/strategic/tasks/{snapshot['task']['id']}"
        f"/world-events/{event['id']}/resolve",  # type: ignore[index]
        json={"session_id": session_id, "idempotency_key": "resolve-recon-different"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "WORLD_EVENT_ALREADY_RESOLVED"


def test_player_approval_is_scoped_replayable_and_executes_only_after_approval(
    client: TestClient,
) -> None:
    session_id, snapshot = _start(client)
    for index in range(12):
        decision = snapshot["active_decision"]
        if isinstance(decision, dict):
            break
        assert isinstance(snapshot["pending_world_event"], dict)
        response = _resolve_event(client, session_id, snapshot, f"seek-decision-{index:02d}")
        assert response.status_code == 200, response.text
        snapshot = _snapshot(client, session_id)
    else:
        raise AssertionError("The command never reached a player approval")

    task = snapshot["task"]
    decision = snapshot["active_decision"]
    assert isinstance(task, dict)
    assert isinstance(decision, dict)
    assert decision["action_tool_name"] == "negotiate_village_support"
    assert snapshot["resources"]["food"] == 100  # type: ignore[index]

    invalid = client.post(
        f"/api/v1/debug/strategic/tasks/{task['id']}/decisions/{decision['id']}/resolve",
        json={"session_id": session_id, "option_id": "INCREASE_TO_100"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "DECISION_OPTION_INVALID"

    approved = client.post(
        f"/api/v1/debug/strategic/tasks/{task['id']}/decisions/{decision['id']}/resolve",
        json={"session_id": session_id, "option_id": "APPROVE"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["event"] == "DECISION_APPROVED"
    after = _snapshot(client, session_id)
    assert after["resources"]["food"] == 65  # type: ignore[index]

    replay = client.post(
        f"/api/v1/debug/strategic/tasks/{task['id']}/decisions/{decision['id']}/resolve",
        json={"session_id": session_id, "option_id": "APPROVE"},
    )
    assert replay.status_code == 200
    assert replay.json()["event"] == "DECISION_ALREADY_RESOLVED"

    conflicting = client.post(
        f"/api/v1/debug/strategic/tasks/{task['id']}/decisions/{decision['id']}/resolve",
        json={"session_id": session_id, "option_id": "REJECT"},
    )
    assert conflicting.status_code == 409


def test_command_endpoint_reuses_the_active_command(client: TestClient) -> None:
    session_id, first = _start(client)
    task = first["task"]
    assert isinstance(task, dict)

    repeated = client.post(
        "/api/v1/debug/strategic/commands",
        json={
            "session_id": session_id,
            "command": "修复星火前哨并重新打通北方商路。",
            "idempotency_key": "boundary-command-0002",
        },
    )

    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["event"] == "EXISTING_TASK"
    assert repeated.json()["task_id"] == task["id"]
