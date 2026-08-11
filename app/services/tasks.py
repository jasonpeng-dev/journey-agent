from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.task_policy import PLANNING_MODES, TASK_EXECUTION_TOOLS
from app.core.errors import AppError, NotFoundError
from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    DecisionStatus,
    EncounterStatus,
    MemoryType,
    StepExecutionType,
    WorldOperationStatus,
)
from app.infrastructure.db.models import (
    NPC,
    AgentPlan,
    AgentRun,
    AgentStep,
    AgentTask,
    ConversationSession,
    EncounterDefinition,
    EncounterRun,
    Memory,
    OfficerAppointment,
    PlayerDecisionRequest,
    WorldOperation,
)
from app.services.game import GameService

TERMINAL_STEP_STATUSES = frozenset(
    {
        AgentStepStatus.SUCCEEDED,
        AgentStepStatus.FAILED,
        AgentStepStatus.BLOCKED,
        AgentStepStatus.SKIPPED,
    }
)


class TaskService:
    def __init__(self, db: Session, max_replans: int = 2):
        self.db = db
        self.max_replans = max_replans

    def create_task(
        self,
        session: ConversationSession,
        goal_description: str,
        scenario_key: str,
        planning_mode: str = "PROVIDER",
    ) -> AgentTask:
        if scenario_key not in {"starfire_outpost", "starfire_command"}:
            raise AppError("TASK_SCENARIO_UNSUPPORTED", "The task scenario is not supported")
        if planning_mode not in PLANNING_MODES:
            raise AppError("PLANNING_MODE_INVALID", "The planning mode is not supported")
        if scenario_key == "starfire_command":
            owner = self.db.get(NPC, session.npc_id)
            appointment = (
                self.db.get(OfficerAppointment, (session.player_id, session.npc_id))
                if owner is not None
                else None
            )
            if (
                owner is None
                or owner.role.value != "STRATEGIST"
                or appointment is None
                or appointment.status != "ACTIVE"
            ):
                raise AppError(
                    "COMMAND_OWNER_INVALID",
                    "The strategic Starfire command must be owned by an appointed strategist",
                    status_code=403,
                )
        active = self.db.scalar(
            select(AgentTask).where(
                AgentTask.player_id == session.player_id,
                AgentTask.scenario_key == scenario_key,
                AgentTask.status.in_(
                    [
                        AgentTaskStatus.ACTIVE,
                        AgentTaskStatus.WAITING_FOR_USER,
                        AgentTaskStatus.REQUIRES_PLAYER_DECISION,
                        AgentTaskStatus.WAITING_FOR_PLAYER_ACTION,
                        AgentTaskStatus.WAITING_FOR_WORLD_EVENT,
                        AgentTaskStatus.BLOCKED,
                    ]
                ),
            )
        )
        if active is not None:
            return active
        task = AgentTask(
            player_id=session.player_id,
            owner_npc_id=session.npc_id,
            origin_session_id=session.id,
            last_session_id=session.id,
            goal_description=goal_description,
            scenario_key=scenario_key,
            planning_mode=planning_mode,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def get_task(self, task_id: UUID, *, lock: bool = False) -> AgentTask:
        query = select(AgentTask).where(AgentTask.id == task_id)
        if lock:
            query = query.with_for_update()
        task = self.db.scalar(query)
        if task is None:
            raise NotFoundError("agent_task", task_id)
        return task

    def current_plan(self, task: AgentTask) -> AgentPlan | None:
        if task.current_plan_version == 0:
            return None
        return self.db.scalar(
            select(AgentPlan).where(
                AgentPlan.task_id == task.id,
                AgentPlan.version == task.current_plan_version,
            )
        )

    def plan_steps(self, plan_id: UUID) -> list[AgentStep]:
        return list(
            self.db.scalars(
                select(AgentStep).where(AgentStep.plan_id == plan_id).order_by(AgentStep.sequence)
            ).all()
        )

    def next_step(self, plan_id: UUID) -> AgentStep | None:
        return self.db.scalar(
            select(AgentStep)
            .where(
                AgentStep.plan_id == plan_id,
                AgentStep.status.in_(
                    [
                        AgentStepStatus.PENDING,
                        AgentStepStatus.IN_PROGRESS,
                        AgentStepStatus.WAITING_FOR_USER,
                        AgentStepStatus.REQUIRES_PLAYER_DECISION,
                        AgentStepStatus.WAITING_FOR_PLAYER_ACTION,
                        AgentStepStatus.WAITING_FOR_WORLD_EVENT,
                    ]
                ),
            )
            .order_by(AgentStep.sequence)
        )

    def create_plan(
        self,
        task_id: UUID,
        strategy_summary: str,
        steps: list[dict[str, Any]],
        *,
        created_by_run_id: UUID,
        replan_reason: str | None = None,
        source: str = "MANUAL",
        planner_model: str | None = None,
        validation_status: str = "PASSED",
        validation_errors: list[dict[str, Any]] | None = None,
    ) -> AgentPlan:
        task = self.get_task(task_id, lock=True)
        if not steps or len(steps) > 12:
            raise AppError("PLAN_STEP_COUNT_INVALID", "A plan must contain 1 to 12 steps")
        old_plan = self.current_plan(task)
        if old_plan is not None:
            if replan_reason is None:
                raise AppError("TASK_ALREADY_PLANNED", "The task already has an active plan")
            if task.replan_count >= self.max_replans:
                task.status = AgentTaskStatus.BLOCKED
                task.last_error_code = "REPLAN_LIMIT_REACHED"
                raise AppError(
                    "REPLAN_LIMIT_REACHED",
                    "The task reached its safe replanning limit",
                )
            old_plan.status = AgentPlanStatus.SUPERSEDED
            for old_step in self.plan_steps(old_plan.id):
                if old_step.status in {
                    AgentStepStatus.PENDING,
                    AgentStepStatus.IN_PROGRESS,
                    AgentStepStatus.WAITING_FOR_USER,
                    AgentStepStatus.REQUIRES_PLAYER_DECISION,
                    AgentStepStatus.WAITING_FOR_PLAYER_ACTION,
                    AgentStepStatus.WAITING_FOR_WORLD_EVENT,
                }:
                    old_step.status = AgentStepStatus.SKIPPED
                    old_step.completed_at = datetime.now(UTC)
            task.replan_count += 1
        version = task.current_plan_version + 1
        plan = AgentPlan(
            task_id=task.id,
            version=version,
            strategy_summary=strategy_summary,
            replan_reason=replan_reason,
            supersedes_plan_id=old_plan.id if old_plan else None,
            created_by_run_id=created_by_run_id,
            source=source,
            planner_model=planner_model,
            validation_status=validation_status,
            validation_errors=validation_errors or [],
        )
        self.db.add(plan)
        self.db.flush()
        created_by_run = self.db.get(AgentRun, created_by_run_id)
        plan.created_by_npc_id = (
            created_by_run.actor_npc_id
            if created_by_run is not None and created_by_run.actor_npc_id is not None
            else task.owner_npc_id
        )
        for sequence, spec in enumerate(steps, start=1):
            execution_type = StepExecutionType(str(spec["execution_type"]))
            tool_name = spec.get("selected_tool_name")
            if execution_type == StepExecutionType.TOOL:
                if not isinstance(tool_name, str) or tool_name not in TASK_EXECUTION_TOOLS:
                    raise AppError(
                        "PLAN_TOOL_NOT_ALLOWED",
                        "A plan selected an unsupported execution tool",
                    )
            elif tool_name is not None:
                raise AppError(
                    "PLAN_WAIT_TOOL_INVALID",
                    "A waiting step cannot select an execution tool",
                )
            assigned_npc = None
            assigned_key = spec.get("assigned_officer_key")
            if isinstance(assigned_key, str):
                assigned_npc = self.db.scalar(select(NPC).where(NPC.key == assigned_key))
                if assigned_npc is None:
                    raise AppError(
                        "PLAN_OFFICER_UNAVAILABLE",
                        f"Assigned officer {assigned_key} does not exist",
                    )
            self.db.add(
                AgentStep(
                    plan_id=plan.id,
                    sequence=sequence,
                    description=str(spec["description"]),
                    execution_type=execution_type,
                    assigned_npc_id=(
                        assigned_npc.id if assigned_npc is not None else task.owner_npc_id
                    ),
                    action_intent=spec.get("action_intent"),
                    constraints=dict(spec.get("constraints", {})),
                    allowed_tool_names=list(spec.get("allowed_tool_names", [])),
                    selected_tool_name=tool_name,
                    tool_arguments=dict(spec.get("tool_arguments", {})),
                    expected_outcome=dict(spec.get("expected_outcome", {})),
                    resume_condition=spec.get("resume_condition"),
                )
            )
        task.current_plan_version = version
        task.status = AgentTaskStatus.ACTIVE
        task.last_error_code = None
        task.version += 1
        self.db.flush()
        return plan

    def mark_waiting(self, task: AgentTask, step: AgentStep) -> None:
        transitioned = (
            step.status != AgentStepStatus.WAITING_FOR_USER
            or task.status != AgentTaskStatus.WAITING_FOR_USER
        )
        step.status = AgentStepStatus.WAITING_FOR_USER
        if step.started_at is None:
            step.started_at = datetime.now(UTC)
        task.status = AgentTaskStatus.WAITING_FOR_USER
        if transitioned:
            task.version += 1
        self.db.flush()

    def mark_waiting_for_world_event(self, task: AgentTask, step: AgentStep) -> None:
        transitioned = (
            step.status != AgentStepStatus.WAITING_FOR_WORLD_EVENT
            or task.status != AgentTaskStatus.WAITING_FOR_WORLD_EVENT
        )
        step.status = AgentStepStatus.WAITING_FOR_WORLD_EVENT
        if step.started_at is None:
            step.started_at = datetime.now(UTC)
        task.status = AgentTaskStatus.WAITING_FOR_WORLD_EVENT
        if transitioned:
            task.version += 1
        self.db.flush()

    def mark_waiting_for_player_action(self, task: AgentTask, step: AgentStep) -> None:
        transitioned = (
            step.status != AgentStepStatus.WAITING_FOR_PLAYER_ACTION
            or task.status != AgentTaskStatus.WAITING_FOR_PLAYER_ACTION
        )
        step.status = AgentStepStatus.WAITING_FOR_PLAYER_ACTION
        if step.started_at is None:
            step.started_at = datetime.now(UTC)
        task.status = AgentTaskStatus.WAITING_FOR_PLAYER_ACTION
        if transitioned:
            task.version += 1
        self.db.flush()

    def evaluate_wait(self, task: AgentTask, step: AgentStep) -> str:
        condition = step.resume_condition or {}
        if condition.get("type") == "WORLD_OPERATION":
            return self._evaluate_world_operation_wait(task, step, condition)
        if condition.get("type") == "PLAYER_ACTION":
            return self._evaluate_player_action_wait(task, step, condition)
        if condition.get("type") != "ENCOUNTER_RESULT":
            raise AppError("RESUME_CONDITION_INVALID", "The resume condition is unsupported")
        if step.status == AgentStepStatus.PENDING:
            self.mark_waiting(task, step)
            return "WAITING"
        encounter_key = condition.get("encounter_key")
        required = condition.get("required_result", "VICTORY")
        query = (
            select(EncounterRun)
            .join(EncounterDefinition)
            .where(
                EncounterRun.player_id == task.player_id,
                EncounterDefinition.key == encounter_key,
            )
            .order_by(EncounterRun.started_at.desc())
        )
        if step.started_at is not None:
            query = query.where(EncounterRun.started_at >= step.started_at)
        run = self.db.scalar(query)
        if run is None or run.status in {EncounterStatus.PENDING, EncounterStatus.ACTIVE}:
            self.mark_waiting(task, step)
            return "WAITING"
        if run.status.value == required:
            step.status = AgentStepStatus.SUCCEEDED
            step.actual_result = {
                "encounter_run_id": str(run.id),
                "status": run.status.value,
            }
            step.completed_at = datetime.now(UTC)
            task.status = AgentTaskStatus.ACTIVE
            task.last_error_code = None
            task.version += 1
            self.db.flush()
            return "RESUMED"
        step.status = AgentStepStatus.FAILED
        step.failure_code = "ENCOUNTER_DEFEAT"
        step.actual_result = {
            "encounter_run_id": str(run.id),
            "status": run.status.value,
        }
        step.completed_at = datetime.now(UTC)
        task.status = AgentTaskStatus.ACTIVE
        task.last_error_code = "ENCOUNTER_DEFEAT"
        task.version += 1
        self.db.flush()
        return "REPLAN_REQUIRED"

    def _evaluate_player_action_wait(
        self,
        task: AgentTask,
        step: AgentStep,
        condition: dict[str, Any],
    ) -> str:
        fact_key = condition.get("fact_key")
        field = condition.get("field")
        if not isinstance(fact_key, str) or not isinstance(field, str):
            raise AppError(
                "RESUME_CONDITION_INVALID",
                "The player action wait must identify a verified fact and field",
            )
        fact = GameService(self.db).get_world_fact(task.player_id, fact_key)
        if fact.get(field) != condition.get("equals"):
            self.mark_waiting_for_player_action(task, step)
            return "WAITING_FOR_PLAYER_ACTION"
        step.status = AgentStepStatus.SUCCEEDED
        step.actual_result = {
            "player_action": "COMPLETED",
            "verified_fact": fact_key,
            "field": field,
            "value": fact.get(field),
        }
        step.completed_at = datetime.now(UTC)
        task.status = AgentTaskStatus.ACTIVE
        task.last_error_code = None
        task.version += 1
        self.db.flush()
        return "RESUMED"

    def _evaluate_world_operation_wait(
        self,
        task: AgentTask,
        step: AgentStep,
        condition: dict[str, Any],
    ) -> str:
        source_sequence = condition.get("source_step_sequence")
        if not isinstance(source_sequence, int):
            raise AppError(
                "RESUME_CONDITION_INVALID",
                "The world event wait does not identify its source step",
            )
        source_step = self.db.scalar(
            select(AgentStep).where(
                AgentStep.plan_id == step.plan_id,
                AgentStep.sequence == source_sequence,
            )
        )
        operation_id = _operation_id_from_step(source_step)
        if operation_id is None:
            raise AppError(
                "WORLD_OPERATION_NOT_STARTED",
                "The source step did not create a world operation",
            )
        operation = self.db.get(WorldOperation, operation_id)
        if (
            source_step is None
            or operation is None
            or operation.player_id != task.player_id
            or operation.task_id != task.id
            or operation.source_step_id != source_step.id
        ):
            raise AppError("WORLD_OPERATION_NOT_FOUND", "The world operation is unavailable")
        if operation.status == WorldOperationStatus.PENDING:
            self.mark_waiting_for_world_event(task, step)
            return "WAITING_FOR_WORLD_EVENT"
        result = (
            str(operation.outcome.get("result"))
            if isinstance(operation.outcome, dict)
            else "UNKNOWN"
        )
        success_outcomes = condition.get("success_outcomes", [])
        if result in success_outcomes:
            step.status = AgentStepStatus.SUCCEEDED
            step.actual_result = {
                "operation_id": str(operation.id),
                "operation_type": operation.operation_type,
                "status": operation.status.value,
                "outcome": operation.outcome,
            }
            step.completed_at = datetime.now(UTC)
            task.status = AgentTaskStatus.ACTIVE
            task.last_error_code = None
            task.version += 1
            self.db.flush()
            return "RESUMED"
        failure_code = (
            str(operation.outcome.get("failure_code"))
            if isinstance(operation.outcome, dict) and operation.outcome.get("failure_code")
            else "WORLD_OPERATION_DEFEAT"
        )
        step.status = AgentStepStatus.FAILED
        step.failure_code = failure_code
        step.actual_result = {
            "operation_id": str(operation.id),
            "operation_type": operation.operation_type,
            "status": operation.status.value,
            "outcome": operation.outcome,
        }
        step.completed_at = datetime.now(UTC)
        task.status = AgentTaskStatus.ACTIVE
        task.last_error_code = failure_code
        task.version += 1
        self.db.flush()
        return "REPLAN_REQUIRED"

    def finish_if_complete(self, task: AgentTask, plan: AgentPlan) -> bool:
        steps = self.plan_steps(plan.id)
        if not steps or any(step.status != AgentStepStatus.SUCCEEDED for step in steps):
            return False
        if task.scenario_key == "starfire_outpost":
            state = GameService(self.db).inspect_starfire_requirements(task.player_id)
            if not state["outpost_operational"] or not state["access_granted"]:
                plan.status = AgentPlanStatus.FAILED
                task.status = AgentTaskStatus.BLOCKED
                task.last_error_code = "TASK_GOAL_NOT_VERIFIED"
                task.version += 1
                self.db.flush()
                return False
        if task.scenario_key == "starfire_command":
            state = GameService(self.db).inspect_command_state(task.player_id)
            world = state["world"]
            assert isinstance(world, dict)
            if (
                world.get("valley_security") != "SAFE"
                or world.get("starfire_outpost_status") not in {"OPERATIONAL", "RESTORED"}
                or world.get("northern_trade_route_status") != "OPEN"
            ):
                plan.status = AgentPlanStatus.FAILED
                task.status = AgentTaskStatus.BLOCKED
                task.last_error_code = "TASK_GOAL_NOT_VERIFIED"
                task.version += 1
                self.db.flush()
                return False
        now = datetime.now(UTC)
        plan.status = AgentPlanStatus.SUCCEEDED
        task.status = AgentTaskStatus.SUCCEEDED
        task.completed_at = now
        task.last_error_code = None
        task.version += 1
        if task.scenario_key == "starfire_command":
            world = GameService(self.db).inspect_command_state(task.player_id)["world"]
            assert isinstance(world, dict)
            source_event_id = f"strategic-task:{task.id}:completed"
            officer_ids = {
                step.assigned_npc_id for step in steps if step.assigned_npc_id is not None
            }
            officer_ids.add(task.owner_npc_id)
            report = (
                f"Command completed under Plan v{plan.version}: "
                f"valley={world.get('valley_security')}, "
                f"outpost={world.get('starfire_outpost_status')}, "
                f"trade_route={world.get('northern_trade_route_status')}."
            )
            for officer_id in officer_ids:
                existing = self.db.scalar(
                    select(Memory).where(
                        Memory.player_id == task.player_id,
                        Memory.npc_id == officer_id,
                        Memory.source_event_id == source_event_id,
                    )
                )
                if existing is None:
                    self.db.add(
                        Memory(
                            player_id=task.player_id,
                            npc_id=officer_id,
                            type=MemoryType.WORLD_EVENT,
                            content=report,
                            importance=8,
                            source_session_id=task.last_session_id,
                            source_event_id=source_event_id,
                        )
                    )
        self.db.flush()
        return True

    def bind_session(self, task: AgentTask, session: ConversationSession) -> None:
        if session.player_id != task.player_id:
            raise AppError(
                "TASK_PLAYER_MISMATCH",
                "The session player does not own this task",
                status_code=403,
            )
        if session.npc_id != task.owner_npc_id:
            raise AppError(
                "TASK_NPC_MISMATCH",
                "The task must resume with its owning NPC",
                status_code=403,
            )
        if task.last_session_id != session.id:
            task.last_session_id = session.id
            task.version += 1
            self.db.flush()

    def resolve_player_decision(
        self,
        task: AgentTask,
        decision_id: UUID,
        option_id: str,
    ) -> tuple[PlayerDecisionRequest, str]:
        """Resolve one scoped approval without performing the approved action.

        Approval only returns the exact frozen Step to PENDING. ToolExecutor must
        subsequently consume the matching officer/tool/argument approval before
        GameService is called.
        """

        decision = self.db.scalar(
            select(PlayerDecisionRequest)
            .where(
                PlayerDecisionRequest.id == decision_id,
                PlayerDecisionRequest.task_id == task.id,
            )
            .with_for_update()
        )
        if decision is None:
            raise NotFoundError("decision_request", decision_id)
        if decision.status != DecisionStatus.PENDING:
            if decision.selected_option == option_id:
                return decision, "DECISION_ALREADY_RESOLVED"
            raise AppError(
                "DECISION_ALREADY_RESOLVED",
                "The player decision has already been resolved",
                status_code=409,
            )
        available = {
            str(option.get("id"))
            for option in decision.options
            if isinstance(option, dict) and option.get("id") is not None
        }
        if option_id not in available:
            raise AppError(
                "DECISION_OPTION_INVALID",
                "The selected option is not available",
            )
        step = self.db.get(AgentStep, decision.step_id)
        if step is None:
            raise NotFoundError("agent_step", decision.step_id)
        if step.status != AgentStepStatus.REQUIRES_PLAYER_DECISION:
            raise AppError(
                "DECISION_STALE",
                "The related task step no longer requires this decision",
                status_code=409,
            )
        decision.selected_option = option_id
        decision.resolved_at = datetime.now(UTC)
        if option_id == "APPROVE":
            decision.status = DecisionStatus.APPROVED
            step.status = AgentStepStatus.PENDING
            step.actual_result = {
                "decision_id": str(decision.id),
                "status": DecisionStatus.APPROVED.value,
            }
            step.failure_code = None
            task.status = AgentTaskStatus.ACTIVE
            task.last_error_code = None
            event = "DECISION_APPROVED"
        else:
            decision.status = DecisionStatus.REJECTED
            step.status = AgentStepStatus.FAILED
            step.completed_at = datetime.now(UTC)
            step.actual_result = {
                "decision_id": str(decision.id),
                "status": DecisionStatus.REJECTED.value,
            }
            step.failure_code = "PLAYER_DECISION_REJECTED"
            task.status = AgentTaskStatus.ACTIVE
            task.last_error_code = "PLAYER_DECISION_REJECTED"
            event = "DECISION_REJECTED"
        task.version += 1
        self.db.flush()
        return decision, event

    def serialize(self, task: AgentTask) -> dict[str, Any]:
        plans = list(
            self.db.scalars(
                select(AgentPlan).where(AgentPlan.task_id == task.id).order_by(AgentPlan.version)
            ).all()
        )
        owner = self.db.get(NPC, task.owner_npc_id)
        current_plan = self.current_plan(task)
        current_step = self.next_step(current_plan.id) if current_plan is not None else None
        current_actor = (
            self.db.get(NPC, current_step.assigned_npc_id)
            if current_step is not None and current_step.assigned_npc_id is not None
            else None
        )
        decision = self.db.scalar(
            select(PlayerDecisionRequest)
            .where(
                PlayerDecisionRequest.task_id == task.id,
                PlayerDecisionRequest.status == DecisionStatus.PENDING,
            )
            .order_by(PlayerDecisionRequest.created_at.desc())
        )
        operation = None
        if (
            current_step is not None
            and current_step.execution_type == StepExecutionType.WAIT_FOR_WORLD_EVENT
        ):
            condition = current_step.resume_condition or {}
            source_sequence = condition.get("source_step_sequence")
            source_step = (
                self.db.scalar(
                    select(AgentStep).where(
                        AgentStep.plan_id == current_step.plan_id,
                        AgentStep.sequence == source_sequence,
                    )
                )
                if isinstance(source_sequence, int)
                else None
            )
            operation_id = _operation_id_from_step(source_step)
            candidate = (
                self.db.get(WorldOperation, operation_id) if operation_id is not None else None
            )
            if (
                candidate is not None
                and candidate.task_id == task.id
                and candidate.status == WorldOperationStatus.PENDING
            ):
                operation = candidate
        pending_decision = (
            None
            if decision is None
            else {
                "id": str(decision.id),
                "status": decision.status.value,
                "decision_kind": decision.decision_kind,
                "summary": decision.summary,
                "options": decision.options,
                "requested_by_officer": _officer_ref(
                    self.db.get(NPC, decision.requested_by_npc_id)
                ),
                "related_task_id": str(decision.task_id),
                "related_step_id": str(decision.step_id),
                "action_tool_name": decision.action_tool_name,
                "action_arguments": decision.action_arguments,
                "policy_snapshot": decision.policy_snapshot,
                "resolve_endpoint": (f"/api/v1/tasks/{task.id}/decisions/{decision.id}/resolve"),
            }
        )
        pending_world_event = (
            None
            if operation is None
            else {
                "id": str(operation.id),
                "operation_id": str(operation.id),
                "event_type": f"{operation.operation_type}_RESOLVED",
                "operation_type": operation.operation_type,
                "target_key": operation.target_key,
                "status": operation.status.value,
                "related_step_id": (
                    str(operation.source_step_id) if operation.source_step_id else None
                ),
                "initiated_by_officer": _officer_ref(self.db.get(NPC, operation.officer_npc_id)),
                "resolve_endpoint": f"/api/v1/debug/world-events/{operation.id}/resolve",
            }
        )
        pending_player_action = (
            None
            if current_step is None
            or current_step.status != AgentStepStatus.WAITING_FOR_PLAYER_ACTION
            else {
                "step_id": str(current_step.id),
                "description": current_step.description,
                "resume_condition": current_step.resume_condition,
                "assigned_officer": _officer_ref(current_actor),
            }
        )
        waiting: dict[str, Any] | None = None
        if pending_decision is not None:
            waiting = {"kind": "PLAYER_DECISION", **pending_decision}
        elif pending_player_action is not None:
            waiting = {"kind": "PLAYER_ACTION", **pending_player_action}
        elif (
            pending_world_event is not None
            and task.status == AgentTaskStatus.WAITING_FOR_WORLD_EVENT
        ):
            waiting = {"kind": "WORLD_EVENT", **pending_world_event}
        return {
            "id": str(task.id),
            "player_id": str(task.player_id),
            "owner_npc_id": str(task.owner_npc_id),
            "owner_officer": _officer_ref(owner),
            "current_step_id": str(current_step.id) if current_step is not None else None,
            "current_actor_officer": _officer_ref(current_actor),
            "pending_decision": pending_decision,
            "pending_player_action": pending_player_action,
            "pending_world_event": pending_world_event,
            "waiting": waiting,
            "origin_session_id": str(task.origin_session_id),
            "last_session_id": str(task.last_session_id),
            "goal_description": task.goal_description,
            "scenario_key": task.scenario_key,
            "planning_mode": task.planning_mode,
            "status": task.status.value,
            "current_plan_version": task.current_plan_version,
            "replan_count": task.replan_count,
            "last_error_code": task.last_error_code,
            "version": task.version,
            "plans": [
                {
                    "id": str(plan.id),
                    "version": plan.version,
                    "status": plan.status.value,
                    "strategy_summary": plan.strategy_summary,
                    "replan_reason": plan.replan_reason,
                    "supersedes_plan_id": (
                        str(plan.supersedes_plan_id) if plan.supersedes_plan_id else None
                    ),
                    "source": plan.source,
                    "planner_model": plan.planner_model,
                    "validation_status": plan.validation_status,
                    "validation_errors": plan.validation_errors,
                    "created_by_officer": _officer_ref(
                        self.db.get(NPC, plan.created_by_npc_id)
                        if plan.created_by_npc_id is not None
                        else owner
                    ),
                    "steps": [
                        {
                            "id": str(step.id),
                            "sequence": step.sequence,
                            "description": step.description,
                            "execution_type": step.execution_type.value,
                            "status": step.status.value,
                            "assigned_officer": _officer_ref(
                                self.db.get(NPC, step.assigned_npc_id)
                                if step.assigned_npc_id is not None
                                else owner
                            ),
                            "assigned_officer_id": (
                                str(step.assigned_npc_id)
                                if step.assigned_npc_id is not None
                                else str(task.owner_npc_id)
                            ),
                            "action_intent": step.action_intent,
                            "constraints": step.constraints,
                            "allowed_tools": step.allowed_tool_names,
                            "selected_tool_name": step.selected_tool_name,
                            "tool_arguments": step.tool_arguments,
                            "expected_outcome": step.expected_outcome,
                            "actual_result": step.actual_result,
                            "failure_code": step.failure_code,
                            "attempts": step.attempts,
                            "resume_condition": step.resume_condition,
                            "started_at": step.started_at,
                            "completed_at": step.completed_at,
                        }
                        for step in self.plan_steps(plan.id)
                    ],
                }
                for plan in plans
            ],
        }


def _operation_id_from_step(step: AgentStep | None) -> UUID | None:
    if step is None or not isinstance(step.actual_result, dict):
        return None
    data = step.actual_result.get("data")
    raw = data.get("operation_id") if isinstance(data, dict) else None
    if not isinstance(raw, str):
        raw = step.actual_result.get("operation_id")
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _officer_ref(officer: NPC | None) -> dict[str, Any] | None:
    if officer is None:
        return None
    return {
        "id": str(officer.id),
        "key": officer.key,
        "name": officer.name,
        "role": officer.role.value,
        "profile_version": officer.profile_version,
    }
