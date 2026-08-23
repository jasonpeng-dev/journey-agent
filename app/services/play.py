"""Formal browser Play orchestration over the existing Generic services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
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
from app.agent.provider import GenericModelProvider, GenericProviderError, PlanRequest
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
        model_max_repair_attempts_per_cycle: int = 2,
    ) -> None:
        self.db = db
        self.scope = GameInstanceService(db).load(game_instance_id)
        self.goal_resolver = GenericGoalResolver(provider=provider)
        self.agent = GenericAgentService(
            db,
            self.scope,
            goal_resolver=self.goal_resolver,
            provider=provider,
            provider_call_observer=self._provider_call_event,
            model_max_repair_attempts_per_cycle=model_max_repair_attempts_per_cycle,
        )

    def _provider_call_event(
        self,
        event: str,
        task: AgentTask,
        request: PlanRequest,
        details: dict[str, object],
    ) -> None:
        """Persist provider-call audit state across the external I/O boundary."""

        if event == "STARTED":
            metadata = dict(task.objective_resolution_metadata or {})
            calls = list(metadata.get("provider_calls", []))
            calls.append(dict(details))
            task.objective_resolution_metadata = {**metadata, "provider_calls": calls}
            self.db.flush()
            # The provider request must never run inside the uncommitted task
            # transaction.  This commit is the durable STARTED checkpoint.
            self.db.commit()
            return
        if event != "FINISHED":
            return
        audit_id = details.get("audit_id")
        metadata = dict(task.objective_resolution_metadata or {})
        calls = list(metadata.get("provider_calls", []))
        for index, call in enumerate(calls):
            if isinstance(call, dict) and call.get("audit_id") == audit_id:
                calls[index] = {**call, **details}
                break
        task.objective_resolution_metadata = {**metadata, "provider_calls": calls}
        self.db.flush()

    def _persist_provider_failure(
        self,
        task: AgentTask,
        checkpoint: PlayerExecutionCheckpoint,
        error: GenericProviderError,
        *,
        operation_kind: str,
        duration_ms: int,
    ) -> None:
        task.status = AgentTaskStatus.FAILED
        task.last_error_code = error.code
        task.last_error_detail = _provider_failure_detail(error.code)
        task.completed_at = datetime.now(UTC)
        task.version += 1
        checkpoint.phase = PlayerExecutionPhase.BLOCKED
        checkpoint.version += 1
        self._record_operation_duration(
            task,
            kind=operation_kind,
            duration_ms=duration_ms,
        )
        self.db.flush()
        # The API boundary rolls back after re-raising the provider error.  A
        # separate commit here makes the failure and its audit irreversible to
        # that rollback while leaving the error response unchanged.
        self.db.commit()

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
        resolution_started = perf_counter()
        resolution = self.goal_resolver.resolve(goal, definition)
        resolution_duration_ms = _duration_ms(resolution_started)
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
            # Goal resolution and initial planning are separate player-facing
            # operations.  GenericAgentService still owns task construction;
            # Formal Play simply defers its first plan until the player
            # acknowledges the resolved Goal.
            task = self.agent.create_task(
                conversation,
                goal,
                resolved_goal=resolution,
                initialize_plan=False,
            )
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
        self._record_operation_duration(
            task,
            kind="GOAL_RESOLUTION",
            duration_ms=resolution_duration_ms,
        )
        self._ensure_checkpoint(task)
        self.db.flush()
        return GoalSubmission(resolution, task)

    def start_initial_planning(self, *, expected_pacing_version: int) -> AgentTask:
        """Generate the first plan after the player accepts the resolved Goal."""

        require_scope_writable(self.db, self.scope.game_instance_id)
        task = self._current_task()
        if task is None:
            raise PlayError("AGENT_TASK_NOT_ACTIVE", "The Game has no active Task")
        checkpoint = self._checkpoint(task, expected_pacing_version=expected_pacing_version)
        if checkpoint.phase != PlayerExecutionPhase.AWAITING_PLAN_START:
            raise PlayError(
                "PLAY_PLAN_ALREADY_STARTED",
                "The Task is not waiting to start initial planning",
            )
        planning_started = perf_counter()
        try:
            plan = self.agent.plan(task)
        except GenericProviderError as exc:
            self._persist_provider_failure(
                task,
                checkpoint,
                exc,
                operation_kind="INITIAL_PLANNING_FAILURE",
                duration_ms=_duration_ms(planning_started),
            )
            raise
        except GenericAgentError as exc:
            if exc.code not in (*_UNREACHABLE_PLANNING_CODES, *_MODEL_PLAN_CODES):
                raise
            self._record_operation_duration(
                task,
                kind="INITIAL_PLANNING",
                duration_ms=_duration_ms(planning_started),
            )
            task.status = AgentTaskStatus.BLOCKED
            task.last_error_code = (
                "MODEL_PLAN_REJECTED"
                if exc.code in _MODEL_PLAN_CODES
                else "UNREACHABLE_IN_CURRENT_STATE"
            )
            checkpoint.phase = PlayerExecutionPhase.BLOCKED
            checkpoint.version += 1
            self.db.flush()
            return task
        self._record_operation_duration(
            task,
            kind="INITIAL_PLANNING",
            duration_ms=_duration_ms(planning_started),
            plan_version=plan.version,
        )
        if task.status == AgentTaskStatus.ACTIVE and self._next_action_step(task) is None:
            self._block_unreachable(task, checkpoint)
            return task
        checkpoint.phase = (
            PlayerExecutionPhase.AWAITING_ACTION_ACK
            if task.status == AgentTaskStatus.ACTIVE
            else self._phase_after_cycle(task)
        )
        checkpoint.version += 1
        self.db.flush()
        return task

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
        checkpoint.phase = self._phase_after_cycle(task, action_step=action_step)
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
        if self._next_action_step(task) is None:
            self._block_unreachable(task, checkpoint)
            return task
        checkpoint.phase = PlayerExecutionPhase.AWAITING_ACTION_ACK
        checkpoint.version += 1
        self.db.flush()
        return task

    def replan(self, *, expected_pacing_version: int) -> AgentTask:
        """Build the next Plan after a visible failure or completed segment.

        This is a small application boundary split: the Generic Agent still
        owns planning and validation, while Formal Play controls when the
        player-visible failure is persisted and when the provider is called.
        Repeated requests are idempotent once a newer Plan exists.
        """

        require_scope_writable(self.db, self.scope.game_instance_id)
        task = self._current_task()
        if task is None:
            raise PlayError("AGENT_TASK_NOT_ACTIVE", "The Game has no active Task")
        checkpoint = self._checkpoint(task, expected_pacing_version=expected_pacing_version)
        if checkpoint.phase != PlayerExecutionPhase.AWAITING_REPLAN_ACK:
            raise PlayError(
                "PLAY_REPLAN_NOT_REQUIRED",
                "The Task is not waiting for a failed Action replan",
            )
        failed_step = (
            self.db.get(AgentStep, checkpoint.last_action_step_id)
            if checkpoint.last_action_step_id is not None
            else None
        )
        plan_invalidated = self.agent.has_pending_plan_invalidation(task)
        latest_plan = self.db.scalar(
            select(AgentPlan)
            .where(
                AgentPlan.task_id == task.id,
                AgentPlan.status == AgentPlanStatus.ACTIVE,
            )
            .order_by(AgentPlan.version.desc())
        )
        segment_complete = bool(
            not plan_invalidated
            and failed_step is not None
            and failed_step.status == AgentStepStatus.SUCCEEDED
            and latest_plan is not None
            and latest_plan.id == failed_step.plan_id
            and task.status == AgentTaskStatus.ACTIVE
            and self._next_action_step(task) is None
            and not self._action_cycle_failed(failed_step)
            and not self.agent.evaluate(task).completed
        )
        if plan_invalidated:
            if failed_step is None or failed_step.status != AgentStepStatus.SUCCEEDED:
                raise PlayError(
                    "PLAY_REPLAN_NOT_REQUIRED",
                    "The current Plan was not invalidated after a successful Action",
                )
        elif not segment_complete and (
            failed_step is None or not self._action_cycle_failed(failed_step)
        ):
            raise PlayError(
                "PLAY_REPLAN_NOT_REQUIRED",
                "The current Action did not require a replan",
            )
        assert failed_step is not None
        if latest_plan is None or latest_plan.id == failed_step.plan_id:
            replan_started = perf_counter()
            try:
                if segment_complete:
                    assert latest_plan is not None
                    replan_reason = (
                        "INFORMATION_BOUNDARY"
                        if latest_plan.stop_reason == "INFORMATION_BOUNDARY"
                        else "PLAN_EXHAUSTED"
                    )
                else:
                    replan_reason = task.last_error_code or "ACTION_FAILED"
                plan = self.agent.plan(task, reason=replan_reason)
            except GenericProviderError as exc:
                self._persist_provider_failure(
                    task,
                    checkpoint,
                    exc,
                    operation_kind="REPLANNING_FAILURE",
                    duration_ms=_duration_ms(replan_started),
                )
                raise
            except GenericAgentError as exc:
                if exc.code not in (*_UNREACHABLE_PLANNING_CODES, *_MODEL_PLAN_CODES):
                    raise
                self._record_operation_duration(
                    task,
                    kind="REPLANNING",
                    duration_ms=_duration_ms(replan_started),
                )
                task.status = AgentTaskStatus.BLOCKED
                task.last_error_code = (
                    "MODEL_PLAN_REJECTED"
                    if exc.code in _MODEL_PLAN_CODES
                    else "UNREACHABLE_IN_CURRENT_STATE"
                )
                checkpoint.phase = PlayerExecutionPhase.BLOCKED
                checkpoint.version += 1
                self.db.flush()
                return task
            self._record_operation_duration(
                task,
                kind="REPLANNING",
                duration_ms=_duration_ms(replan_started),
                plan_version=plan.version,
            )
        if task.status == AgentTaskStatus.ACTIVE and self._next_action_step(task) is None:
            self._block_unreachable(task, checkpoint)
            return task
        checkpoint.phase = (
            PlayerExecutionPhase.AWAITING_ACTION_ACK
            if task.status == AgentTaskStatus.ACTIVE
            else self._phase_after_cycle(task)
        )
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
            if phase == PlayerExecutionPhase.AWAITING_PLAN_START:
                self.start_initial_planning(expected_pacing_version=checkpoint.version)
            elif phase == PlayerExecutionPhase.AWAITING_ACTION_ACK:
                self.acknowledge_action(expected_pacing_version=checkpoint.version)
            elif phase == PlayerExecutionPhase.AWAITING_REPLAN_ACK:
                self.replan(expected_pacing_version=checkpoint.version)
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
            checkpoint.phase = self._phase_after_cycle(task, action_step=step)
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
        replan_started = perf_counter()
        try:
            plan = self.agent.plan(task, reason="PLAYER_REJECTED")
        except GenericProviderError as exc:
            self._persist_provider_failure(
                task,
                checkpoint,
                exc,
                operation_kind="REPLANNING_FAILURE",
                duration_ms=_duration_ms(replan_started),
            )
            raise
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
        self._record_operation_duration(
            task,
            kind="REPLANNING",
            duration_ms=_duration_ms(replan_started),
            plan_version=plan.version,
        )
        checkpoint.last_action_step_id = step.id if step is not None else None
        if task.status == AgentTaskStatus.ACTIVE and self._next_action_step(task) is not None:
            checkpoint.phase = PlayerExecutionPhase.AWAITING_ACTION_ACK
        else:
            checkpoint.phase = self._phase_after_cycle(task)
        checkpoint.version += 1
        self.db.flush()
        return task

    def _execute_action_cycle(self, task: AgentTask, action_step: AgentStep) -> None:
        """Run one TOOL action plus its internal async settlement.

        Formal Play owns the player acknowledgement boundary; it never starts
        a new Provider cycle from this action request.
        """

        try:
            self.agent.execute_next(task, replan_on_failure=False)
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
                self.agent.execute_next(task, replan_on_failure=False)
                GenericActionService(self.db, self.scope).resolve_operation(
                    operation.id, resolution_key=f"formal-play:{operation.id}"
                )
                task.status = AgentTaskStatus.ACTIVE
                self.db.flush()
                self.agent.execute_next(task, replan_on_failure=False)
            if self._action_cycle_failed(action_step):
                return
            # Formal Play deliberately pauses after the final successful step
            # of an incomplete segment. The player must acknowledge the
            # completed segment before a new Provider planning cycle starts.
        except GenericProviderError as exc:
            checkpoint = self._ensure_checkpoint(task)
            checkpoint.last_action_step_id = action_step.id
            self._persist_provider_failure(
                task,
                checkpoint,
                exc,
                operation_kind="REPLANNING_FAILURE",
                duration_ms=0,
            )
            raise
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
        if self.agent.has_pending_plan_invalidation(task):
            return
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
            phase = (
                PlayerExecutionPhase.AWAITING_PLAN_START
                if task.current_plan_version == 0
                else PlayerExecutionPhase.AWAITING_ACTION_ACK
            )
        checkpoint = PlayerExecutionCheckpoint(
            task_id=task.id,
            game_instance_id=task.game_instance_id,
            phase=phase,
            version=1,
        )
        self.db.add(checkpoint)
        self.db.flush()
        return checkpoint

    def _failure_requires_replan(
        self, task: AgentTask, checkpoint: PlayerExecutionCheckpoint
    ) -> bool:
        step = (
            self.db.get(AgentStep, checkpoint.last_action_step_id)
            if checkpoint.last_action_step_id is not None
            else None
        )
        if step is None or not self._action_cycle_failed(step):
            return False
        latest_plan = self.db.scalar(
            select(AgentPlan)
            .where(AgentPlan.task_id == task.id, AgentPlan.status == AgentPlanStatus.ACTIVE)
            .order_by(AgentPlan.version.desc())
        )
        return latest_plan is not None and latest_plan.id == step.plan_id

    def _action_cycle_failed(self, action_step: AgentStep) -> bool:
        if action_step.status == AgentStepStatus.FAILED:
            return True
        return (
            self.db.scalar(
                select(AgentStep)
                .where(
                    AgentStep.plan_id == action_step.plan_id,
                    AgentStep.sequence > action_step.sequence,
                    AgentStep.action_intent == action_step.action_intent,
                    AgentStep.execution_type == StepExecutionType.WAIT_FOR_WORLD_EVENT,
                    AgentStep.status == AgentStepStatus.FAILED,
                )
                .order_by(AgentStep.sequence)
            )
            is not None
        )

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

    def _phase_after_cycle(
        self, task: AgentTask, *, action_step: AgentStep | None = None
    ) -> PlayerExecutionPhase:
        if task.status == AgentTaskStatus.REQUIRES_PLAYER_DECISION:
            return PlayerExecutionPhase.APPROVAL_REQUIRED
        if task.status == AgentTaskStatus.SUCCEEDED:
            self._finalize_plan(task)
            return PlayerExecutionPhase.COMPLETED
        if task.status in (AgentTaskStatus.FAILED, AgentTaskStatus.BLOCKED):
            return PlayerExecutionPhase.BLOCKED
        if task.status == AgentTaskStatus.ABORTED:
            return PlayerExecutionPhase.ABORTED
        if action_step is not None and self._action_cycle_failed(action_step):
            return PlayerExecutionPhase.AWAITING_REPLAN_ACK
        if self.agent.has_pending_plan_invalidation(task):
            return PlayerExecutionPhase.AWAITING_REPLAN_ACK
        if action_step is not None and self._next_action_step(task) is None:
            # A segment can end before the frozen objective is complete (for
            # example at an INFORMATION_BOUNDARY). Do not silently call the
            # Provider from the action acknowledgement request.
            return PlayerExecutionPhase.AWAITING_REPLAN_ACK
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

    def _record_operation_duration(
        self,
        task: AgentTask,
        *,
        kind: str,
        duration_ms: int,
        plan_version: int | None = None,
    ) -> None:
        """Persist a completed player-facing operation snapshot.

        This is presentation/audit metadata only.  It does not participate in
        planning, validation, execution, or task state transitions.
        """

        metadata = dict(task.objective_resolution_metadata or {})
        durations = metadata.get("operation_durations")
        snapshots = list(durations) if isinstance(durations, list) else []
        snapshot: dict[str, object] = {
            "kind": kind,
            "duration_ms": max(0, duration_ms),
        }
        if plan_version is not None:
            snapshot["plan_version"] = plan_version
        snapshots.append(snapshot)
        metadata["operation_durations"] = snapshots
        task.objective_resolution_metadata = metadata
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


def _duration_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _provider_failure_detail(code: str) -> str:
    return {
        "MODEL_PROVIDER_TIMEOUT": "模型调用超时",
        "MODEL_PROVIDER_HTTP_ERROR": "模型服务返回错误",
        "MODEL_PROVIDER_RESPONSE_INVALID": "模型返回无效",
        "MODEL_PROVIDER_CONFIGURATION_INVALID": "模型服务配置无效",
    }.get(code, "模型调用失败")


__all__ = ["GoalSubmission", "PlayError", "PlayOrchestrator"]
