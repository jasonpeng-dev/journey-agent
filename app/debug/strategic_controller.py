from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.providers import build_provider
from app.agent.task_orchestrator import TaskOrchestrator
from app.core.config import Settings
from app.core.errors import AppError, NotFoundError
from app.domain.enums import AgentTaskStatus, MessageRole, SessionStatus, WorldOperationStatus
from app.infrastructure.db.models import (
    NPC,
    AgentTask,
    ConversationMessage,
    ConversationSession,
    WorldOperation,
)
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService

TERMINAL_TASK_STATUSES = {
    AgentTaskStatus.SUCCEEDED,
    AgentTaskStatus.FAILED,
    AgentTaskStatus.BLOCKED,
}
PAUSED_TASK_STATUSES = {
    AgentTaskStatus.REQUIRES_PLAYER_DECISION,
    AgentTaskStatus.WAITING_FOR_PLAYER_ACTION,
}


class StrategicDebugController:
    """Drives the existing orchestrator until the strategic flow reaches a real pause."""

    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.tasks = TaskService(db, max_replans=settings.planner_max_replans)

    def reset(self) -> dict[str, object]:
        player = GameService(self.db).create_player("Strategic Commander")
        player.level = 2
        player.gold = 80
        shen_ce = self.db.get(NPC, seed_id("npc:shen_ce"))
        if shen_ce is None:
            raise AppError(
                "WORLD_NOT_SEEDED",
                "Strategic officer content has not been seeded",
                status_code=503,
            )
        session = ConversationSession(player_id=player.id, npc_id=shen_ce.id)
        self.db.add(session)
        self.db.commit()
        return {
            "event": "STRATEGIC_SCENARIO_RESET",
            "scenario_key": "starfire_command",
            "player_id": str(player.id),
            "session_id": str(session.id),
            "commanding_officer": _officer_ref(shen_ce),
        }

    async def issue_command(
        self,
        session_id: UUID,
        command: str,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        session = self._strategic_session(session_id)
        existing = self._latest_task(session.player_id)
        if existing is not None and existing.status not in TERMINAL_TASK_STATUSES:
            task = existing
            start_event = "EXISTING_TASK"
            run = None
        else:
            self.db.add(
                ConversationMessage(
                    session_id=session.id,
                    role=MessageRole.USER,
                    content=command,
                )
            )
            self.db.commit()
            task, run, start_event = await TaskOrchestrator(
                self.db,
                build_provider(self.settings),
                self.settings,
            ).start(
                session,
                command,
                "starfire_command",
                planning_mode="PROVIDER",
            )
            response = (
                f"军令已接收。沈策已提出 Plan v{task.current_plan_version}, "
                "各部下将按职责和权限执行。"
                if start_event == "PLANNED"
                else f"军令规划停止: {task.last_error_code or start_event}。"
            )
            self.db.add(
                ConversationMessage(
                    session_id=session.id,
                    role=MessageRole.ASSISTANT,
                    content=response,
                    model_name=run.model if run is not None else "strategic-controller",
                    token_usage=run.token_usage if run is not None else 0,
                )
            )
            self.db.commit()
        events = await self.drive_until_pause(task.id, session)
        return {
            "event": start_event,
            "command_id": idempotency_key or f"command-{uuid4().hex}",
            "task_id": str(task.id),
            "transitions": events,
            "task_status": task.status.value,
        }

    async def resolve_decision(
        self,
        task_id: UUID,
        decision_id: UUID,
        session_id: UUID,
        option_id: str,
    ) -> dict[str, object]:
        session = self._strategic_session(session_id)
        task = self._strategic_task(task_id, session)
        decision, event = self.tasks.resolve_player_decision(
            task,
            decision_id,
            option_id,
        )
        self.db.commit()
        transitions = await self.drive_until_pause(task.id, session)
        return {
            "event": event,
            "decision_id": str(decision.id),
            "task_id": str(task.id),
            "transitions": transitions,
            "task_status": task.status.value,
        }

    async def resolve_world_event(
        self,
        task_id: UUID,
        operation_id: UUID,
        session_id: UUID,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        session = self._strategic_session(session_id)
        task = self._strategic_task(task_id, session)
        operation = self.db.get(WorldOperation, operation_id)
        if operation is None:
            raise NotFoundError("world_operation", operation_id)
        if operation.task_id != task.id or operation.player_id != session.player_id:
            raise AppError(
                "WORLD_EVENT_SCOPE_INVALID",
                "The world event does not belong to this strategic command",
                status_code=403,
            )
        resolution_key = idempotency_key or f"strategic-resolve-{operation.id}"
        operation = GameService(self.db).resolve_world_operation(operation.id, resolution_key)
        self.db.commit()
        transitions = await self.drive_until_pause(task.id, session)
        return {
            "event": "WORLD_EVENT_RESOLVED",
            "operation_id": str(operation.id),
            "outcome": operation.outcome,
            "task_id": str(task.id),
            "transitions": transitions,
            "task_status": task.status.value,
        }

    async def drive_until_pause(
        self,
        task_id: UUID,
        session: ConversationSession,
        *,
        max_transitions: int = 64,
    ) -> list[str]:
        orchestrator = TaskOrchestrator(
            self.db,
            build_provider(self.settings),
            self.settings,
        )
        events: list[str] = []
        for _ in range(max_transitions):
            task = self.tasks.get_task(task_id)
            if task.status in TERMINAL_TASK_STATUSES | PAUSED_TASK_STATUSES:
                return events
            if task.status == AgentTaskStatus.WAITING_FOR_WORLD_EVENT:
                operation = self.tasks.serialize(task).get("pending_world_event")
                if isinstance(operation, dict) and operation.get("status") == (
                    WorldOperationStatus.PENDING.value
                ):
                    return events
            task, _run, event = await orchestrator.advance(task.id, session)
            events.append(event)
            if event in {
                "REQUIRES_PLAYER_DECISION",
                "WAITING_FOR_PLAYER_ACTION",
                "WAITING_FOR_WORLD_EVENT",
                "BLOCKED",
                "TASK_SUCCEEDED",
                "ALREADY_SUCCEEDED",
            }:
                return events
        raise AppError(
            "STRATEGIC_DRIVE_LIMIT_REACHED",
            "The strategic command did not reach a safe pause within the transition limit",
            status_code=409,
        )

    def _strategic_session(self, session_id: UUID) -> ConversationSession:
        session = self.db.get(ConversationSession, session_id)
        if session is None:
            raise NotFoundError("session", session_id)
        if session.status != SessionStatus.ACTIVE:
            raise AppError("SESSION_CLOSED", "The strategic command session is closed")
        if session.npc_id != seed_id("npc:shen_ce"):
            raise AppError(
                "STRATEGIC_SESSION_OWNER_INVALID",
                "Strategic commands must be issued through Shen Ce",
                status_code=403,
            )
        return session

    def _strategic_task(
        self,
        task_id: UUID,
        session: ConversationSession,
    ) -> AgentTask:
        task = self.tasks.get_task(task_id, lock=True)
        if (
            task.player_id != session.player_id
            or task.scenario_key != "starfire_command"
            or task.owner_npc_id != seed_id("npc:shen_ce")
        ):
            raise AppError(
                "STRATEGIC_TASK_SCOPE_INVALID",
                "The task does not belong to this strategic command session",
                status_code=403,
            )
        self.tasks.bind_session(task, session)
        return task

    def _latest_task(self, player_id: UUID) -> AgentTask | None:
        return self.db.scalar(
            select(AgentTask)
            .where(
                AgentTask.player_id == player_id,
                AgentTask.scenario_key == "starfire_command",
            )
            .order_by(AgentTask.created_at.desc())
            .limit(1)
        )


def _officer_ref(officer: NPC) -> dict[str, object]:
    return {
        "id": str(officer.id),
        "key": officer.key,
        "name": officer.name,
        "role": officer.role.value,
    }
