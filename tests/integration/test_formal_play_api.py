from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.provider import PlanProposal, PlanRequest, PlanStepProposal
from app.domain.enums import NodeStatus, StepExecutionType
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentPlan,
    AgentStep,
    AgentTask,
    GameInstanceFactState,
    GameInstanceNodeState,
    WorldOperation,
)
from app.scenarios.builtin import (
    LINJIANG_INFRASTRUCTURE_RECOVERY_V1,
    MEDICAL_EMERGENCY_V2,
    require_builtin_v2_version,
)


class FormalKnowledgeRevalidationProvider:
    model_name = "formal-knowledge-revalidation-provider"

    def __init__(self) -> None:
        self.requests: list[PlanRequest] = []

    def select_objectives(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("The exact Linjiang goal alias should resolve deterministically")

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.requests.append(request)
        if request.call_type == "INITIAL_PLAN":
            steps = (
                PlanStepProposal(
                    action_key="travel",
                    actor_key="logistics_team_alpha",
                    target_key="west_logistics_district",
                    purpose="Travel to the West logistics district.",
                ),
                PlanStepProposal(
                    action_key="transport_resource",
                    actor_key="logistics_team_alpha",
                    target_key="central_district",
                    purpose="Transport ten electrical repair parts to Central.",
                    parameters={
                        "resource_key": "electrical_repair_parts",
                        "amount": 10,
                    },
                ),
                PlanStepProposal(
                    action_key="repair_electrical",
                    actor_key="electrical_team_beta",
                    target_key="central_hospital",
                    purpose="Restore Central Hospital emergency power.",
                ),
            )
        else:
            steps = (
                PlanStepProposal(
                    action_key="clear_transport",
                    actor_key="municipal_repair_team_alpha",
                    target_key="west_freight_corridor",
                    purpose="Clear the known West corridor blockage.",
                ),
                PlanStepProposal(
                    action_key="travel",
                    actor_key="logistics_team_alpha",
                    target_key="west_logistics_district",
                    purpose="Travel to the West logistics district.",
                ),
                PlanStepProposal(
                    action_key="transport_resource",
                    actor_key="logistics_team_alpha",
                    target_key="central_district",
                    purpose="Transport ten electrical repair parts to Central.",
                    parameters={
                        "resource_key": "electrical_repair_parts",
                        "amount": 10,
                    },
                ),
                PlanStepProposal(
                    action_key="repair_electrical",
                    actor_key="electrical_team_beta",
                    target_key="central_hospital",
                    purpose="Restore Central Hospital emergency power.",
                ),
            )
        return PlanProposal(plan_summary=request.call_type, steps=steps)


def _new_game(client: TestClient, version_id: str) -> str:
    response = client.post(
        "/api/v1/games",
        json={"scenario_version_id": version_id, "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _ack_action(client: TestClient, game_id: str, task: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/games/{game_id}/play/acknowledge-action",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_task"] is not None
    return response.json()["current_task"]


def _start_planning(client: TestClient, game_id: str, task: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/games/{game_id}/play/start-planning",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_task"] is not None
    return response.json()["current_task"]


def _ack_debrief(client: TestClient, game_id: str, task: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/games/{game_id}/play/acknowledge-debrief",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["current_task"] is not None
    return response.json()["current_task"]


def _drive_task(
    client: TestClient, game_id: str, task: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    action_results: list[dict[str, Any]] = []
    for _ in range(30):
        if task["execution_phase"] in ("COMPLETED", "BLOCKED", "ABORTED"):
            return task, action_results
        if task["execution_phase"] == "AWAITING_PLAN_START":
            task = _start_planning(client, game_id, task)
        elif task["execution_phase"] == "AWAITING_ACTION_ACK":
            task = _ack_action(client, game_id, task)
            action_results.append(task)
        elif task["execution_phase"] == "AWAITING_REPLAN_ACK":
            response = client.post(
                f"/api/v1/games/{game_id}/play/replan",
                json={"expected_pacing_version": task["pacing_version"]},
            )
            assert response.status_code == 200, response.text
            task = response.json()["current_task"]
        elif task["execution_phase"] == "AWAITING_DEBRIEF_ACK":
            task = _ack_debrief(client, game_id, task)
        else:
            raise AssertionError(f"Unexpected phase: {task['execution_phase']}")
    raise AssertionError("Task did not stop within the test safety bound")


def test_goal_submission_stops_at_first_briefing_and_ack_runs_one_action_cycle(
    client: TestClient, session: Session
) -> None:
    scenario = next(
        item for item in client.get("/api/v1/scenarios").json() if item["key"] == "starfire_command"
    )
    game_id = _new_game(client, scenario["current_published_version_id"])
    initial = client.get(f"/api/v1/games/{game_id}/play")
    assert initial.status_code == 200
    assert "supply_status" not in initial.text
    assert "truth_value" not in initial.text
    denied = client.get(f"/api/v1/developer/games/{game_id}/snapshot")
    assert denied.status_code == 403
    developer = client.get(
        f"/api/v1/developer/games/{game_id}/snapshot",
        headers={"x-developer-token": "test-developer"},
    )
    assert developer.status_code == 200
    hidden_key = "enemy_north_supply_route.supply_status"
    assert hidden_key in developer.json()["truth"]["facts"]
    assert hidden_key not in developer.json()["knowledge"]["facts"]
    denied_history = client.get(f"/api/v1/developer/games/{game_id}/history")
    assert denied_history.status_code == 403
    developer_history = client.get(
        f"/api/v1/developer/games/{game_id}/history",
        headers={"x-developer-token": "test-developer"},
    )
    assert developer_history.status_code == 200

    goal_key = str(uuid4())
    goal = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "gather valley intelligence", "idempotency_key": goal_key},
    )
    assert goal.status_code == 200, goal.text
    task = goal.json()["task"]
    assert task["status"] == "ACTIVE"
    assert task["execution_phase"] == "AWAITING_PLAN_START"
    assert task["briefing"] is None
    assert task["debrief"] is None
    assert task["plan_history"] == []
    goal_event = next(event for event in task["timeline"] if event["kind"] == "GOAL_ACCEPTED")
    assert goal_event["duration_ms"] is not None
    task = _start_planning(client, game_id, task)
    assert task["execution_phase"] == "AWAITING_ACTION_ACK"
    assert task["briefing"]["action_name"] == "侦察北部山谷"
    assert len(task["plan_history"]) == 1
    assert task["plan_history"][0]["ordinal"] == 1
    assert task["plan_history"][0]["steps"][0]["status"] == "CURRENT"
    assert task["plan_history"][0]["steps"][0]["action_name"] == "侦察北部山谷"
    plan_event = next(event for event in task["timeline"] if event["kind"] == "PLAN_CREATED")
    assert plan_event["duration_ms"] is not None
    persisted_plan = session.scalar(select(AgentPlan).where(AgentPlan.task_id == UUID(task["id"])))
    assert persisted_plan is not None
    persisted_tool_steps = tuple(
        session.scalars(
            select(AgentStep)
            .where(
                AgentStep.plan_id == persisted_plan.id,
                AgentStep.execution_type == StepExecutionType.TOOL,
            )
            .order_by(AgentStep.sequence)
        )
    )
    assert [step["id"] for step in task["plan_history"][0]["steps"]] == [
        str(step.id) for step in persisted_tool_steps
    ]
    assert all(event["kind"] != "ACTION_BRIEFING" for event in task["timeline"])
    assert session.scalar(select(func.count()).select_from(ActionDecisionRequest)) == 0

    completed = _ack_action(client, game_id, task)
    assert completed["execution_phase"] == "COMPLETED"
    assert completed["status"] == "COMPLETED"
    assert session.scalar(select(func.count()).select_from(ActionDecisionRequest)) == 0
    assert all(
        step["description"].lower().find("wait") == -1 for step in completed["plan"]["steps"]
    )
    assert all("wait" not in event["title"].lower() for event in completed["timeline"])
    assert completed["plan_history"][-1]["status"] == "COMPLETED"
    assert completed["plan_history"][-1]["steps"][0]["status"] == "COMPLETED"

    replay = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "gather valley intelligence", "idempotency_key": goal_key},
    )
    assert replay.json()["task"]["id"] == task["id"]
    conflict = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "secure the northern valley", "idempotency_key": goal_key},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "GOAL_IDEMPOTENCY_CONFLICT"


def test_medical_uses_same_stepwise_play_and_game_remains_active(
    client: TestClient, session: Session
) -> None:
    version = require_builtin_v2_version(session, MEDICAL_EMERGENCY_V2)
    session.commit()
    game_id = _new_game(client, str(version.id))
    first = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    ).json()["task"]
    completed, action_results = _drive_task(client, game_id, first)

    assert completed["status"] == "COMPLETED"
    assert len(action_results) == 2
    assert all(stage["status"] == "COMPLETED" for stage in completed["roadmap"]["stages"])
    result_titles = {
        event["title"] for event in completed["timeline"] if event["kind"] == "ACTION_RESULT"
    }
    assert result_titles == {"诊断患者", "治疗患者"}
    assert completed["plan_history"][-1]["status"] == "COMPLETED"
    assert [step["action_name"] for step in completed["plan_history"][-1]["steps"]] == [
        "诊断患者",
        "治疗患者",
    ]
    state = client.get(f"/api/v1/games/{game_id}/play").json()
    assert state["game"]["status"] == "ACTIVE"
    assert state["game"]["active_task_id"] is None


@pytest.mark.parametrize(
    "goal",
    [
        "恢复中央医院应急供电",
        "恢复中央医院的应急供电",
        "恢复中央医院应急电力",
        "Restore emergency power to Central Hospital.",
    ],
)
def test_linjiang_goal_aliases_create_confirmed_single_objective_scope(
    client: TestClient,
    session: Session,
    goal: str,
) -> None:
    version = require_builtin_v2_version(session, LINJIANG_INFRASTRUCTURE_RECOVERY_V1)
    session.commit()
    game_id = _new_game(client, str(version.id))

    response = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": goal, "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 200, response.text
    task_payload = response.json()["task"]
    assert task_payload["execution_phase"] == "AWAITING_PLAN_START"
    persisted = session.get(AgentTask, UUID(task_payload["id"]))
    assert persisted is not None
    assert persisted.objective_resolution_status == "CONFIRMED"
    assert persisted.objective_scope_keys == ["restore_central_hospital_emergency_power"]
    assert persisted.objective_catalog_version == f"scenario-version:{version.id}"
    assert persisted.objective_scope_hash


def test_linjiang_unrelated_goal_is_unsupported_without_a_task(
    client: TestClient,
    session: Session,
) -> None:
    version = require_builtin_v2_version(session, LINJIANG_INFRASTRUCTURE_RECOVERY_V1)
    session.commit()
    game_id = _new_game(client, str(version.id))

    response = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "恢复临江市机场运行", "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "UNSUPPORTED"
    assert session.scalar(select(func.count()).select_from(AgentTask)) == 0


def test_failure_debrief_contains_knowledge_and_same_task_replan(
    client: TestClient, session: Session
) -> None:
    scenario = next(
        item for item in client.get("/api/v1/scenarios").json() if item["key"] == "starfire_command"
    )
    game_id = _new_game(client, scenario["current_published_version_id"])
    task = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "secure the northern valley", "idempotency_key": str(uuid4())},
    ).json()["task"]
    original_task_id = task["id"]

    failed_round: dict[str, Any] | None = None
    for _ in range(10):
        if task["execution_phase"] == "AWAITING_PLAN_START":
            task = _start_planning(client, game_id, task)
        task = _ack_action(client, game_id, task)
        if task["execution_phase"] == "AWAITING_REPLAN_ACK":
            failed_round = task
            break
        if task["execution_phase"] == "AWAITING_DEBRIEF_ACK":
            task = _ack_debrief(client, game_id, task)
    assert failed_round is not None
    assert failed_round["id"] == original_task_id
    assert failed_round["debrief"]["knowledge_changes"]
    assert failed_round["debrief"]["success"] is False
    assert not any(event["kind"] == "PLAN_UPDATED" for event in failed_round["timeline"])
    initial_plan_event = next(
        event for event in failed_round["timeline"] if event["kind"] == "PLAN_CREATED"
    )
    assert any(
        change["key"] == "enemy_north_supply_route.supply_status"
        for change in failed_round["debrief"]["knowledge_changes"]
    )
    assert len(failed_round["plan_history"]) == 1
    replanned_response = client.post(
        f"/api/v1/games/{game_id}/play/replan",
        json={"expected_pacing_version": failed_round["pacing_version"]},
    )
    assert replanned_response.status_code == 200, replanned_response.text
    replanned = replanned_response.json()["current_task"]
    assert replanned["id"] == original_task_id
    assert replanned["execution_phase"] == "AWAITING_ACTION_ACK"
    assert replanned["debrief"] is None
    replanned_event = next(
        event for event in replanned["timeline"] if event["kind"] == "PLAN_UPDATED"
    )
    assert replanned_event["detail"] is not None
    assert initial_plan_event["detail"] is not None
    assert len(replanned["plan_history"]) >= 2
    old_plan = replanned["plan_history"][-2]
    old_plan_snapshot = dict(old_plan)
    new_plan = replanned["plan_history"][-1]
    assert old_plan["status"] == "ADJUSTED"
    assert old_plan["interruption"]["kind"] == "FAILURE"
    assert any(step["status"] == "FAILED" for step in old_plan["steps"])
    assert all(step["status"] in {"COMPLETED", "FAILED", "CANCELLED"} for step in old_plan["steps"])
    assert new_plan["ordinal"] == old_plan["ordinal"] + 1
    assert new_plan["status"] == "EXECUTING"
    assert "supply_status" not in str(new_plan)
    assert replanned["briefing"] is not None
    completed, _ = _drive_task(client, game_id, replanned)
    assert completed["status"] == "COMPLETED"
    assert completed["explanation"] is None
    assert (
        next(plan for plan in completed["plan_history"] if plan["id"] == old_plan_snapshot["id"])
        == old_plan_snapshot
    )
    completed_replanned_event = next(
        event for event in completed["timeline"] if event["id"] == replanned_event["id"]
    )
    assert completed_replanned_event["detail"] == replanned_event["detail"]
    persisted = session.get(AgentTask, UUID(original_task_id))
    assert persisted is not None
    assert persisted.objective_scope_keys == ["secure_northern_valley"]


def test_formal_play_revalidates_known_block_and_survives_restart(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FormalKnowledgeRevalidationProvider()
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    version = require_builtin_v2_version(session, LINJIANG_INFRASTRUCTURE_RECOVERY_V1)
    session.commit()
    game_id = _new_game(client, str(version.id))

    initial = client.get(f"/api/v1/games/{game_id}/play")
    assert initial.status_code == 200
    assert "west_freight_corridor.passable" not in initial.text
    task = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "恢复中央医院应急供电", "idempotency_key": str(uuid4())},
    ).json()["task"]
    task = _start_planning(client, game_id, task)
    assert task["execution_phase"] == "AWAITING_ACTION_ACK"
    assert task["briefing"]["action_name"]
    assert len(provider.requests) == 1

    interrupted = _ack_action(client, game_id, task)
    assert interrupted["execution_phase"] == "AWAITING_REPLAN_ACK"
    assert interrupted["debrief"]["success"] is False
    assert interrupted["debrief"]["plan_invalidated"] is False
    assert interrupted["debrief"]["knowledge_changes"]
    assert len(provider.requests) == 1
    operations = tuple(
        session.scalars(
            select(WorldOperation)
            .where(WorldOperation.game_instance_id == UUID(game_id))
            .order_by(WorldOperation.created_at)
        )
    )
    assert [operation.action_key for operation in operations] == ["travel"]
    assert interrupted["plan_history"][0]["status"] == "EXECUTING"
    interruption = interrupted["plan_history"][0]["interruption"]
    assert interruption["kind"] == "FAILURE"
    assert interruption["sequence"] == 1
    assert interruption["step_name"] == "前往区域"
    # A failed MAY_ATTEMPT travel reveals passability and stops this plan.
    assert interrupted["debrief"]["result_summary"]
    assert [step["status"] for step in interrupted["plan_history"][0]["steps"]] == [
        "FAILED",
        "PLANNED",
        "PLANNED",
    ]

    session.expire_all()
    after_restart = client.get(f"/api/v1/games/{game_id}/play")
    assert after_restart.status_code == 200
    reloaded = after_restart.json()["current_task"]
    assert reloaded["execution_phase"] == "AWAITING_REPLAN_ACK"
    assert reloaded["debrief"]["success"] is False
    assert len(provider.requests) == 1

    replanned_response = client.post(
        f"/api/v1/games/{game_id}/play/replan",
        json={"expected_pacing_version": reloaded["pacing_version"]},
    )
    assert replanned_response.status_code == 200, replanned_response.text
    replanned = replanned_response.json()["current_task"]
    assert replanned["execution_phase"] == "AWAITING_ACTION_ACK"
    assert replanned["briefing"]["action_name"]
    assert len(provider.requests) == 2
    assert provider.requests[1].call_type == "REPLAN"
    assert provider.requests[1].replan_reason == "TRAVEL_BLOCKED"

    completed, _rounds = _drive_task(client, game_id, replanned)
    assert completed["status"] == "COMPLETED"
    assert completed["execution_phase"] == "COMPLETED"
    terminal_event = next(
        event for event in completed["timeline"] if event["kind"] == "TASK_COMPLETED"
    )
    assert terminal_event["title"] == "目标已完成"
    assert terminal_event["detail"] == "恢复中央医院应急供电"
    operations = tuple(
        session.scalars(
            select(WorldOperation)
            .where(WorldOperation.game_instance_id == UUID(game_id))
            .order_by(WorldOperation.created_at)
        )
    )
    assert [operation.action_key for operation in operations] == [
        "travel",
        "clear_transport",
        "travel",
        "transport_resource",
        "repair_electrical",
    ]
    assert completed["plan_history"][0]["steps"][0]["status"] == "FAILED"


def test_trade_goal_advances_one_cycle_per_ack_and_preserves_scope(
    client: TestClient, session: Session
) -> None:
    scenario = next(
        item for item in client.get("/api/v1/scenarios").json() if item["key"] == "starfire_command"
    )
    game_id = _new_game(client, scenario["current_published_version_id"])
    task = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "open the northern trade route", "idempotency_key": str(uuid4())},
    ).json()["task"]
    task = _start_planning(client, game_id, task)
    first_briefing_id = task["briefing"]["step_id"]
    after_one = _ack_action(client, game_id, task)
    assert after_one["execution_phase"] in {"AWAITING_DEBRIEF_ACK", "AWAITING_REPLAN_ACK"}
    assert sum(event["kind"] == "ACTION_RESULT" for event in after_one["timeline"]) == 1
    assert after_one["debrief"]["step_id"] == first_briefing_id
    operation_count = session.scalar(
        select(func.count())
        .select_from(WorldOperation)
        .where(WorldOperation.game_instance_id == UUID(game_id))
    )
    fact_snapshot = tuple(
        session.execute(
            select(
                GameInstanceFactState.node_key,
                GameInstanceFactState.fact_key,
                GameInstanceFactState.truth_value,
            ).where(GameInstanceFactState.game_instance_id == UUID(game_id))
        )
    )

    if not after_one["debrief"]["success"]:
        replan_response = client.post(
            f"/api/v1/games/{game_id}/play/replan",
            json={"expected_pacing_version": after_one["pacing_version"]},
        )
        assert replan_response.status_code == 200, replan_response.text
        after_one = replan_response.json()["current_task"]
    elif after_one["execution_phase"] == "AWAITING_DEBRIEF_ACK":
        after_one = _ack_debrief(client, game_id, after_one)
    task = after_one
    assert (
        session.scalar(
            select(func.count())
            .select_from(WorldOperation)
            .where(WorldOperation.game_instance_id == UUID(game_id))
        )
        == operation_count
    )
    assert (
        tuple(
            session.execute(
                select(
                    GameInstanceFactState.node_key,
                    GameInstanceFactState.fact_key,
                    GameInstanceFactState.truth_value,
                ).where(GameInstanceFactState.game_instance_id == UUID(game_id))
            )
        )
        == fact_snapshot
    )
    completed, rounds = _drive_task(client, game_id, task)
    assert completed["status"] == "COMPLETED"
    assert len(rounds) >= 4
    assert all(stage["status"] == "COMPLETED" for stage in completed["roadmap"]["stages"])
    assert all(
        event["kind"] != "ACTION_BRIEFING" or "Wait" not in event["title"]
        for event in completed["timeline"]
    )
    persisted = session.get(AgentTask, UUID(completed["id"]))
    assert persisted is not None
    assert persisted.objective_scope_keys == ["open_northern_trade_route"]


def test_play_state_exposes_task_history_and_scopes_selected_task(
    client: TestClient, session: Session
) -> None:
    scenario = next(
        item for item in client.get("/api/v1/scenarios").json() if item["key"] == "starfire_command"
    )
    game_id = _new_game(client, scenario["current_published_version_id"])
    first = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "gather valley intelligence", "idempotency_key": str(uuid4())},
    ).json()["task"]
    completed, _ = _drive_task(client, game_id, first)
    assert completed["status"] == "COMPLETED"
    second = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "restore the starfire outpost", "idempotency_key": str(uuid4())},
    ).json()["task"]

    latest = client.get(f"/api/v1/games/{game_id}/play")
    assert latest.status_code == 200
    latest_state = latest.json()
    assert [item["id"] for item in latest_state["task_history"]] == [first["id"], second["id"]]
    assert latest_state["task_history"][0]["goal"] == "gather valley intelligence"
    assert latest_state["task_history"][0]["objective_names"] == ["收集北部山谷情报"]
    assert latest_state["task_history"][1]["goal"] == "restore the starfire outpost"
    assert latest_state["task_history"][1]["objective_names"] == ["恢复星火前哨"]
    assert latest_state["current_task"]["id"] == second["id"]
    assert latest_state["game"]["active_task_id"] == second["id"]

    historical = client.get(f"/api/v1/games/{game_id}/play?task_id={first['id']}")
    assert historical.status_code == 200
    historical_state = historical.json()
    assert historical_state["current_task"]["id"] == first["id"]
    assert historical_state["current_task"]["timeline"]
    assert historical_state["game"]["active_task_id"] == second["id"]
    assert historical_state["known_facts"] == latest_state["known_facts"]

    missing = client.get(f"/api/v1/games/{game_id}/play?task_id={uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_pacing_version_and_phase_are_server_enforced(client: TestClient) -> None:
    scenario = next(
        item for item in client.get("/api/v1/scenarios").json() if item["key"] == "starfire_command"
    )
    game_id = _new_game(client, scenario["current_published_version_id"])
    task = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "secure the northern valley", "idempotency_key": str(uuid4())},
    ).json()["task"]
    wrong_phase = client.post(
        f"/api/v1/games/{game_id}/play/acknowledge-debrief",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert wrong_phase.status_code == 409
    assert wrong_phase.json()["error"]["code"] == "PLAYER_PACING_PHASE_INVALID"
    task = _start_planning(client, game_id, task)
    advanced = _ack_action(client, game_id, task)
    stale = client.post(
        f"/api/v1/games/{game_id}/play/acknowledge-debrief",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "PLAYER_PACING_CONFLICT"
    assert advanced["pacing_version"] > task["pacing_version"]


def test_truly_unreachable_goal_stops_reliably(client: TestClient, session: Session) -> None:
    scenario = next(
        item for item in client.get("/api/v1/scenarios").json() if item["key"] == "starfire_command"
    )
    game_id = _new_game(client, scenario["current_published_version_id"])
    valley = session.get(GameInstanceNodeState, (UUID(game_id), "northern_valley"))
    assert valley is not None
    valley.status = NodeStatus.LOCKED
    session.flush()
    task = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "secure the northern valley", "idempotency_key": str(uuid4())},
    ).json()["task"]
    assert task["status"] == "ACTIVE"
    task = _start_planning(client, game_id, task)
    assert task["status"] == "UNREACHABLE_IN_CURRENT_STATE"
    assert task["execution_phase"] == "BLOCKED"


def test_unsupported_goal_does_not_create_a_task(client: TestClient) -> None:
    scenario = next(
        item for item in client.get("/api/v1/scenarios").json() if item["key"] == "starfire_command"
    )
    game_id = _new_game(client, scenario["current_published_version_id"])
    response = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "invent warp travel", "idempotency_key": str(uuid4())},
    )
    assert response.json()["status"] == "UNSUPPORTED"
    no_task = client.post(
        f"/api/v1/games/{game_id}/play/acknowledge-action",
        json={"expected_pacing_version": 1},
    )
    assert no_task.status_code == 409
    assert no_task.json()["error"]["code"] == "AGENT_TASK_NOT_ACTIVE"
    missing_task = client.post(f"/api/v1/games/{game_id}/tasks/{uuid4()}/abandon")
    assert missing_task.status_code in (404, 409)
    assert client.post(f"/api/v1/games/{game_id}/archive").status_code == 200
    archived_again = client.post(f"/api/v1/games/{game_id}/archive")
    assert archived_again.status_code == 200
    archived_goal = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "gather valley intelligence", "idempotency_key": str(uuid4())},
    )
    assert archived_goal.status_code == 409
    assert archived_goal.json()["error"]["code"] == "GAME_INSTANCE_READ_ONLY"


def test_play_resources_reject_unknown_identifiers(client: TestClient) -> None:
    missing = uuid4()
    assert client.get(f"/api/v1/games/{missing}/play").status_code == 404
    invalid_version = client.post(
        "/api/v1/games",
        json={"scenario_version_id": str(uuid4()), "idempotency_key": str(uuid4())},
    )
    assert invalid_version.status_code == 404
    assert invalid_version.json()["error"]["code"] == "SCENARIO_VERSION_NOT_FOUND"
