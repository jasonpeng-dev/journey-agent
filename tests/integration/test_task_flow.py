import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.providers import MockModelProvider
from app.agent.task_orchestrator import TaskOrchestrator
from app.agent.types import MockStep
from app.core.config import Settings
from app.domain.enums import AgentTaskStatus
from app.infrastructure.db.models import AgentTask, ConversationSession, ToolExecution
from app.services.game import GameService, seed_id


def _advance(client: TestClient, task_id: str, session_id: str) -> dict[str, object]:
    response = client.post(
        f"/api/v1/tasks/{task_id}/advance",
        json={"session_id": session_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_starfire_task_replans_waits_resumes_and_completes(client: TestClient) -> None:
    fixture = client.post(
        "/api/v1/debug/scenarios/starfire",
        json={"variant": "underpowered"},
    ).json()
    created = client.post(
        "/api/v1/tasks",
        json={
            "session_id": fixture["session_id"],
            "goal_description": ("Restore Starfire Outpost and obtain safe access for the player."),
            "scenario_key": "starfire_outpost",
            "planning_mode": "DETERMINISTIC_BASELINE",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    task_id = body["task"]["id"]
    assert body["event"] == "PLANNED"
    assert body["task"]["current_plan_version"] == 1
    assert len(body["task"]["plans"][0]["steps"]) == 8

    assert _advance(client, task_id, fixture["session_id"])["event"] == "STEP_SUCCEEDED"
    assert _advance(client, task_id, fixture["session_id"])["event"] == "STEP_SUCCEEDED"
    assert _advance(client, task_id, fixture["session_id"])["event"] == "STEP_SUCCEEDED"
    waiting = _advance(client, task_id, fixture["session_id"])
    assert waiting["event"] == "WAITING"
    assert waiting["task"]["status"] == "WAITING_FOR_USER"

    defeat = client.post(
        f"/api/v1/debug/scenarios/starfire/{fixture['player_id']}/encounter-turn",
        json={"strategy": "CAUTIOUS"},
    )
    assert defeat.status_code == 200, defeat.text
    assert defeat.json()["result"] == "DEFEAT"

    replanned = _advance(client, task_id, fixture["session_id"])
    assert replanned["event"] == "REPLANNED"
    assert replanned["task"]["current_plan_version"] == 2
    assert replanned["task"]["replan_count"] == 1
    assert replanned["task"]["plans"][0]["status"] == "SUPERSEDED"

    assert _advance(client, task_id, fixture["session_id"])["event"] == "STEP_SUCCEEDED"
    assert _advance(client, task_id, fixture["session_id"])["event"] == "STEP_SUCCEEDED"
    waiting_again = _advance(client, task_id, fixture["session_id"])
    assert waiting_again["event"] == "WAITING"

    resumed_session = client.post(
        "/api/v1/sessions",
        json={"player_id": fixture["player_id"], "npc_id": fixture["npc_id"]},
    ).json()
    victory = client.post(
        f"/api/v1/debug/scenarios/starfire/{fixture['player_id']}/encounter-turn",
        json={"strategy": "CAUTIOUS"},
    )
    assert victory.status_code == 200, victory.text
    assert victory.json()["result"] == "VICTORY"
    assert victory.json()["reward_status"] == "CLAIMED"

    resumed = _advance(client, task_id, resumed_session["id"])
    assert resumed["event"] == "RESUMED"
    for _ in range(4):
        progress = _advance(client, task_id, resumed_session["id"])
        assert progress["event"] == "STEP_SUCCEEDED"

    final_task = client.get(f"/api/v1/tasks/{task_id}").json()
    assert final_task["status"] == "SUCCEEDED"
    assert final_task["last_session_id"] == resumed_session["id"]
    assert final_task["plans"][1]["status"] == "SUCCEEDED"
    assert all(step["status"] == "SUCCEEDED" for step in final_task["plans"][1]["steps"])

    player_state = client.get(f"/api/v1/players/{fixture['player_id']}/state").json()
    outpost = next(node for node in player_state["nodes"] if node["key"] == "starfire_outpost")
    assert outpost["status"] == "AVAILABLE"
    assert player_state["player"]["gold"] == 30

    trace = client.get(f"/api/v1/tasks/{task_id}/trace").json()
    tool_names = [tool["tool_name"] for run in trace["runs"] for tool in run["tools"]]
    assert "create_task_plan" in tool_names
    assert "replan_task" in tool_names
    assert "request_npc_assistance" in tool_names
    assert "restore_outpost" in tool_names
    assert "grant_access" in tool_names


def test_task_resume_rejects_a_different_npc_without_replanning(
    client: TestClient,
) -> None:
    fixture = client.post(
        "/api/v1/debug/scenarios/starfire",
        json={"variant": "combat_ready"},
    ).json()
    task = client.post(
        "/api/v1/tasks",
        json={
            "session_id": fixture["session_id"],
            "goal_description": "Restore Starfire Outpost through the verified task workflow.",
            "planning_mode": "DETERMINISTIC_BASELINE",
        },
    ).json()["task"]
    context = client.get("/api/v1/debug/context").json()
    guanyin = next(npc for npc in context["npcs"] if npc["key"] == "guanyin")
    wrong_session = client.post(
        "/api/v1/sessions",
        json={"player_id": fixture["player_id"], "npc_id": guanyin["id"]},
    ).json()

    response = client.post(
        f"/api/v1/tasks/{task['id']}/advance",
        json={"session_id": wrong_session["id"]},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TASK_NPC_MISMATCH"
    unchanged = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert unchanged["replan_count"] == 0


def test_mock_planner_generates_and_validates_plan_v1(client: TestClient) -> None:
    fixture = client.post(
        "/api/v1/debug/scenarios/starfire",
        json={"variant": "combat_ready"},
    ).json()

    response = client.post(
        "/api/v1/tasks",
        json={
            "session_id": fixture["session_id"],
            "goal_description": "Restore Starfire Outpost and obtain safe access.",
            "planning_mode": "PROVIDER",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    plan = body["task"]["plans"][0]
    run = client.get(f"/api/v1/agent-runs/{body['agent_run_id']}").json()
    assert body["event"] == "PLANNED"
    assert body["task"]["planning_mode"] == "PROVIDER"
    assert plan["source"] == "MOCK_PLANNER"
    assert plan["planner_model"] == "mock-model"
    assert plan["validation_status"] == "PASSED"
    assert len(plan["steps"]) == 8
    assert run["purpose"] == "PLAN"
    assert run["validation_status"] == "PASSED"
    assert run["model_rounds"][0]["proposal"]["steps"]


def test_planner_repairs_one_invalid_response(session: Session) -> None:
    player = GameService(session).create_player("Planner Repair")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:captain_aria"),
    )
    session.add(conversation)
    session.commit()
    orchestrator = TaskOrchestrator(
        session,
        MockModelProvider(steps=[MockStep(content="I forgot the tool call")]),
        Settings(database_url="sqlite+pysqlite:///:memory:", model_provider="mock"),
    )

    task, run, event = asyncio.run(
        orchestrator.start(
            conversation,
            "Restore Starfire Outpost and obtain safe access.",
            "starfire_outpost",
        )
    )

    assert event == "PLANNED"
    assert run is not None
    assert run.actual_rounds == 2
    assert run.model_rounds[0]["plan_validation_status"] == "REJECTED"
    assert run.model_rounds[1]["plan_validation_status"] == "PASSED"
    assert task.current_plan_version == 1


def test_repeated_invalid_plans_stop_without_persisting_a_plan(session: Session) -> None:
    player = GameService(session).create_player("Planner Safe Stop")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:captain_aria"),
    )
    session.add(conversation)
    session.commit()
    orchestrator = TaskOrchestrator(
        session,
        MockModelProvider(
            steps=[
                MockStep(content="No structured plan"),
                MockStep(content="Still no structured plan"),
            ]
        ),
        Settings(database_url="sqlite+pysqlite:///:memory:", model_provider="mock"),
    )

    task, run, event = asyncio.run(
        orchestrator.start(
            conversation,
            "Restore Starfire Outpost and obtain safe access.",
            "starfire_outpost",
        )
    )

    assert event == "PLANNING_FAILED"
    assert run is not None
    assert run.validation_status == "REJECTED"
    assert task.status == AgentTaskStatus.BLOCKED
    assert task.current_plan_version == 0
    traces = session.scalars(
        select(ToolExecution).where(ToolExecution.agent_run_id == run.id)
    ).all()
    assert traces == []
    stored = session.get(AgentTask, task.id)
    assert stored is not None
    assert stored.last_error_code == "PLAN_VALIDATION_FAILED"


def test_mock_replanner_generates_plan_v2_after_recoverable_failure(
    client: TestClient,
) -> None:
    fixture = client.post(
        "/api/v1/debug/scenarios/starfire",
        json={"variant": "underpowered"},
    ).json()
    created = client.post(
        "/api/v1/tasks",
        json={
            "session_id": fixture["session_id"],
            "goal_description": "Restore Starfire Outpost and obtain safe access.",
            "planning_mode": "PROVIDER",
        },
    ).json()
    task_id = created["task"]["id"]
    for _ in range(4):
        _advance(client, task_id, fixture["session_id"])
    defeat = client.post(
        f"/api/v1/debug/scenarios/starfire/{fixture['player_id']}/encounter-turn",
        json={"strategy": "CAUTIOUS"},
    )
    assert defeat.json()["result"] == "DEFEAT"

    replanned = _advance(client, task_id, fixture["session_id"])

    assert replanned["event"] == "REPLANNED"
    plans = replanned["task"]["plans"]
    assert plans[0]["status"] == "SUPERSEDED"
    assert plans[0]["source"] == "MOCK_PLANNER"
    assert plans[1]["source"] == "MOCK_PLANNER"
    assert plans[1]["validation_status"] == "PASSED"
    assert plans[1]["replan_reason"] == "ENCOUNTER_DEFEAT"
    v2_tools = {step["selected_tool_name"] for step in plans[1]["steps"]}
    assert "create_quest" not in v2_tools
    assert "request_npc_assistance" in v2_tools
