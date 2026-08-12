from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import perf_counter

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.authority import evaluate_authority
from app.agent.types import ToolCall, ToolContext, ToolResult
from app.core.errors import AppError
from app.domain.enums import (
    AgentStepStatus,
    AgentTaskStatus,
    AuthorityOutcome,
    DecisionStatus,
)
from app.infrastructure.db.models import (
    NPC,
    AgentRun,
    AgentStep,
    AgentTask,
    ConversationSession,
    OfficerAppointment,
    PlayerDecisionRequest,
    ToolExecution,
)
from app.tools.handlers import snapshot
from app.tools.interaction_validation import resolve_tool_interaction
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, db: Session, registry: ToolRegistry):
        self.db = db
        self.registry = registry

    def execute(self, context: ToolContext, call: ToolCall) -> ToolResult:
        started = perf_counter()
        actual_profile_version: int | None = None
        actual_policy_version: int | None = None
        proposal_policy_version: int | None = None
        tool = self.registry.get(call.name)
        trace = ToolExecution(
            agent_run_id=context.agent_run_id,
            step_id=context.step_id,
            tool_call_id=call.id,
            tool_name=call.name,
            arguments=call.arguments,
            validation_status="PENDING",
            authorization_status="PENDING",
            business_rule_status="PENDING",
            execution_status="PENDING",
        )
        self.db.add(trace)
        try:
            if not tool:
                raise AppError("UNKNOWN_TOOL", "Tool is not registered")
            try:
                args = tool.arguments_model.model_validate(call.arguments)
                trace.validation_status = "PASSED"
            except ValidationError:
                trace.validation_status = "FAILED"
                raise AppError(
                    "INVALID_TOOL_ARGUMENTS",
                    "Tool arguments failed schema validation",
                    details={"tool_name": call.name},
                ) from None
            npc = self.db.scalar(
                select(NPC)
                .where(NPC.id == context.npc_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if not npc or not npc.enabled:
                raise AppError("NPC_PERMISSION_DENIED", "NPC is disabled", status_code=403)
            run = self.db.get(AgentRun, context.agent_run_id)
            if run is not None:
                proposal_policy_version = run.authority_policy_version
                actual_profile_version = npc.profile_version
                run.officer_profile_version = actual_profile_version
            if tool.allowed_roles and npc.role.value not in tool.allowed_roles:
                trace.authorization_status = "DENIED"
                trace.authority_details = {
                    "outcome": AuthorityOutcome.DENY.value,
                    "reason_code": "ROLE_CAPABILITY_DENIED",
                }
                raise AppError(
                    "NPC_PERMISSION_DENIED",
                    "NPC role cannot execute this tool",
                    status_code=403,
                )
            if tool.require_permission_profile and not npc.permission_profile.get(call.name, False):
                trace.authorization_status = "DENIED"
                trace.authority_details = {
                    "outcome": AuthorityOutcome.DENY.value,
                    "reason_code": "TOOL_PERMISSION_DENIED",
                }
                raise AppError(
                    "NPC_PERMISSION_DENIED",
                    "NPC permission profile does not allow this tool",
                    status_code=403,
                )
            step = (
                self.db.scalar(
                    select(AgentStep)
                    .where(AgentStep.id == context.step_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if context.step_id
                else None
            )
            if context.step_id and step is None:
                raise AppError("TASK_STEP_NOT_FOUND", "The task step was not found")
            if step is not None:
                if step.assigned_npc_id is not None and step.assigned_npc_id != context.npc_id:
                    trace.authorization_status = "DENIED"
                    trace.authority_details = {
                        "outcome": AuthorityOutcome.DENY.value,
                        "reason_code": "STEP_ACTOR_MISMATCH",
                    }
                    raise AppError(
                        "STEP_ACTOR_MISMATCH",
                        "The executing officer is not assigned to this task step",
                        status_code=403,
                    )
                if step.selected_tool_name != call.name:
                    trace.authorization_status = "DENIED"
                    raise AppError(
                        "STEP_TOOL_MISMATCH",
                        "The tool does not match the audited task step",
                        status_code=403,
                    )
                if (
                    context.planned_arguments is not None
                    and call.arguments != context.planned_arguments
                ):
                    trace.authorization_status = "DENIED"
                    raise AppError(
                        "STEP_ARGUMENT_MISMATCH",
                        "Tool arguments differ from the audited task step",
                        status_code=403,
                    )
                if step.status not in {
                    AgentStepStatus.PENDING,
                    AgentStepStatus.IN_PROGRESS,
                }:
                    raise AppError(
                        "STEP_NOT_EXECUTABLE",
                        "The task step is not executable in its current state",
                    )
                step.status = AgentStepStatus.IN_PROGRESS
                step.attempts += 1
                step.started_at = step.started_at or datetime.now(UTC)
            appointment = None
            if npc.role.value in {"STRATEGIST", "GENERAL", "STEWARD"}:
                appointment = self.db.scalar(
                    select(OfficerAppointment)
                    .where(
                        OfficerAppointment.player_id == context.player_id,
                        OfficerAppointment.npc_id == npc.id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                actual_policy_version = (
                    appointment.version if appointment is not None else npc.profile_version
                )
                if run is not None:
                    run.authority_policy_version = actual_policy_version
                if appointment is None or appointment.status != "ACTIVE":
                    trace.authorization_status = "DENIED"
                    trace.authority_details = {
                        "outcome": AuthorityOutcome.DENY.value,
                        "reason_code": "OFFICER_NOT_APPOINTED",
                        "policy_version": actual_policy_version,
                        "proposal_policy_version": proposal_policy_version,
                        "policy_changed_since_prompt": (
                            proposal_policy_version is not None
                            and proposal_policy_version != actual_policy_version
                        ),
                    }
                    raise AppError(
                        "OFFICER_NOT_APPOINTED",
                        "The officer is not appointed to this player's domain",
                        status_code=403,
                    )
            else:
                actual_policy_version = npc.profile_version
                if run is not None:
                    run.authority_policy_version = actual_policy_version
            if tool.interaction_requirement is not None:
                resolve_tool_interaction(
                    context.scenario_key,
                    tool.interaction_requirement,
                    args,
                )
            if tool.preflight is not None:
                tool.preflight(self.db, context, args)
            if tool.interaction_requirement is not None or tool.preflight is not None:
                trace.business_rule_status = "PREFLIGHT_PASSED"
            authority = evaluate_authority(
                npc,
                call.name,
                call.arguments,
                authority_overrides=(
                    appointment.authority_overrides if appointment is not None else None
                ),
                policy_version=(
                    appointment.version if appointment is not None else npc.profile_version
                ),
            )
            trace.authority_details = {
                "outcome": authority.outcome.value,
                "reason_code": authority.reason_code,
                "summary": authority.summary,
                **authority.details,
                "proposal_policy_version": proposal_policy_version,
                "policy_changed_since_prompt": (
                    proposal_policy_version is not None
                    and proposal_policy_version != actual_policy_version
                ),
            }
            if authority.outcome == AuthorityOutcome.DENY:
                trace.authorization_status = "DENIED"
                raise AppError(
                    authority.reason_code,
                    authority.summary,
                    status_code=403,
                )
            if authority.outcome == AuthorityOutcome.REQUIRE_PLAYER_DECISION:
                approved = self._approved_decision(context, call)
                if approved is None:
                    return self._pause_for_player_decision(
                        context=context,
                        call=call,
                        trace=trace,
                        officer=npc,
                        summary=authority.summary,
                        policy_snapshot=trace.authority_details,
                        started=started,
                    )
                approved.status = DecisionStatus.CONSUMED
                approved.consumed_at = datetime.now(UTC)
                trace.authority_details = {
                    **(trace.authority_details or {}),
                    "outcome": AuthorityOutcome.ALLOW.value,
                    "reason_code": "PLAYER_APPROVAL_CONSUMED",
                    "approval_id": str(approved.id),
                }
            trace.authorization_status = "PASSED"
            idempotency_key = call.arguments.get("idempotency_key")
            trace.idempotency_key = (
                str(idempotency_key) if isinstance(idempotency_key, str) else None
            )
            if tool.write and trace.idempotency_key:
                prior = self.db.scalar(
                    select(ToolExecution)
                    .join(AgentRun, AgentRun.id == ToolExecution.agent_run_id)
                    .join(
                        ConversationSession,
                        ConversationSession.id == AgentRun.session_id,
                    )
                    .where(
                        ConversationSession.player_id == context.player_id,
                        ToolExecution.idempotency_key == trace.idempotency_key,
                        ToolExecution.execution_status == "SUCCEEDED",
                    )
                )
                if prior and prior.result:
                    if prior.tool_name != call.name or prior.arguments != call.arguments:
                        raise AppError(
                            "IDEMPOTENCY_KEY_REUSED",
                            "The idempotency key is already bound to a different action",
                            status_code=409,
                        )
                    self.db.expunge(trace)
                    replay = ToolResult.model_validate(prior.result)
                    if step is not None:
                        step.status = AgentStepStatus.SUCCEEDED
                        step.actual_result = replay.model_dump(mode="json")
                        step.failure_code = None
                        step.completed_at = datetime.now(UTC)
                        self.db.commit()
                    return replay
            trace.before_state = snapshot(self.db, context) if tool.write else None
            data = tool.handler(self.db, context, args)
            trace.business_rule_status = "PASSED"
            trace.after_state = snapshot(self.db, context) if tool.write else None
            result = ToolResult(
                ok=True,
                code="OK",
                message="Tool executed successfully",
                data=data,
            )
            trace.execution_status = "SUCCEEDED"
            trace.result = result.model_dump(mode="json")
            if step is not None:
                step.status = AgentStepStatus.SUCCEEDED
                step.actual_result = result.model_dump(mode="json")
                step.failure_code = None
                step.completed_at = datetime.now(UTC)
            trace.duration_ms = int((perf_counter() - started) * 1000)
            self.db.commit()
            return result
        except AppError as exc:
            self.db.rollback()
            self._apply_run_policy_versions(
                context,
                actual_profile_version,
                actual_policy_version,
            )
            failure = ToolExecution(
                agent_run_id=context.agent_run_id,
                step_id=context.step_id,
                tool_call_id=call.id,
                tool_name=call.name,
                arguments=call.arguments,
                validation_status=trace.validation_status,
                authorization_status=trace.authorization_status,
                authority_details=trace.authority_details,
                business_rule_status="FAILED",
                execution_status="FAILED",
                error_code=exc.code,
                duration_ms=int((perf_counter() - started) * 1000),
                idempotency_key=trace.idempotency_key,
            )
            result = ToolResult(
                ok=False,
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
            )
            failure.result = result.model_dump(mode="json")
            self.db.add(failure)
            self._mark_step_failed(context, result)
            self.db.commit()
            return result
        except IntegrityError:
            self.db.rollback()
            self._apply_run_policy_versions(
                context,
                actual_profile_version,
                actual_policy_version,
            )
            result = ToolResult(
                ok=False,
                code="STATE_VERSION_CONFLICT",
                message="Concurrent state change was rejected",
                retryable=True,
            )
            self._persist_internal_failure(context, call, result, started)
            return result
        except Exception:
            logger.exception(
                "Unexpected tool execution failure",
                extra={
                    "tool_name": call.name,
                    "agent_run_id": str(context.agent_run_id),
                    "step_id": str(context.step_id) if context.step_id else None,
                },
            )
            self.db.rollback()
            self._apply_run_policy_versions(
                context,
                actual_profile_version,
                actual_policy_version,
            )
            result = ToolResult(
                ok=False,
                code="INTERNAL_ERROR",
                message="Tool execution failed safely",
            )
            self._persist_internal_failure(context, call, result, started)
            return result

    def _apply_run_policy_versions(
        self,
        context: ToolContext,
        profile_version: int | None,
        policy_version: int | None,
    ) -> None:
        run = self.db.get(AgentRun, context.agent_run_id)
        if run is None:
            return
        if profile_version is not None:
            run.officer_profile_version = profile_version
        if policy_version is not None:
            run.authority_policy_version = policy_version

    def _persist_internal_failure(
        self, context: ToolContext, call: ToolCall, result: ToolResult, started: float
    ) -> None:
        self.db.add(
            ToolExecution(
                agent_run_id=context.agent_run_id,
                step_id=context.step_id,
                tool_call_id=call.id,
                tool_name=call.name,
                arguments=call.arguments,
                validation_status="PASSED",
                authorization_status="PASSED",
                business_rule_status="FAILED",
                execution_status="FAILED",
                result=result.model_dump(mode="json"),
                error_code=result.code,
                duration_ms=int((perf_counter() - started) * 1000),
            )
        )
        self._mark_step_failed(context, result)
        self.db.commit()

    def _mark_step_failed(self, context: ToolContext, result: ToolResult) -> None:
        if context.step_id is None:
            return
        step = self.db.get(AgentStep, context.step_id)
        task = self.db.get(AgentTask, context.task_id) if context.task_id else None
        if step is None:
            return
        security_failure = result.code in {
            "NPC_PERMISSION_DENIED",
            "STEP_TOOL_MISMATCH",
            "STEP_ARGUMENT_MISMATCH",
            "TASK_PLAYER_MISMATCH",
            "TASK_NPC_MISMATCH",
            "STEP_ACTOR_MISMATCH",
            "OFFICER_NOT_APPOINTED",
            "AUTHORITY_POLICY_INVALID",
            "IDEMPOTENCY_KEY_REUSED",
        }
        step.status = AgentStepStatus.BLOCKED if security_failure else AgentStepStatus.FAILED
        step.failure_code = result.code
        step.actual_result = result.model_dump(mode="json")
        step.completed_at = datetime.now(UTC)
        if task is not None:
            task.status = AgentTaskStatus.BLOCKED if security_failure else AgentTaskStatus.ACTIVE
            task.last_error_code = result.code
            task.version += 1

    def _approved_decision(
        self,
        context: ToolContext,
        call: ToolCall,
    ) -> PlayerDecisionRequest | None:
        if context.task_id is None or context.step_id is None:
            return None
        candidates = self.db.scalars(
            select(PlayerDecisionRequest)
            .where(
                PlayerDecisionRequest.task_id == context.task_id,
                PlayerDecisionRequest.step_id == context.step_id,
                PlayerDecisionRequest.requested_by_npc_id == context.npc_id,
                PlayerDecisionRequest.action_tool_name == call.name,
                PlayerDecisionRequest.status == DecisionStatus.APPROVED,
            )
            .with_for_update()
        ).all()
        return next(
            (item for item in candidates if item.action_arguments == call.arguments),
            None,
        )

    def _pause_for_player_decision(
        self,
        *,
        context: ToolContext,
        call: ToolCall,
        trace: ToolExecution,
        officer: NPC,
        summary: str,
        policy_snapshot: dict[str, object],
        started: float,
    ) -> ToolResult:
        if context.task_id is None or context.step_id is None:
            raise AppError(
                "PLAYER_APPROVAL_REQUIRED",
                "The action exceeds autonomous authority and requires a task decision",
            )
        pending = list(
            self.db.scalars(
                select(PlayerDecisionRequest).where(
                    PlayerDecisionRequest.task_id == context.task_id,
                    PlayerDecisionRequest.step_id == context.step_id,
                    PlayerDecisionRequest.status == DecisionStatus.PENDING,
                )
            ).all()
        )
        decision = next(
            (
                item
                for item in pending
                if item.action_tool_name == call.name and item.action_arguments == call.arguments
            ),
            None,
        )
        if decision is None:
            decision = PlayerDecisionRequest(
                player_id=context.player_id,
                task_id=context.task_id,
                step_id=context.step_id,
                requested_by_npc_id=context.npc_id,
                decision_kind="AUTHORITY_OVERRIDE",
                summary=summary,
                options=[
                    {
                        "id": "APPROVE",
                        "label": "批准此项精确行动",
                        "expected_cost": policy_snapshot.get("exceeded_limits", []),
                        "expected_risk": "MEDIUM",
                        "irreversible": False,
                    },
                    {
                        "id": "REJECT",
                        "label": "拒绝并要求重新制定方案",
                        "expected_cost": {},
                        "expected_risk": "LOW",
                        "irreversible": False,
                    },
                ],
                action_tool_name=call.name,
                action_arguments=call.arguments,
                policy_snapshot=policy_snapshot,
            )
            self.db.add(decision)
            self.db.flush()
        step = self.db.get(AgentStep, context.step_id)
        task = self.db.get(AgentTask, context.task_id)
        assert step is not None and task is not None
        step.status = AgentStepStatus.REQUIRES_PLAYER_DECISION
        step.actual_result = {
            "decision_id": str(decision.id),
            "status": DecisionStatus.PENDING.value,
        }
        step.failure_code = None
        task.status = AgentTaskStatus.REQUIRES_PLAYER_DECISION
        task.last_error_code = None
        task.version += 1
        trace.authorization_status = AuthorityOutcome.REQUIRE_PLAYER_DECISION.value
        trace.business_rule_status = (
            "PREFLIGHT_PASSED" if trace.business_rule_status == "PREFLIGHT_PASSED" else "NOT_RUN"
        )
        trace.execution_status = "WAITING"
        result = ToolResult(
            ok=False,
            code="PLAYER_APPROVAL_REQUIRED",
            message="The exact action requires the player's decision before execution",
            data={"decision_id": str(decision.id), "options": decision.options},
        )
        trace.result = result.model_dump(mode="json")
        trace.duration_ms = int((perf_counter() - started) * 1000)
        self.db.commit()
        return result
