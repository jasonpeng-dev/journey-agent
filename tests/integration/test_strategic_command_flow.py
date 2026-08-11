from fastapi.testclient import TestClient


def _snapshot(client: TestClient, session_id: str) -> dict[str, object]:
    response = client.get(
        "/api/v1/debug/strategic/snapshot",
        params={"session_id": session_id, "include_trace": "true"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_strategic_command_switches_officers_replans_and_completes(
    client: TestClient,
) -> None:
    fixture = client.post("/api/v1/debug/strategic/reset", json={}).json()
    session_id = str(fixture["session_id"])
    command = client.post(
        "/api/v1/debug/strategic/commands",
        json={
            "session_id": session_id,
            "command": "修复星火前哨并重新打通北方商路。",
            "idempotency_key": "full-flow-command-0001",
        },
    )
    assert command.status_code == 201, command.text

    saw_replan = False
    saw_decision = False
    for index in range(24):
        snapshot = _snapshot(client, session_id)
        task = snapshot["task"]
        assert isinstance(task, dict)
        assert task["owner_officer"]["key"] == "shen_ce"  # type: ignore[index]
        saw_replan = saw_replan or int(task["current_plan_version"]) > 1
        if task["status"] == "SUCCEEDED":
            break

        decision = snapshot["active_decision"]
        if isinstance(decision, dict):
            saw_decision = True
            response = client.post(
                f"/api/v1/debug/strategic/tasks/{task['id']}/decisions/{decision['id']}/resolve",
                json={"session_id": session_id, "option_id": "APPROVE"},
            )
            assert response.status_code == 200, response.text
            continue

        operation = snapshot["pending_world_event"]
        assert isinstance(operation, dict), snapshot
        response = client.post(
            f"/api/v1/debug/strategic/tasks/{task['id']}/world-events/{operation['id']}/resolve",
            json={
                "session_id": session_id,
                "idempotency_key": f"full-flow-resolution-{index:02d}",
            },
        )
        assert response.status_code == 200, response.text
    else:
        raise AssertionError("Strategic command did not complete")

    final = _snapshot(client, session_id)
    assert final["task"]["status"] == "SUCCEEDED"  # type: ignore[index]
    assert saw_replan
    assert saw_decision
    world = final["known_world_state"]
    assert world["valley_intelligence"] == "COMPLETE"  # type: ignore[index]
    assert world["enemy_supply_route"] == "DISRUPTED"  # type: ignore[index]
    assert world["valley_security"] == "SAFE"  # type: ignore[index]
    assert world["village_support"] in {"GUIDE", "SUPPLIES"}  # type: ignore[index]
    assert world["starfire_outpost_status"] in {"OPERATIONAL", "RESTORED"}  # type: ignore[index]
    assert world["northern_trade_route_status"] == "OPEN"  # type: ignore[index]
    plans = final["plan_history"]
    assert isinstance(plans, list)
    assert len(plans) >= 2
    assert plans[-1]["status"] == "SUCCEEDED"

    trace = final["recent_traces"]
    assert isinstance(trace, list)
    actors = {run["actor"]["key"] for run in trace if isinstance(run.get("actor"), dict)}
    assert {"shen_ce", "han_lie", "lu_ning"}.issubset(actors)
    assert any(run["tools"] for run in trace)
