"""Formal browser Play orchestration over the existing Generic services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import (
    GenericAgentError,
    GenericAgentService,
    GenericGoalResolution,
    GenericGoalResolver,
    proposal_signature,
)
from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    DecisionStatus,
    WorldOperationStatus,
)
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import StrictScalar
from app.infrastructure.db.models import (
    ActionDecisionRequest,
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
        try:
            task = self.agent.create_task(conversation, goal)
        except GenericAgentError as exc:
            if exc.code not in _UNREACHABLE_PLANNING_CODES:
                raise
            blocked_task = self._current_task()
            if blocked_task is None:
                raise
            task = blocked_task
            task.status = AgentTaskStatus.BLOCKED
            task.last_error_code = "UNREACHABLE_IN_CURRENT_STATE"
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
            try:
                self.agent.execute_next(task)
            except GenericAgentError as exc:
                if exc.code not in _UNREACHABLE_PLANNING_CODES:
                    raise
                task.status = AgentTaskStatus.BLOCKED
                task.last_error_code = "UNREACHABLE_IN_CURRENT_STATE"
                self.db.flush()
                return task
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

    def decide(
        self,
        decision_id: UUID,
        *,
        approve: bool,
        expected_task_version: int,
    ) -> AgentTask:
        require_scope_writable(self.db, self.scope.game_instance_id)
        decision = self.db.scalar(
            select(ActionDecisionRequest).where(
                ActionDecisionRequest.id == decision_id,
                ActionDecisionRequest.game_instance_id == self.scope.game_instance_id,
                ActionDecisionRequest.status == DecisionStatus.PENDING,
            )
        )
        if decision is None or decision.task_id is None:
            raise PlayError("ACTION_DECISION_INVALID", "Approval is absent or not pending")
        task = self.db.get(AgentTask, decision.task_id)
        if task is None or task.version != expected_task_version:
            raise PlayError("AGENT_TASK_CONFLICT", "The Task changed before this decision")
        GenericActionService(self.db, self.scope).decide(decision.id, approve=approve)
        task.version += 1
        task.status = AgentTaskStatus.ACTIVE
        if approve:
            return self.advance_until_pause(task)
        signature = proposal_signature(
            decision.actor_key,
            decision.action_key,
            decision.target_key,
            cast(dict[str, StrictScalar], decision.parameters),
        )
        task.rejected_proposal_signatures = [
            *task.rejected_proposal_signatures,
            signature,
        ]
        step = self.db.get(AgentStep, decision.source_step_id) if decision.source_step_id else None
        if step is not None:
            step.status = AgentStepStatus.FAILED
            step.failure_code = "PLAYER_REJECTED"
        try:
            self.agent.plan(task, reason="PLAYER_REJECTED")
        except GenericAgentError as exc:
            if exc.code not in _UNREACHABLE_PLANNING_CODES:
                raise
            task.status = AgentTaskStatus.BLOCKED
            task.last_error_code = "BLOCKED_BY_PLAYER_DECISION"
            self.db.flush()
            return task
        return self.advance_until_pause(task)

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
_UNREACHABLE_PLANNING_CODES = {
    "GENERIC_PLAN_NOT_FOUND",
    "GENERIC_PLAN_PARAMETER_REQUIRED",
    "GENERIC_PROVIDER_PLAN_INVALID",
    "GENERIC_REPLAN_LIMIT",
}

__all__ = ["GoalSubmission", "PlayError", "PlayOrchestrator"]
