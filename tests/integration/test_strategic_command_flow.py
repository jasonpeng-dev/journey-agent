import json

import pytest
from fastapi.testclient import TestClient

from app.agent.providers import MockModelProvider
from app.agent.types import Message, ModelResponse, ToolDefinition


class _RecordingProvider:
    name = "mock-model"

    def __init__(self) -> None:
        self.delegate = MockModelProvider()
        self.payloads: list[str] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        self.payloads.append(
            json.dumps(
                {
                    "messages": [message.model_dump(mode="json") for message in messages],
                    "tools": [tool.model_dump(mode="json") for tool in tools],
                },
                ensure_ascii=False,
            )
        )
        return await self.delegate.complete(messages, tools)


def _snapshot(
    client: TestClient,
    session_id: str,
    *,
    hidden: bool = False,
) -> dict[str, object]:
    response = client.get(
        "/api/v1/debug/strategic/snapshot",
        params={
            "session_id": session_id,
            "include_trace": "true",
            "include_hidden_truth": str(hidden).lower(),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_strategic_command_switches_officers_replans_and_completes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _RecordingProvider()
    monkeypatch.setattr(
        "app.debug.strategic_controller.build_provider",
        lambda _settings: provider,
    )
    fixture = client.post("/api/v1/debug/strategic/reset", json={}).json()
    session_id = str(fixture["session_id"])
    initial = _snapshot(client, session_id, hidden=True)
    assert "ambush_status" not in initial["known_world_state"]
    assert "enemy_supply_route" not in initial["known_world_state"]
    observer_nodes = {node["key"]: node for node in initial["observer_world_state"]["nodes"]}
    initial_valley_facts = {
        fact["key"]: fact for fact in observer_nodes["northern_valley"]["facts"]
    }
    assert initial_valley_facts["ambush_status"]["truth"] == "ACTIVE"
    assert initial_valley_facts["ambush_status"]["knowledge"] == "HIDDEN"
    assert observer_nodes["enemy_north_supply_route"]["knowledge"] == "HIDDEN"
    assert observer_nodes["starfire_outpost"]["access"] == "LOCKED"
    assert observer_nodes["northern_trade_route"]["access"] == "LOCKED"
    command = client.post(
        "/api/v1/debug/strategic/commands",
        json={
            "session_id": session_id,
            "command": "修复星火前哨并重新打通北方商路。",
            "idempotency_key": "full-flow-command-0001",
        },
    )
    assert command.status_code == 201, command.text
    assert provider.payloads
    assert all(
        token not in "\n".join(provider.payloads)
        for token in ("ambush_status", "enemy_north_supply_route", "supply_status")
    )
    provider_call_count = len(provider.payloads)

    saw_replan = False
    saw_decision = False
    saw_recon_reveal = False
    saw_defeat_reveal = False
    saw_supply_disruption = False
    saw_valley_clearance = False
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
            new_payloads = provider.payloads[provider_call_count:]
            if not saw_defeat_reveal:
                assert all("enemy_north_supply_route" not in item for item in new_payloads)
                assert all("supply_status" not in item for item in new_payloads)
            provider_call_count = len(provider.payloads)
            continue

        operation = snapshot["pending_world_event"]
        assert isinstance(operation, dict), snapshot
        operation_type = operation["operation_type"]
        operation_target = operation["target_key"]
        response = client.post(
            f"/api/v1/debug/strategic/tasks/{task['id']}/world-events/{operation['id']}/resolve",
            json={
                "session_id": session_id,
                "idempotency_key": f"full-flow-resolution-{index:02d}",
            },
        )
        assert response.status_code == 200, response.text
        resolved = response.json()
        stage = _snapshot(client, session_id)
        known = stage["known_world_state"]
        if operation_type == "RECONNAISSANCE":
            assert known["ambush_status"] == "ACTIVE"
            assert "enemy_supply_route" not in known
            new_payloads = provider.payloads[provider_call_count:]
            assert any("ambush_status" in item for item in new_payloads)
            assert all("enemy_north_supply_route" not in item for item in new_payloads)
            assert all("supply_status" not in item for item in new_payloads)
            saw_recon_reveal = True
        elif operation_type == "MILITARY" and operation_target == "northern_valley":
            if resolved["outcome"]["result"] == "DEFEAT":
                assert known["enemy_supply_route"] == "ACTIVE"
                assert int(stage["task"]["current_plan_version"]) >= 2
                new_payloads = provider.payloads[provider_call_count:]
                assert any("enemy_north_supply_route" in item for item in new_payloads)
                assert any("supply_status" in item for item in new_payloads)
                saw_defeat_reveal = True
            else:
                assert resolved["outcome"]["result"] == "VICTORY"
                assert known["valley_security"] == "SAFE"
                new_payloads = provider.payloads[provider_call_count:]
                assert any(
                    "ambush_status" in item
                    and "CLEARED" in item
                    and "valley_security" in item
                    and "SAFE" in item
                    for item in new_payloads
                )
                saw_valley_clearance = True
        elif operation_type == "MILITARY" and operation_target == "enemy_north_supply_route":
            assert known["enemy_supply_route"] == "DISRUPTED"
            new_payloads = provider.payloads[provider_call_count:]
            assert any(
                "enemy_supply_route" in item and "DISRUPTED" in item for item in new_payloads
            )
            saw_supply_disruption = True
        provider_call_count = len(provider.payloads)
    else:
        raise AssertionError("Strategic command did not complete")

    final = _snapshot(client, session_id)
    assert final["task"]["status"] == "SUCCEEDED"  # type: ignore[index]
    assert saw_replan
    assert saw_decision
    assert saw_recon_reveal
    assert saw_defeat_reveal
    assert saw_supply_disruption
    assert saw_valley_clearance
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
    assert plans[-1]["replan_reason"] == "ENCOUNTER_DEFEAT"
    replanned_tools = {
        step["selected_tool_name"]
        for step in plans[-1]["steps"]
        if step["selected_tool_name"] is not None
    }
    assert "start_recon_operation" not in replanned_tools
    assert "start_military_operation" in replanned_tools

    trace = final["recent_traces"]
    assert isinstance(trace, list)
    actors = {run["actor"]["key"] for run in trace if isinstance(run.get("actor"), dict)}
    assert {"shen_ce", "han_lie", "lu_ning"}.issubset(actors)
    assert any(run["tools"] for run in trace)
