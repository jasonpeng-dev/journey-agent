"""Formal browser Play orchestration over the existing Generic services."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService, GenericGoalResolution, GenericGoalResolver
from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    WorldOperationStatus,
)
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import (
    AgentPlan,
    AgentStep,
    AgentTask,
    ConversationSession,
    WorldOperation,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import require_scope_writable
from app.services.generic_actions import GenericActionService


class PlayError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class GoalSubmission:
    resolution: GenericGoalResolution
    task: AgentTask | None
    replayed: bool = False


class PlayOrchestrator:
    """Advance a Task until completion or a durable player-facing pause."""

    MAX_TRANSITIONS = 50

    def __init__(self, db: Session, game_instance_id: GameInstanceId) -> None:
        self.db = db
        self.scope = GameInstanceService(db).load(game_instance_id)
        self.agent = GenericAgentService(db, self.scope)

    def submit_goal(self, goal: str, *, idempotency_key: str) -> GoalSubmission:
        require_scope_writable(self.db, self.scope.game_instance_id)
        if not idempotency_key.strip():
            raise PlayError("GOAL_IDEMPOTENCY_KEY_REQUIRED", "Goal requires an idempotency key")
        existing = self.db.scalar(
            select(AgentTask).where(
                AgentTask.game_instance_id == self.scope.game_instance_id,
                AgentTask.submission_idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.goal_description != goal:
                raise PlayError(
                    "GOAL_IDEMPOTENCY_CONFLICT",
                    "The idempotency key is bound to another Goal",
                )
            return GoalSubmission(
                GenericGoalResolution(
                    "RESOLVED",
                    objective_keys=tuple(existing.objective_scope_keys or ()),
                ),
                existing,
                True,
            )
        definition = (
            ScenarioVersionRepository(self.db).load(self.scope.scenario_version_id).definition
        )
        resolution = GenericGoalResolver().resolve(goal, definition)
        if resolution.status != "RESOLVED":
            return GoalSubmission(resolution, None)
        conversation = self.db.scalar(
            select(ConversationSession).where(
                ConversationSession.game_instance_id == self.scope.game_instance_id,
                ConversationSession.actor_key.is_not(None),
            )
        )
        if conversation is None:
            raise PlayError("PLAY_SESSION_NOT_FOUND", "The Game has no playable Actor session")
        task = self.agent.create_task(conversation, goal)
        task.submission_idempotency_key = idempotency_key
        self.db.flush()
        self.advance_until_pause(task)
        return GoalSubmission(resolution, task)

    def continue_current(self) -> AgentTask:
        require_scope_writable(self.db, self.scope.game_instance_id)
        task = self._current_task()
        if task is None:
            raise PlayError("AGENT_TASK_NOT_ACTIVE", "The Game has no active Task")
        self.advance_until_pause(task)
        return task

    def advance_until_pause(self, task: AgentTask) -> AgentTask:
        for _transition in range(self.MAX_TRANSITIONS):
            if task.status in _PAUSE_OR_TERMINAL:
                self._finalize_plan(task)
                return task
            self.agent.execute_next(task)
            if task.status == AgentTaskStatus.WAITING_FOR_WORLD_EVENT:
                operation = self.db.scalar(
                    select(WorldOperation)
                    .where(
                        WorldOperation.game_instance_id == self.scope.game_instance_id,
                        WorldOperation.task_id == task.id,
                        WorldOperation.status == WorldOperationStatus.PENDING,
                    )
                    .order_by(WorldOperation.created_at.desc())
                )
                if operation is None:
                    return task
                GenericActionService(self.db, self.scope).resolve_operation(
                    operation.id, resolution_key=f"formal-play:{operation.id}"
                )
                task.status = AgentTaskStatus.ACTIVE
                self.db.flush()
        raise PlayError("PLAY_TRANSITION_LIMIT", "Formal Play reached its safety bound")

    def _finalize_plan(self, task: AgentTask) -> None:
        if task.status != AgentTaskStatus.SUCCEEDED:
            return
        plan = self.db.scalar(
            select(AgentPlan).where(
                AgentPlan.task_id == task.id,
                AgentPlan.status == AgentPlanStatus.ACTIVE,
            )
        )
        if plan is None:
            return
        for step in self.db.scalars(
            select(AgentStep).where(
                AgentStep.plan_id == plan.id,
                AgentStep.status.in_(
                    (
                        AgentStepStatus.PENDING,
                        AgentStepStatus.WAITING_FOR_WORLD_EVENT,
                        AgentStepStatus.WAITING_FOR_PLAYER_ACTION,
                    )
                ),
            )
        ):
            step.status = AgentStepStatus.SKIPPED
        plan.status = AgentPlanStatus.SUCCEEDED
        self.db.flush()

    def _current_task(self) -> AgentTask | None:
        return self.db.scalar(
            select(AgentTask)
            .where(
                AgentTask.game_instance_id == self.scope.game_instance_id,
                AgentTask.status.in_(_ACTIVE_STATUSES),
            )
            .order_by(AgentTask.created_at.desc())
        )


_ACTIVE_STATUSES = (
    AgentTaskStatus.ACTIVE,
    AgentTaskStatus.REQUIRES_PLAYER_DECISION,
    AgentTaskStatus.WAITING_FOR_PLAYER_ACTION,
    AgentTaskStatus.WAITING_FOR_WORLD_EVENT,
)
_PAUSE_OR_TERMINAL = (
    AgentTaskStatus.REQUIRES_PLAYER_DECISION,
    AgentTaskStatus.WAITING_FOR_PLAYER_ACTION,
    AgentTaskStatus.SUCCEEDED,
    AgentTaskStatus.FAILED,
    AgentTaskStatus.BLOCKED,
    AgentTaskStatus.ABORTED,
)

__all__ = ["GoalSubmission", "PlayError", "PlayOrchestrator"]
