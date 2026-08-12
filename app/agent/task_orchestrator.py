from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent.authority import effective_authority_limits
from app.agent.planning import PlanValidationIssue, PlanValidator, build_planning_request
from app.agent.providers import ProviderFailure, ProviderOutputFailure
from app.agent.strategic_starfire_plans import (
    initial_strategic_starfire_plan,
    recovery_strategic_starfire_plan,
    state_aware_strategic_recovery_plan,
)
from app.agent.task_policy import (
    IDEMPOTENT_TASK_TOOLS,
    PLAN_SECURITY_FAILURES,
    RECOVERABLE_FAILURES,
    SECURITY_FAILURES,
)
from app.agent.types import Message, ModelProvider, ToolCall, ToolContext, ToolResult
from app.core.config import Settings
from app.domain.enums import (
    AgentStepStatus,
    AgentTaskStatus,
    RunStatus,
    StepExecutionType,
    TerminationReason,
)
from app.infrastructure.db.models import (
    NPC,
    AgentPlan,
    AgentRun,
    AgentStep,
    AgentTask,
    ConversationSession,
    Memory,
    OfficerAppointment,
)
from app.services.game import GameService
from app.services.tasks import TaskService
from app.tools.catalog import build_registry
from app.tools.executor import ToolExecutor


class TaskOrchestrator:
    """Executes one auditable unit of Task -> Plan -> Step -> Tool progress."""

    def __init__(self, db: Session, provider: ModelProvider, settings: Settings):
        self.db = db
        self.provider = provider
        self.settings = settings
        self.registry = build_registry()
        self.tasks = TaskService(db, max_replans=settings.planner_max_replans)

    async def start(
        self,
        session: ConversationSession,
        goal_description: str,
        scenario_key: str,
        planning_mode: str = "PROVIDER",
    ) -> tuple[AgentTask, AgentRun | None, str]:
        task = self.tasks.create_task(
            session,
            goal_description,
            scenario_key,
            planning_mode=planning_mode,
        )
        self.db.commit()
        if self.tasks.current_plan(task) is not None:
            return task, None, "EXISTING_TASK"
        run, result = await self._generate_plan(session, task)
        return task, run, "PLANNED" if result.ok else "PLANNING_FAILED"

    async def advance(
        self,
        task_id: UUID,
        session: ConversationSession,
    ) -> tuple[AgentTask, AgentRun | None, str]:
        task = self.tasks.get_task(task_id, lock=True)
        self.tasks.bind_session(task, session)
        if task.status == AgentTaskStatus.SUCCEEDED:
            self.db.commit()
            return task, None, "ALREADY_SUCCEEDED"
        if task.status == AgentTaskStatus.BLOCKED:
            self.db.commit()
            return task, None, "BLOCKED"
        if task.status == AgentTaskStatus.REQUIRES_PLAYER_DECISION:
            self.db.commit()
            return task, None, "REQUIRES_PLAYER_DECISION"
        plan = self.tasks.current_plan(task)
        if plan is None:
            self.db.commit()
            return await self._plan_missing_task(task, session)
        failed = self.db.scalar(
            select(AgentStep)
            .where(
                AgentStep.plan_id == plan.id,
                AgentStep.status == AgentStepStatus.FAILED,
            )
            .order_by(AgentStep.sequence.desc())
        )
        if failed is not None:
            self.db.commit()
            return await self._replan(task, session, failed.failure_code or "RECOVERABLE_FAILURE")
        step = self.tasks.next_step(plan.id)
        if step is None:
            completed = self.tasks.finish_if_complete(task, plan)
            self.db.commit()
            return task, None, "TASK_SUCCEEDED" if completed else "NO_EXECUTABLE_STEP"
        if step.status == AgentStepStatus.REQUIRES_PLAYER_DECISION:
            self.db.commit()
            return task, None, "REQUIRES_PLAYER_DECISION"
        if step.execution_type != StepExecutionType.TOOL:
            outcome = self.tasks.evaluate_wait(task, step)
            self.db.commit()
            run = self._record_wait_check(session, task, plan, step, outcome)
            if outcome == "REPLAN_REQUIRED":
                return await self._replan(task, session, step.failure_code or "ENCOUNTER_DEFEAT")
            return task, run, outcome
        if step.status == AgentStepStatus.IN_PROGRESS:
            self.db.commit()
            return task, None, "STEP_IN_PROGRESS"
        if not self._claim_tool_step(step):
            self.db.refresh(task)
            return task, None, "STEP_IN_PROGRESS"
        arguments = self._resolved_arguments(task, step)
        run, result = await self._invoke_tool(
            session,
            task,
            step.selected_tool_name or "",
            arguments,
            instruction=(
                f"Execute task step {step.sequence}: {step.description}. "
                "Call exactly the selected tool with exactly the audited arguments."
            ),
            plan=plan,
            step=step,
        )
        plan = self.tasks.current_plan(task)
        assert plan is not None
        if result.ok and self.tasks.next_step(plan.id) is None:
            self.tasks.finish_if_complete(task, plan)
            self.db.commit()
        if result.code == "PLAYER_APPROVAL_REQUIRED":
            return task, run, "REQUIRES_PLAYER_DECISION"
        return task, run, "STEP_SUCCEEDED" if result.ok else "STEP_FAILED"

    async def _plan_missing_task(
        self, task: AgentTask, session: ConversationSession
    ) -> tuple[AgentTask, AgentRun | None, str]:
        run, result = await self._generate_plan(session, task)
        return task, run, "PLANNED" if result.ok else "PLANNING_FAILED"

    async def _replan(
        self,
        task: AgentTask,
        session: ConversationSession,
        reason: str,
    ) -> tuple[AgentTask, AgentRun | None, str]:
        if reason in SECURITY_FAILURES:
            task.status = AgentTaskStatus.BLOCKED
            task.last_error_code = reason
            self.db.commit()
            return task, None, "BLOCKED"
        if reason not in RECOVERABLE_FAILURES:
            task.status = AgentTaskStatus.BLOCKED
            task.last_error_code = "FAILURE_NOT_REPLANNABLE"
            self.db.commit()
            return task, None, "BLOCKED"
        if task.replan_count >= self.settings.planner_max_replans:
            task.status = AgentTaskStatus.BLOCKED
            task.last_error_code = "REPLAN_LIMIT_REACHED"
            self.db.commit()
            return task, None, "BLOCKED"
        run, result = await self._generate_plan(
            session,
            task,
            replan_reason=reason,
        )
        if not result.ok and reason in {"ENCOUNTER_DEFEAT", "TRADE_SUPPORT_REQUIRED"}:
            world = GameService(self.db).inspect_command_state(task.player_id)["world"]
            assert isinstance(world, dict)
            task.status = AgentTaskStatus.ACTIVE
            task.last_error_code = reason
            self.db.commit()
            run, result = self._submit_baseline_plan(
                session,
                task,
                state_aware_strategic_recovery_plan(
                    task.id,
                    task.current_plan_version + 1,
                    reason,
                    world,
                ),
                tool_name="replan_task",
                purpose="REPLAN",
                replan_reason=reason,
                plan_source="DETERMINISTIC_RECOVERY_FALLBACK",
            )
        return task, run, "REPLANNED" if result.ok else "REPLAN_FAILED"

    async def _generate_plan(
        self,
        session: ConversationSession,
        task: AgentTask,
        *,
        replan_reason: str | None = None,
    ) -> tuple[AgentRun, ToolResult]:
        purpose: Literal["PLAN", "REPLAN"] = "REPLAN" if replan_reason is not None else "PLAN"
        tool_name = "replan_task" if replan_reason is not None else "create_task_plan"
        owner = self.db.get(NPC, task.owner_npc_id)
        assert owner is not None
        _authority_limits, authority_policy_version = self._authority_context(
            task.player_id,
            owner,
        )
        definition = next(
            (
                item
                for item in self.registry.definitions(task.scenario_key)
                if item.name == tool_name
            ),
            None,
        )
        run = AgentRun(
            request_id=uuid4(),
            session_id=session.id,
            task_id=task.id,
            actor_npc_id=owner.id,
            officer_profile_version=owner.profile_version,
            authority_policy_version=authority_policy_version,
            model=self.provider.name,
            input_message=task.goal_description,
            max_rounds=self.settings.planner_max_generation_attempts,
            purpose=purpose,
        )
        self.db.add(run)
        self.db.commit()
        if definition is None:
            return run, self._finish_planning_failure(
                run,
                task,
                "PLANNING_TOOL_UNAVAILABLE",
                TerminationReason.INTERNAL_ERROR,
            )
        request = build_planning_request(
            db=self.db,
            registry=self.registry,
            settings=self.settings,
            task=task,
            session=session,
            kind=purpose,
            replan_reason=replan_reason,
        )
        request["next_plan_version"] = task.current_plan_version + 1
        request_json = json.dumps(request, ensure_ascii=False)
        replan_instruction = (
            " This is a replan: preserve every already succeeded step and do not "
            "repeat completed world operations or resource-consuming writes. "
            "Rebuild only the failed step's remaining suffix, following the supplied "
            "replan_guidance, current verified_state, and "
            "constraints.strategic_replan_blueprint. A step may select only a tool "
            "that is present in the current allowed_tools list."
            if replan_reason is not None
            else ""
        )
        initial_plan_instruction = (
            " For the initial starfire_command plan, follow "
            "constraints.strategic_initial_plan_blueprint exactly: use ten steps in "
            "the listed phase order and do not add a redundant inspection step."
            if replan_reason is None and task.scenario_key == "starfire_command"
            else ""
        )
        messages = [
            Message(
                role="system",
                content=(
                    "You are the constrained planner for Journey Agent. Produce no hidden "
                    "reasoning and no prose answer. Submit exactly one native tool call using "
                    f"{tool_name}. Build a concise ordered plan from the verified context. "
                    "Each TOOL step selects one allowed tool; every waiting step contains "
                    "a supported typed resume condition. Assign each strategic step to an "
                    "appointed officer. Do not include idempotency_key inside step "
                    "tool arguments. Do not execute game tools during planning. "
                    f"{replan_instruction}{initial_plan_instruction} "
                    "For starfire_command, write strategy_summary and every step "
                    "description in concise Simplified Chinese. "
                    "For each TOOL step, expected_outcome is a deterministic contract, "
                    "not a prediction. Copy every literal in that tool's "
                    "required_expected_outcomes exactly. "
                    f"PLANNER_REQUEST_JSON:{request_json}"
                ),
            )
        ]
        validator = PlanValidator(self.db, self.registry, self.settings)
        rounds: list[dict[str, object]] = []
        for attempt in range(1, self.settings.planner_max_generation_attempts + 1):
            try:
                response = await asyncio.wait_for(
                    self.provider.complete(messages, [definition]),
                    timeout=self.settings.model_timeout_seconds,
                )
            except TimeoutError:
                run.model_rounds = rounds
                return run, self._finish_planning_failure(
                    run,
                    task,
                    "MODEL_TIMEOUT",
                    TerminationReason.MODEL_TIMEOUT,
                )
            except ProviderOutputFailure:
                output_error = PlanValidationIssue(
                    code="PLAN_PROVIDER_OUTPUT_INVALID",
                    path="tool_arguments",
                    message="The provider returned malformed structured tool arguments",
                ).model_dump(mode="json")
                rounds.append(
                    {
                        "round": attempt,
                        "model": self.provider.name,
                        "token_usage": 0,
                        "tool_call_ids": [],
                        "proposal": None,
                        "plan_validation_status": "REJECTED",
                        "plan_validation_errors": [output_error],
                    }
                )
                run.actual_rounds = attempt
                run.validation_status = "REJECTED"
                run.validation_errors = [output_error]
                if attempt < self.settings.planner_max_generation_attempts:
                    messages.append(
                        Message(
                            role="system",
                            content=(
                                "The prior structured tool arguments were malformed. "
                                "Return one valid native planning tool call."
                            ),
                        )
                    )
                    continue
                run.model_rounds = rounds
                return run, self._finish_planning_failure(
                    run,
                    task,
                    "PLAN_PROVIDER_OUTPUT_INVALID",
                    TerminationReason.REPEATED_INVALID_TOOL_CALL,
                )
            except ProviderFailure as exc:
                run.validation_errors = [
                    {
                        "code": "PROVIDER_ERROR",
                        "path": "provider",
                        "message": str(exc),
                    }
                ]
                run.model_rounds = rounds
                return run, self._finish_planning_failure(
                    run,
                    task,
                    "PROVIDER_ERROR",
                    TerminationReason.PROVIDER_ERROR,
                )
            run.actual_rounds = attempt
            run.token_usage += response.token_usage
            proposal: dict[str, Any] | None = None
            call: ToolCall | None = None
            if len(response.tool_calls) == 1:
                call = response.tool_calls[0]
                proposal = call.arguments
                validation = validator.validate(
                    task=task,
                    session=session,
                    tool_name=call.name,
                    arguments=call.arguments,
                    replan_reason=replan_reason,
                )
            else:
                validation = PlanValidator._rejected(
                    "PLAN_TOOL_REQUIRED",
                    "tool_calls",
                    (
                        f"Planner returned {len(response.tool_calls)} tool calls; "
                        "exactly one is required"
                    ),
                )
            error_payload = [item.model_dump(mode="json") for item in validation.errors]
            rounds.append(
                {
                    "round": attempt,
                    "model": response.model,
                    "token_usage": response.token_usage,
                    "tool_call_ids": [item.id for item in response.tool_calls],
                    "proposal": proposal,
                    "plan_validation_status": validation.status,
                    "plan_validation_errors": error_payload,
                }
            )
            run.structured_output = proposal
            run.validation_status = validation.status
            run.validation_errors = error_payload
            if (
                validation.passed
                and validation.normalized_arguments is not None
                and call is not None
            ):
                source = "MOCK_PLANNER" if self.provider.name == "mock-model" else "MODEL_PLANNER"
                normalized_call = ToolCall(
                    id=call.id,
                    name=call.name,
                    arguments=validation.normalized_arguments,
                )
                # Preserve the accepted proposal even if execution later rolls
                # back, so a rejected planning tool remains auditable.
                self.db.commit()
                result = ToolExecutor(self.db, self.registry).execute(
                    ToolContext(
                        player_id=session.player_id,
                        npc_id=session.npc_id,
                        session_id=session.id,
                        agent_run_id=run.id,
                        message_id=run.request_id,
                        scenario_key=task.scenario_key,
                        task_id=task.id,
                        plan_source=source,
                        planner_model=response.model,
                        plan_validation_status="PASSED",
                        plan_validation_errors=[],
                    ),
                    normalized_call,
                )
                run.structured_output = validation.normalized_arguments
                run.model_rounds = rounds
                run.status = RunStatus.COMPLETED if result.ok else RunStatus.FAILED
                run.termination_reason = (
                    TerminationReason.FINAL_RESPONSE
                    if result.ok
                    else (
                        TerminationReason.SECURITY_REJECTION
                        if result.code in SECURITY_FAILURES
                        else TerminationReason.INTERNAL_ERROR
                    )
                )
                run.finished_at = datetime.now(UTC)
                if not result.ok:
                    task.status = AgentTaskStatus.BLOCKED
                    task.last_error_code = result.code
                self.db.commit()
                return run, result
            security_code = next(
                (item.code for item in validation.errors if item.code in PLAN_SECURITY_FAILURES),
                None,
            )
            if security_code is not None:
                run.model_rounds = rounds
                return run, self._finish_planning_failure(
                    run,
                    task,
                    security_code,
                    TerminationReason.SECURITY_REJECTION,
                )
            if attempt < self.settings.planner_max_generation_attempts:
                messages.append(
                    Message(
                        role="system",
                        content=(
                            "The prior plan proposal was rejected. Return one complete corrected "
                            "proposal through the required native tool call. Preserve valid "
                            "fields, step count, and valid phase ordering; change only what "
                            "the exact validation errors require. Continue to obey the initial "
                            "scenario blueprint or replan guidance. For expected_outcome, "
                            "copy required_expected_outcomes literal values exactly. "
                            "VALIDATION_ERRORS_JSON:"
                            f"{json.dumps(error_payload, ensure_ascii=False)}"
                        ),
                    )
                )
        run.model_rounds = rounds
        return run, self._finish_planning_failure(
            run,
            task,
            "PLAN_VALIDATION_FAILED",
            TerminationReason.REPEATED_INVALID_TOOL_CALL,
        )

    def _submit_baseline_plan(
        self,
        session: ConversationSession,
        task: AgentTask,
        arguments: dict[str, Any],
        *,
        tool_name: str,
        purpose: str,
        replan_reason: str | None,
        plan_source: str = "DETERMINISTIC_RECOVERY_FALLBACK",
    ) -> tuple[AgentRun, ToolResult]:
        owner = self.db.get(NPC, task.owner_npc_id)
        assert owner is not None
        _authority_limits, authority_policy_version = self._authority_context(
            task.player_id,
            owner,
        )
        run = AgentRun(
            request_id=uuid4(),
            session_id=session.id,
            task_id=task.id,
            actor_npc_id=owner.id,
            officer_profile_version=owner.profile_version,
            authority_policy_version=authority_policy_version,
            model="deterministic-baseline",
            input_message=task.goal_description,
            max_rounds=0,
            actual_rounds=0,
            model_rounds=[],
            purpose=purpose,
        )
        self.db.add(run)
        self.db.commit()
        validation = PlanValidator(self.db, self.registry, self.settings).validate(
            task=task,
            session=session,
            tool_name=tool_name,
            arguments=arguments,
            replan_reason=replan_reason,
        )
        errors = [item.model_dump(mode="json") for item in validation.errors]
        run.structured_output = validation.normalized_arguments or arguments
        run.validation_status = validation.status
        run.validation_errors = errors
        if not validation.passed or validation.normalized_arguments is None:
            return run, self._finish_planning_failure(
                run,
                task,
                "BASELINE_PLAN_INVALID",
                TerminationReason.INTERNAL_ERROR,
            )
        # ToolExecutor owns its transaction and may roll it back. Commit the
        # validator evidence first so planning failures keep their proposal.
        self.db.commit()
        result = ToolExecutor(self.db, self.registry).execute(
            ToolContext(
                player_id=session.player_id,
                npc_id=session.npc_id,
                session_id=session.id,
                agent_run_id=run.id,
                message_id=run.request_id,
                scenario_key=task.scenario_key,
                task_id=task.id,
                plan_source=plan_source,
                planner_model="deterministic-baseline",
                plan_validation_status="PASSED",
                plan_validation_errors=[],
            ),
            ToolCall(
                id=f"baseline-{purpose.lower()}-{task.id}-{task.current_plan_version + 1}",
                name=tool_name,
                arguments=validation.normalized_arguments,
            ),
        )
        run.status = RunStatus.COMPLETED if result.ok else RunStatus.FAILED
        run.termination_reason = (
            TerminationReason.FINAL_RESPONSE
            if result.ok
            else (
                TerminationReason.SECURITY_REJECTION
                if result.code in SECURITY_FAILURES
                else TerminationReason.INTERNAL_ERROR
            )
        )
        run.finished_at = datetime.now(UTC)
        if not result.ok:
            task.status = AgentTaskStatus.BLOCKED
            task.last_error_code = result.code
        self.db.commit()
        return run, result

    def _finish_planning_failure(
        self,
        run: AgentRun,
        task: AgentTask,
        code: str,
        termination: TerminationReason,
    ) -> ToolResult:
        run.status = RunStatus.FAILED
        run.termination_reason = termination
        run.finished_at = datetime.now(UTC)
        run.validation_status = run.validation_status or "REJECTED"
        if run.validation_errors is None:
            run.validation_errors = [
                PlanValidationIssue(
                    code=code,
                    path="planner",
                    message="Planning stopped safely",
                ).model_dump(mode="json")
            ]
        task.status = AgentTaskStatus.BLOCKED
        task.last_error_code = code
        self.db.commit()
        return ToolResult(
            ok=False,
            code=code,
            message="Planning stopped safely; no executable plan was accepted",
        )

    async def _invoke_tool(
        self,
        session: ConversationSession,
        task: AgentTask,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        instruction: str,
        plan: AgentPlan | None = None,
        step: AgentStep | None = None,
    ) -> tuple[AgentRun, ToolResult]:
        actor = (
            self.db.get(NPC, step.assigned_npc_id)
            if step is not None and step.assigned_npc_id is not None
            else self.db.get(NPC, task.owner_npc_id)
        )
        assert actor is not None
        authority_limits, authority_policy_version = self._authority_context(
            task.player_id,
            actor,
        )
        memories = self.db.scalars(
            select(Memory)
            .where(
                Memory.player_id == task.player_id,
                Memory.npc_id == actor.id,
            )
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(5)
        ).all()
        run = AgentRun(
            request_id=uuid4(),
            session_id=session.id,
            task_id=task.id,
            plan_id=plan.id if plan else None,
            step_id=step.id if step else None,
            actor_npc_id=actor.id,
            officer_profile_version=actor.profile_version,
            authority_policy_version=authority_policy_version,
            model=self.provider.name,
            input_message=instruction,
            max_rounds=1,
            purpose="STEP",
            context_record_ids=[str(memory.id) for memory in memories],
        )
        self.db.add(run)
        self.db.commit()
        definition = next(
            (
                item
                for item in self.registry.definitions(task.scenario_key)
                if item.name == tool_name
            ),
            None,
        )
        if definition is None:
            return run, self._finish_without_tool(
                run,
                task,
                "UNKNOWN_TOOL",
                TerminationReason.INTERNAL_ERROR,
            )
        control = {
            "tool_name": tool_name,
            "tool_call_id": f"task-{task.id}-{run.id}",
            "arguments": arguments,
        }
        system_content = (
            f"You are {actor.name}, serving as {actor.role.value}. "
            f"Persona: {actor.persona}. Doctrine: "
            f"{json.dumps(actor.doctrine, ensure_ascii=False)}. "
            f"Authority limits: {json.dumps(authority_limits, ensure_ascii=False)}. "
            f"Relevant memories: {[memory.content for memory in memories]}. "
            "You are executing one assigned step in a structured command plan. "
            "Do not reveal chain-of-thought. "
            "Return exactly one native tool call. "
            f"{instruction}\nTASK_CONTROL_JSON:{json.dumps(control)}"
        )
        try:
            response = await asyncio.wait_for(
                self.provider.complete(
                    [Message(role="system", content=system_content)],
                    [definition],
                ),
                timeout=self.settings.model_timeout_seconds,
            )
        except TimeoutError:
            return run, self._finish_without_tool(
                run,
                task,
                "MODEL_TIMEOUT",
                TerminationReason.MODEL_TIMEOUT,
            )
        except ProviderFailure:
            return run, self._finish_without_tool(
                run,
                task,
                "PROVIDER_ERROR",
                TerminationReason.PROVIDER_ERROR,
            )
        run.actual_rounds = 1
        run.token_usage = response.token_usage
        run.model_rounds = [
            {
                "round": 1,
                "model": response.model,
                "token_usage": response.token_usage,
                "tool_call_ids": [call.id for call in response.tool_calls],
            }
        ]
        if len(response.tool_calls) != 1 or response.tool_calls[0].name != tool_name:
            return run, self._finish_without_tool(
                run,
                task,
                "TASK_TOOL_REQUIRED",
                TerminationReason.REPEATED_INVALID_TOOL_CALL,
            )
        call = response.tool_calls[0]
        result = ToolExecutor(self.db, self.registry).execute(
            ToolContext(
                player_id=session.player_id,
                npc_id=actor.id,
                session_id=session.id,
                agent_run_id=run.id,
                message_id=run.request_id,
                scenario_key=task.scenario_key,
                task_id=task.id,
                plan_id=plan.id if plan else None,
                step_id=step.id if step else None,
                planned_arguments=arguments if step else None,
            ),
            ToolCall(id=call.id, name=call.name, arguments=call.arguments),
        )
        if result.ok and step is not None:
            result = self._verify_step_outcome(task, step, result)
        expected_pause = result.code == "PLAYER_APPROVAL_REQUIRED"
        run.status = RunStatus.COMPLETED if result.ok or expected_pause else RunStatus.FAILED
        run.termination_reason = (
            TerminationReason.FINAL_RESPONSE
            if result.ok or expected_pause
            else (
                TerminationReason.SECURITY_REJECTION
                if result.code in SECURITY_FAILURES
                else TerminationReason.INTERNAL_ERROR
            )
        )
        run.finished_at = datetime.now(UTC)
        self.db.commit()
        return run, result

    def _verify_step_outcome(
        self,
        task: AgentTask,
        step: AgentStep,
        result: ToolResult,
    ) -> ToolResult:
        data = result.data if isinstance(result.data, dict) else {}
        mismatches: dict[str, dict[str, Any]] = {}
        for key, expected in step.expected_outcome.items():
            if key.endswith("_min"):
                actual_key = key.removesuffix("_min")
                actual = data.get(actual_key)
                if not isinstance(actual, (int, float)) or actual < expected:
                    mismatches[key] = {"expected": expected, "actual": actual}
            elif data.get(key) != expected:
                mismatches[key] = {"expected": expected, "actual": data.get(key)}
        if not mismatches:
            return result
        step.status = AgentStepStatus.FAILED
        asynchronous_side_effect = isinstance(data.get("operation_id"), str)
        failure_code = (
            "WORLD_OPERATION_CONTRACT_VIOLATION"
            if asynchronous_side_effect
            else "EXPECTED_OUTCOME_NOT_MET"
        )
        step.failure_code = failure_code
        step.actual_result = {
            "tool_result": result.model_dump(mode="json"),
            "verification_mismatches": mismatches,
        }
        step.completed_at = datetime.now(UTC)
        task.status = (
            AgentTaskStatus.BLOCKED if asynchronous_side_effect else AgentTaskStatus.ACTIVE
        )
        task.last_error_code = failure_code
        task.version += 1
        self.db.commit()
        return ToolResult(
            ok=False,
            code=failure_code,
            message="The tool ran, but the step's expected outcome was not verified",
            retryable=True,
            data={"mismatches": mismatches},
        )

    def _finish_without_tool(
        self,
        run: AgentRun,
        task: AgentTask,
        code: str,
        termination: TerminationReason,
    ) -> ToolResult:
        run.status = RunStatus.FAILED
        run.termination_reason = termination
        run.finished_at = datetime.now(UTC)
        task.last_error_code = code
        step = self.db.get(AgentStep, run.step_id) if run.step_id is not None else None
        if step is not None and step.status == AgentStepStatus.IN_PROGRESS:
            if code in {"MODEL_TIMEOUT", "PROVIDER_ERROR", "TASK_TOOL_REQUIRED"}:
                step.status = AgentStepStatus.PENDING
                step.failure_code = code
                step.started_at = None
            else:
                step.status = AgentStepStatus.FAILED
                step.failure_code = code
                step.completed_at = datetime.now(UTC)
        if code in SECURITY_FAILURES:
            task.status = AgentTaskStatus.BLOCKED
        self.db.commit()
        return ToolResult(ok=False, code=code, message="Task execution stopped safely")

    def _record_wait_check(
        self,
        session: ConversationSession,
        task: AgentTask,
        plan: AgentPlan,
        step: AgentStep,
        outcome: str,
    ) -> AgentRun:
        actor = (
            self.db.get(NPC, step.assigned_npc_id)
            if step.assigned_npc_id is not None
            else self.db.get(NPC, task.owner_npc_id)
        )
        assert actor is not None
        _authority_limits, authority_policy_version = self._authority_context(
            task.player_id,
            actor,
        )
        run = AgentRun(
            request_id=uuid4(),
            session_id=session.id,
            task_id=task.id,
            plan_id=plan.id,
            step_id=step.id,
            actor_npc_id=actor.id,
            officer_profile_version=actor.profile_version,
            authority_policy_version=authority_policy_version,
            model="deterministic-resume-check",
            input_message=f"Evaluate resume condition for step {step.sequence}",
            max_rounds=0,
            actual_rounds=0,
            model_rounds=[],
            status=RunStatus.COMPLETED,
            termination_reason=TerminationReason.FINAL_RESPONSE,
            finished_at=datetime.now(UTC),
            purpose="WAIT_CHECK",
        )
        self.db.add(run)
        self.db.commit()
        return run

    def _authority_context(
        self,
        player_id: UUID,
        officer: NPC,
    ) -> tuple[dict[str, Any], int]:
        appointment = self.db.get(OfficerAppointment, (player_id, officer.id))
        if appointment is not None and appointment.status == "ACTIVE":
            return (
                effective_authority_limits(
                    officer,
                    appointment.authority_overrides,
                ),
                appointment.version,
            )
        return effective_authority_limits(officer), officer.profile_version

    def _resolved_arguments(self, task: AgentTask, step: AgentStep) -> dict[str, Any]:
        arguments = dict(step.tool_arguments)
        if step.selected_tool_name in IDEMPOTENT_TASK_TOOLS:
            arguments["idempotency_key"] = (
                f"task-{task.id}-plan-{task.current_plan_version}-step-{step.sequence}"
            )
        return arguments

    def _claim_tool_step(self, step: AgentStep) -> bool:
        claimed = self.db.execute(
            update(AgentStep)
            .where(
                AgentStep.id == step.id,
                AgentStep.status == AgentStepStatus.PENDING,
            )
            .values(
                status=AgentStepStatus.IN_PROGRESS,
                started_at=step.started_at or datetime.now(UTC),
            )
            .execution_options(synchronize_session=False)
        )
        succeeded = bool(getattr(claimed, "rowcount", 0) == 1)
        self.db.commit()
        self.db.expire(step)
        return succeeded

    @staticmethod
    def _initial_plan(task: AgentTask) -> dict[str, Any]:
        return initial_strategic_starfire_plan(task.id)

    @staticmethod
    def _recovery_plan(task: AgentTask, reason: str) -> dict[str, Any]:
        next_version = task.current_plan_version + 1
        return recovery_strategic_starfire_plan(task.id, next_version, reason)
