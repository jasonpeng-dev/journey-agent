from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import AgentStepStatus
from app.infrastructure.db.models import AgentRun, AgentStep


def _create_strategic_task(client: TestClient) -> tuple[dict[str, object], dict[str, object]]:
    fixture_response = client.post(
        "/api/v1/debug/scenarios/starfire",
        json={"variant": "strategic"},
    )
    assert fixture_response.status_code == 201, fixture_response.text
    fixture = fixture_response.json()
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
    assert created.json()["event"] == "PLANNED"
    return fixture, created.json()["task"]


def _advance(client: TestClient, task_id: str, session_id: str) -> dict[str, object]:
    response = client.post(
        f"/api/v1/tasks/{task_id}/advance",
        json={"session_id": session_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _drive_to_decision(
    client: TestClient,
    task_id: str,
    session_id: str,
) -> dict[str, object]:
    for _ in range(40):
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        status = task["status"]
        if status == "REQUIRES_PLAYER_DECISION":
            return task
        if status == "ACTIVE":
            _advance(client, task_id, session_id)
            continue
        if status == "WAITING_FOR_WORLD_EVENT":
            event = task["pending_world_event"]
            if event is None:
                _advance(client, task_id, session_id)
            else:
                resolved = client.post(
                    f"/api/v1/debug/world-events/{event['id']}/resolve",
                    json={},
                )
                assert resolved.status_code == 200, resolved.text
            continue
        raise AssertionError(f"Unexpected task state while seeking decision: {task}")
    raise AssertionError("Strategic task did not reach its authority decision")


def test_world_event_wait_and_resolution_are_idempotent(client: TestClient) -> None:
    fixture, task = _create_strategic_task(client)
    task_id = str(task["id"])
    session_id = str(fixture["session_id"])

    _advance(client, task_id, session_id)
    _advance(client, task_id, session_id)
    waiting = _advance(client, task_id, session_id)["task"]
    assert waiting["status"] == "WAITING_FOR_WORLD_EVENT"
    event = waiting["pending_world_event"]
    assert event is not None

    state_before = client.get(f"/api/v1/players/{fixture['player_id']}/state").json()
    assert state_before["domain"]["soldiers_committed"] == 60
    assert len(state_before["operations"]) == 1
    version_before = waiting["version"]

    repeated_wait = _advance(client, task_id, session_id)["task"]
    state_after_wait = client.get(f"/api/v1/players/{fixture['player_id']}/state").json()
    assert repeated_wait["version"] == version_before
    assert state_after_wait["domain"]["soldiers_committed"] == 60
    assert len(state_after_wait["operations"]) == 1

    spoofed = client.post(
        f"/api/v1/debug/world-events/{event['id']}/resolve",
        json={"outcome": {"result": "VICTORY"}},
    )
    assert spoofed.status_code == 422
    oversized_key = client.post(
        f"/api/v1/debug/world-events/{event['id']}/resolve",
        json={"idempotency_key": "x" * 161},
    )
    assert oversized_key.status_code == 422

    first = client.post(
        f"/api/v1/debug/world-events/{event['id']}/resolve",
        json={"idempotency_key": "resolve-recon-0001"},
    )
    assert first.status_code == 200, first.text
    first_state = client.get(f"/api/v1/players/{fixture['player_id']}/state").json()
    assert first.json()["outcome"]["result"] == "PARTIAL_SUCCESS"
    assert first_state["domain"]["soldiers_committed"] == 0
    assert first_state["domain"]["soldiers_total"] == 300

    replay = client.post(
        f"/api/v1/debug/world-events/{event['id']}/resolve",
        json={"idempotency_key": "resolve-recon-0001"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["outcome"] == first.json()["outcome"]
    replay_state = client.get(f"/api/v1/players/{fixture['player_id']}/state").json()
    assert replay_state["domain"] == first_state["domain"]

    conflict = client.post(
        f"/api/v1/debug/world-events/{event['id']}/resolve",
        json={"idempotency_key": "different-resolution-key"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "WORLD_EVENT_ALREADY_RESOLVED"


def test_player_decision_is_scoped_replayable_and_non_mutating(
    client: TestClient,
) -> None:
    fixture, task = _create_strategic_task(client)
    task_id = str(task["id"])
    session_id = str(fixture["session_id"])
    waiting = _drive_to_decision(client, task_id, session_id)
    decision = waiting["pending_decision"]
    assert decision is not None
    decision_id = decision["id"]

    invalid = client.post(
        f"/api/v1/tasks/{task_id}/decisions/{decision_id}/resolve",
        json={"session_id": session_id, "option_id": "INCREASE_TO_100"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "DECISION_OPTION_INVALID"
    unchanged = client.get(f"/api/v1/tasks/{task_id}").json()
    assert unchanged["status"] == "REQUIRES_PLAYER_DECISION"
    assert unchanged["pending_decision"]["id"] == decision_id

    spoofed = client.post(
        f"/api/v1/tasks/{task_id}/decisions/{decision_id}/resolve",
        json={
            "session_id": session_id,
            "option_id": "APPROVE",
            "action_arguments": {"food_offer": 100},
        },
    )
    assert spoofed.status_code == 422

    approved = client.post(
        f"/api/v1/tasks/{task_id}/decisions/{decision_id}/resolve",
        json={"session_id": session_id, "option_id": "APPROVE"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["event"] == "DECISION_APPROVED"
    approved_version = approved.json()["task"]["version"]
    before_execution = client.get(f"/api/v1/players/{fixture['player_id']}/state").json()
    assert before_execution["domain"]["food"] == 100

    replay = client.post(
        f"/api/v1/tasks/{task_id}/decisions/{decision_id}/resolve",
        json={"session_id": session_id, "option_id": "APPROVE"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["event"] == "DECISION_ALREADY_RESOLVED"
    assert replay.json()["task"]["version"] == approved_version

    conflicting = client.post(
        f"/api/v1/tasks/{task_id}/decisions/{decision_id}/resolve",
        json={"session_id": session_id, "option_id": "REJECT"},
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "DECISION_ALREADY_RESOLVED"

    executed = _advance(client, task_id, session_id)
    assert executed["event"] == "STEP_SUCCEEDED"
    after_execution = client.get(f"/api/v1/players/{fixture['player_id']}/state").json()
    assert after_execution["domain"]["food"] == 65
    trace = client.get(f"/api/v1/tasks/{task_id}/trace").json()
    stored = trace["decisions"][0]
    assert stored["status"] == "CONSUMED"
    assert stored["action_tool_name"] == "negotiate_village_support"
    assert stored["action_arguments"]["food_offer"] == 35


def test_only_the_plan_owner_session_can_advance_assigned_officer_steps(
    client: TestClient,
) -> None:
    fixture, task = _create_strategic_task(client)
    task_id = str(task["id"])
    command_session_id = str(fixture["session_id"])

    _advance(client, task_id, command_session_id)
    current = client.get(f"/api/v1/tasks/{task_id}").json()
    assert current["current_actor_officer"]["key"] == "han_lie"
    current_step_id = current["current_step_id"]
    officers = {item["key"]: item for item in fixture["officers"]}

    han_session = client.post(
        "/api/v1/sessions",
        json={
            "player_id": fixture["player_id"],
            "npc_id": officers["han_lie"]["id"],
        },
    )
    assert han_session.status_code == 201, han_session.text
    wrong = client.post(
        f"/api/v1/tasks/{task_id}/advance",
        json={"session_id": han_session.json()["id"]},
    )
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "TASK_NPC_MISMATCH"

    resumed = client.post(
        "/api/v1/sessions",
        json={
            "player_id": fixture["player_id"],
            "npc_id": officers["shen_ce"]["id"],
        },
    )
    assert resumed.status_code == 201, resumed.text
    result = _advance(client, task_id, resumed.json()["id"])
    assert result["event"] == "STEP_SUCCEEDED"
    assert result["task"]["last_session_id"] == resumed.json()["id"]

    trace = client.get(f"/api/v1/tasks/{task_id}/trace").json()
    run = next(
        item
        for item in trace["runs"]
        if item["purpose"] == "STEP" and item["step_id"] == current_step_id
    )
    assert run["session_id"] == resumed.json()["id"]
    assert run["actor_officer"]["key"] == "han_lie"


def test_rejected_decision_closes_the_step_and_replans_once(
    client: TestClient,
) -> None:
    fixture, task = _create_strategic_task(client)
    task_id = str(task["id"])
    session_id = str(fixture["session_id"])
    waiting = _drive_to_decision(client, task_id, session_id)
    decision = waiting["pending_decision"]
    assert decision is not None

    rejected = client.post(
        f"/api/v1/tasks/{task_id}/decisions/{decision['id']}/resolve",
        json={"session_id": session_id, "option_id": "REJECT"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["event"] == "DECISION_REJECTED"
    rejected_task = rejected.json()["task"]
    rejected_step = next(
        step
        for plan in rejected_task["plans"]
        for step in plan["steps"]
        if step["id"] == decision["related_step_id"]
    )
    assert rejected_step["status"] == "FAILED"
    assert rejected_step["failure_code"] == "PLAYER_DECISION_REJECTED"
    assert rejected_step["completed_at"] is not None
    state = client.get(f"/api/v1/players/{fixture['player_id']}/state").json()
    assert state["domain"]["food"] == 100

    replanned = _advance(client, task_id, session_id)
    assert replanned["event"] == "REPLANNED"
    assert replanned["task"]["current_plan_version"] == 3
    assert replanned["task"]["replan_count"] == 2
    assert replanned["task"]["plans"][-1]["replan_reason"] == ("PLAYER_DECISION_REJECTED")

    replay = client.post(
        f"/api/v1/tasks/{task_id}/decisions/{decision['id']}/resolve",
        json={"session_id": session_id, "option_id": "REJECT"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["event"] == "DECISION_ALREADY_RESOLVED"
    assert replay.json()["task"]["current_plan_version"] == 3
    assert replay.json()["task"]["replan_count"] == 2


def test_strategic_command_must_be_issued_through_the_strategist(
    client: TestClient,
) -> None:
    legacy_fixture = client.post(
        "/api/v1/debug/scenarios/starfire",
        json={"variant": "combat_ready"},
    ).json()

    response = client.post(
        "/api/v1/tasks",
        json={
            "session_id": legacy_fixture["session_id"],
            "goal_description": ("Restore Starfire Outpost and reopen the northern trade route."),
            "scenario_key": "starfire_command",
            "planning_mode": "DETERMINISTIC_BASELINE",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "COMMAND_OWNER_INVALID"


def test_an_in_progress_step_is_not_executed_by_a_second_advance(
    client: TestClient,
    session: Session,
) -> None:
    fixture, task = _create_strategic_task(client)
    task_id = str(task["id"])
    step = session.get(AgentStep, UUID(str(task["current_step_id"])))
    assert step is not None
    step.status = AgentStepStatus.IN_PROGRESS
    session.commit()
    run_count = session.scalar(
        select(func.count()).select_from(AgentRun).where(AgentRun.task_id == UUID(task_id))
    )

    response = client.post(
        f"/api/v1/tasks/{task_id}/advance",
        json={"session_id": fixture["session_id"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["event"] == "STEP_IN_PROGRESS"
    assert (
        session.scalar(
            select(func.count()).select_from(AgentRun).where(AgentRun.task_id == UUID(task_id))
        )
        == run_count
    )
