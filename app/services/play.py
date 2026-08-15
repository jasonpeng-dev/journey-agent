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
from app.agent.provider import GenericModelProvider
from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    DecisionStatus,
    StepExecutionType,
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
    PlayerExecutionCheckpoint,
    WorldOperation,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import require_scope_writable
from app.services.generic_actions import GenericActionService
from app.services.player_pacing import PlayerExecutionPhase


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
    """Phase D pacing over the unchanged Generic Agent and Action services."""

    MAX_TRANSITIONS = 50

    def __init__(
        self,
        db: Session,
        game_instance_id: GameInstanceId,
        *,
        provider: GenericModelProvider | None = None,
    ) -> None:
        self.db = db
        self.scope = GameInstanceService(db).load(game_instance_id)
        self.goal_resolver = GenericGoalResolver(provider=provider)
        self.agent = GenericAgentService(
            db,
            self.scope,
            goal_resolver=self.goal_resolver,
            provider=provider,
        )

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
            self._ensure_checkpoint(existing)
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
        resolution = self.goal_resolver.resolve(goal, definition)
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
            task = self.agent.create_task(conversation, goal, resolved_goal=resolution)
        except GenericAgentError as exc:
            if exc.code not in (*_UNREACHABLE_PLANNING_CODES, *_MODEL_PLAN_CODES):
                raise
            blocked_task = self._current_task()
            if blocked_task is None:
                raise
            task = blocked_task
            task.status = AgentTaskStatus.BLOCKED
            task.last_error_code = (
                "MODEL_PLAN_REJECTED"
                if exc.code in _MODEL_PLAN_CODES
                else "UNREACHABLE_IN_CURRENT_STATE"
            )
        task.submission_idempotency_key = idempotency_key
        self._ensure_checkpoint(task)
        self.db.flush()
        return GoalSubmission(resolution, task)

    def acknowledge_action(self, *, expected_pacing_version: int) -> AgentTask:
        require_scope_writable(self.db, self.scope.game_instance_id)
        task = self._current_task()
        if task is None:
            raise PlayError("AGENT_TASK_NOT_ACTIVE", "The Game has no active Task")
        checkpoint = self._checkpoint(task, expected_pacing_version=expected_pacing_version)
        if checkpoint.phase != PlayerExecutionPhase.AWAITING_ACTION_ACK:
            raise PlayError(
                "PLAYER_PACING_PHASE_INVALID",
                "The Task is not waiting for action acknowledgement",
            )
        action_step = self._next_action_step(task)
        if action_step is None:
            self._block_unreachable(task, checkpoint)
            return task
        self._execute_action_cycle(task, action_step)
        checkpoint.last_action_step_id = action_step.id
        checkpoint.phase = self._phase_after_cycle(task)
        checkpoint.version += 1
        self.db.flush()
        return task

    def acknowledge_debrief(self, *, expected_pacing_version: int) -> AgentTask:
        require_scope_writable(self.db, self.scope.game_instance_id)
        task = self._current_task()
        if task is None:
            raise PlayError("AGENT_TASK_NOT_ACTIVE", "The Game has no active Task")
        checkpoint = self._checkpoint(task, expected_pacing_version=expected_pacing_version)
        if checkpoint.phase != PlayerExecutionPhase.AWAITING_DEBRIEF_ACK:
            raise PlayError(
                "PLAYER_PACING_PHASE_INVALID",
                "The Task is not waiting for debrief acknowledgement",
            )
        # This is only a presentation transition.  All gameplay work, including
        # any required replan, was completed inside the preceding action cycle.
        if self._next_action_step(task) is None:
            self._block_unreachable(task, checkpoint)
            return task
        checkpoint.phase = PlayerExecutionPhase.AWAITING_ACTION_ACK
        checkpoint.version += 1
        self.db.flush()
        return task

    def advance_sandbox_until_pause(self, task: AgentTask) -> AgentTask:
        """Auto-drive action checkpoints for the isolated Draft sandbox only."""

        checkpoint = self._ensure_checkpoint(task)
        for _transition in range(self.MAX_TRANSITIONS):
            phase = PlayerExecutionPhase(checkpoint.phase)
            if phase in _PRODUCT_TERMINAL or phase == PlayerExecutionPhase.APPROVAL_REQUIRED:
                return task
            if phase == PlayerExecutionPhase.AWAITING_ACTION_ACK:
                self.acknowledge_action(expected_pacing_version=checkpoint.version)
            else:
                self.acknowledge_debrief(expected_pacing_version=checkpoint.version)
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
        checkpoint = self._ensure_checkpoint(task)
        if approve:
            step = (
                self.db.get(AgentStep, decision.source_step_id) if decision.source_step_id else None
            )
            if step is None:
                raise PlayError("ACTION_DECISION_INVALID", "Approval has no executable Action")
            self._execute_action_cycle(task, step)
            checkpoint.last_action_step_id = step.id
            checkpoint.phase = self._phase_after_cycle(task)
            checkpoint.version += 1
            self.db.flush()
            return task
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
            if exc.code not in (*_UNREACHABLE_PLANNING_CODES, *_MODEL_PLAN_CODES):
                raise
            task.status = AgentTaskStatus.BLOCKED
            task.last_error_code = (
                "MODEL_PLAN_REJECTED"
                if exc.code in _MODEL_PLAN_CODES
                else "BLOCKED_BY_PLAYER_DECISION"
            )
            checkpoint.phase = PlayerExecutionPhase.BLOCKED
            checkpoint.version += 1
            self.db.flush()
            return task
        checkpoint.last_action_step_id = step.id if step is not None else None
        checkpoint.phase = PlayerExecutionPhase.AWAITING_DEBRIEF_ACK
        checkpoint.version += 1
        self.db.flush()
        return task

    def _execute_action_cycle(self, task: AgentTask, action_step: AgentStep) -> None:
        """Run one TOOL action plus its internal async settlement and replan."""

        try:
            self.agent.execute_next(task)
            if task.status == AgentTaskStatus.REQUIRES_PLAYER_DECISION:
                return
            operation = self.db.scalar(
                select(WorldOperation)
                .where(
                    WorldOperation.game_instance_id == self.scope.game_instance_id,
                    WorldOperation.task_id == task.id,
                    WorldOperation.source_step_id == action_step.id,
                    WorldOperation.status == WorldOperationStatus.PENDING,
                )
                .order_by(WorldOperation.created_at.desc())
            )
            if operation is not None:
                # The Generic Agent owns the WAIT state; Formal Play merely
                # hides it inside this one player-visible action cycle.
                self.agent.execute_next(task)
                GenericActionService(self.db, self.scope).resolve_operation(
                    operation.id, resolution_key=f"formal-play:{operation.id}"
                )
                task.status = AgentTaskStatus.ACTIVE
                self.db.flush()
                self.agent.execute_next(task)
            self._ensure_next_plan(task)
        except GenericAgentError as exc:
            if exc.code not in (*_UNREACHABLE_PLANNING_CODES, *_MODEL_PLAN_CODES):
                raise
            task.status = AgentTaskStatus.BLOCKED
            task.last_error_code = (
                "MODEL_PLAN_REJECTED"
                if exc.code in _MODEL_PLAN_CODES
                else "UNREACHABLE_IN_CURRENT_STATE"
            )
            self.db.flush()

    def _ensure_next_plan(self, task: AgentTask) -> None:
        if task.status != AgentTaskStatus.ACTIVE or self._next_action_step(task) is not None:
            return
        if self.agent.evaluate(task).completed:
            self.agent.execute_next(task)
            return
        self.agent.plan(task, reason="PLAN_EXHAUSTED")

    def _next_action_step(self, task: AgentTask) -> AgentStep | None:
        plan = self.db.scalar(
            select(AgentPlan).where(
                AgentPlan.task_id == task.id,
                AgentPlan.status == AgentPlanStatus.ACTIVE,
            )
        )
        if plan is None:
            return None
        return self.db.scalar(
            select(AgentStep)
            .where(
                AgentStep.plan_id == plan.id,
                AgentStep.execution_type == StepExecutionType.TOOL,
                AgentStep.status.in_(
                    (AgentStepStatus.PENDING, AgentStepStatus.REQUIRES_PLAYER_DECISION)
                ),
            )
            .order_by(AgentStep.sequence)
        )

    def _ensure_checkpoint(self, task: AgentTask) -> PlayerExecutionCheckpoint:
        checkpoint = self.db.get(PlayerExecutionCheckpoint, task.id)
        if checkpoint is not None:
            return checkpoint
        phase = self._phase_after_cycle(task)
        if task.status == AgentTaskStatus.ACTIVE:
            phase = PlayerExecutionPhase.AWAITING_ACTION_ACK
        checkpoint = PlayerExecutionCheckpoint(
            task_id=task.id,
            game_instance_id=task.game_instance_id,
            phase=phase,
            version=1,
        )
        self.db.add(checkpoint)
        self.db.flush()
        return checkpoint

    def _checkpoint(
        self, task: AgentTask, *, expected_pacing_version: int
    ) -> PlayerExecutionCheckpoint:
        checkpoint = self.db.scalar(
            select(PlayerExecutionCheckpoint)
            .where(PlayerExecutionCheckpoint.task_id == task.id)
            .with_for_update()
        )
        if checkpoint is None:
            checkpoint = self._ensure_checkpoint(task)
        if checkpoint.version != expected_pacing_version:
            raise PlayError("PLAYER_PACING_CONFLICT", "The player checkpoint has changed")
        return checkpoint

    def _phase_after_cycle(self, task: AgentTask) -> PlayerExecutionPhase:
        if task.status == AgentTaskStatus.REQUIRES_PLAYER_DECISION:
            return PlayerExecutionPhase.APPROVAL_REQUIRED
        if task.status == AgentTaskStatus.SUCCEEDED:
            self._finalize_plan(task)
            return PlayerExecutionPhase.COMPLETED
        if task.status in (AgentTaskStatus.FAILED, AgentTaskStatus.BLOCKED):
            return PlayerExecutionPhase.BLOCKED
        if task.status == AgentTaskStatus.ABORTED:
            return PlayerExecutionPhase.ABORTED
        return PlayerExecutionPhase.AWAITING_DEBRIEF_ACK

    def _block_unreachable(self, task: AgentTask, checkpoint: PlayerExecutionCheckpoint) -> None:
        task.status = AgentTaskStatus.BLOCKED
        task.last_error_code = "UNREACHABLE_IN_CURRENT_STATE"
        checkpoint.phase = PlayerExecutionPhase.BLOCKED
        checkpoint.version += 1
        self.db.flush()

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
    "GENERIC_REPLAN_LIMIT",
}
_MODEL_PLAN_CODES = {"MODEL_PLAN_REJECTED"}
_PRODUCT_TERMINAL = (
    PlayerExecutionPhase.COMPLETED,
    PlayerExecutionPhase.BLOCKED,
    PlayerExecutionPhase.ABORTED,
)

__all__ = ["GoalSubmission", "PlayError", "PlayOrchestrator"]
