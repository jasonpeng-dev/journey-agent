from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import authority_policy_errors, effective_authority_limits
from app.core.config import Settings
from app.domain.enums import AgentStepStatus, StepExecutionType
from app.infrastructure.db.models import (
    NPC,
    AgentStep,
    AgentTask,
    ConversationSession,
    Memory,
    OfficerAppointment,
)
from app.scenarios.contracts import ScenarioPlanningPolicy, ScenarioRuntimeState
from app.scenarios.registry import scenario_binding
from app.services.game import GameService
from app.services.tasks import TaskService
from app.tools.handlers import CreateTaskPlanArgs, ReplanTaskArgs
from app.tools.registry import ToolRegistry


class PlanValidationIssue(BaseModel):
    code: str
    path: str
    message: str


class PlanValidationResult(BaseModel):
    status: Literal["PASSED", "REJECTED"]
    normalized_arguments: dict[str, Any] | None = None
    errors: list[PlanValidationIssue] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "PASSED"


class PlanValidator:
    """Validates a complete model proposal before any Plan rows are changed."""

    def __init__(self, db: Session, registry: ToolRegistry, settings: Settings):
        self.db = db
        self.registry = registry
        self.settings = settings

    def validate(
        self,
        *,
        task: AgentTask,
        session: ConversationSession,
        tool_name: str,
        arguments: dict[str, Any],
        replan_reason: str | None = None,
    ) -> PlanValidationResult:
        issues: list[PlanValidationIssue] = []
        expected_tool = "replan_task" if replan_reason is not None else "create_task_plan"
        if tool_name != expected_tool:
            return self._rejected(
                "PLAN_SUBMISSION_TOOL_INVALID",
                "tool_name",
                f"Planner must call {expected_tool}",
            )
        model_type = ReplanTaskArgs if replan_reason is not None else CreateTaskPlanArgs
        try:
            parsed = model_type.model_validate(arguments)
        except ValidationError as exc:
            return PlanValidationResult(
                status="REJECTED",
                errors=[
                    PlanValidationIssue(
                        code="PLAN_SCHEMA_INVALID",
                        path=".".join(str(part) for part in error["loc"]),
                        message=str(error["msg"]),
                    )
                    for error in exc.errors()[:12]
                ],
            )
        if parsed.task_id != task.id:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_TASK_MISMATCH",
                    path="task_id",
                    message="The proposal targets a different task",
                )
            )
        if len(parsed.steps) > self.settings.planner_max_steps:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_STEP_LIMIT_EXCEEDED",
                    path="steps",
                    message=(
                        f"Plan has {len(parsed.steps)} steps; "
                        f"the limit is {self.settings.planner_max_steps}"
                    ),
                )
            )
        owner_npc = self.db.get(NPC, session.npc_id)
        if owner_npc is None or not owner_npc.enabled:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_NPC_UNAVAILABLE",
                    path="npc_id",
                    message="The task NPC is unavailable",
                )
            )
            return PlanValidationResult(status="REJECTED", errors=issues)
        scenario = scenario_binding(task.scenario_key)
        if scenario is None:
            return self._rejected(
                "PLAN_SCENARIO_UNSUPPORTED",
                "scenario_key",
                "The task scenario does not provide a planning policy",
            )
        policy = scenario.planning_policy
        scenario_state = GameService(self.db).scenario_state(task.player_id)

        wait_count = 0
        descriptions: set[str] = set()
        selected_tools: list[str] = []
        for index, step in enumerate(parsed.steps):
            path = f"steps.{index}"
            description_key = step.description.strip().casefold()
            if description_key in descriptions:
                issues.append(
                    PlanValidationIssue(
                        code="PLAN_DUPLICATE_STEP",
                        path=f"{path}.description",
                        message="Step descriptions must be distinct",
                    )
                )
            descriptions.add(description_key)
            try:
                execution_type = StepExecutionType(step.execution_type)
            except ValueError:
                issues.append(
                    PlanValidationIssue(
                        code="PLAN_EXECUTION_TYPE_INVALID",
                        path=f"{path}.execution_type",
                        message="The execution type is not supported",
                    )
                )
                continue
            step_npc = self._resolve_step_officer(
                task=task,
                owner_npc=owner_npc,
                officer_key=step.assigned_officer_key,
                path=path,
                issues=issues,
            )
            if step_npc is None:
                continue
            if execution_type == StepExecutionType.WAIT_FOR_WORLD_EVENT:
                wait_count += 1
                self._validate_world_wait_step(
                    step.model_dump(mode="json"),
                    index=index,
                    all_steps=parsed.steps,
                    path=path,
                    issues=issues,
                    policy=policy,
                )
                continue
            if execution_type == StepExecutionType.WAIT_FOR_PLAYER_ACTION:
                wait_count += 1
                self._validate_player_action_wait_step(
                    step.model_dump(mode="json"),
                    path,
                    issues,
                    policy,
                )
                continue
            tool_name_for_step = step.selected_tool_name
            if not isinstance(tool_name_for_step, str):
                issues.append(
                    PlanValidationIssue(
                        code="PLAN_TOOL_REQUIRED",
                        path=f"{path}.selected_tool_name",
                        message="A TOOL step must select one tool",
                    )
                )
                continue
            selected_tools.append(tool_name_for_step)
            if step.allowed_tool_names and tool_name_for_step not in step.allowed_tool_names:
                issues.append(
                    PlanValidationIssue(
                        code="PLAN_SELECTED_TOOL_OUTSIDE_STEP_BOUNDARY",
                        path=f"{path}.allowed_tool_names",
                        message="The selected tool is outside the step's declared tool boundary",
                    )
                )
            self._validate_tool_step(
                npc=step_npc,
                tool_name=tool_name_for_step,
                tool_arguments=step.tool_arguments,
                expected_outcome=step.expected_outcome,
                path=path,
                issues=issues,
                policy=policy,
            )
        if wait_count > self.settings.planner_max_wait_steps:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_WAIT_LIMIT_EXCEEDED",
                    path="steps",
                    message=(
                        f"Plan has {wait_count} waiting steps; "
                        f"the limit is {self.settings.planner_max_wait_steps}"
                    ),
                )
            )
        if not selected_tools:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_HAS_NO_EXECUTION",
                    path="steps",
                    message="A plan must contain at least one executable tool step",
                )
            )
        parsed_steps = parsed.model_dump(mode="json")["steps"]
        issues.extend(
            PlanValidationIssue(code=item.code, path=item.path, message=item.message)
            for item in policy.validate_candidate_plan(
                parsed_steps,
                selected_tools,
                wait_count,
                is_replan=replan_reason is not None,
                state=scenario_state,
            )
        )
        self._validate_operation_wait_pairing(parsed.steps, issues, policy)
        if replan_reason is not None:
            self._validate_replan(
                task,
                parsed_steps,
                issues,
                policy,
                scenario_state,
            )
        if issues:
            return PlanValidationResult(status="REJECTED", errors=issues)

        normalized = parsed.model_dump(mode="json")
        normalized["task_id"] = str(task.id)
        normalized["idempotency_key"] = (
            f"task-replan-{task.id}-v{task.current_plan_version + 1}"
            if replan_reason is not None
            else f"task-plan-{task.id}-v1"
        )
        if replan_reason is not None:
            normalized["replan_reason"] = replan_reason
        return PlanValidationResult(
            status="PASSED",
            normalized_arguments=normalized,
        )

    def _resolve_step_officer(
        self,
        *,
        task: AgentTask,
        owner_npc: NPC,
        officer_key: str | None,
        path: str,
        issues: list[PlanValidationIssue],
    ) -> NPC | None:
        officer = (
            owner_npc
            if officer_key is None
            else self.db.scalar(select(NPC).where(NPC.key == officer_key))
        )
        if officer is None or not officer.enabled:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_OFFICER_UNAVAILABLE",
                    path=f"{path}.assigned_officer_key",
                    message=f"Officer {officer_key} is unavailable",
                )
            )
            return None
        appointment = self.db.get(OfficerAppointment, (task.player_id, officer.id))
        if appointment is None or appointment.status != "ACTIVE":
            issues.append(
                PlanValidationIssue(
                    code="PLAN_OFFICER_NOT_APPOINTED",
                    path=f"{path}.assigned_officer_key",
                    message=f"Officer {officer_key} is not appointed to this player's domain",
                )
            )
            return None
        policy_errors = authority_policy_errors(
            officer.authority_limits,
            appointment.authority_overrides,
        )
        if policy_errors:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_AUTHORITY_POLICY_INVALID",
                    path=f"{path}.assigned_officer_key",
                    message=f"Officer {officer.key} has an invalid appointment authority policy",
                )
            )
            return None
        return officer

    def _validate_tool_step(
        self,
        *,
        npc: NPC,
        tool_name: str,
        tool_arguments: dict[str, Any],
        expected_outcome: dict[str, Any],
        path: str,
        issues: list[PlanValidationIssue],
        policy: ScenarioPlanningPolicy,
    ) -> None:
        if tool_name not in policy.execution_tools:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_TOOL_NOT_ALLOWED",
                    path=f"{path}.selected_tool_name",
                    message=f"{tool_name} is not an allowed task execution tool",
                )
            )
            return
        tool = self.registry.get(tool_name)
        if tool is None:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_UNKNOWN_TOOL",
                    path=f"{path}.selected_tool_name",
                    message=f"{tool_name} is not registered",
                )
            )
            return
        if tool.allowed_roles and npc.role.value not in tool.allowed_roles:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_TOOL_UNAUTHORIZED",
                    path=f"{path}.selected_tool_name",
                    message=f"{npc.role.value} cannot execute {tool_name}",
                )
            )
        if tool.require_permission_profile and not npc.permission_profile.get(tool_name, False):
            issues.append(
                PlanValidationIssue(
                    code="PLAN_TOOL_UNAUTHORIZED",
                    path=f"{path}.selected_tool_name",
                    message=f"The NPC permission profile does not allow {tool_name}",
                )
            )
        if "idempotency_key" in tool_arguments:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_IDEMPOTENCY_SERVER_CONTROLLED",
                    path=f"{path}.tool_arguments.idempotency_key",
                    message="Task idempotency keys are generated by the backend",
                )
            )
        final_arguments = dict(tool_arguments)
        if tool_name in policy.idempotent_tools:
            final_arguments["idempotency_key"] = "planner-validation-key"
        try:
            tool.arguments_model.model_validate(final_arguments)
        except ValidationError as exc:
            issues.extend(
                PlanValidationIssue(
                    code="PLAN_TOOL_ARGUMENTS_INVALID",
                    path=(
                        f"{path}.tool_arguments." + ".".join(str(part) for part in error["loc"])
                    ).rstrip("."),
                    message=str(error["msg"]),
                )
                for error in exc.errors()[:6]
            )
        allowed_outcomes = policy.expected_outcome_fields.get(tool_name, frozenset())
        if not expected_outcome:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_EXPECTED_OUTCOME_REQUIRED",
                    path=f"{path}.expected_outcome",
                    message="Every tool step must define a concise expected outcome",
                )
            )
        for key, value in expected_outcome.items():
            if key not in allowed_outcomes:
                issues.append(
                    PlanValidationIssue(
                        code="PLAN_EXPECTED_OUTCOME_INVALID",
                        path=f"{path}.expected_outcome.{key}",
                        message=f"{key} is not a verifiable output of {tool_name}",
                    )
                )
            if isinstance(value, (dict, list)):
                issues.append(
                    PlanValidationIssue(
                        code="PLAN_EXPECTED_OUTCOME_COMPLEX",
                        path=f"{path}.expected_outcome.{key}",
                        message="Expected outcomes must use scalar values",
                    )
                )
        fixed = policy.fixed_tool_expected_outcomes.get(tool_name)
        if fixed is not None:
            for key, required_value in fixed.items():
                if expected_outcome.get(key) != required_value:
                    issues.append(
                        PlanValidationIssue(
                            code="PLAN_EXPECTED_OUTCOME_VALUE_INVALID",
                            path=f"{path}.expected_outcome.{key}",
                            message=(f"{tool_name} must expect {key}={required_value}"),
                        )
                    )
            requirement = tool.interaction_requirement
            target_arguments = (
                (requirement.target_argument,)
                if requirement is not None and requirement.target_argument is not None
                else ()
            ) + (requirement.legacy_target_arguments if requirement is not None else ())
            expected_target = next(
                (
                    tool_arguments[argument]
                    for argument in target_arguments
                    if argument in tool_arguments
                ),
                None,
            )
            if (
                "target_key" in expected_outcome
                and expected_outcome.get("target_key") != expected_target
            ):
                issues.append(
                    PlanValidationIssue(
                        code="PLAN_EXPECTED_OUTCOME_VALUE_INVALID",
                        path=f"{path}.expected_outcome.target_key",
                        message=(
                            "The expected operation target must match the selected tool arguments"
                        ),
                    )
                )

    def _validate_generic_wait_step(
        self,
        step: dict[str, Any],
        path: str,
        issues: list[PlanValidationIssue],
    ) -> None:
        if step.get("selected_tool_name") is not None or step.get("tool_arguments"):
            issues.append(
                PlanValidationIssue(
                    code="PLAN_WAIT_TOOL_INVALID",
                    path=path,
                    message="A waiting step cannot select a tool or arguments",
                )
            )
        if not isinstance(step.get("resume_condition"), dict):
            issues.append(
                PlanValidationIssue(
                    code="PLAN_RESUME_CONDITION_INVALID",
                    path=f"{path}.resume_condition",
                    message="A waiting step requires a structured resume condition",
                )
            )

    def _validate_world_wait_step(
        self,
        step: dict[str, Any],
        *,
        index: int,
        all_steps: list[Any],
        path: str,
        issues: list[PlanValidationIssue],
        policy: ScenarioPlanningPolicy,
    ) -> None:
        self._validate_generic_wait_step(step, path, issues)
        condition = step.get("resume_condition")
        if not isinstance(condition, dict) or condition.get("type") != "WORLD_OPERATION":
            issues.append(
                PlanValidationIssue(
                    code="PLAN_RESUME_CONDITION_INVALID",
                    path=f"{path}.resume_condition",
                    message="World waits require a WORLD_OPERATION resume condition",
                )
            )
            return
        source_sequence = condition.get("source_step_sequence")
        if not isinstance(source_sequence, int) or source_sequence < 1 or source_sequence > index:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_WORLD_EVENT_SOURCE_INVALID",
                    path=f"{path}.resume_condition.source_step_sequence",
                    message="The world wait must reference an earlier operation-start step",
                )
            )
            source_tool = None
        else:
            source = all_steps[source_sequence - 1]
            source_tool = (
                source.selected_tool_name
                if source.execution_type == StepExecutionType.TOOL.value
                else None
            )
            if source_tool not in policy.operation_tools:
                issues.append(
                    PlanValidationIssue(
                        code="PLAN_WORLD_EVENT_SOURCE_INVALID",
                        path=f"{path}.resume_condition.source_step_sequence",
                        message=(
                            "The world wait must reference a Step that starts "
                            "a deterministic world operation"
                        ),
                    )
                )
                source_tool = None
        outcomes = condition.get("success_outcomes")
        if (
            not isinstance(outcomes, list)
            or not outcomes
            or not all(isinstance(item, str) for item in outcomes)
        ):
            issues.append(
                PlanValidationIssue(
                    code="PLAN_WORLD_EVENT_OUTCOMES_INVALID",
                    path=f"{path}.resume_condition.success_outcomes",
                    message="World waits require at least one supported success outcome",
                )
            )
        elif source_tool is not None:
            allowed_outcomes = set(policy.world_operation_success_outcomes[source_tool])
            if not set(outcomes).issubset(allowed_outcomes):
                issues.append(
                    PlanValidationIssue(
                        code="PLAN_WORLD_EVENT_OUTCOMES_INVALID",
                        path=f"{path}.resume_condition.success_outcomes",
                        message=(
                            f"{source_tool} can only resume successfully from "
                            f"{sorted(allowed_outcomes)}"
                        ),
                    )
                )
        expected = step.get("expected_outcome")
        if expected != {"operation_result_in": outcomes}:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_WAIT_OUTCOME_INVALID",
                    path=f"{path}.expected_outcome",
                    message="The expected operation outcomes must match the resume condition",
                )
            )

    def _validate_player_action_wait_step(
        self,
        step: dict[str, Any],
        path: str,
        issues: list[PlanValidationIssue],
        policy: ScenarioPlanningPolicy,
    ) -> None:
        self._validate_generic_wait_step(step, path, issues)
        condition = step.get("resume_condition")
        if not isinstance(condition, dict) or condition.get("type") != "PLAYER_ACTION":
            issues.append(
                PlanValidationIssue(
                    code="PLAN_RESUME_CONDITION_INVALID",
                    path=f"{path}.resume_condition",
                    message="Player-action waits require a PLAYER_ACTION resume condition",
                )
            )
            return
        if condition.get("fact_key") not in policy.allowed_player_action_facts:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_PLAYER_ACTION_FACT_INVALID",
                    path=f"{path}.resume_condition.fact_key",
                    message="The player action must reference an approved public fact",
                )
            )
        if condition.get("field") not in {"status", "value"}:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_PLAYER_ACTION_FIELD_INVALID",
                    path=f"{path}.resume_condition.field",
                    message="The player action must verify a supported fact field",
                )
            )
        expected = step.get("expected_outcome")
        if expected != {"player_action": "COMPLETED"}:
            issues.append(
                PlanValidationIssue(
                    code="PLAN_WAIT_OUTCOME_INVALID",
                    path=f"{path}.expected_outcome",
                    message="Player-action waits must expect player_action=COMPLETED",
                )
            )

    def _validate_replan(
        self,
        task: AgentTask,
        proposed_steps: list[dict[str, Any]],
        issues: list[PlanValidationIssue],
        policy: ScenarioPlanningPolicy,
        scenario_state: ScenarioRuntimeState,
    ) -> None:
        old_plan = TaskService(self.db).current_plan(task)
        if old_plan is None:
            return
        old_steps = TaskService(self.db).plan_steps(old_plan.id)
        completed_writes = [
            step
            for step in old_steps
            if step.status == AgentStepStatus.SUCCEEDED
            and step.selected_tool_name in policy.idempotent_tools
            and not (
                step.selected_tool_name in policy.operation_tools
                and _operation_wait_failed(step, old_steps)
            )
        ]
        for index, proposed in enumerate(proposed_steps):
            proposed_tool = proposed.get("selected_tool_name")
            if proposed_tool not in policy.idempotent_tools:
                continue
            proposed_arguments = proposed.get("tool_arguments", {})
            if policy.effect_satisfied(
                str(proposed_tool),
                proposed_arguments if isinstance(proposed_arguments, dict) else {},
                scenario_state,
            ):
                issues.append(
                    PlanValidationIssue(
                        code="PLAN_REPEATS_SATISFIED_EFFECT",
                        path=f"steps.{index}",
                        message=(
                            f"{proposed_tool} would repeat a strategic effect that is "
                            "already verified in the current world state"
                        ),
                    )
                )
                continue
            for completed in completed_writes:
                if completed.selected_tool_name == proposed_tool:
                    if completed.tool_arguments != proposed.get("tool_arguments", {}):
                        continue
                    issues.append(
                        PlanValidationIssue(
                            code="PLAN_REPEATS_COMPLETED_WRITE",
                            path=f"steps.{index}",
                            message=(
                                f"{proposed_tool} was already completed in Plan v{old_plan.version}"
                            ),
                        )
                    )

    def _validate_operation_wait_pairing(
        self,
        steps: list[Any],
        issues: list[PlanValidationIssue],
        policy: ScenarioPlanningPolicy,
    ) -> None:
        for index, step in enumerate(steps):
            if step.selected_tool_name not in policy.operation_tools:
                continue
            next_step = steps[index + 1] if index + 1 < len(steps) else None
            condition = next_step.resume_condition if next_step is not None else None
            if (
                next_step is None
                or next_step.execution_type != StepExecutionType.WAIT_FOR_WORLD_EVENT.value
                or not isinstance(condition, dict)
                or condition.get("source_step_sequence") != index + 1
            ):
                issues.append(
                    PlanValidationIssue(
                        code="PLAN_WORLD_EVENT_PAIRING_INVALID",
                        path=f"steps.{index}",
                        message=(
                            "Every world-operation start must be followed immediately "
                            "by the wait that references it"
                        ),
                    )
                )

    @staticmethod
    def _rejected(code: str, path: str, message: str) -> PlanValidationResult:
        return PlanValidationResult(
            status="REJECTED",
            errors=[PlanValidationIssue(code=code, path=path, message=message)],
        )


def build_planning_request(
    *,
    db: Session,
    registry: ToolRegistry,
    settings: Settings,
    task: AgentTask,
    session: ConversationSession,
    kind: Literal["PLAN", "REPLAN"],
    replan_reason: str | None = None,
) -> dict[str, Any]:
    npc = db.get(NPC, session.npc_id)
    assert npc is not None
    appointments: dict[UUID, OfficerAppointment] = {}
    officer_rows = db.execute(
        select(NPC, OfficerAppointment)
        .join(OfficerAppointment, OfficerAppointment.npc_id == NPC.id)
        .where(
            OfficerAppointment.player_id == task.player_id,
            OfficerAppointment.status == "ACTIVE",
            NPC.enabled.is_(True),
        )
        .order_by(NPC.key)
    ).all()
    execution_officers = [officer for officer, _appointment in officer_rows]
    appointments = {officer.id: appointment for officer, appointment in officer_rows}
    valid_policy_officer_ids = {
        officer.id
        for officer in execution_officers
        if not authority_policy_errors(
            officer.authority_limits,
            (appointments[officer.id].authority_overrides if officer.id in appointments else None),
        )
    }
    verified_state = GameService(db).inspect_command_state(task.player_id)
    scenario = scenario_binding(task.scenario_key)
    if scenario is None:
        raise ValueError(f"Scenario planning policy is not registered: {task.scenario_key}")
    policy = scenario.planning_policy
    scenario_state = GameService(db).scenario_state(task.player_id)
    allowed_tools: list[dict[str, Any]] = []
    scenario_tools = policy.execution_tools
    for definition in registry.definitions(task.scenario_key):
        if definition.name not in scenario_tools:
            continue
        if kind == "REPLAN" and policy.effect_satisfied(definition.name, {}, scenario_state):
            continue
        tool = registry.get(definition.name)
        assert tool is not None
        authorized_officers = [
            officer
            for officer in execution_officers
            if officer.id in valid_policy_officer_ids
            and (not tool.allowed_roles or officer.role.value in tool.allowed_roles)
            and (
                not tool.require_permission_profile
                or officer.permission_profile.get(tool.name, False)
            )
        ]
        if not authorized_officers:
            continue
        parameters = deepcopy(definition.parameters)
        properties = parameters.get("properties")
        if isinstance(properties, dict):
            properties.pop("idempotency_key", None)
        required = parameters.get("required")
        if isinstance(required, list):
            parameters["required"] = [item for item in required if item != "idempotency_key"]
        allowed_tools.append(
            {
                "name": definition.name,
                "description": definition.description,
                "planning_parameters": parameters,
                "allowed_expected_outcomes": sorted(
                    policy.expected_outcome_fields.get(definition.name, frozenset())
                ),
                "required_expected_outcomes": dict(
                    policy.fixed_tool_expected_outcomes.get(
                        definition.name,
                        {},
                    )
                ),
                "world_wait_success_outcomes": list(
                    policy.world_operation_success_outcomes.get(definition.name, ())
                ),
                "allowed_officer_keys": [officer.key for officer in authorized_officers],
            }
        )
    prior_plans: list[dict[str, Any]] = []
    service = TaskService(db)
    old_plan = service.current_plan(task)
    if old_plan is not None:
        prior_plans.append(
            {
                "version": old_plan.version,
                "status": old_plan.status.value,
                "strategy_summary": old_plan.strategy_summary,
                "steps": [
                    {
                        "description": step.description,
                        "status": step.status.value,
                        "assigned_officer_key": _officer_key(
                            db,
                            step.assigned_npc_id,
                            npc.key,
                        ),
                        "action_intent": step.action_intent,
                        "constraints": step.constraints,
                        "selected_tool_name": step.selected_tool_name,
                        "tool_arguments": step.tool_arguments,
                        "actual_result": step.actual_result,
                        "failure_code": step.failure_code,
                    }
                    for step in service.plan_steps(old_plan.id)
                ],
            }
        )
    owner_appointment = appointments.get(npc.id)
    owner_policy_errors = authority_policy_errors(
        npc.authority_limits,
        (owner_appointment.authority_overrides if owner_appointment is not None else None),
    )
    return {
        "kind": kind,
        "submission_tool": "replan_task" if kind == "REPLAN" else "create_task_plan",
        "task_id": str(task.id),
        "goal": task.goal_description,
        "scenario_key": task.scenario_key,
        "npc": {
            "key": npc.key,
            "role": npc.role.value,
            "persona": npc.persona,
            "doctrine": npc.doctrine,
            "authority_limits": effective_authority_limits(
                npc,
                (owner_appointment.authority_overrides if owner_appointment is not None else None),
            ),
            "authority_policy_version": (
                owner_appointment.version if owner_appointment is not None else npc.profile_version
            ),
            "authority_policy_status": "INVALID" if owner_policy_errors else "VALID",
            "authority_policy_errors": owner_policy_errors,
            "memory_summary": _officer_memories(db, task.player_id, npc.id),
        },
        "officers": [
            {
                "key": officer.key,
                "name": officer.name,
                "role": officer.role.value,
                "persona": officer.persona,
                "doctrine": officer.doctrine,
                "authority_limits": effective_authority_limits(
                    officer,
                    (
                        appointments[officer.id].authority_overrides
                        if officer.id in appointments
                        else None
                    ),
                ),
                "profile_version": officer.profile_version,
                "authority_policy_version": (
                    appointments[officer.id].version
                    if officer.id in appointments
                    else officer.profile_version
                ),
                "authority_policy_status": (
                    "VALID" if officer.id in valid_policy_officer_ids else "INVALID"
                ),
                "authority_policy_errors": authority_policy_errors(
                    officer.authority_limits,
                    (
                        appointments[officer.id].authority_overrides
                        if officer.id in appointments
                        else None
                    ),
                ),
                "memory_summary": _officer_memories(
                    db,
                    task.player_id,
                    officer.id,
                ),
            }
            for officer in execution_officers
        ],
        "verified_state": verified_state,
        "allowed_tools": allowed_tools,
        "prior_plans": prior_plans,
        "failure_code": replan_reason,
        "replan_guidance": policy.replan_guidance(replan_reason),
        "constraints": {
            "max_steps": settings.planner_max_steps,
            "max_wait_steps": settings.planner_max_wait_steps,
            "idempotency_keys": "BACKEND_GENERATED_DO_NOT_INCLUDE_IN_STEP_ARGUMENTS",
            "no_chain_of_thought": True,
            "no_database_access": True,
            "no_state_patches": True,
            "security_failures_are_terminal": True,
            **policy.build_planning_constraints(kind, replan_reason, scenario_state),
            "step_shapes": {
                "tool_step": {
                    "execution_type": "TOOL",
                    "selected_tool_name": "ONE_ALLOWED_TOOL_NAME",
                    "allowed_tool_names": ["SAME_SELECTED_TOOL_NAME"],
                    "tool_arguments": "JSON_OBJECT_MATCHING_PLANNING_PARAMETERS",
                    "expected_outcome": "JSON_OBJECT_USING_REQUIRED_LITERAL_VALUES",
                    "resume_condition": None,
                },
                "world_wait_step": {
                    "execution_type": "WAIT_FOR_WORLD_EVENT",
                    "selected_tool_name": None,
                    "allowed_tool_names": [],
                    "tool_arguments": {},
                    "expected_outcome": {"operation_result_in": ["SUPPORTED_SUCCESS_OUTCOME"]},
                    "resume_condition": {
                        "type": "WORLD_OPERATION",
                        "source_step_sequence": "IMMEDIATELY_PREVIOUS_ONE_BASED_SEQUENCE",
                        "success_outcomes": ["SAME_SUPPORTED_SUCCESS_OUTCOME"],
                    },
                },
            },
            "world_operation_pairing": (
                "Every operation-start TOOL step must be followed immediately by exactly "
                "one WAIT_FOR_WORLD_EVENT step that references its one-based sequence"
            ),
        },
    }


def _operation_wait_failed(
    operation_step: AgentStep,
    plan_steps: list[AgentStep],
) -> bool:
    return any(
        candidate.sequence == operation_step.sequence + 1
        and candidate.execution_type == StepExecutionType.WAIT_FOR_WORLD_EVENT.value
        and candidate.status == AgentStepStatus.FAILED
        and isinstance(candidate.resume_condition, dict)
        and candidate.resume_condition.get("source_step_sequence") == operation_step.sequence
        for candidate in plan_steps
    )


def _officer_key(db: Session, officer_id: Any, fallback: str) -> str:
    officer = db.get(NPC, officer_id) if officer_id is not None else None
    return officer.key if officer is not None else fallback


def _officer_memories(db: Session, player_id: Any, officer_id: Any) -> list[str]:
    return [
        memory.content
        for memory in db.scalars(
            select(Memory)
            .where(Memory.player_id == player_id, Memory.npc_id == officer_id)
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(5)
        ).all()
    ]
