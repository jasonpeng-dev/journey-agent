from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.providers import build_provider
from app.agent.task_orchestrator import TaskOrchestrator
from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.domain.enums import SessionStatus
from app.infrastructure.db.models import (
    NPC,
    AgentRun,
    AgentTask,
    ConversationSession,
    PlayerDecisionRequest,
    ToolExecution,
    WorldOperation,
)
from app.infrastructure.db.session import get_db
from app.services.tasks import TaskService

router = APIRouter(prefix="/api/v1", tags=["tasks"])


class StrictTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCreate(StrictTaskRequest):
    session_id: UUID
    goal_description: str = Field(min_length=10, max_length=1000)
    scenario_key: Literal["starfire_outpost", "starfire_command"] = "starfire_outpost"
    planning_mode: Literal["DETERMINISTIC_BASELINE", "PROVIDER"] = "PROVIDER"


class TaskAdvance(StrictTaskRequest):
    session_id: UUID


class DecisionResolve(StrictTaskRequest):
    session_id: UUID
    option_id: str


@router.post("/tasks", status_code=201)
async def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> dict[str, object]:
    session = _active_session(db, payload.session_id)
    settings = get_settings()
    task, run, event = await TaskOrchestrator(db, build_provider(settings), settings).start(
        session,
        payload.goal_description,
        payload.scenario_key,
        planning_mode=payload.planning_mode,
    )
    return {
        "event": event,
        "agent_run_id": str(run.id) if run else None,
        "task": TaskService(db).serialize(task),
    }


@router.post("/tasks/{task_id}/advance")
async def advance_task(
    task_id: UUID,
    payload: TaskAdvance,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = _active_session(db, payload.session_id)
    settings = get_settings()
    task, run, event = await TaskOrchestrator(db, build_provider(settings), settings).advance(
        task_id, session
    )
    return {
        "event": event,
        "agent_run_id": str(run.id) if run else None,
        "task": TaskService(db).serialize(task),
    }


@router.post("/tasks/{task_id}/decisions/{decision_id}/resolve")
def resolve_task_decision(
    task_id: UUID,
    decision_id: UUID,
    payload: DecisionResolve,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    session = _active_session(db, payload.session_id)
    service = TaskService(db)
    task = service.get_task(task_id, lock=True)
    service.bind_session(task, session)
    decision, event = service.resolve_player_decision(task, decision_id, payload.option_id)
    db.commit()
    return {
        "event": event,
        "decision_id": str(decision.id),
        "task": service.serialize(task),
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: UUID, db: Session = Depends(get_db)) -> dict[str, object]:
    return TaskService(db).serialize(TaskService(db).get_task(task_id))


@router.get("/players/{player_id}/tasks")
def list_player_tasks(player_id: UUID, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    tasks = db.scalars(
        select(AgentTask)
        .where(AgentTask.player_id == player_id)
        .order_by(AgentTask.created_at.desc())
    ).all()
    service = TaskService(db)
    return [service.serialize(task) for task in tasks]


@router.get("/tasks/{task_id}/trace")
def task_trace(task_id: UUID, db: Session = Depends(get_db)) -> dict[str, object]:
    task = TaskService(db).get_task(task_id)
    runs = db.scalars(
        select(AgentRun).where(AgentRun.task_id == task.id).order_by(AgentRun.started_at)
    ).all()
    return {
        "task": TaskService(db).serialize(task),
        "runs": [
            {
                "id": str(run.id),
                "session_id": str(run.session_id),
                "plan_id": str(run.plan_id) if run.plan_id else None,
                "step_id": str(run.step_id) if run.step_id else None,
                "status": run.status.value,
                "model": run.model,
                "actual_rounds": run.actual_rounds,
                "token_usage": run.token_usage,
                "termination_reason": (
                    run.termination_reason.value if run.termination_reason else None
                ),
                "purpose": run.purpose,
                "actor_officer": _officer_ref(
                    db.get(NPC, run.actor_npc_id) if run.actor_npc_id else None
                ),
                "actor_officer_id": str(run.actor_npc_id) if run.actor_npc_id else None,
                "officer_profile_version": run.officer_profile_version,
                "authority_policy_version": run.authority_policy_version,
                "structured_output": run.structured_output,
                "validation_status": run.validation_status,
                "validation_errors": run.validation_errors,
                "tools": [
                    {
                        "id": str(trace.id),
                        "step_id": str(trace.step_id) if trace.step_id else None,
                        "tool_name": trace.tool_name,
                        "arguments": trace.arguments,
                        "validation_status": trace.validation_status,
                        "authorization_status": trace.authorization_status,
                        "authority_details": trace.authority_details,
                        "business_rule_status": trace.business_rule_status,
                        "execution_status": trace.execution_status,
                        "result": trace.result,
                        "error_code": trace.error_code,
                        "before_state": trace.before_state,
                        "after_state": trace.after_state,
                        "duration_ms": trace.duration_ms,
                    }
                    for trace in db.scalars(
                        select(ToolExecution)
                        .where(ToolExecution.agent_run_id == run.id)
                        .order_by(ToolExecution.created_at)
                    ).all()
                ],
            }
            for run in runs
        ],
        "decisions": [
            {
                "id": str(item.id),
                "step_id": str(item.step_id),
                "requested_by_officer": _officer_ref(db.get(NPC, item.requested_by_npc_id)),
                "status": item.status.value,
                "decision_kind": item.decision_kind,
                "summary": item.summary,
                "options": item.options,
                "action_tool_name": item.action_tool_name,
                "action_arguments": item.action_arguments,
                "selected_option": item.selected_option,
                "policy_snapshot": item.policy_snapshot,
                "resolved_at": item.resolved_at,
                "consumed_at": item.consumed_at,
            }
            for item in db.scalars(
                select(PlayerDecisionRequest)
                .where(PlayerDecisionRequest.task_id == task.id)
                .order_by(PlayerDecisionRequest.created_at)
            ).all()
        ],
        "world_events": [
            {
                "id": str(item.id),
                "source_step_id": str(item.source_step_id) if item.source_step_id else None,
                "initiated_by_officer": _officer_ref(db.get(NPC, item.officer_npc_id)),
                "event_type": f"{item.operation_type}_RESOLVED",
                "operation_type": item.operation_type,
                "target_key": item.target_key,
                "status": item.status.value,
                "parameters": item.parameters,
                "outcome": item.outcome,
            }
            for item in db.scalars(
                select(WorldOperation)
                .where(WorldOperation.task_id == task.id)
                .order_by(WorldOperation.created_at)
            ).all()
        ],
    }


def _active_session(db: Session, session_id: UUID) -> ConversationSession:
    session = db.get(ConversationSession, session_id)
    if session is None:
        raise NotFoundError("session", session_id)
    if session.status != SessionStatus.ACTIVE:
        raise AppError("SESSION_CLOSED", "Conversation session is closed")
    return session


def _officer_ref(officer: NPC | None) -> dict[str, object] | None:
    if officer is None:
        return None
    return {
        "id": str(officer.id),
        "key": officer.key,
        "name": officer.name,
        "role": officer.role.value,
    }
