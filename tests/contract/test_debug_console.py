from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app


def test_debug_console_and_assets_are_served(client: TestClient) -> None:
    page = client.get("/debug")
    script = client.get("/debug-assets/app.js")
    api_client = client.get("/debug-assets/api.js")
    renderer = client.get("/debug-assets/render.js")
    polling = client.get("/debug-assets/polling.js")
    stylesheet = client.get("/debug-assets/styles.css")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "战略军令控制台" in page.text
    assert "向沈策下达命令" in page.text
    assert "开发者执行审计" in page.text
    assert "修复星火前哨" in page.text
    assert "重新打通北方商路" in page.text
    assert "Advance one step" not in page.text
    assert "npc-select" not in page.text
    assert script.status_code == 200
    assert api_client.status_code == 200
    assert renderer.status_code == 200
    assert polling.status_code == 200
    assert stylesheet.status_code == 200


def test_debug_context_exposes_only_safe_bootstrap_metadata(client: TestClient) -> None:
    response = client.get("/api/v1/debug/context")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"]["type"] in {"mock", "openai_compatible"}
    assert isinstance(body["provider"]["key_configured"], bool)
    assert {
        "captain_aria",
        "guanyin",
        "han_lie",
        "lu_ning",
        "red_boy",
        "shen_ce",
    }.issubset({npc["key"] for npc in body["npcs"]})
    assert {officer["key"] for officer in body["officers"]} == {
        "han_lie",
        "lu_ning",
        "shen_ce",
    }
    assert {officer["role"] for officer in body["officers"]} == {
        "GENERAL",
        "STEWARD",
        "STRATEGIST",
    }
    assert all(officer["profile_version"] >= 1 for officer in body["officers"])
    assert body["worlds"][0]["key"] == "fire_mountain"

    serialized = response.text.lower()
    for forbidden in {
        "model_api_key",
        "memory_summary",
        "hidden_ambush",
        "approval_token",
        "approval_receipt",
    }:
        assert forbidden not in serialized


def test_debug_console_backing_api_flow_is_complete(client: TestClient) -> None:
    player = client.post("/api/v1/players", json={"name": "Console Pilgrim"}).json()
    context = client.get("/api/v1/debug/context").json()
    guanyin = next(npc for npc in context["npcs"] if npc["key"] == "guanyin")

    session_response = client.post(
        "/api/v1/sessions",
        json={"player_id": player["id"], "npc_id": guanyin["id"]},
    )
    assert session_response.status_code == 201
    session_id = session_response.json()["id"]

    message_response = client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={
            "content": (
                "Use get_player_state to check my current gold and level. "
                "Do not answer from memory."
            )
        },
    )
    assert message_response.status_code == 200
    run_id = message_response.json()["agent_run_id"]

    run = client.get(f"/api/v1/agent-runs/{run_id}").json()
    traces = client.get(f"/api/v1/agent-runs/{run_id}/tool-executions").json()
    player_state = client.get(f"/api/v1/players/{player['id']}/state").json()

    assert run["status"] == "COMPLETED"
    assert run["model"] == "mock-model"
    assert traces[0]["tool_name"] == "get_player_state"
    assert traces[0]["validation_status"] == "PASSED"
    assert traces[0]["authorization_status"] == "PASSED"
    assert traces[0]["business_rule_status"] == "PASSED"
    assert traces[0]["execution_status"] == "SUCCEEDED"
    assert player_state["player"]["id"] == player["id"]


def test_player_session_history_exposes_navigation_metadata(client: TestClient) -> None:
    player = client.post("/api/v1/players", json={"name": "Session Historian"}).json()
    context = client.get("/api/v1/debug/context").json()
    guanyin = next(npc for npc in context["npcs"] if npc["key"] == "guanyin")

    first_session = client.post(
        "/api/v1/sessions",
        json={"player_id": player["id"], "npc_id": guanyin["id"]},
    ).json()
    message = client.post(
        f"/api/v1/sessions/{first_session['id']}/messages",
        json={"content": "Inspect my verified state."},
    )
    assert message.status_code == 200
    run_id = message.json()["agent_run_id"]

    second_session = client.post(
        "/api/v1/sessions",
        json={"player_id": player["id"], "npc_id": guanyin["id"]},
    ).json()
    response = client.get(f"/api/v1/players/{player['id']}/sessions")

    assert response.status_code == 200
    history = response.json()
    assert [item["id"] for item in history] == [
        second_session["id"],
        first_session["id"],
    ]
    assert history[0]["message_count"] == 0
    assert history[0]["latest_run_id"] is None
    assert history[1]["npc_name"] == "Guanyin"
    assert history[1]["npc_role"] == "QUEST_GIVER"
    assert history[1]["message_count"] == 2
    assert history[1]["latest_message_preview"]
    assert history[1]["latest_run_id"] == run_id

    trace = client.get(f"/api/v1/sessions/{first_session['id']}/trace")
    assert trace.status_code == 200
    session_runs = trace.json()
    assert len(session_runs) == 1
    assert session_runs[0]["run"]["id"] == run_id
    assert session_runs[0]["run"]["model"] == "mock-model"
    assert session_runs[0]["run"]["status"] == "COMPLETED"
    assert session_runs[0]["tools"][0]["tool_name"] == "get_player_state"


def test_debug_scenario_fixture_is_disabled_in_production(client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(app_env="production")

    response = client.post(
        "/api/v1/debug/scenarios/starfire",
        json={"variant": "underpowered"},
    )
    resolver = client.post(
        "/api/v1/debug/world-events/00000000-0000-0000-0000-000000000001/resolve",
        json={},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DEBUG_FIXTURE_DISABLED"
    assert resolver.status_code == 404
    assert resolver.json()["error"]["code"] == "DEBUG_FIXTURE_DISABLED"


def test_natural_complex_goal_routes_to_a_validated_task(client: TestClient) -> None:
    fixture = client.post(
        "/api/v1/debug/scenarios/starfire",
        json={"variant": "combat_ready"},
    ).json()

    response = client.post(
        f"/api/v1/sessions/{fixture['session_id']}/messages",
        json={"content": "Help me restore Starfire Outpost and obtain safe access."},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["route_mode"] == "STRUCTURED_TASK"
    assert body["route_reason_code"] == "KNOWN_MULTI_STEP_GOAL"
    assert body["task_event"] == "PLANNED"
    task = client.get(f"/api/v1/tasks/{body['task_id']}").json()
    assert task["planning_mode"] == "PROVIDER"
    assert task["plans"][0]["source"] == "MOCK_PLANNER"
    assert task["plans"][0]["validation_status"] == "PASSED"


def test_simple_query_bypasses_task_router(client: TestClient) -> None:
    fixture = client.post(
        "/api/v1/debug/scenarios/starfire",
        json={"variant": "combat_ready"},
    ).json()

    response = client.post(
        f"/api/v1/sessions/{fixture['session_id']}/messages",
        json={"content": "What is the current Starfire Outpost status?"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["route_mode"] == "CONVERSATION"
    assert body["task_id"] is None
    assert body["task_event"] is None
