from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import AgentPlanStatus, AgentStepStatus
from app.infrastructure.db.models import AgentPlan, AgentStep
from app.scenarios.builtin import require_builtin_v2_version
from tests.scenario_fixtures import GENERIC_TEST


def _new_game(client: TestClient, session: Session) -> str:
    version = require_builtin_v2_version(session, GENERIC_TEST)
    session.commit()
    response = client.post(
        "/api/v1/games",
        json={"scenario_version_id": str(version.id), "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _accepted_task(client: TestClient, game_id: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]


def _start_planning(client: TestClient, game_id: str, task: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/games/{game_id}/play/start-planning",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["current_task"]


def _continuous(client: TestClient, game_id: str, task: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/games/{game_id}/play/run-until-boundary",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["current_task"]


def test_continuous_execution_runs_current_plan_to_completion(
    client: TestClient, session: Session
) -> None:
    game_id = _new_game(client, session)
    task = _start_planning(client, game_id, _accepted_task(client, game_id))

    completed = _continuous(client, game_id, task)

    assert completed["status"] == "COMPLETED"
    assert completed["execution_phase"] == "COMPLETED"
    history = completed["plan_history"][-1]
    assert history["completed_steps"] == history["total_steps"] == 2
    plan = session.scalar(
        select(AgentPlan).where(
            AgentPlan.task_id == UUID(completed["id"]),
            AgentPlan.status == AgentPlanStatus.SUCCEEDED,
        )
    )
    assert plan is not None
    steps = session.scalars(
        select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
    ).all()
    assert [step.status for step in steps] == [AgentStepStatus.SUCCEEDED] * 2


def test_continuous_execution_starts_after_a_manually_completed_step(
    client: TestClient, session: Session
) -> None:
    game_id = _new_game(client, session)
    task = _start_planning(client, game_id, _accepted_task(client, game_id))
    first = client.post(
        f"/api/v1/games/{game_id}/play/acknowledge-action",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert first.status_code == 200, first.text
    after_first = first.json()["current_task"]
    assert after_first["execution_phase"] == "AWAITING_DEBRIEF_ACK"
    continued = client.post(
        f"/api/v1/games/{game_id}/play/acknowledge-debrief",
        json={"expected_pacing_version": after_first["pacing_version"]},
    )
    assert continued.status_code == 200, continued.text
    ready = continued.json()["current_task"]

    completed = _continuous(client, game_id, ready)

    assert completed["status"] == "COMPLETED"
    history = completed["plan_history"][-1]
    assert history["completed_steps"] == history["total_steps"] == 2
    plan = session.scalar(
        select(AgentPlan).where(
            AgentPlan.task_id == UUID(completed["id"]),
            AgentPlan.status == AgentPlanStatus.SUCCEEDED,
        )
    )
    assert plan is not None
    steps = session.scalars(
        select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
    ).all()
    assert [step.attempts for step in steps] == [1, 1]


def test_continuous_execution_stops_at_current_plan_boundary_without_replanning(
    client: TestClient, session: Session
) -> None:
    game_id = _new_game(client, session)
    task = _start_planning(client, game_id, _accepted_task(client, game_id))
    task_id = UUID(task["id"])
    plan = session.scalar(
        select(AgentPlan).where(
            AgentPlan.task_id == task_id,
            AgentPlan.status == AgentPlanStatus.ACTIVE,
        )
    )
    assert plan is not None
    steps = session.scalars(
        select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
    ).all()
    assert len(steps) == 2
    plan.stop_reason = "INFORMATION_BOUNDARY"
    steps[1].status = AgentStepStatus.SKIPPED
    session.flush()

    stopped = _continuous(client, game_id, task)

    assert stopped["status"] == "ACTIVE"
    assert stopped["execution_phase"] == "AWAITING_REPLAN_ACK"
    assert stopped["plan_history"][-1]["completed_steps"] == 1
    assert (
        session.scalar(
            select(AgentPlan).where(
                AgentPlan.task_id == task_id,
                AgentPlan.status == AgentPlanStatus.ACTIVE,
                AgentPlan.version == plan.version,
            )
        )
        is not None
    )


def test_continuous_execution_stops_on_failure_without_running_later_steps(
    client: TestClient, session: Session
) -> None:
    game_id = _new_game(client, session)
    task = _start_planning(client, game_id, _accepted_task(client, game_id))
    task_id = UUID(task["id"])
    plan = session.scalar(
        select(AgentPlan).where(
            AgentPlan.task_id == task_id,
            AgentPlan.status == AgentPlanStatus.ACTIVE,
        )
    )
    assert plan is not None
    steps = session.scalars(
        select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
    ).all()
    assert len(steps) == 2
    steps[0].action_intent = "treat_patient"
    steps[0].tool_arguments = {
        **steps[0].tool_arguments,
        "action_key": "treat_patient",
        "parameters": {"dosage": 2},
    }
    session.flush()

    stopped = _continuous(client, game_id, task)

    assert stopped["status"] == "ACTIVE"
    assert stopped["execution_phase"] == "AWAITING_REPLAN_ACK"
    persisted_steps = session.scalars(
        select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
    ).all()
    assert persisted_steps[0].status == AgentStepStatus.FAILED
    assert persisted_steps[1].status == AgentStepStatus.SKIPPED
    assert plan.status == AgentPlanStatus.SUPERSEDED
