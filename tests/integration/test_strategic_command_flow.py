from fastapi.testclient import TestClient


def test_strategic_starfire_command_switches_officers_and_completes(
    client: TestClient,
) -> None:
    fixture_response = client.post(
        "/api/v1/debug/scenarios/starfire",
        json={"variant": "strategic"},
    )
    assert fixture_response.status_code == 201, fixture_response.text
    fixture = fixture_response.json()
    assert {item["key"] for item in fixture["officers"]} == {
        "shen_ce",
        "han_lie",
        "lu_ning",
    }

    created = client.post(
        "/api/v1/tasks",
        json={
            "session_id": fixture["session_id"],
            "goal_description": ("Restore Starfire Outpost and reopen the northern trade route."),
            "scenario_key": "starfire_command",
            "planning_mode": "DETERMINISTIC_BASELINE",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    if body["event"] != "PLANNED":
        print(client.get(f"/api/v1/agent-runs/{body['agent_run_id']}").json())
    assert body["event"] == "PLANNED", (
        body,
        client.get(f"/api/v1/agent-runs/{body['agent_run_id']}").json(),
    )
    task = body["task"]
    task_id = task["id"]
    assert task["owner_officer"]["key"] == "shen_ce"
    assert task["plans"][0]["created_by_officer"]["key"] == "shen_ce"
    assert {step["assigned_officer"]["key"] for step in task["plans"][0]["steps"]} == {
        "shen_ce",
        "han_lie",
        "lu_ning",
    }

    transitions: list[str] = []
    decision_id: str | None = None
    for _ in range(80):
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        transitions.append(
            f"{task['status']}:v{task['current_plan_version']}:{task['current_step_id']}"
        )
        if task["status"] == "SUCCEEDED":
            break
        if task["status"] in {"FAILED", "BLOCKED"}:
            raise AssertionError(
                f"Strategic command stopped unexpectedly: {transitions}; "
                f"error={task['last_error_code']}"
            )
        if task["status"] == "ACTIVE":
            response = client.post(
                f"/api/v1/tasks/{task_id}/advance",
                json={"session_id": fixture["session_id"]},
            )
            assert response.status_code == 200, response.text
            continue
        if task["status"] == "REQUIRES_PLAYER_DECISION":
            decision = task["pending_decision"]
            assert decision is not None
            decision_id = decision["id"]
            assert decision["requested_by_officer"]["key"] == "lu_ning"
            assert decision["action_tool_name"] == "negotiate_village_support"
            assert decision["action_arguments"]["food_offer"] == 35
            assert {option["id"] for option in decision["options"]} == {
                "APPROVE",
                "REJECT",
            }
            assert decision["policy_snapshot"]["outcome"] == "REQUIRE_PLAYER_DECISION"
            assert decision["policy_snapshot"]["exceeded_limits"] == [
                {"field": "food_offer", "requested": 35, "limit": 30}
            ]
            before = client.get(f"/api/v1/players/{fixture['player_id']}/state").json()
            assert before["domain"]["food"] == 100
            response = client.post(
                f"/api/v1/tasks/{task_id}/decisions/{decision['id']}/resolve",
                json={
                    "session_id": fixture["session_id"],
                    "option_id": "APPROVE",
                },
            )
            assert response.status_code == 200, response.text
            after = client.get(f"/api/v1/players/{fixture['player_id']}/state").json()
            assert after["domain"]["food"] == 100
            continue
        if task["status"] == "WAITING_FOR_WORLD_EVENT":
            event = task["pending_world_event"]
            if event is not None:
                response = client.post(
                    f"/api/v1/debug/world-events/{event['id']}/resolve",
                    json={},
                )
                assert response.status_code == 200, response.text
            else:
                response = client.post(
                    f"/api/v1/tasks/{task_id}/advance",
                    json={"session_id": fixture["session_id"]},
                )
                assert response.status_code == 200, response.text
            continue
        if task["status"] == "WAITING_FOR_PLAYER_ACTION":
            raise AssertionError(
                f"Starfire strategic scenario unexpectedly needs a player action: {task}"
            )
        raise AssertionError(f"Unknown task status: {task['status']}; {transitions}")
    else:
        raise AssertionError(f"Strategic command did not reach a terminal state: {transitions}")

    assert task["status"] == "SUCCEEDED"
    assert decision_id is not None
    assert task["current_plan_version"] == 2
    assert task["replan_count"] == 1
    assert len(task["plans"]) == 2
    first_plan, second_plan = task["plans"]
    assert first_plan["status"] == "SUPERSEDED"
    assert second_plan["status"] == "SUCCEEDED"
    assert second_plan["supersedes_plan_id"] == first_plan["id"]
    assert second_plan["replan_reason"] == "ENCOUNTER_DEFEAT"
    assert first_plan["created_by_officer"]["key"] == "shen_ce"
    assert second_plan["created_by_officer"]["key"] == "shen_ce"
    assert first_plan["steps"][-1]["status"] == "SKIPPED"
    assert second_plan["steps"][-1]["assigned_officer"]["key"] == "shen_ce"
    assert second_plan["steps"][-1]["action_intent"] == "VERIFY_AND_REPORT"
    second_tools = [
        step["selected_tool_name"]
        for step in second_plan["steps"]
        if step["selected_tool_name"] is not None
    ]
    assert "start_recon_operation" not in second_tools
    assert "negotiate_village_support" in second_tools
    assert second_tools.count("start_military_operation") == 2

    player_state = client.get(f"/api/v1/players/{fixture['player_id']}/state").json()
    facts = {item["key"]: item["value"] for item in player_state["facts"]}
    nodes = {item["key"]: item["status"] for item in player_state["nodes"]}
    assert facts["valley_intelligence"]["status"] == "COMPLETE"
    assert facts["enemy_supply_route"]["status"] == "DISRUPTED"
    assert facts["valley_security"]["status"] == "SAFE"
    assert facts["village_support"]["status"] == "GUIDE"
    assert facts["starfire_outpost_status"]["status"] == "OPERATIONAL"
    assert facts["northern_trade_route_status"]["status"] == "OPEN"
    assert nodes["northern_trade_route"] == "AVAILABLE"
    assert player_state["domain"]["soldiers_total"] == 277
    assert player_state["domain"]["soldiers_committed"] == 0
    assert player_state["domain"]["food"] == 45
    assert player_state["domain"]["morale"] == 58
    assert player_state["player"]["gold"] == 60
    assert {officer["key"] for officer in player_state["officers"]} == {
        "shen_ce",
        "han_lie",
        "lu_ning",
    }
    assert all(
        officer["authority_policy_status"] == "VALID" and not officer["authority_policy_errors"]
        for officer in player_state["officers"]
    )
    assert all(
        any("Command completed under Plan v2" in item for item in officer["memory_summary"])
        for officer in player_state["officers"]
    )

    trace = client.get(f"/api/v1/tasks/{task_id}/trace").json()
    step_actors = {
        step["id"]: step["assigned_officer"]["id"]
        for plan in trace["task"]["plans"]
        for step in plan["steps"]
    }
    for run in trace["runs"]:
        assert run["session_id"] == fixture["session_id"]
        if run["purpose"] in {"STEP", "WAIT_CHECK"}:
            assert run["actor_officer_id"] == step_actors[run["step_id"]]
        if run["purpose"] in {"PLAN", "REPLAN"}:
            assert run["actor_officer"]["key"] == "shen_ce"

    events = trace["world_events"]
    assert len(events) == 6
    assert len({event["id"] for event in events}) == 6
    assert len({event["source_step_id"] for event in events}) == 6
    assert [
        (
            event["operation_type"],
            event["parameters"].get("mission_type"),
            event["outcome"]["result"],
            event["initiated_by_officer"]["key"],
        )
        for event in events
    ] == [
        ("RECONNAISSANCE", None, "PARTIAL_SUCCESS", "han_lie"),
        ("MILITARY", "CLEAR_VALLEY", "DEFEAT", "han_lie"),
        ("MILITARY", "DISRUPT_SUPPLY", "VICTORY", "han_lie"),
        ("MILITARY", "CLEAR_VALLEY", "VICTORY", "han_lie"),
        ("CONSTRUCTION", None, "COMPLETED", "lu_ning"),
        ("TRADE_TEST", None, "COMPLETED", "lu_ning"),
    ]
    assert all(event["status"] == "RESOLVED" for event in events)

    assert len(trace["decisions"]) == 1
    decision = trace["decisions"][0]
    assert decision["id"] == decision_id
    assert decision["status"] == "CONSUMED"
    assert decision["selected_option"] == "APPROVE"
    assert decision["action_arguments"]["food_offer"] == 35
    decision_step_id = decision["step_id"]
    decision_tools = [
        tool
        for run in trace["runs"]
        for tool in run["tools"]
        if tool["step_id"] == decision_step_id
    ]
    assert [tool["execution_status"] for tool in decision_tools] == [
        "WAITING",
        "SUCCEEDED",
    ]
    assert decision_tools[0]["authority_details"]["outcome"] == ("REQUIRE_PLAYER_DECISION")
    assert decision_tools[1]["authority_details"]["reason_code"] == ("PLAYER_APPROVAL_CONSUMED")
    assert decision_tools[1]["authority_details"]["approval_id"] == decision_id
