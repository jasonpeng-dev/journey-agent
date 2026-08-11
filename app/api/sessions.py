from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.orchestrator import AgentOrchestrator
from app.agent.providers import build_provider
from app.agent.task_orchestrator import TaskOrchestrator
from app.agent.task_router import TaskRouter
from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError
from app.domain.enums import MemoryType, MessageRole, SessionStatus
from app.infrastructure.db.models import (
    NPC,
    AgentRun,
    ConversationMessage,
    ConversationSession,
    Memory,
    ToolExecution,
)
from app.infrastructure.db.session import get_db
from app.services.game import GameService
from app.services.tasks import TaskService

router = APIRouter(prefix="/api/v1")


class SessionCreate(BaseModel):
    player_id: UUID
    npc_id: UUID


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


@router.post("/sessions", status_code=201)
def create_session(payload: SessionCreate, db: Session = Depends(get_db)) -> dict[str, object]:
    GameService(db).get_player(payload.player_id)
    npc = db.get(NPC, payload.npc_id)
    if not npc or not npc.enabled:
        raise NotFoundError("npc", payload.npc_id)
    session = ConversationSession(player_id=payload.player_id, npc_id=payload.npc_id)
    db.add(session)
    db.commit()
    return {"id": session.id, "status": session.status}


@router.get("/players/{player_id}/sessions")
def list_player_sessions(
    player_id: UUID,
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    GameService(db).get_player(player_id)
    sessions = db.scalars(
        select(ConversationSession)
        .where(ConversationSession.player_id == player_id)
        .order_by(ConversationSession.created_at.desc())
        .limit(limit)
    ).all()
    history: list[dict[str, object]] = []
    for session in sessions:
        npc = db.get(NPC, session.npc_id)
        latest_message = db.scalar(
            select(ConversationMessage)
            .where(ConversationMessage.session_id == session.id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(1)
        )
        latest_run = db.scalar(
            select(AgentRun)
            .where(AgentRun.session_id == session.id)
            .order_by(AgentRun.started_at.desc())
            .limit(1)
        )
        message_count = db.scalar(
            select(func.count())
            .select_from(ConversationMessage)
            .where(ConversationMessage.session_id == session.id)
        )
        history.append(
            {
                "id": session.id,
                "player_id": session.player_id,
                "npc_id": session.npc_id,
                "npc_name": npc.name if npc else "Unknown NPC",
                "npc_role": npc.role if npc else None,
                "status": session.status,
                "summary": session.summary,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "message_count": int(message_count or 0),
                "latest_message_preview": (
                    latest_message.content[:120] if latest_message is not None else ""
                ),
                "latest_run_id": latest_run.id if latest_run is not None else None,
            }
        )
    return history


@router.get("/sessions/{session_id}")
def get_session(session_id: UUID, db: Session = Depends(get_db)) -> dict[str, object]:
    session = db.get(ConversationSession, session_id)
    if not session:
        raise NotFoundError("session", session_id)
    return {
        "id": session.id,
        "player_id": session.player_id,
        "npc_id": session.npc_id,
        "status": session.status,
        "summary": session.summary,
    }


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: UUID, payload: MessageCreate, db: Session = Depends(get_db)
) -> dict[str, object]:
    session = db.get(ConversationSession, session_id)
    if not session:
        raise NotFoundError("session", session_id)
    if session.status != SessionStatus.ACTIVE:
        raise AppError("SESSION_CLOSED", "Conversation session is closed")
    settings = get_settings()
    route = TaskRouter().route(payload.content)
    if route.mode == "STRUCTURED_TASK" and route.scenario_key is not None:
        user_message = ConversationMessage(
            session_id=session.id,
            role=MessageRole.USER,
            content=payload.content,
        )
        db.add(user_message)
        db.commit()
        task, run, event = await TaskOrchestrator(
            db,
            build_provider(settings),
            settings,
        ).start(
            session,
            payload.content,
            route.scenario_key,
            planning_mode="PROVIDER",
        )
        current_plan = TaskService(db).current_plan(task)
        if event == "PLANNED" and current_plan is not None:
            content = (
                f"I created a structured task and accepted Plan v{current_plan.version} "
                "after backend validation. You can now advance its audited steps."
            )
        elif event == "EXISTING_TASK":
            content = "This goal already has an active structured task; no duplicate was created."
        else:
            content = (
                "I could not accept a safe executable plan for this goal. "
                f"Planning stopped with {task.last_error_code or event}."
            )
        db.add(
            ConversationMessage(
                session_id=session.id,
                role=MessageRole.ASSISTANT,
                content=content,
                model_name=run.model if run is not None else "task-router",
                token_usage=run.token_usage if run is not None else 0,
            )
        )
        db.commit()
        player = GameService(db).get_player(session.player_id)
        return {
            "message": {"role": "assistant", "content": content},
            "agent_run_id": run.id if run is not None else None,
            "status": run.status if run is not None else "COMPLETED",
            "state_version": player.version,
            "route_mode": route.mode,
            "route_reason_code": route.reason_code,
            "task_id": task.id,
            "task_event": event,
        }
    content, run = await AgentOrchestrator(db, build_provider(settings), settings).run(
        session, payload.content
    )
    player = GameService(db).get_player(session.player_id)
    return {
        "message": {"role": "assistant", "content": content},
        "agent_run_id": run.id,
        "status": run.status,
        "state_version": player.version,
        "route_mode": route.mode,
        "route_reason_code": route.reason_code,
        "task_id": None,
        "task_event": None,
    }


@router.get("/sessions/{session_id}/messages")
def list_messages(session_id: UUID, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.session_id == session_id)
        .order_by(ConversationMessage.created_at)
    ).all()
    return [
        {"id": item.id, "role": item.role, "content": item.content, "created_at": item.created_at}
        for item in messages
    ]


@router.post("/sessions/{session_id}/close")
def close_session(session_id: UUID, db: Session = Depends(get_db)) -> dict[str, object]:
    session = db.get(ConversationSession, session_id)
    if not session:
        raise NotFoundError("session", session_id)
    messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.session_id == session_id)
        .order_by(ConversationMessage.created_at.desc())
        .limit(6)
    ).all()
    session.summary = " | ".join(item.content[:160] for item in reversed(messages))[:1000]
    session.status = SessionStatus.CLOSED
    db.add(
        Memory(
            player_id=session.player_id,
            npc_id=session.npc_id,
            type=MemoryType.CONVERSATION_SUMMARY,
            content=session.summary or "Conversation closed without messages.",
            importance=5,
            source_session_id=session.id,
        )
    )
    db.commit()
    return {"id": session.id, "status": session.status, "summary": session.summary}


@router.get("/agent-runs/{run_id}")
def get_run(run_id: UUID, db: Session = Depends(get_db)) -> dict[str, object]:
    run = db.get(AgentRun, run_id)
    if not run:
        raise NotFoundError("agent_run", run_id)
    return _run(run)


@router.get("/agent-runs/{run_id}/tool-executions")
def get_tool_executions(run_id: UUID, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    traces = db.scalars(
        select(ToolExecution)
        .where(ToolExecution.agent_run_id == run_id)
        .order_by(ToolExecution.created_at)
    ).all()
    return [_trace(item) for item in traces]


@router.get("/sessions/{session_id}/trace")
def session_trace(session_id: UUID, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    runs = db.scalars(
        select(AgentRun).where(AgentRun.session_id == session_id).order_by(AgentRun.started_at)
    ).all()
    return [
        {
            "run": _run(run),
            "tools": [
                _trace(item)
                for item in db.scalars(
                    select(ToolExecution)
                    .where(ToolExecution.agent_run_id == run.id)
                    .order_by(ToolExecution.created_at)
                ).all()
            ],
        }
        for run in runs
    ]


def _run(run: AgentRun) -> dict[str, object]:
    return {
        "id": run.id,
        "request_id": run.request_id,
        "session_id": run.session_id,
        "task_id": run.task_id,
        "plan_id": run.plan_id,
        "step_id": run.step_id,
        "actor_officer_id": run.actor_npc_id,
        "actor_npc_id": run.actor_npc_id,
        "officer_profile_version": run.officer_profile_version,
        "authority_policy_version": run.authority_policy_version,
        "input_message": run.input_message,
        "status": run.status,
        "model": run.model,
        "actual_rounds": run.actual_rounds,
        "token_usage": run.token_usage,
        "model_rounds": run.model_rounds,
        "termination_reason": run.termination_reason,
        "purpose": run.purpose,
        "structured_output": run.structured_output,
        "validation_status": run.validation_status,
        "validation_errors": run.validation_errors,
    }


def _trace(item: ToolExecution) -> dict[str, object]:
    return {
        "id": item.id,
        "step_id": item.step_id,
        "tool_call_id": item.tool_call_id,
        "tool_name": item.tool_name,
        "arguments": item.arguments,
        "validation_status": item.validation_status,
        "authorization_status": item.authorization_status,
        "authority_details": item.authority_details,
        "business_rule_status": item.business_rule_status,
        "execution_status": item.execution_status,
        "result": item.result,
        "error_code": item.error_code,
        "before_state": item.before_state,
        "after_state": item.after_state,
        "duration_ms": item.duration_ms,
    }
