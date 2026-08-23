"""Generic exact-Version goal resolution, planning, validation and execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import actor_binding_matches, evaluate_authority
from app.agent.objective_scope import ObjectiveScope, ObjectiveScopeError
from app.agent.planner_contract import action_planner_effects
from app.agent.planning_context import (
    PlanningActionCatalogBuilder,
    PlanningContextBuilder,
    objective_context,
)
from app.agent.provider import (
    AntiRegressionMemoryItem,
    GenericModelProvider,
    GenericProviderError,
    GoalSelectionRequest,
    PlannerActionContract,
    PlannerActorState,
    PlannerInput,
    PlannerTargetBinding,
    PlanningActionCandidate,
    PlanningContext,
    PlanProposal,
    PlanRequest,
    PlanViolation,
    provider_call_metadata,
    provider_call_start_metadata,
)
from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    AuthorityOutcome,
    CommandReachability,
    DecisionStatus,
    NodeStatus,
    RelationVisibility,
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
    StepExecutionType,
    WorldOperationStatus,
)
from app.domain.resources import resource_state_key
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import (
    ActionBehavior,
    ActionDefinitionV2,
    ActionExecutionMode,
    ActionTargetKind,
    ComparisonOperator,
    ConditionV2,
    EffectKind,
    EffectV2,
    NodeSelectorKind,
    NodeSelectorV2,
    ObjectiveDefinitionV2,
    RuleDefinitionV2,
    RulePhase,
    ScenarioDefinitionV2,
    StrictScalar,
    ValueExpressionV2,
    ValueSource,
    normalize_action_parameters,
    relation_identity,
)
from app.domain.world import Visibility
from app.engine.locality import (
    LocalityEngineError,
    region_for_node,
    resolve_resource_scope,
    validate_action_locality,
)
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentPlan,
    AgentStep,
    AgentTask,
    ConversationSession,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceResourceState,
    PlanningAttempt,
    PlanningCycle,
    WorldOperation,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import require_scope_writable
from app.services.generic_actions import (
    GenericActionError,
    GenericActionService,
    GenericApprovalRequired,
)
from app.services.knowledge_projection import SharedKnowledgeProjection, resource_knowledge_status

ObjectiveSelector = Callable[[str, tuple[ObjectiveDefinitionV2, ...]], str | None]

ProviderCallObserver = Callable[
    [str, AgentTask, PlanRequest, dict[str, object]],
    None,
]


@dataclass(slots=True)
class _ProjectedResourcePool:
    pool_key: str
    resource_key: str
    region_key: str | None
    facility_key: str | None
    quantity: int | None
    visibility: ResourcePoolVisibility
    availability: ResourcePoolAvailability
    survey_discoverable: bool


@dataclass(slots=True)
class _ProjectedRegionResourceKnowledge:
    visibility: ResourceInventoryVisibility
    survey_completed: bool


@dataclass(slots=True)
class _ProjectedFact:
    value: StrictScalar
    visibility: Visibility


@dataclass(frozen=True, slots=True)
class _KnownPreflightFailure:
    failure_code: str
    known_predicate: dict[str, object]


@dataclass(frozen=True, slots=True)
class _StaticProposalBinding:
    index: int
    raw_step: object
    candidate: PlanningActionCandidate | None
    action: ActionDefinitionV2
    actor: GameInstanceActor
    target_key: str
    parameters: dict[str, StrictScalar]


_NON_TERMINAL_TASK_STATUSES = (
    AgentTaskStatus.ACTIVE,
    AgentTaskStatus.REQUIRES_PLAYER_DECISION,
    AgentTaskStatus.WAITING_FOR_PLAYER_ACTION,
    AgentTaskStatus.WAITING_FOR_WORLD_EVENT,
)


class GenericAgentError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


PLAN_INVALIDATED_BY_NEW_KNOWLEDGE = "PLAN_INVALIDATED_BY_NEW_KNOWLEDGE"


@dataclass(frozen=True, slots=True)
class GenericGoalResolution:
    status: str
    objective_key: str | None = None
    objective_keys: tuple[str, ...] = ()
    candidate_keys: tuple[str, ...] = ()
    clarification_prompt: str | None = None
    source: str = "DETERMINISTIC"
    provider_observation: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class GenericObjectiveEvaluation:
    objective_keys: tuple[str, ...]
    completed: bool
    requirements: tuple[tuple[str, StrictScalar, bool], ...]


@dataclass(frozen=True, slots=True)
class PlanRevalidationResult:
    invalidated: bool
    reason: str | None = None
    diagnostics: tuple[dict[str, object], ...] = ()


class GenericGoalResolver:
    def __init__(
        self,
        selector: ObjectiveSelector | None = None,
        provider: GenericModelProvider | None = None,
    ) -> None:
        self.selector = selector
        self.provider = provider

    def resolve(
        self,
        goal: str,
        definition: ScenarioDefinitionV2,
    ) -> GenericGoalResolution:
        normalized = _normalize(goal)
        matches = [
            objective
            for objective in definition.objectives
            if normalized
            in {
                _normalize(objective.key),
                _normalize(objective.name),
                *(_normalize(alias) for alias in objective.goal_aliases),
                *(_normalize(example) for example in objective.goal_examples),
            }
        ]
        if len(matches) == 1:
            return GenericGoalResolution("RESOLVED", matches[0].key, (matches[0].key,))
        if len(matches) > 1:
            return GenericGoalResolution(
                "NEEDS_CLARIFICATION",
                candidate_keys=tuple(sorted(item.key for item in matches)),
                clarification_prompt=definition.goal_resolution.clarification_prompt,
            )
        if definition.goal_resolution.allow_llm_fallback and self.provider is not None:
            selection = self.provider.select_objectives(
                GoalSelectionRequest(
                    goal=goal,
                    objective_candidates=tuple(
                        {
                            "key": item.key,
                            "name": item.name,
                            "description": item.description,
                            "aliases": item.goal_aliases,
                        }
                        for item in definition.objectives
                    ),
                )
            )
            valid = {objective.key for objective in definition.objectives}
            keys = tuple(sorted(set(selection.objective_keys)))
            observation = {
                "call_type": "GOAL_RESOLUTION",
                "model": self.provider.model_name,
                "selected_objective_keys": list(keys),
                **provider_call_metadata(self.provider),
            }
            if selection.status == "NEEDS_CLARIFICATION":
                return GenericGoalResolution(
                    "NEEDS_CLARIFICATION",
                    candidate_keys=tuple(key for key in keys if key in valid),
                    clarification_prompt=(
                        selection.clarification_prompt
                        or definition.goal_resolution.clarification_prompt
                    ),
                    source="MODEL_VALIDATED",
                    provider_observation={**observation, "validation": "ACCEPTED"},
                )
            if selection.status == "UNSUPPORTED":
                return GenericGoalResolution(
                    "UNSUPPORTED",
                    source="MODEL_VALIDATED",
                    provider_observation={**observation, "validation": "ACCEPTED"},
                )
            if keys and set(keys).issubset(valid):
                canonical_keys = normalize_objective_keys(definition, keys)
                return GenericGoalResolution(
                    "RESOLVED",
                    canonical_keys[0] if len(canonical_keys) == 1 else None,
                    canonical_keys,
                    source="MODEL_VALIDATED",
                    provider_observation={
                        **observation,
                        "normalized_objective_keys": list(canonical_keys),
                        "validation": "ACCEPTED",
                    },
                )
            return GenericGoalResolution(
                "UNSUPPORTED",
                source="MODEL_VALIDATED",
                provider_observation={
                    **observation,
                    "validation": "REJECTED",
                    "rejection_code": "UNKNOWN_OBJECTIVE",
                },
            )
        if definition.goal_resolution.allow_llm_fallback and self.selector is not None:
            selected = self.selector(goal, definition.objectives)
            if selected in {objective.key for objective in definition.objectives}:
                return GenericGoalResolution(
                    "RESOLVED", selected, (selected,), source="MODEL_VALIDATED"
                )
        return GenericGoalResolution("UNSUPPORTED")


class GenericAgentService:
    """A compact persistent Agent loop driven only by exact v2 Version data."""

    MAX_REPLANS = 5

    def __init__(
        self,
        db: Session,
        scope: RuntimeScope,
        *,
        goal_resolver: GenericGoalResolver | None = None,
        provider: GenericModelProvider | None = None,
        provider_call_observer: ProviderCallObserver | None = None,
        model_max_repair_attempts_per_cycle: int = 2,
    ) -> None:
        self.db = db
        self.scope = scope
        self.provider = provider
        self.goal_resolver = goal_resolver or GenericGoalResolver(provider=provider)
        self.provider_call_observer = provider_call_observer
        self.model_max_repair_attempts_per_cycle = (
            model_max_repair_attempts_per_cycle
        )
        self._provider_call_started_at: dict[str, float] = {}
        self._last_provider_plan_summary: str | None = None
        self._last_provider_stop_reason: str | None = None
        self._last_provider_attempt: dict[str, object] | None = None

    def create_task(
        self,
        session: ConversationSession,
        goal: str,
        *,
        resolved_goal: GenericGoalResolution | None = None,
        initialize_plan: bool = True,
    ) -> AgentTask:
        require_scope_writable(self.db, self.scope.game_instance_id)
        existing = self.db.scalar(
            select(AgentTask).where(
                AgentTask.game_instance_id == self.scope.game_instance_id,
                AgentTask.status.in_(_NON_TERMINAL_TASK_STATUSES),
            )
        )
        if existing is not None:
            raise GenericAgentError(
                "AGENT_TASK_ALREADY_ACTIVE",
                "A GameInstance may have only one active Task",
            )
        definition = self._definition()
        if session.game_instance_id != self.scope.game_instance_id or not session.actor_key:
            raise GenericAgentError(
                "GENERIC_SESSION_SCOPE_INVALID",
                "Generic task creation requires the Instance primary Actor session",
            )
        resolution = resolved_goal or self.goal_resolver.resolve(goal, definition)
        if resolution.status != "RESOLVED" or not resolution.objective_keys:
            raise GenericAgentError(
                f"GOAL_{resolution.status}",
                resolution.clarification_prompt or "Goal does not resolve in the exact Version",
            )
        valid_objectives = {objective.key for objective in definition.objectives}
        if not set(resolution.objective_keys).issubset(valid_objectives):
            raise GenericAgentError(
                "GOAL_UNSUPPORTED",
                "Goal does not resolve to Objectives in the exact Version",
            )
        objective_keys = normalize_objective_keys(definition, resolution.objective_keys)
        now = datetime.now(UTC)
        catalog_version = f"scenario-version:{self.scope.scenario_version_id}"
        objective_scope = ObjectiveScope.create(objective_keys, catalog_version)
        task = AgentTask(
            player_id=self.scope.player_id,
            game_instance_id=self.scope.game_instance_id,
            owner_actor_key=session.actor_key,
            origin_session_id=session.id,
            last_session_id=session.id,
            goal_description=goal,
            scenario_key=definition.metadata.key,
            objective_resolution_status="CONFIRMED",
            objective_scope_keys=list(objective_scope.objective_keys),
            objective_catalog_version=objective_scope.catalog_version,
            objective_scope_hash=objective_scope.content_hash,
            objective_resolver_source=resolution.source,
            objective_resolver_version="generic-goal-resolver@1",
            objective_resolution_metadata={
                "exact_version": str(self.scope.scenario_version_id),
                "provider_calls": (
                    [resolution.provider_observation]
                    if resolution.provider_observation is not None
                    else []
                ),
            },
            objective_resolved_at=now,
            objective_confirmed_at=now,
            objective_confirmation_source=resolution.source,
            objective_frozen_at=now,
            objective_freeze_source="GENERIC_AGENT",
            planning_mode="PROVIDER" if self.provider is not None else "GENERIC",
        )
        self.db.add(task)
        self.db.flush()
        # Formal Play uses the same task/objective construction but lets the
        # player explicitly acknowledge the resolved Goal before initial
        # planning starts.  The default keeps the generic engine's existing
        # eager-planning behavior unchanged for all other callers.
        if initialize_plan:
            self.plan(task)
        return task

    def plan(self, task: AgentTask, *, reason: str | None = None) -> AgentPlan:
        require_scope_writable(self.db, self.scope.game_instance_id)
        if reason is not None and task.replan_count >= self.MAX_REPLANS:
            raise GenericAgentError(
                "GENERIC_REPLAN_LIMIT",
                "The Task reached the generic replan safety limit",
            )
        definition = self._definition()
        self._task_scope(task)
        objectives = self._objectives(task, definition)
        next_version = task.current_plan_version + 1
        steps = self._candidate_steps(
            definition,
            objectives,
            task=task,
            reason=reason,
            plan_version=next_version,
        )
        self._last_provider_stop_reason = None
        if self.provider is not None:
            steps = self._provider_steps(task, definition, objectives, reason, next_version)
        if (
            not steps
            and not self.evaluate(task).completed
            and getattr(self, "_last_provider_stop_reason", None) != "BLOCKED"
        ):
            raise GenericAgentError(
                "GENERIC_PLAN_NOT_FOUND",
                "No exact-Version Action can advance the frozen Objective from current Knowledge",
            )
        return self._persist_plan(task, steps, reason=reason, plan_version=next_version)

    def plan_one_attempt(
        self,
        task: AgentTask,
        *,
        reason: str | None = None,
    ) -> tuple[AgentPlan | None, PlanningCycle | None]:
        """Run exactly one Provider attempt for a Formal Play planning cycle.

        Rejected proposals remain on the durable PlanningCycle and never become
        AgentPlan/AgentStep rows.  The next HTTP request calls this method again
        and supplies the prior typed diagnostics as a REPAIR request.
        """

        require_scope_writable(self.db, self.scope.game_instance_id)
        if self.provider is None:
            return self.plan(task, reason=reason), None
        if reason is not None and task.replan_count >= self.MAX_REPLANS:
            raise GenericAgentError(
                "GENERIC_REPLAN_LIMIT",
                "The Task reached the generic replan safety limit",
            )
        definition = self._definition()
        self._task_scope(task)
        objectives = self._objectives(task, definition)
        call_type = "INITIAL_PLAN" if reason is None else "REPLAN"
        cycle = self.db.scalar(
            select(PlanningCycle)
            .where(
                PlanningCycle.task_id == task.id,
                PlanningCycle.base_call_type == call_type,
                PlanningCycle.status == "RUNNING",
            )
            .order_by(PlanningCycle.created_at.desc())
        )
        if cycle is None:
            start_attempt = 0
            rejected_segment = None
            diagnostics: tuple[PlanViolation, ...] = ()
            memory: tuple[AntiRegressionMemoryItem, ...] = ()
            planner_input_override = None
        else:
            start_attempt = cycle.current_attempt + 1
            rejected_segment = cycle.rejected_segment
            diagnostics = tuple(
                PlanViolation.model_validate(item)
                for item in cycle.current_violations
                if isinstance(item, dict)
            )
            memory = tuple(
                AntiRegressionMemoryItem.model_validate(item)
                for item in cycle.anti_regression_memory
                if isinstance(item, dict)
            )
            planner_input_override = PlannerInput.model_validate(cycle.planner_input)
            if start_attempt > self.model_max_repair_attempts_per_cycle:
                cycle.status = "REJECTED"
                task.status = AgentTaskStatus.BLOCKED
                task.last_error_code = "MODEL_PLAN_REJECTED"
                self.db.flush()
                return None, cycle
        next_version = task.current_plan_version + 1
        steps = self._provider_steps(
            task,
            definition,
            objectives,
            reason,
            next_version,
            single_attempt=True,
            starting_repair_attempt=start_attempt,
            rejected_segment=rejected_segment,
            initial_diagnostics=diagnostics,
            initial_memory=memory,
            planning_cycle=cycle,
            planner_input_override=planner_input_override,
        )
        # A fresh cycle is created inside _provider_steps for the first
        # attempt.  Resolve it here so the caller can persist the rejection
        # state and expose it to the next HTTP repair request.
        if cycle is None:
            cycle = self._latest_planning_cycle(task)
        attempt_result = self._last_provider_attempt or {}
        accepted = bool(attempt_result.get("accepted"))
        if not accepted:
            assert cycle is not None
            if start_attempt >= self.model_max_repair_attempts_per_cycle:
                cycle.status = "REJECTED"
                task.status = AgentTaskStatus.BLOCKED
                task.last_error_code = "MODEL_PLAN_REJECTED"
                self.db.flush()
            return None, cycle
        plan = self._persist_plan(task, steps, reason=reason, plan_version=next_version)
        return plan, cycle

    def _persist_plan(
        self,
        task: AgentTask,
        steps: list[dict[str, object]],
        *,
        reason: str | None,
        plan_version: int,
    ) -> AgentPlan:
        actor = self._actor(task.owner_actor_key)
        old_plan = self.db.scalar(
            select(AgentPlan).where(
                AgentPlan.task_id == task.id,
                AgentPlan.status == AgentPlanStatus.ACTIVE,
            )
        )
        if old_plan is not None:
            old_plan.status = AgentPlanStatus.SUPERSEDED
        plan = AgentPlan(
            task_id=task.id,
            version=plan_version,
            status=AgentPlanStatus.ACTIVE,
            strategy_summary=(
                getattr(self, "_last_provider_plan_summary", None)
                or "Execute exact-Version actions for the frozen objective scope"
            ),
            replan_reason=reason,
            supersedes_plan_id=old_plan.id if old_plan else None,
            created_by_actor_key=actor.actor_key,
            source="PROVIDER" if self.provider is not None else "GENERIC",
            planner_model=(self.provider.model_name if self.provider is not None else None),
            validation_status="PASSED",
            validation_errors=[],
            stop_reason=self._last_provider_stop_reason or "OBJECTIVE_COMPLETION",
        )
        self.db.add(plan)
        self.db.flush()
        for sequence, candidate in enumerate(steps, start=1):
            self.db.add(
                AgentStep(
                    plan_id=plan.id,
                    sequence=sequence,
                    planner_step_id=(
                        str(candidate["planner_step_id"])
                        if candidate.get("planner_step_id")
                        else None
                    ),
                    description=candidate["description"],
                    execution_type=candidate["execution_type"],
                    assigned_actor_key=str(candidate["actor_key"]),
                    action_intent=candidate["action_intent"],
                    constraints={
                        "scenario_version_id": str(self.scope.scenario_version_id),
                        **(
                            {"planner_step_id": candidate["planner_step_id"]}
                            if candidate.get("planner_step_id")
                            else {}
                        ),
                    },
                    allowed_tool_names=(
                        ["execute_action"]
                        if candidate["execution_type"] == StepExecutionType.TOOL
                        else []
                    ),
                    selected_tool_name=(
                        "execute_action"
                        if candidate["execution_type"] == StepExecutionType.TOOL
                        else None
                    ),
                    tool_arguments=candidate["arguments"],
                    expected_outcome=candidate["expected_outcome"],
                    resume_condition=candidate["resume_condition"],
                )
            )
        task.current_plan_version = plan.version
        if getattr(self, "_last_provider_stop_reason", None) == "BLOCKED":
            task.status = AgentTaskStatus.BLOCKED
            task.last_error_code = "PLAN_SEGMENT_BLOCKED"
        if reason is not None:
            task.replan_count += 1
        if task.last_error_code == PLAN_INVALIDATED_BY_NEW_KNOWLEDGE:
            task.last_error_code = None
        self.db.flush()
        return plan

    def execute_next(self, task: AgentTask, *, replan_on_failure: bool = True) -> AgentStep | None:
        require_scope_writable(self.db, self.scope.game_instance_id)
        self._task_scope(task)
        if self.evaluate(task).completed:
            self._complete_task(task)
            return None
        plan = self._active_plan(task)
        step = self.db.scalar(
            select(AgentStep)
            .where(
                AgentStep.plan_id == plan.id,
                AgentStep.status.in_(
                    [
                        AgentStepStatus.PENDING,
                        AgentStepStatus.REQUIRES_PLAYER_DECISION,
                        AgentStepStatus.WAITING_FOR_WORLD_EVENT,
                    ]
                ),
            )
            .order_by(AgentStep.sequence)
        )
        if step is None:
            self.plan(
                task,
                reason=(
                    "INFORMATION_BOUNDARY"
                    if plan.stop_reason == "INFORMATION_BOUNDARY"
                    else "PLAN_EXHAUSTED"
                ),
            )
            return self.execute_next(task, replan_on_failure=replan_on_failure)
        if step.execution_type == StepExecutionType.WAIT_FOR_WORLD_EVENT:
            operation = self.db.scalar(
                select(WorldOperation)
                .where(
                    WorldOperation.game_instance_id == self.scope.game_instance_id,
                    WorldOperation.task_id == task.id,
                )
                .order_by(WorldOperation.created_at.desc())
            )
            if operation is None or operation.status == WorldOperationStatus.PENDING:
                step.status = AgentStepStatus.WAITING_FOR_WORLD_EVENT
                task.status = AgentTaskStatus.WAITING_FOR_WORLD_EVENT
                self.db.flush()
                return step
            step.status = AgentStepStatus.SUCCEEDED
            step.actual_result = operation.outcome
            step.completed_at = datetime.now(UTC)
            task.status = AgentTaskStatus.ACTIVE
            failure_payload = (
                operation.outcome.get("failure") if isinstance(operation.outcome, dict) else None
            )
            if isinstance(failure_payload, dict) and failure_payload.get("code"):
                failure_code = str(failure_payload["code"])
                self._record_action_failure(
                    task,
                    step,
                    failure_code,
                    retryable=bool(failure_payload.get("retryable", False)),
                    replan=replan_on_failure,
                )
                self.db.flush()
                return step
        else:
            decision_id = None
            if step.status == AgentStepStatus.REQUIRES_PLAYER_DECISION:
                decision = self.db.scalar(
                    select(ActionDecisionRequest).where(
                        ActionDecisionRequest.game_instance_id == self.scope.game_instance_id,
                        ActionDecisionRequest.source_step_id == step.id,
                    )
                )
                if decision is None or decision.status == DecisionStatus.PENDING:
                    task.status = AgentTaskStatus.REQUIRES_PLAYER_DECISION
                    return step
                decision_id = decision.id
            step.status = AgentStepStatus.IN_PROGRESS
            step.attempts += 1
            step.started_at = datetime.now(UTC)
            arguments = step.tool_arguments
            try:
                result = GenericActionService(self.db, self.scope).execute_action(
                    actor_key=step.assigned_actor_key or "",
                    action_key=str(arguments["action_key"]),
                    target_key=str(arguments["target_key"]),
                    parameters=dict(arguments["parameters"]),
                    idempotency_key=str(arguments["idempotency_key"]),
                    task_id=task.id,
                    source_step_id=step.id,
                    decision_id=decision_id,
                )
            except GenericApprovalRequired as exc:
                step.status = AgentStepStatus.REQUIRES_PLAYER_DECISION
                step.actual_result = {"decision_id": str(exc.decision.id)}
                task.status = AgentTaskStatus.REQUIRES_PLAYER_DECISION
                self.db.flush()
                return step
            except GenericActionError as exc:
                self._record_action_failure(
                    task,
                    step,
                    exc.code,
                    retryable=exc.retryable,
                    replan=replan_on_failure,
                )
                self.db.flush()
                return step
            step.actual_result = {
                "operation_id": str(result.operation.id),
                "status": result.operation.status.value,
                "outcome": result.operation.outcome,
            }
            task.status = AgentTaskStatus.ACTIVE
            if result.applied is not None and result.applied.outcome.failure is not None:
                failure = result.applied.outcome.failure
                self._record_action_failure(
                    task,
                    step,
                    failure.code,
                    retryable=failure.retryable,
                    replan=replan_on_failure,
                )
                self.db.flush()
                return step
            if result.operation.status == WorldOperationStatus.PENDING:
                step.status = AgentStepStatus.SUCCEEDED
                step.completed_at = datetime.now(UTC)
            else:
                step.status = AgentStepStatus.SUCCEEDED
                step.completed_at = datetime.now(UTC)
        if step.status == AgentStepStatus.SUCCEEDED:
            revalidation = self.revalidate_remaining_plan(task, completed_step=step)
            if revalidation.invalidated and replan_on_failure:
                self.plan(task, reason=PLAN_INVALIDATED_BY_NEW_KNOWLEDGE)
        if self.evaluate(task).completed:
            self._complete_task(task)
        self.db.flush()
        return step

    def revalidate_remaining_plan(
        self,
        task: AgentTask,
        *,
        completed_step: AgentStep,
    ) -> PlanRevalidationResult:
        """Revalidate only the not-yet-executed suffix after new Knowledge.

        This deliberately uses persisted Knowledge and current Runtime actor
        state.  It never consults a hidden Truth value to retroactively reject
        a Plan that was valid when it was generated.
        """

        if not self._step_has_knowledge_changes(completed_step):
            return PlanRevalidationResult(False)
        plan = self.db.scalar(
            select(AgentPlan).where(
                AgentPlan.id == completed_step.plan_id,
                AgentPlan.status == AgentPlanStatus.ACTIVE,
            )
        )
        if plan is None:
            return PlanRevalidationResult(False)
        remaining_steps = tuple(
            self.db.scalars(
                select(AgentStep)
                .where(
                    AgentStep.plan_id == plan.id,
                    AgentStep.sequence > completed_step.sequence,
                    AgentStep.status.in_(
                        (
                            AgentStepStatus.PENDING,
                            AgentStepStatus.REQUIRES_PLAYER_DECISION,
                            AgentStepStatus.WAITING_FOR_WORLD_EVENT,
                        )
                    ),
                )
                .order_by(AgentStep.sequence)
            )
        )
        diagnostics = self._revalidate_remaining_plan_sequential(
            definition=self._definition(),
            remaining_steps=remaining_steps,
        )
        if not diagnostics:
            return PlanRevalidationResult(False)

        knowledge_changes = list(self._step_knowledge_changes(completed_step))
        plan.status = AgentPlanStatus.SUPERSEDED
        for step in remaining_steps:
            step.status = AgentStepStatus.SKIPPED
        task.last_error_code = PLAN_INVALIDATED_BY_NEW_KNOWLEDGE
        metadata = dict(task.objective_resolution_metadata or {})
        metadata["plan_invalidation"] = {
            "reason": PLAN_INVALIDATED_BY_NEW_KNOWLEDGE,
            "plan_version": plan.version,
            "completed_step_sequence": completed_step.sequence,
            "completed_step_action": completed_step.action_intent,
            "knowledge_changes": knowledge_changes,
            "diagnostics": list(diagnostics),
        }
        task.objective_resolution_metadata = metadata
        self.db.flush()
        return PlanRevalidationResult(
            True,
            reason=PLAN_INVALIDATED_BY_NEW_KNOWLEDGE,
            diagnostics=diagnostics,
        )

    def has_pending_plan_invalidation(self, task: AgentTask) -> bool:
        """Return whether Formal Play must obtain a player-approved Replan."""

        if task.last_error_code != PLAN_INVALIDATED_BY_NEW_KNOWLEDGE:
            return False
        marker = self._plan_invalidation(task)
        return (
            marker is not None
            and marker.get("plan_version") == task.current_plan_version
            and self.db.scalar(
                select(AgentPlan.id).where(
                    AgentPlan.task_id == task.id,
                    AgentPlan.status == AgentPlanStatus.ACTIVE,
                )
            )
            is None
        )

    def _revalidate_remaining_plan_sequential(
        self,
        *,
        definition: ScenarioDefinitionV2,
        remaining_steps: tuple[AgentStep, ...],
    ) -> tuple[dict[str, object], ...]:
        """Validate the remaining suffix as one projected Plan.

        The runtime actor locations are the starting state.  Only the same
        narrow projections used by initial proposal validation are advanced:
        travel/transport moves an actor, and a declarative passability effect
        can update the validation-only known-passability map.  No hidden Truth,
        resource simulation, or rule preflight is consulted here.
        """

        actors = {
            actor.actor_key: actor
            for actor in self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == self.scope.game_instance_id
                )
            )
        }
        projected_actor_locations = {
            actor_key: actor.current_node_key for actor_key, actor in actors.items()
        }
        projected_command_reachability = {
            actor_key: _actor_command_reachability(actor) for actor_key, actor in actors.items()
        }
        projected_known_passability = self._known_passability(definition)
        projected_known_facts = self._known_fact_projection()
        projected_known_nodes = self._known_node_keys()
        projected_known_relations = self._known_relation_keys(definition)
        projected_resource_pools, projected_region_resource_knowledge = (
            self._projected_resource_state(definition)
        )
        actions = {action.key: action for action in definition.actions}

        for step in remaining_steps:
            if step.execution_type != StepExecutionType.TOOL:
                continue
            action_key = step.tool_arguments.get("action_key")
            actor_key = step.assigned_actor_key
            target_key = step.tool_arguments.get("target_key")
            if (
                not isinstance(action_key, str)
                or not action_key
                or not isinstance(actor_key, str)
                or not actor_key
                or not isinstance(target_key, str)
                or not target_key
            ):
                return (
                    {
                        "code": "KNOWN_PLAN_STEP_INVALID",
                        "sequence": step.sequence,
                        "action_key": action_key,
                    },
                )

            action = actions.get(action_key)
            actor = actors.get(actor_key)
            if action is None or actor is None:
                return (
                    {
                        "code": "KNOWN_PLAN_STEP_INVALID",
                        "sequence": step.sequence,
                        "action_key": action_key,
                    },
                )
            parameters_value = step.tool_arguments.get("parameters", {})
            parameters = dict(parameters_value) if isinstance(parameters_value, dict) else {}
            try:
                parameters = normalize_action_parameters(action, parameters)
            except ValueError as exc:
                return (
                    {
                        "code": "PARAMETER_INVALID",
                        "failure_code": "GENERIC_PLAN_PARAMETER_INVALID",
                        "dimension": "PARAMETER",
                        "step_id": step.planner_step_id,
                        "sequence": step.sequence,
                        "action_key": action.key,
                        "actor_key": actor_key,
                        "target_key": target_key,
                        "actual_parameters": parameters,
                        "validation_error": str(exc),
                    },
                )
            planning_failure_code = self._planning_action_failure_code(
                definition, action, actor, target_key
            )
            if planning_failure_code is not None:
                diagnostic = _structured_plan_diagnostic(
                    GenericAgentError(
                        planning_failure_code,
                        "The Action assignment no longer satisfies its static contract",
                        details=_planning_failure_details(
                            definition,
                            action,
                            actor,
                            target_key,
                            planning_failure_code,
                        ),
                    ),
                    action=action,
                    step_id=step.planner_step_id or "",
                    actor_key=actor_key,
                    target_key=target_key,
                    projected_command_reachability=projected_command_reachability,
                )
                diagnostic["sequence"] = step.sequence
                return (diagnostic,)
            projected_resolution_effects = self._projected_resolution_effects(
                definition,
                action,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
            try:
                self._validate_projected_command_reachability(
                    action,
                    actor_key,
                    target_key,
                    actors,
                    projected_command_reachability,
                )
                self._validate_projected_action_state(
                    definition,
                    action,
                    actor_key,
                    target_key,
                    parameters,
                    projected_actor_locations,
                    projected_known_passability,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                    actors=actors,
                    projected_command_reachability=projected_command_reachability,
                )
                self._validate_and_advance_projected_resources(
                    definition,
                    action,
                    actor_key,
                    target_key,
                    parameters,
                    projected_actor_locations,
                    projected_resource_pools,
                    projected_region_resource_knowledge,
                    projected_resolution_effects,
                )
            except GenericAgentError as exc:
                diagnostic = _structured_plan_diagnostic(
                    exc,
                    action=action,
                    step_id=step.planner_step_id or "",
                    actor_key=actor_key,
                    target_key=target_key,
                    projected_command_reachability=projected_command_reachability,
                )
                diagnostic["sequence"] = step.sequence
                return (diagnostic,)

            self._advance_projected_action_state(
                definition,
                action,
                actor_key,
                target_key,
                parameters,
                projected_actor_locations,
                projected_known_passability,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
                projected_resolution_effects,
                projected_command_reachability=projected_command_reachability,
            )
        return ()

    @staticmethod
    def _step_has_knowledge_changes(step: AgentStep) -> bool:
        return bool(GenericAgentService._step_knowledge_changes(step))

    @staticmethod
    def _step_knowledge_changes(step: AgentStep) -> tuple[dict[str, object], ...]:
        actual_result = step.actual_result
        if not isinstance(actual_result, dict):
            return ()
        outcome = actual_result.get("outcome", actual_result)
        if not isinstance(outcome, dict):
            return ()
        changes = outcome.get("knowledge_changes")
        if not isinstance(changes, list):
            return ()
        return tuple(item for item in changes if isinstance(item, dict))

    @staticmethod
    def _plan_invalidation(task: AgentTask) -> dict[str, object] | None:
        metadata = task.objective_resolution_metadata
        if not isinstance(metadata, dict):
            return None
        marker = metadata.get("plan_invalidation")
        return marker if isinstance(marker, dict) else None

    def evaluate(self, task: AgentTask) -> GenericObjectiveEvaluation:
        definition = self._definition()
        objectives = self._objectives(task, definition)
        evaluations: list[tuple[str, StrictScalar, bool]] = []
        for objective in objectives:
            for requirement in objective.completion_requirements:
                row = self.db.get(
                    GameInstanceFactState,
                    (self.scope.game_instance_id, requirement.node_key, requirement.fact_key),
                )
                if row is None:
                    raise GenericAgentError(
                        "OBJECTIVE_TRUTH_MISSING", "Objective Truth is missing from this Instance"
                    )
                evaluations.append(
                    (
                        f"{objective.key}:{requirement.key}",
                        row.truth_value,
                        row.truth_value in requirement.accepted_values,
                    )
                )
        return GenericObjectiveEvaluation(
            tuple(objective.key for objective in objectives),
            bool(evaluations) and all(item[2] for item in evaluations),
            tuple(evaluations),
        )

    def _candidate_steps(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        *,
        task: AgentTask,
        reason: str | None,
        plan_version: int,
    ) -> list[dict[str, object]]:
        objective_needed = [
            (requirement.node_key, requirement.fact_key)
            for objective in objectives
            for prerequisite in objective.prerequisites
            for requirement in prerequisite.requirements
            if not self._known_requirement_satisfied(requirement)
        ] + [
            (requirement.node_key, requirement.fact_key)
            for objective in objectives
            for requirement in objective.completion_requirements
            if not self._known_requirement_satisfied(requirement)
        ]
        needed = list(objective_needed)
        recovery_refs = {
            (item.node_key, item.fact_key)
            for action in definition.actions
            for item in action.planning.supporting_effects
        }
        if reason is not None:
            needed = [*sorted(recovery_refs), *needed]
        actions = sorted(
            definition.actions,
            key=lambda item: (
                0 if reason is not None and item.planning.supporting_effects else 1,
                item.key,
            ),
        )
        candidates: list[dict[str, object]] = []
        covered: set[tuple[str, str]] = set()
        rejected_signatures = set(task.rejected_proposal_signatures)
        successful_signatures = self._successful_proposal_signatures(task)
        for action in actions:
            effects = {
                (item.node_key, item.fact_key)
                for item in (
                    *action.planning.terminal_effects,
                    *action.planning.supporting_effects,
                )
            }
            matched = [item for item in needed if item in effects and item not in covered]
            if not matched:
                continue
            target_key = matched[0][0]
            parameters = self._default_parameters(action)
            actor = self._delegate_actor(definition, action, target_key, parameters)
            if actor is None:
                continue
            arguments = {
                "action_key": action.key,
                "target_key": target_key,
                "parameters": parameters,
                "idempotency_key": (
                    f"task-{'-'.join(item.key for item in objectives)}-plan-{plan_version}-"
                    f"{self.scope.game_instance_id}-"
                    f"{action.key}"
                )[:160],
            }
            signature = proposal_signature(actor.actor_key, action.key, target_key, parameters)
            if signature in rejected_signatures:
                continue
            # A historical success is only used together with the current
            # unsatisfied objective projection.  Supporting effects that are
            # no longer needed are directly proven redundant by that current
            # state; a state-dependent action that still covers an objective
            # requirement remains eligible for re-use.
            if signature in successful_signatures and not set(matched).intersection(
                objective_needed
            ):
                continue
            candidates.append(
                {
                    "description": f"Execute {action.name}",
                    "actor_key": actor.actor_key,
                    "execution_type": StepExecutionType.TOOL,
                    "action_intent": action.key,
                    "arguments": arguments,
                    "expected_outcome": {"codes": list(action.planning.success_outcome_codes)},
                    "resume_condition": None,
                }
            )
            if action.execution_mode == ActionExecutionMode.ASYNC:
                candidates.append(
                    {
                        "description": f"Wait for {action.name}",
                        "actor_key": actor.actor_key,
                        "execution_type": StepExecutionType.WAIT_FOR_WORLD_EVENT,
                        "action_intent": action.key,
                        "arguments": {},
                        "expected_outcome": {
                            "codes": list(action.planning.wait_success_outcome_codes)
                        },
                        "resume_condition": {"action_key": action.key},
                    }
                )
            covered.update(matched)
        return candidates

    def _validate_known_action(
        self,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor: GameInstanceActor,
        target_key: str,
    ) -> bool:
        target = definition.world.node(target_key)
        node_state = self.db.get(GameInstanceNodeState, (self.scope.game_instance_id, target_key))
        return bool(
            target
            and node_state
            and actor_binding_matches(definition, actor)
            and node_state.visibility == Visibility.KNOWN
            and node_state.status != NodeStatus.LOCKED
            and action.required_interaction_key in target.interaction_keys
            and action.key in actor.allowed_action_keys
            and {item.value for item in action.allowed_actor_capabilities}.issubset(
                set(actor.capabilities)
            )
            and (
                action.required_actor_role_key is None
                or actor.role_key == action.required_actor_role_key
            )
        )

    def _delegate_actor(
        self,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        target_key: str,
        parameters: dict[str, StrictScalar],
    ) -> GameInstanceActor | None:
        actors = self.db.scalars(
            select(GameInstanceActor).where(
                GameInstanceActor.game_instance_id == self.scope.game_instance_id,
                GameInstanceActor.status == "ACTIVE",
            )
        ).all()
        for actor in sorted(actors, key=lambda item: (item.is_primary, item.actor_key)):
            if not self._validate_known_action(definition, action, actor, target_key):
                continue
            authority = evaluate_authority(actor, action, parameters)
            if authority.outcome != AuthorityOutcome.DENY:
                return actor
        return None

    def _provider_steps(
        self,
        task: AgentTask,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        reason: str | None,
        plan_version: int,
        *,
        single_attempt: bool = False,
        starting_repair_attempt: int = 0,
        rejected_segment: dict[str, object] | None = None,
        initial_diagnostics: tuple[PlanViolation, ...] = (),
        initial_memory: tuple[AntiRegressionMemoryItem, ...] = (),
        planning_cycle: PlanningCycle | None = None,
        planner_input_override: PlannerInput | None = None,
    ) -> list[dict[str, object]]:
        assert self.provider is not None
        context_builder = PlanningContextBuilder(self.db, self.scope)
        planning_context = context_builder.build(
            definition,
            objectives,
            task=task,
            replan_reason=reason,
        )
        planner_input = context_builder.build_v2(
            definition,
            objectives,
            task=task,
            replan_reason=reason,
        )
        if planner_input_override is not None:
            planner_input = planner_input_override
        # The old catalog is retained only as a compatibility projection for
        # existing in-process FakeProviders. It is never serialized by the
        # OpenAI-compatible provider when ``planner_input`` is present.
        catalog_builder = PlanningActionCatalogBuilder(self.db, self.scope)
        catalog = catalog_builder.build(
            definition,
            objectives,
            task=task,
            replan_reason=reason,
            planner_input=planner_input,
        )
        call_type = "INITIAL_PLAN" if reason is None else "REPLAN"
        if not planning_context.relevant_actions and not self.evaluate(task).completed:
            raise GenericAgentError(
                "GENERIC_PLAN_NOT_FOUND",
                "No known public Action can advance the frozen ObjectiveScope",
            )
        if planning_cycle is None:
            planning_cycle = self._start_planning_cycle(
                task,
                call_type=call_type,
                planner_input=planner_input,
                objectives=objectives,
                replan_reason=reason,
            )
        diagnostics: tuple[PlanViolation, ...] = initial_diagnostics
        anti_regression_memory: tuple[AntiRegressionMemoryItem, ...] = initial_memory
        for repair_attempt in (
            [starting_repair_attempt]
            if single_attempt
            else range(self.model_max_repair_attempts_per_cycle + 1)
        ):
            request = PlanRequest(
                call_type=(call_type if repair_attempt == 0 else "REPAIR"),
                goal=task.goal_description,
                objective_keys=tuple(item.key for item in objectives),
                objective_scope=objective_context(
                    objectives,
                    known_fact_refs=catalog_builder.known_fact_refs(),
                ),
                replan_reason=reason,
                known_world=planning_context.current_knowledge,
                actors=planning_context.relevant_actors,
                planning_metadata=definition.planning.model_dump(mode="json"),
                planning_action_catalog=catalog,
                planning_context=planning_context,
                planner_input=planner_input,
                rejected_segment=rejected_segment,
                repair_attempt=repair_attempt,
                repair_diagnostics=diagnostics,
                anti_regression_memory=anti_regression_memory,
            )
            planning_attempt = PlanningAttempt(
                cycle_id=planning_cycle.id,
                task_id=task.id,
                attempt_index=repair_attempt,
                call_type=request.call_type,
                status="RUNNING",
                provider_payload=request.provider_payload(),
                anti_regression_memory=[
                    item.model_dump(mode="json", exclude_none=True)
                    for item in anti_regression_memory
                ],
                started_at=datetime.now(UTC),
            )
            self.db.add(planning_attempt)
            self.db.flush()
            audit_id = str(uuid4())
            provider_started_at = perf_counter()
            self._provider_call_started_at[audit_id] = provider_started_at
            start_metadata = provider_call_start_metadata(self.provider, request)
            start_metadata.update(
                {
                    "audit_id": audit_id,
                    "repair_attempt": repair_attempt,
                    "started_at": datetime.now(UTC).isoformat(),
                    "outcome": "RUNNING",
                }
            )
            self._notify_provider_call("STARTED", task, request, start_metadata)
            try:
                proposal = self.provider.propose_plan(request)
            except GenericProviderError as exc:
                self._finish_planning_attempt(
                    planning_attempt,
                    status="ERROR",
                    finished_at=datetime.now(UTC),
                    latency_ms=_duration_ms(provider_started_at),
                    finish_reason=None,
                )
                planning_cycle.current_attempt = repair_attempt
                planning_cycle.status = "ERROR"
                self.db.flush()
                self._notify_provider_call(
                    "FINISHED",
                    task,
                    request,
                    {
                        **provider_call_metadata(self.provider),
                        "audit_id": audit_id,
                        "finished_at": datetime.now(UTC).isoformat(),
                        "latency_ms": _duration_ms(provider_started_at),
                        "wall_clock_latency_ms": _duration_ms(provider_started_at),
                        "outcome": ("TIMEOUT" if exc.code == "MODEL_PROVIDER_TIMEOUT" else "ERROR"),
                        "error_code": exc.code,
                        "error_category": _provider_error_category(exc),
                    },
                )
                self._provider_call_started_at.pop(audit_id, None)
                raise
            except Exception as exc:
                self._finish_planning_attempt(
                    planning_attempt,
                    status="ERROR",
                    finished_at=datetime.now(UTC),
                    latency_ms=_duration_ms(provider_started_at),
                    finish_reason=None,
                )
                planning_cycle.current_attempt = repair_attempt
                planning_cycle.status = "ERROR"
                self.db.flush()
                self._notify_provider_call(
                    "FINISHED",
                    task,
                    request,
                    {
                        **provider_call_metadata(self.provider),
                        "audit_id": audit_id,
                        "finished_at": datetime.now(UTC).isoformat(),
                        "latency_ms": _duration_ms(provider_started_at),
                        "wall_clock_latency_ms": _duration_ms(provider_started_at),
                        "outcome": "ERROR",
                        "error_code": "MODEL_PROVIDER_ERROR",
                        "error_category": type(exc).__name__,
                    },
                )
                self._provider_call_started_at.pop(audit_id, None)
                raise
            diagnostics = _validate_plan_segment_contract(proposal, planner_input)
            if diagnostics:
                steps: list[dict[str, object]] = []
            else:
                steps, raw_diagnostics = self._validate_provider_proposal_v1(
                    task,
                    definition,
                    objectives,
                    reason,
                    plan_version,
                    catalog,
                    proposal.steps,
                    planning_context,
                    planner_input=planner_input,
                    stop_reason=proposal.stop_reason,
                )
                diagnostics = tuple(
                    PlanViolation.model_validate(item) for item in raw_diagnostics
                )
            self._record_provider_plan_call(
                task,
                request=request,
                proposal_steps=proposal.steps,
                proposal_candidate_ids=tuple(
                    item.candidate_id or "" for item in proposal.steps if item.candidate_id
                ),
                diagnostics=diagnostics,
                proposal_stop_reason=proposal.stop_reason,
                accepted=not diagnostics,
                audit_id=audit_id,
            )
            self._finish_planning_attempt(
                planning_attempt,
                status="ACCEPTED" if not diagnostics else "REJECTED",
                finished_at=datetime.now(UTC),
                latency_ms=_duration_ms(provider_started_at),
                proposal=proposal.model_dump(mode="json"),
                rejected_segment=(proposal.model_dump(mode="json") if diagnostics else None),
                validator_violations=[
                    violation.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
                    for violation in diagnostics
                ],
                stop_reason=proposal.stop_reason,
                provider_metadata=provider_call_metadata(self.provider),
            )
            self._last_provider_attempt = {
                "accepted": not diagnostics,
                "proposal": proposal.model_dump(mode="json"),
                "diagnostics": diagnostics,
                "repair_attempt": repair_attempt,
            }
            planning_cycle.current_attempt = repair_attempt
            if repair_attempt > 0:
                anti_regression_memory = _remember_prior_contradictions(
                    anti_regression_memory,
                    request.repair_diagnostics,
                    seen_attempt=repair_attempt - 1,
                )
            self._provider_call_started_at.pop(audit_id, None)
            if not diagnostics:
                planning_cycle.status = "ACCEPTED"
                planning_cycle.current_violations = []
                planning_cycle.rejected_segment = None
                self._last_provider_plan_summary = proposal.plan_summary.strip() or None
                self._last_provider_stop_reason = proposal.stop_reason
                return steps
            rejected_segment = proposal.model_dump(mode="json")
            planning_cycle.rejected_segment = rejected_segment
            planning_cycle.current_violations = [
                violation.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
                for violation in diagnostics
            ]
            planning_cycle.anti_regression_memory = [
                item.model_dump(mode="json", exclude_none=True)
                for item in anti_regression_memory
            ]
            if single_attempt:
                self.db.flush()
                return []
        planning_cycle.status = "REJECTED"
        self.db.flush()
        raise GenericAgentError(
            "MODEL_PLAN_REJECTED",
            "The model provider could not produce a backend-valid current Plan",
        )

    def _notify_provider_call(
        self,
        event: str,
        task: AgentTask,
        request: PlanRequest,
        details: dict[str, object],
    ) -> None:
        if self.provider_call_observer is not None:
            self.provider_call_observer(event, task, request, details)

    def _validate_provider_proposal_v1(
        self,
        task: AgentTask,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        reason: str | None,
        plan_version: int,
        catalog: tuple[PlanningActionCandidate, ...],
        proposed_steps: tuple[object, ...],
        planning_context: PlanningContext,
        *,
        planner_input: PlannerInput | None = None,
        stop_reason: str = "OBJECTIVE_COMPLETION",
    ) -> tuple[list[dict[str, object]], tuple[dict[str, object], ...]]:
        """Validate direct V1 bindings while accepting legacy candidate IDs.

        Only hard constraints are enforced here.  Current access, resources,
        Rule preflight, and dynamic approval remain execution-time concerns in
        the existing Generic Action service.  A future locked target is thus a
        valid Plan member when its static visibility/interaction contract is
        valid.
        """

        actors = {
            item.actor_key: item
            for item in self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == self.scope.game_instance_id
                )
            )
        }
        projected_actor_locations = {
            actor_key: actor.current_node_key for actor_key, actor in actors.items()
        }
        projected_command_reachability = {
            actor_key: _actor_command_reachability(actor) for actor_key, actor in actors.items()
        }
        projected_known_passability = self._known_passability(definition)
        projected_known_facts = self._known_fact_projection()
        projected_known_nodes = self._known_node_keys()
        projected_known_relations = self._known_relation_keys(definition)
        projected_resource_pools, projected_region_resource_knowledge = (
            self._projected_resource_state(definition)
        )

        successful_signatures = self._successful_proposal_signatures(task)
        result: list[dict[str, object]] = []
        step_effects: list[set[tuple[str, str]]] = []
        if not proposed_steps and stop_reason != "BLOCKED":
            return [], (
                {
                    "code": "NO_STEPS",
                    "failure_code": "NO_STEPS",
                    "dimension": "PLAN_STRUCTURE",
                    "required": "AT_LEAST_ONE_STEP",
                    "actual": 0,
                    "message": "Proposal contains no steps",
                },
            )
        if not proposed_steps:
            return [], ()

        static_bindings, diagnostics = self._static_proposal_bindings(
            task=task,
            definition=definition,
            objectives=objectives,
            catalog=catalog,
            proposed_steps=proposed_steps,
            planning_context=planning_context,
            planner_input=planner_input,
            actors=actors,
            projected_command_reachability=projected_command_reachability,
        )
        if diagnostics:
            return [], tuple(
                _diagnostic_with_step_id(item, proposed_steps) for item in diagnostics
            )

        for static_binding in static_bindings:
            index = static_binding.index
            raw_step = static_binding.raw_step
            candidate = static_binding.candidate
            action = static_binding.action
            actor = static_binding.actor
            action_key = action.key
            actor_key = actor.actor_key
            target_key = static_binding.target_key
            parameters = static_binding.parameters
            projected_resolution_effects = self._projected_resolution_effects(
                definition,
                action,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
            try:
                signature = proposal_signature(actor_key, action_key, target_key, parameters)
                if signature in successful_signatures and self._historical_success_is_redundant(
                    definition=definition,
                    action=action,
                    actor=actor,
                    target_key=target_key,
                    planner_input=planner_input,
                    projected_actor_locations=projected_actor_locations,
                ):
                    raise GenericAgentError(
                        "OBJECTIVE_IRRELEVANT",
                        "The previously successful location is already the projected location",
                        details={
                            "dimension": "OBJECTIVE_RELEVANCE",
                            "required": "ADVANCES_FROZEN_OBJECTIVE_SCOPE",
                            "actual": "ALREADY_SUCCEEDED_WITHOUT_NEEDED_EFFECT",
                        },
                    )
                self._validate_projected_command_reachability(
                    action,
                    actor_key,
                    target_key,
                    actors,
                    projected_command_reachability,
                )
                self._validate_projected_action_state(
                    definition,
                    action,
                    actor_key,
                    target_key,
                    parameters,
                    projected_actor_locations,
                    projected_known_passability,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                    actors=actors,
                    projected_command_reachability=projected_command_reachability,
                )
                self._validate_and_advance_projected_resources(
                    definition,
                    action,
                    actor_key,
                    target_key,
                    parameters,
                    projected_actor_locations,
                    projected_resource_pools,
                    projected_region_resource_knowledge,
                    projected_resolution_effects,
                )
            except GenericAgentError as exc:
                diagnostics.append(
                    _structured_plan_diagnostic(
                        exc,
                        action=action,
                        step_id=str(getattr(raw_step, "step_id", "")),
                        actor_key=actor_key,
                        target_key=target_key,
                        projected_command_reachability=projected_command_reachability,
                    )
                )
                break
            effect_refs = self._objective_effect_refs(
                planning_context,
                action_key,
                target_key,
                planner_input=planner_input,
            )
            target_definition = definition.world.node(target_key)
            target_actor = (
                actors.get(target_key) if action.target_kind == ActionTargetKind.ACTOR else None
            )
            target_name = (
                target_definition.name
                if target_definition is not None
                else target_actor.name
                if target_actor is not None
                else target_key
            )
            binding = PlanningActionCandidate(
                candidate_id=(
                    candidate.candidate_id
                    if candidate is not None
                    else f"binding:{action_key}:{actor_key}:{target_key}"
                ),
                action_key=action_key,
                action_name=action.name,
                actor_key=actor_key,
                actor_name=actor.name,
                target_key=target_key,
                target_name=target_name,
                target_kind=action.target_kind.value,
                parameter_domain=tuple(item.model_dump(mode="json") for item in action.parameters),
                public_effects=tuple(
                    {
                        "kind": (
                            "TERMINAL" if item in action.planning.terminal_effects else "SUPPORTING"
                        ),
                        "node_key": item.node_key,
                        "fact_key": item.fact_key,
                    }
                    for item in (
                        *action.planning.terminal_effects,
                        *action.planning.supporting_effects,
                    )
                    if (item.node_key, item.fact_key) in effect_refs
                ),
                currently_executable=True,
            )
            try:
                generated = self._validated_proposed_step(
                    definition,
                    binding,
                    parameters,
                    objectives,
                    plan_version,
                    index,
                    reason,
                    allow_epistemic=True,
                )
            except GenericAgentError as exc:
                diagnostics.append(
                    _structured_plan_diagnostic(
                        exc,
                        action=action,
                        step_id=str(getattr(raw_step, "step_id", "")),
                        actor_key=actor_key,
                        target_key=target_key,
                        projected_command_reachability=projected_command_reachability,
                    )
                )
                break
            purpose = getattr(raw_step, "purpose", "")
            if isinstance(purpose, str) and purpose.strip() and generated:
                generated[0]["description"] = purpose.strip()[:400]
            if generated:
                generated[0]["planner_step_id"] = getattr(raw_step, "step_id", "")
            result.extend(generated)
            step_effects.append(effect_refs)
            self._advance_projected_action_state(
                definition,
                action,
                actor_key,
                target_key,
                parameters,
                projected_actor_locations,
                projected_known_passability,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
                projected_resolution_effects,
                planner_input=planner_input,
                projected_command_reachability=projected_command_reachability,
            )

        if diagnostics:
            return [], tuple(
                _diagnostic_with_step_id(item, proposed_steps) for item in diagnostics
            )

        covered_before: set[tuple[str, str]] = set()
        for index, effects in enumerate(step_effects, start=1):
            for objective in objectives:
                completion_refs = {
                    (requirement.node_key, requirement.fact_key)
                    for requirement in objective.completion_requirements
                    if self._known_requirement_public(requirement)
                    and not self._known_requirement_satisfied(requirement)
                }
                prerequisite_refs = {
                    (requirement.node_key, requirement.fact_key)
                    for prerequisite in objective.prerequisites
                    for requirement in prerequisite.requirements
                    if self._known_requirement_public(requirement)
                    and not self._known_requirement_satisfied(requirement)
                }
                missing_before = prerequisite_refs - covered_before
                if effects & completion_refs and missing_before:
                    return [], (
                        {
                            "code": "PLAN_ORDER_INVALID",
                            "failure_code": "PLAN_ORDER_INVALID",
                            "dimension": "PLAN_ORDER",
                            "step_id": str(getattr(proposed_steps[index - 1], "step_id", "")),
                            "required": "PUBLIC_PREREQUISITES_BEFORE_TERMINAL_EFFECT",
                            "actual": [
                                {"node_key": node_key, "fact_key": fact_key}
                                for node_key, fact_key in sorted(missing_before)
                            ],
                            "missing_prior_public_requirements": [
                                {"node_key": node_key, "fact_key": fact_key}
                                for node_key, fact_key in sorted(missing_before)
                            ],
                        },
                    )
            covered_before.update(effects)
        objective_needed = {
            (requirement.node_key, requirement.fact_key)
            for objective in objectives
            for prerequisite in objective.prerequisites
            for requirement in prerequisite.requirements
            if self._known_requirement_public(requirement)
            and not self._known_requirement_satisfied(requirement)
        } | {
            (requirement.node_key, requirement.fact_key)
            for objective in objectives
            for requirement in objective.completion_requirements
            if self._known_requirement_public(requirement)
            and not self._known_requirement_satisfied(requirement)
        }
        missing_refs = objective_needed - set().union(*step_effects)
        if missing_refs and stop_reason == "OBJECTIVE_COMPLETION":
            return [], (
                {
                    "code": "OBJECTIVE_COVERAGE_INCOMPLETE",
                    "failure_code": "OBJECTIVE_COVERAGE_INCOMPLETE",
                    "dimension": "OBJECTIVE_COVERAGE",
                    "required": "ALL_KNOWN_PUBLIC_REQUIREMENTS_COVERED",
                    "actual": [
                        {"node_key": node_key, "fact_key": fact_key}
                        for node_key, fact_key in sorted(missing_refs)
                    ],
                    "missing_public_requirements": [
                        {"node_key": node_key, "fact_key": fact_key}
                        for node_key, fact_key in sorted(missing_refs)
                    ],
                },
            )
        return result, ()

    def _start_planning_cycle(
        self,
        task: AgentTask,
        *,
        call_type: str,
        planner_input: PlannerInput,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        replan_reason: str | None = None,
    ) -> PlanningCycle:
        payload = planner_input.model_dump(mode="json")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        cycle = PlanningCycle(
            task_id=task.id,
            game_instance_id=self.scope.game_instance_id,
            base_call_type=call_type,
            replan_reason=replan_reason,
            frozen_objective_scope=[item.key for item in objectives],
            planner_input=payload,
            planner_input_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            status="RUNNING",
            current_attempt=0,
            current_violations=[],
            anti_regression_memory=[],
        )
        self.db.add(cycle)
        self.db.flush()
        return cycle

    def _latest_planning_cycle(self, task: AgentTask) -> PlanningCycle:
        cycle = self.db.scalar(
            select(PlanningCycle)
            .where(PlanningCycle.task_id == task.id)
            .order_by(PlanningCycle.created_at.desc())
        )
        if cycle is None:
            raise GenericAgentError("PLANNING_CYCLE_MISSING", "No planning cycle is persisted")
        return cycle

    @staticmethod
    def _finish_planning_attempt(
        attempt: PlanningAttempt,
        *,
        status: str,
        finished_at: datetime,
        latency_ms: int,
        proposal: dict[str, object] | None = None,
        rejected_segment: dict[str, object] | None = None,
        validator_violations: list[dict[str, object]] | None = None,
        stop_reason: str | None = None,
        provider_metadata: dict[str, object] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        attempt.status = status
        attempt.finished_at = finished_at
        attempt.latency_ms = latency_ms
        attempt.proposal = proposal
        attempt.rejected_segment = rejected_segment
        if validator_violations is not None:
            attempt.validator_violations = validator_violations
        attempt.stop_reason = stop_reason
        metadata = provider_metadata or {}
        usage = metadata.get("usage")
        attempt.usage = usage if isinstance(usage, dict) else None
        raw_finish = metadata.get("finish_reason")
        attempt.finish_reason = raw_finish if isinstance(raw_finish, str) else finish_reason

    def _static_proposal_bindings(
        self,
        *,
        task: AgentTask,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        catalog: tuple[PlanningActionCandidate, ...],
        proposed_steps: tuple[object, ...],
        planning_context: PlanningContext,
        planner_input: PlannerInput | None = None,
        actors: dict[str, GameInstanceActor],
        projected_command_reachability: dict[str, CommandReachability],
    ) -> tuple[list[_StaticProposalBinding], list[dict[str, object]]]:
        """Validate all ordering-independent proposal facts without projecting state."""

        candidates = {item.candidate_id: item for item in catalog}
        actions = {item.key: item for item in definition.actions}
        target_keys = {
            str(item.get("target_key"))
            for item in planning_context.relevant_targets
            if isinstance(item.get("target_key"), str)
        }
        context_action_keys = {
            str(item.get("action_key"))
            for item in planning_context.relevant_actions
            if isinstance(item.get("action_key"), str)
            and (
                bool(item.get("objective_relevance"))
                or bool(item.get("declared_knowledge_effects"))
                or item.get("behavior") != "RULE"
                or item.get("locality") != "NONE"
            )
        }
        canonical_action_keys = (
            {item.action_key for item in planner_input.action_contracts}
            if planner_input is not None
            else None
        )
        canonical_actor_actions = (
            {item.actor_key: set(item.allowed_action_keys) for item in planner_input.actors}
            if planner_input is not None
            else None
        )
        objective_refs = {
            (requirement.node_key, requirement.fact_key)
            for objective in objectives
            for requirement in (
                *objective.completion_requirements,
                *(item for group in objective.prerequisites for item in group.requirements),
            )
        }
        bindings: list[_StaticProposalBinding] = []
        diagnostics: list[dict[str, object]] = []
        for index, raw_step in enumerate(proposed_steps, start=1):
            candidate_id = getattr(raw_step, "candidate_id", None)
            action_key = getattr(raw_step, "action_key", None)
            actor_key = getattr(raw_step, "actor_key", None)
            target_key = getattr(raw_step, "target_key", None)
            candidate = candidates.get(candidate_id) if isinstance(candidate_id, str) else None
            if isinstance(candidate_id, str) and candidate is None and not action_key:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_CANDIDATE",
                        "failure_code": "UNKNOWN_CANDIDATE",
                        "dimension": "CANDIDATE_BINDING",
                        "step": index,
                        "candidate_id": candidate_id,
                        "required": "KNOWN_CANDIDATE_OR_DIRECT_BINDING",
                        "actual": candidate_id,
                    }
                )
                break
            if candidate is not None:
                action_key = action_key or candidate.action_key
                actor_key = actor_key or candidate.actor_key
                target_key = target_key or candidate.target_key
            if not isinstance(action_key, str) or not action_key:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_ACTION",
                        "failure_code": "UNKNOWN_ACTION",
                        "dimension": "ACTION_BINDING",
                        "step": index,
                        "required": "KNOWN_ACTION_KEY",
                        "actual": "MISSING",
                    }
                )
                continue
            if not isinstance(actor_key, str) or not actor_key:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_ACTOR",
                        "failure_code": "UNKNOWN_ACTOR",
                        "dimension": "ACTOR_BINDING",
                        "step": index,
                        "required": "KNOWN_ACTOR_KEY",
                        "actual": "MISSING",
                    }
                )
                continue
            if not isinstance(target_key, str) or not target_key:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_TARGET",
                        "failure_code": "UNKNOWN_TARGET",
                        "dimension": "TARGET_BINDING",
                        "step": index,
                        "required": "KNOWN_VISIBLE_TARGET_KEY",
                        "actual": "MISSING",
                    }
                )
                continue
            action = actions.get(action_key)
            actor = actors.get(actor_key)
            if action is None:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_ACTION",
                        "failure_code": "UNKNOWN_ACTION",
                        "dimension": "ACTION_BINDING",
                        "step": index,
                        "action_key": action_key,
                        "required": "KNOWN_ACTION_KEY",
                        "actual": action_key,
                    }
                )
                continue
            if canonical_action_keys is not None and action_key not in canonical_action_keys:
                diagnostics.append(
                    {
                        "code": "ACTION_OUTSIDE_PLANNER_CONTEXT",
                        "failure_code": "ACTION_OUTSIDE_PLANNER_CONTEXT",
                        "dimension": "ACTION_BINDING",
                        "step": index,
                        "action_key": action_key,
                        "actor_key": actor_key,
                        "target_key": target_key,
                        "required": "ACTION_IN_CANONICAL_ACTION_CONTRACTS",
                        "actual": action_key,
                    }
                )
                continue
            if actor is None:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_ACTOR",
                        "failure_code": "UNKNOWN_ACTOR",
                        "dimension": "ACTOR_BINDING",
                        "step": index,
                        "actor_key": actor_key,
                        "required": "KNOWN_ACTOR_KEY",
                        "actual": actor_key,
                    }
                )
                continue
            if (
                canonical_actor_actions is not None
                and action_key not in canonical_actor_actions.get(actor_key, set())
            ):
                diagnostics.append(
                    {
                        "code": "ACTOR_ACTION_OUTSIDE_PLANNER_CONTEXT",
                        "failure_code": "ACTOR_ACTION_OUTSIDE_PLANNER_CONTEXT",
                        "dimension": "ACTOR_ELIGIBILITY",
                        "step": index,
                        "action_key": action_key,
                        "actor_key": actor_key,
                        "target_key": target_key,
                        "required": "ACTOR_ALLOWED_ACTION_IN_CANONICAL_CONTEXT",
                        "actual": action_key,
                    }
                )
                continue
            if target_key not in target_keys:
                diagnostics.append(
                    {
                        "code": "UNKNOWN_TARGET",
                        "failure_code": "UNKNOWN_TARGET",
                        "dimension": "TARGET_BINDING",
                        "step": index,
                        "target_key": target_key,
                        "required": "KNOWN_VISIBLE_TARGET_KEY",
                        "actual": target_key,
                    }
                )
                continue
            raw_parameters = dict(getattr(raw_step, "parameters", {}) or {})
            try:
                parameters = normalize_action_parameters(action, raw_parameters)
            except ValueError as exc:
                diagnostics.append(
                    {
                        "code": "PARAMETER_INVALID",
                        "failure_code": "GENERIC_PLAN_PARAMETER_INVALID",
                        "step": index,
                        "action_key": action_key,
                        "actor_key": actor_key,
                        "target_key": target_key,
                        "dimension": "PARAMETER",
                        "actual_parameters": raw_parameters,
                        "validation_error": str(exc),
                    }
                )
                continue
            failure_code = self._planning_action_failure_code(
                definition, action, actor, target_key
            )
            if failure_code is not None:
                diagnostics.append(
                    _structured_plan_diagnostic(
                        GenericAgentError(
                            failure_code,
                            "The Action assignment does not satisfy its static contract",
                            details=_planning_failure_details(
                                definition, action, actor, target_key, failure_code
                            ),
                        ),
                        action=action,
                        step_id=str(getattr(raw_step, "step_id", "")),
                        actor_key=actor_key,
                        target_key=target_key,
                        projected_command_reachability=projected_command_reachability,
                    )
                )
                continue
            signature = proposal_signature(actor_key, action_key, target_key, parameters)
            effect_refs = self._objective_effect_refs(
                planning_context,
                action_key,
                target_key,
                planner_input=planner_input,
            )
            if signature in set(task.rejected_proposal_signatures):
                diagnostics.append(
                    {
                        "code": "REJECTED_PROPOSAL",
                        "failure_code": "REJECTED_PROPOSAL",
                        "dimension": "PROPOSAL_HISTORY",
                        "step": index,
                        "action_key": action_key,
                        "actor_key": actor_key,
                        "target_key": target_key,
                        "required": "NEW_OR_CORRECTED_BINDING",
                        "actual": "PREVIOUSLY_REJECTED_BINDING",
                    }
                )
                continue
            projected_refs = effect_refs
            actual_relevance = (
                "NO_DECLARED_RELEVANT_EFFECT"
                if objective_refs.isdisjoint(projected_refs)
                and not action.planning.supporting_effects
                and action_key not in context_action_keys
                else None
            )
            if actual_relevance is not None:
                diagnostics.append(
                    {
                        "code": "OBJECTIVE_IRRELEVANT",
                        "failure_code": "OBJECTIVE_IRRELEVANT",
                        "step": index,
                        "action_key": action_key,
                        "actor_key": actor_key,
                        "target_key": target_key,
                        "dimension": "OBJECTIVE_RELEVANCE",
                        "required": "ADVANCES_FROZEN_OBJECTIVE_SCOPE",
                        "actual": actual_relevance,
                    }
                )
                continue
            bindings.append(
                _StaticProposalBinding(
                    index=index,
                    raw_step=raw_step,
                    candidate=candidate,
                    action=action,
                    actor=actor,
                    target_key=target_key,
                    parameters=parameters,
                )
            )
        return bindings, diagnostics

    @staticmethod
    def _context_effect_refs(
        planning_context: PlanningContext, action_key: str
    ) -> set[tuple[str, str]]:
        refs: set[tuple[str, str]] = set()
        for entry in planning_context.relevant_actions:
            if entry.get("action_key") != action_key:
                continue
            for field in ("declared_world_effects", "declared_knowledge_effects"):
                values = entry.get(field)
                if not isinstance(values, list):
                    continue
                for effect in values:
                    if not isinstance(effect, dict):
                        continue
                    node_key = effect.get("node_key")
                    fact_key = effect.get("fact_key")
                    if isinstance(node_key, str) and isinstance(fact_key, str):
                        refs.add((node_key, fact_key))
        return refs

    @staticmethod
    def _historical_success_is_redundant(
        *,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor: GameInstanceActor,
        target_key: str,
        planner_input: PlannerInput | None,
        projected_actor_locations: dict[str, str] | None = None,
    ) -> bool:
        """Use a prior success only when the current state proves redundancy.

        A historical operation is evidence, not proof that a newly proposed
        operation is still unnecessary. Movement is the one generic case where
        the public current location gives a direct proof. Other resource,
        knowledge, and declarative effects remain state-dependent and are
        validated by the current projected state instead of by this signature.
        """

        effect_changes_location = action.behavior == ActionBehavior.TRAVEL
        if planner_input is not None:
            contract = next(
                (
                    item
                    for item in planner_input.action_contracts
                    if item.action_key == action.key
                ),
                None,
            )
            binding = next(
                (
                    item
                    for item in planner_input.target_bindings
                    if item.action_key == action.key and item.target_key == target_key
                ),
                None,
            )
            effect_changes_location = effect_changes_location or bool(
                contract is not None
                and any(
                    effect.get("type") == "ACTOR_LOCATION"
                    and effect.get("value") in {"target_key", "target_node"}
                    for effect in contract.deterministic_effects
                )
            )
            effect_changes_location = effect_changes_location or bool(
                binding is not None
                and any(
                    effect.get("type") == "ACTOR_LOCATION"
                    and effect.get("value") in {"target_key", "target_node"}
                    for effect in binding.deterministic_effects
                )
            )
        if not effect_changes_location:
            return False
        actor_node_key = (
            projected_actor_locations.get(actor.actor_key)
            if projected_actor_locations is not None
            else actor.current_node_key
        )
        if actor_node_key is None:
            return False
        try:
            actor_region = region_for_node(definition, actor_node_key)
            target_region = region_for_node(definition, target_key)
        except LocalityEngineError:
            return False
        return actor_region == target_region

    @classmethod
    def _objective_effect_refs(
        cls,
        planning_context: PlanningContext,
        action_key: str,
        target_key: str,
        *,
        planner_input: PlannerInput | None,
    ) -> set[tuple[str, str]]:
        """Return deterministic FACT effects for the selected Action/Target.

        The canonical V2 contract is authoritative when available.  In
        particular, target bindings are selected by the submitted target;
        effects from another target are never treated as coverage for this
        step.  The legacy context fallback exists only for old in-process
        callers that do not provide a V2 input.
        """

        if planner_input is None:
            return cls._context_effect_refs(planning_context, action_key)

        contracts = {item.action_key: item for item in planner_input.action_contracts}
        contract = contracts.get(action_key)
        if contract is None:
            return set()
        binding = next(
            (
                item
                for item in planner_input.target_bindings
                if item.action_key == action_key and item.target_key == target_key
            ),
            None,
        )
        effects = (
            *(contract.deterministic_effects if contract is not None else ()),
            *(binding.deterministic_effects if binding is not None else ()),
        )
        refs: set[tuple[str, str]] = set()
        for effect in effects:
            if effect.get("type") != "FACT_MUTATION":
                continue
            fact_key = effect.get("fact_key")
            if not isinstance(fact_key, str):
                continue
            effect_target = effect.get("target")
            if effect_target in {"target_key", "target_node"}:
                resolved_target = target_key
            elif isinstance(effect_target, str):
                resolved_target = effect_target
            else:
                continue
            refs.add((resolved_target, fact_key))
        return refs

    @staticmethod
    def _validate_projected_command_reachability(
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        actors: dict[str, GameInstanceActor],
        projected_command_reachability: dict[str, CommandReachability],
    ) -> None:
        if projected_command_reachability.get(actor_key) != CommandReachability.ONLINE:
            raise GenericAgentError(
                "ACTOR_COMMAND_DISCONNECTED",
                "A disconnected Actor cannot receive an ordinary Action",
                details={
                    "dimension": "COMMAND_REACHABILITY",
                    "actor_key": actor_key,
                    "required": CommandReachability.ONLINE.value,
                    "actual": (
                        projected_command_reachability[actor_key].value
                        if actor_key in projected_command_reachability
                        else "UNKNOWN"
                    ),
                },
            )
        if action.target_kind == ActionTargetKind.ACTOR:
            if target_key not in actors:
                raise GenericAgentError(
                    "RELAY_TARGET_INVALID",
                    "The proposed Actor target does not exist",
                    details={
                        "dimension": "TARGET",
                        "target_key": target_key,
                        "required": "ACTOR_EXISTS",
                        "actual": "NOT_FOUND",
                    },
                )
            if action.behavior == ActionBehavior.RELAY_MESSAGE and (
                projected_command_reachability.get(target_key) != CommandReachability.DISCONNECTED
            ):
                raise GenericAgentError(
                    "RELAY_TARGET_NOT_DISCONNECTED",
                    "Relay requires a disconnected target Actor",
                    details={
                        "dimension": "TARGET_COMMAND_REACHABILITY",
                        "target_key": target_key,
                        "required": CommandReachability.DISCONNECTED.value,
                        "actual": (
                            projected_command_reachability[target_key].value
                            if target_key in projected_command_reachability
                            else "UNKNOWN"
                        ),
                    },
                )

    @staticmethod
    def _validate_projected_plan_locality(
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_actor_locations: dict[str, str],
        actors: dict[str, GameInstanceActor] | None = None,
        projected_command_reachability: dict[str, CommandReachability] | None = None,
    ) -> str | None:
        source_node_key = projected_actor_locations.get(actor_key)
        if source_node_key is None:
            raise GenericAgentError(
                "LOCALITY_ACTOR_REGION_REQUIRED",
                "The Actor has no projected current location",
                details={
                    "dimension": "LOCALITY",
                    "required": "ACTOR_REGION",
                    "actual": "UNKNOWN",
                    "actor_key": actor_key,
                },
            )
        try:
            return validate_action_locality(
                definition,
                action,
                actor_current_node_key=source_node_key,
                target_node_key=target_key,
                parameters=parameters,
                target_actor_node_key=(
                    projected_actor_locations.get(target_key)
                    if action.target_kind == ActionTargetKind.ACTOR
                    else None
                ),
            )
        except LocalityEngineError as exc:
            raise GenericAgentError(
                exc.code,
                exc.message,
                details={"dimension": "LOCALITY", **exc.details},
            ) from exc

    def _validate_projected_action_state(
        self,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_actor_locations: dict[str, str],
        projected_known_passability: dict[str, bool],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
        *,
        actors: dict[str, GameInstanceActor] | None = None,
        projected_command_reachability: dict[str, CommandReachability] | None = None,
    ) -> str | None:
        if (
            action.target_kind == ActionTargetKind.ACTOR
            and actors is not None
            and target_key not in actors
        ):
            raise GenericAgentError("RELAY_TARGET_INVALID", "The Actor target does not exist")
        actor = actors.get(actor_key) if actors is not None else None
        if (
            actor is not None
            and action.required_actor_role_key is not None
            and actor.role_key != action.required_actor_role_key
        ):
            raise GenericAgentError(
                "ACTOR_ROLE_MISSING",
                "The proposed Actor does not have the Action's required Role",
                details={
                    "dimension": "ACTOR_ROLE",
                    "actor_key": actor_key,
                    "required": action.required_actor_role_key,
                    "actual": actor.role_key,
                },
            )
        # Existing rule preconditions remain execution-time guards unless an
        # action opts into proposal-time known-state validation.  The new
        # capability actions always opt in; a declared actor-role contract also
        # opts in so declarative specialist actions can reject known-invalid
        # plans without changing legacy Starfire/Medical sequencing behavior.
        validates_known_preflight = (
            action.behavior
            in {
                ActionBehavior.SUPPLY_POWER,
                ActionBehavior.DEPLOY_HEAVY_ENGINEERING_SUPPORT,
            }
            or action.required_actor_role_key is not None
        )
        if validates_known_preflight:
            known_failure = self._known_preflight_failure(
                definition,
                action,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
            if known_failure is not None:
                raise GenericAgentError(
                    known_failure.failure_code,
                    "A known Action requirement is not satisfied",
                    details={
                        "dimension": "ACTION_PRECONDITION",
                        "known_predicate": known_failure.known_predicate,
                        "required": "PREFLIGHT_CONDITION_NOT_MATCHED",
                        "actual": "KNOWN_FAILURE_CONDITION_MATCHED",
                    },
                )
        if action.behavior == ActionBehavior.SUPPLY_POWER:
            self._validate_projected_supply_power(
                definition,
                action,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
        if (
            action.locality.value == "NONE"
            and action.behavior
            not in {ActionBehavior.TRAVEL, ActionBehavior.TRANSPORT_RESOURCE}
        ):
            return None
        connector = self._validate_projected_plan_locality(
            definition,
            action,
            actor_key,
            target_key,
            parameters,
            projected_actor_locations,
            actors=actors,
            projected_command_reachability=projected_command_reachability,
        )
        if (
            action.behavior
            in {ActionBehavior.TRAVEL, ActionBehavior.TRANSPORT_RESOURCE}
            and connector is not None
            and projected_known_passability.get(connector) is False
        ):
            source_region = region_for_node(definition, projected_actor_locations[actor_key])
            target_node_key = (
                projected_actor_locations[target_key]
                if action.target_kind == ActionTargetKind.ACTOR
                else target_key
            )
            raise GenericAgentError(
                "KNOWN_TRANSPORT_BLOCKED",
                "The proposed route is known to be blocked",
                details={
                    "dimension": "TRANSPORT_PASSABILITY",
                    "transport_key": connector,
                    "source_region": source_region,
                    "target_region": region_for_node(definition, target_node_key),
                    "required": "PASSABLE",
                    "actual": "BLOCKED",
                },
            )
        return connector

    @staticmethod
    def _validate_projected_supply_power(
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> None:
        source_key = parameters.get("source_key")
        if not isinstance(source_key, str) or source_key not in projected_known_nodes:
            raise GenericAgentError(
                "SUPPLY_POWER_RELATION_UNKNOWN",
                "The proposed power source is not currently known",
                details={
                    "dimension": "POWER_SOURCE_REQUIREMENT",
                    "required": "KNOWN_SOURCE_NODE",
                    "actual": "UNKNOWN",
                    "known_predicate": {
                        "parameter_key": "source_key",
                        "operator": "REFERENCES_KNOWN_NODE",
                        "expected": True,
                        "actual": False,
                    },
                },
            )
        if target_key not in projected_known_nodes:
            raise GenericAgentError(
                "SUPPLY_POWER_RELATION_UNKNOWN",
                "The proposed power target is not currently known",
                details={
                    "dimension": "POWER_SOURCE_REQUIREMENT",
                    "required": "KNOWN_TARGET_NODE",
                    "actual": "UNKNOWN",
                    "known_predicate": {
                        "node_key": target_key,
                        "operator": "VISIBLE",
                        "expected": True,
                        "actual": False,
                    },
                },
            )
        if not any(
            relation_identity(relation) in projected_known_relations
            and relation.source_node_key == source_key
            and relation.relation_type_key == action.source_relation_type_key
            and relation.target_node_key == target_key
            and relation.source_node_key in projected_known_nodes
            and relation.target_node_key in projected_known_nodes
            for relation in definition.world.relations
        ):
            raise GenericAgentError(
                "SUPPLY_POWER_RELATION_UNKNOWN",
                "No known direct power relation connects the source and target",
                details={
                    "dimension": "POWER_SOURCE_REQUIREMENT",
                    "required": "KNOWN_DIRECT_RELATION",
                    "actual": "UNKNOWN",
                    "known_predicate": {
                        "node_key": source_key,
                        "relation_type": action.source_relation_type_key,
                        "target_key": target_key,
                        "operator": "EXISTS",
                        "expected": True,
                        "actual": False,
                    },
                },
            )
        for fact_key, expected, code in (
            ("operational", True, "SUPPLY_POWER_SOURCE_NOT_OPERATIONAL"),
            ("power_supply", "AVAILABLE", "SUPPLY_POWER_SOURCE_UNAVAILABLE"),
        ):
            fact = projected_known_facts.get((source_key, fact_key))
            if fact is not None and fact.visibility == Visibility.KNOWN and fact.value != expected:
                raise GenericAgentError(
                    code,
                    "The power source does not satisfy its known power requirement",
                    details={
                        "dimension": "POWER_SOURCE_REQUIREMENT",
                        "required": expected,
                        "actual": fact.value,
                        "known_predicate": {
                            "node_key": source_key,
                            "fact_key": fact_key,
                            "operator": "EQ",
                            "expected": expected,
                            "actual": fact.value,
                        },
                    },
                )

    @classmethod
    def _known_preflight_failure(
        cls,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> _KnownPreflightFailure | None:
        matches: list[tuple[int, _KnownPreflightFailure]] = []
        for rule in definition.rules:
            if rule.phase != RulePhase.PREFLIGHT or rule.action_key != action.key:
                continue
            status = cls._known_condition_status(
                definition,
                rule.condition,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
            if status is not True:
                continue
            failure = next(
                (effect.failure_code for effect in rule.effects if effect.failure_code),
                None,
            )
            if failure is not None:
                witness = cls._known_condition_witness(
                    definition,
                    rule.condition,
                    target_key,
                    parameters,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                    expected_status=True,
                )
                matches.append(
                    (
                        rule.priority,
                        _KnownPreflightFailure(
                            failure,
                            witness or {"condition": "KNOWN_TRUE"},
                        ),
                    )
                )
        if not matches:
            return None
        return max(matches, key=lambda item: item[0])[1]

    @classmethod
    def _known_condition_witness(
        cls,
        definition: ScenarioDefinitionV2,
        condition: ConditionV2 | None,
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
        *,
        expected_status: bool,
    ) -> dict[str, object] | None:
        """Return one public Known predicate explaining a condition result."""

        if condition is None:
            return {"condition": "ALWAYS", "actual": True}
        kind = condition.kind.value
        if kind == "ALL" and expected_status:
            predicates: list[dict[str, object]] = []
            for child in condition.conditions:
                witness = cls._known_condition_witness(
                    definition,
                    child,
                    target_key,
                    parameters,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                    expected_status=True,
                )
                if witness is not None:
                    predicates.append(witness)
            return {"operator": "ALL", "predicates": predicates} if predicates else None
        if kind in {"ALL", "ANY"}:
            for child in condition.conditions:
                status = cls._known_condition_status(
                    definition,
                    child,
                    target_key,
                    parameters,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                )
                if status is expected_status:
                    witness = cls._known_condition_witness(
                        definition,
                        child,
                        target_key,
                        parameters,
                        projected_known_facts,
                        projected_known_nodes,
                        projected_known_relations,
                        expected_status=expected_status,
                    )
                    if witness is not None:
                        return {"parent_operator": kind, **witness}
            return None
        if kind == "NOT":
            witness = cls._known_condition_witness(
                definition,
                condition.condition,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
                expected_status=not expected_status,
            )
            return {"negated": True, **witness} if witness is not None else None

        node_key = cls._projected_selector_key(
            definition,
            condition.node,
            target_key,
            parameters,
            projected_known_facts,
            projected_known_nodes,
            projected_known_relations,
        )
        if kind in {"FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN", "FACT_COMPARE"}:
            if node_key is None or not isinstance(condition.fact_key, str):
                return None
            fact = projected_known_facts.get((node_key, condition.fact_key))
            if fact is None or fact.visibility != Visibility.KNOWN:
                return None
            operator: object = {
                "FACT_EQUALS": "EQ",
                "FACT_NOT_EQUALS": "NE",
                "FACT_IN": "IN",
            }.get(kind, condition.operator.value if condition.operator is not None else None)
            expected: object = condition.values if kind == "FACT_IN" else condition.value
            return {
                "kind": kind,
                "node_key": node_key,
                "fact_key": condition.fact_key,
                "operator": operator,
                "expected": expected,
                "actual": fact.value,
            }
        if kind == "PARAMETER_COMPARE" and isinstance(condition.parameter_key, str):
            return {
                "kind": kind,
                "parameter_key": condition.parameter_key,
                "operator": condition.operator.value if condition.operator is not None else None,
                "expected": condition.value,
                "actual": parameters.get(condition.parameter_key),
            }
        if kind == "RELATION_EXISTS" and condition.relation_type_key is not None:
            return {
                "kind": kind,
                "node_key": node_key,
                "relation_type": condition.relation_type_key,
                "operator": "EXISTS",
                "expected": expected_status,
                "actual": expected_status,
            }
        if kind == "NODE_VISIBLE":
            return {
                "kind": kind,
                "node_key": node_key,
                "operator": "VISIBILITY_EQUALS",
                "expected": (
                    condition.visibility.value if condition.visibility is not None else None
                ),
                "actual": (
                    Visibility.KNOWN.value
                    if node_key in projected_known_nodes
                    else Visibility.HIDDEN.value
                ),
            }
        return None

    @classmethod
    def _known_condition_status(
        cls,
        definition: ScenarioDefinitionV2,
        condition: ConditionV2 | None,
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> bool | None:
        if condition is None:
            return True
        kind = condition.kind
        if kind.value == "ALL":
            statuses = [
                cls._known_condition_status(
                    definition,
                    child,
                    target_key,
                    parameters,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                )
                for child in condition.conditions
            ]
            if any(status is False for status in statuses):
                return False
            return True if all(status is True for status in statuses) else None
        if kind.value == "ANY":
            statuses = [
                cls._known_condition_status(
                    definition,
                    child,
                    target_key,
                    parameters,
                    projected_known_facts,
                    projected_known_nodes,
                    projected_known_relations,
                )
                for child in condition.conditions
            ]
            if any(status is True for status in statuses):
                return True
            return False if all(status is False for status in statuses) else None
        if kind.value == "NOT":
            status = cls._known_condition_status(
                definition,
                condition.condition,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
            return None if status is None else not status
        node_key = cls._projected_selector_key(
            definition,
            condition.node,
            target_key,
            parameters,
            projected_known_facts,
            projected_known_nodes,
            projected_known_relations,
        )
        if kind.value in {"FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN", "FACT_COMPARE"}:
            if node_key is None or not isinstance(condition.fact_key, str):
                return None
            fact = projected_known_facts.get((node_key, condition.fact_key))
            if fact is None or fact.visibility != Visibility.KNOWN:
                return None
            if kind.value == "FACT_EQUALS":
                return fact.value == condition.value
            if kind.value == "FACT_NOT_EQUALS":
                return fact.value != condition.value
            if kind.value == "FACT_IN":
                return fact.value in condition.values
            return cls._compare_projected(
                fact.value,
                condition.operator,
                condition.value,
            )
        if kind.value == "PARAMETER_COMPARE":
            if not isinstance(condition.parameter_key, str):
                return None
            return cls._compare_projected(
                parameters.get(condition.parameter_key),
                condition.operator,
                condition.value,
            )
        if kind.value == "RELATION_EXISTS":
            if (
                node_key is None
                or condition.relation_type_key is None
                or condition.relation_direction is None
            ):
                return None
            relation_direction = condition.relation_direction
            return any(
                (
                    relation.relation_type_key == condition.relation_type_key
                    and (
                        (
                            relation_direction.value == "SOURCE"
                            and relation.source_node_key == node_key
                            and relation.target_node_key in projected_known_nodes
                        )
                        or (
                            relation_direction.value == "TARGET"
                            and relation.target_node_key == node_key
                            and relation.source_node_key in projected_known_nodes
                        )
                    )
                )
                for relation in definition.world.relations
                if relation_identity(relation) in projected_known_relations
            )
        if kind.value == "NODE_VISIBLE":
            if node_key is None or condition.visibility is None:
                return None
            return (node_key in projected_known_nodes) == (condition.visibility == Visibility.KNOWN)
        return None

    @staticmethod
    def _compare_projected(
        actual: object,
        operator: ComparisonOperator | None,
        expected: object,
    ) -> bool | None:
        if operator is None or actual is None:
            return None
        if not isinstance(actual, (bool, int, str)) or not isinstance(expected, (bool, int, str)):
            return None
        actual_value = cast(Any, actual)
        expected_value = cast(Any, expected)
        try:
            if operator == ComparisonOperator.EQ:
                return bool(actual_value == expected_value)
            if operator == ComparisonOperator.NE:
                return bool(actual_value != expected_value)
            if operator == ComparisonOperator.LT:
                return bool(actual_value < expected_value)
            if operator == ComparisonOperator.LTE:
                return bool(actual_value <= expected_value)
            if operator == ComparisonOperator.GT:
                return bool(actual_value > expected_value)
            return bool(actual_value >= expected_value)
        except TypeError:
            return None

    @staticmethod
    def _projected_selector_key(
        definition: ScenarioDefinitionV2,
        selector: NodeSelectorV2 | None,
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> str | None:
        if selector is None:
            return None
        kind = selector.kind
        if kind == NodeSelectorKind.CURRENT_TARGET:
            return target_key
        if kind == NodeSelectorKind.ACTION_SOURCE:
            source = parameters.get("source_key")
            return source if isinstance(source, str) else None
        if kind == NodeSelectorKind.EXPLICIT:
            node_key = selector.node_key
            return node_key if isinstance(node_key, str) else None
        if kind != NodeSelectorKind.RELATED:
            return None
        if selector.relation_type_key is None:
            return None
        anchor = selector.anchor_node_key or target_key
        direction = selector.direction.value if selector.direction is not None else None
        candidates = []
        for relation in definition.world.relations:
            if relation_identity(relation) not in projected_known_relations:
                continue
            if relation.relation_type_key != selector.relation_type_key:
                continue
            if direction == "SOURCE" and relation.source_node_key == anchor:
                candidate = relation.target_node_key
            elif direction == "TARGET" and relation.target_node_key == anchor:
                candidate = relation.source_node_key
            else:
                continue
            if candidate not in projected_known_nodes:
                continue
            if selector.required_fact_key is not None:
                fact = projected_known_facts.get((candidate, selector.required_fact_key))
                if fact is None:
                    continue
            candidates.append(candidate)
        unique = sorted(set(candidates))
        return unique[0] if len(unique) == 1 else None

    def _projected_resource_state(
        self,
        definition: ScenarioDefinitionV2,
    ) -> tuple[
        dict[str, _ProjectedResourcePool],
        dict[str, _ProjectedRegionResourceKnowledge],
    ]:
        projection = SharedKnowledgeProjection(self.db, self.scope, definition)
        known_pools = projection.visible_resource_pools()
        known_identities = {
            resource_state_key(item.resource_key, item.region_key, item.pool_key)
            for item in known_pools
        }
        pools = {
            resource_state_key(
                item.resource_key, item.region_key, item.pool_key
            ): _ProjectedResourcePool(
                pool_key=item.pool_key,
                resource_key=item.resource_key,
                region_key=item.region_key,
                facility_key=item.facility_key,
                quantity=item.available_quantity,
                visibility=ResourcePoolVisibility.VISIBLE,
                availability=item.availability,
                survey_discoverable=False,
            )
            for item in known_pools
        }
        for row in self.db.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == self.scope.game_instance_id
            )
        ):
            identity = resource_state_key(row.resource_key, row.scope_node_key, row.pool_key)
            if identity in pools:
                continue
            visibility = ResourcePoolVisibility(row.visibility)
            pools[identity] = _ProjectedResourcePool(
                pool_key=row.pool_key,
                resource_key=row.resource_key,
                region_key=row.scope_node_key,
                facility_key=row.facility_key,
                quantity=(row.value if identity in known_identities else None),
                visibility=visibility,
                availability=ResourcePoolAvailability(row.availability),
                survey_discoverable=row.survey_discoverable,
            )
        # A fully surveyed, visible Region with no visible Pool row is a
        # deterministic known-zero inventory.  Represent that fact in the
        # in-memory projected state without creating a persisted Resource row.
        resource_keys = {item.key for item in definition.world.resources}
        public_pool_pairs = {
            (item.resource_key, item.region_key)
            for item in known_pools
            if item.region_key is not None
        }
        for region_key, knowledge in projection.region_states().items():
            for resource_key in resource_keys:
                if (resource_key, region_key) in public_pool_pairs:
                    continue
                if resource_knowledge_status(
                    inventory_visibility=knowledge.resource_inventory_visibility,
                    survey_completed=knowledge.resource_survey_completed,
                    has_visible_pool=False,
                ) != "KNOWN_ZERO":
                    continue
                identity = resource_state_key(
                    resource_key, region_key, "__known_zero__"
                )
                pools[identity] = _ProjectedResourcePool(
                    pool_key="__known_zero__",
                    resource_key=resource_key,
                    region_key=region_key,
                    facility_key=None,
                    quantity=0,
                    visibility=ResourcePoolVisibility.VISIBLE,
                    availability=ResourcePoolAvailability.AVAILABLE,
                    survey_discoverable=False,
                )
        region_knowledge = {
            key: _ProjectedRegionResourceKnowledge(
                visibility=value.resource_inventory_visibility,
                survey_completed=value.resource_survey_completed,
            )
            for key, value in projection.region_states().items()
        }
        return pools, region_knowledge

    def _validate_and_advance_projected_resources(
        self,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_actor_locations: dict[str, str],
        projected_pools: dict[str, _ProjectedResourcePool],
        projected_region_knowledge: dict[str, _ProjectedRegionResourceKnowledge],
        projected_resolution_effects: Sequence[EffectV2],
    ) -> None:
        if action.behavior == ActionBehavior.SURVEY_RESOURCES:
            knowledge = projected_region_knowledge.get(target_key)
            if knowledge is None:
                raise GenericAgentError(
                    "RESOURCE_REGION_KNOWLEDGE_MISSING",
                    "The target Region resource knowledge is missing",
                )
            if knowledge.survey_completed:
                raise GenericAgentError(
                    "RESOURCE_SURVEY_ALREADY_COMPLETED",
                    "The target Region has already completed a resource survey",
                    details={
                        "dimension": "RESOURCE_SURVEY_STATE",
                        "target_key": target_key,
                        "required": "NOT_COMPLETED",
                        "actual": "COMPLETED",
                    },
                )
            knowledge.visibility = ResourceInventoryVisibility.VISIBLE
            knowledge.survey_completed = True
            for pool in projected_pools.values():
                if (
                    pool.region_key == target_key
                    and pool.facility_key is not None
                    and pool.visibility == ResourcePoolVisibility.HIDDEN
                    and pool.survey_discoverable
                ):
                    pool.visibility = ResourcePoolVisibility.VISIBLE
            self._apply_projected_resource_effects(
                definition,
                actor_key,
                target_key,
                parameters,
                projected_actor_locations,
                projected_pools,
                projected_region_knowledge,
                projected_resolution_effects,
            )
            return

        if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
            resource_key = parameters.get("resource_key")
            amount = parameters.get("amount")
            if not isinstance(resource_key, str) or not isinstance(amount, int):
                raise GenericAgentError(
                    "TRANSPORT_PARAMETERS_INVALID",
                    "Transport requires a Resource key and integer amount",
                )
            source_node_key = projected_actor_locations.get(actor_key)
            if source_node_key is None:
                raise GenericAgentError(
                    "LOCALITY_ACTOR_REGION_REQUIRED",
                    "The Actor has no projected current location",
                )
            source_region = region_for_node(definition, source_node_key)
            destination_region = region_for_node(definition, target_key)
            consumed = self._consume_projected_resource(
                source_region,
                resource_key,
                amount,
                projected_pools,
                projected_region_knowledge,
            )
            if consumed:
                self._add_projected_resource(
                    destination_region,
                    resource_key,
                    amount,
                    projected_pools,
                )

        self._apply_projected_resource_effects(
            definition,
            actor_key,
            target_key,
            parameters,
            projected_actor_locations,
            projected_pools,
            projected_region_knowledge,
            projected_resolution_effects,
        )

    def _consume_projected_resource(
        self,
        region_key: str | None,
        resource_key: str,
        amount: int,
        projected_pools: dict[str, _ProjectedResourcePool],
        projected_region_knowledge: dict[str, _ProjectedRegionResourceKnowledge],
        *,
        require_known: bool = True,
    ) -> bool:
        if amount < 0:
            raise GenericAgentError(
                "RESOURCE_AMOUNT_INVALID",
                "A Resource operation amount cannot be negative",
            )
        candidates = [
            pool
            for pool in projected_pools.values()
            if (
                pool.resource_key == resource_key
                and pool.region_key == region_key
                and pool.visibility == ResourcePoolVisibility.VISIBLE
                and (
                    region_key is None
                    or pool.facility_key is not None
                    or (
                        region_key in projected_region_knowledge
                        and projected_region_knowledge[region_key].visibility
                        == ResourceInventoryVisibility.VISIBLE
                    )
                )
            )
        ]
        available = [
            pool for pool in candidates if pool.availability == ResourcePoolAvailability.AVAILABLE
        ]
        if not candidates:
            knowledge = (
                projected_region_knowledge.get(region_key)
                if region_key is not None
                else None
            )
            knowledge_status = resource_knowledge_status(
                inventory_visibility=(
                    knowledge.visibility
                    if knowledge is not None
                    else ResourceInventoryVisibility.HIDDEN
                ),
                survey_completed=(knowledge.survey_completed if knowledge is not None else False),
                has_visible_pool=False,
            )
            if knowledge_status == "KNOWN_ZERO":
                raise GenericAgentError(
                    "KNOWN_RESOURCE_INSUFFICIENT",
                    "Known available Resource quantity is insufficient",
                    details={
                        "dimension": "RESOURCE_QUANTITY",
                        "resource_key": resource_key,
                        "scope_region": region_key,
                        "required_amount": amount,
                        "projected_known_available_amount": 0,
                        "deficit": amount,
                    },
                )
            raise GenericAgentError(
                "RESOURCE_INVENTORY_UNKNOWN",
                "The source Region Resource inventory is not known",
                details={
                    "dimension": "RESOURCE_KNOWLEDGE",
                    "resource_key": resource_key,
                    "scope_region": region_key,
                    "required_amount": amount,
                    "required": "KNOWN_VISIBLE_AVAILABLE",
                    "actual": "UNKNOWN",
                },
            )
        known_available = sum(pool.quantity for pool in available if pool.quantity is not None)
        has_unknown_available = any(pool.quantity is None for pool in available)
        if known_available < amount and not has_unknown_available:
            knowledge = (
                projected_region_knowledge.get(region_key)
                if region_key is not None
                else None
            )
            inventory_complete = region_key is None or (
                knowledge is not None
                and knowledge.visibility == ResourceInventoryVisibility.VISIBLE
                and knowledge.survey_completed
            )
            if not inventory_complete:
                raise GenericAgentError(
                    "RESOURCE_INVENTORY_UNKNOWN",
                    "The source Region Resource inventory is not known",
                    details={
                        "dimension": "RESOURCE_KNOWLEDGE",
                        "resource_key": resource_key,
                        "scope_region": region_key,
                        "required_amount": amount,
                        "required": "KNOWN_VISIBLE_AVAILABLE",
                        "actual": "UNKNOWN",
                    },
                )
            raise GenericAgentError(
                "KNOWN_RESOURCE_INSUFFICIENT",
                "Known available Resource quantity is insufficient",
                details={
                    "dimension": "RESOURCE_QUANTITY",
                    "resource_key": resource_key,
                    "scope_region": region_key,
                    "required_amount": amount,
                    "projected_known_available_amount": known_available,
                    "deficit": amount - known_available,
                },
            )
        if known_available < amount:
            raise GenericAgentError(
                "RESOURCE_INVENTORY_UNKNOWN",
                "The source Region Resource inventory is not known",
                details={
                    "dimension": "RESOURCE_KNOWLEDGE",
                    "resource_key": resource_key,
                    "scope_region": region_key,
                    "required_amount": amount,
                    "required": "KNOWN_VISIBLE_AVAILABLE",
                    "actual": "UNKNOWN",
                },
            )
        remaining = amount
        for pool in sorted(available, key=lambda item: item.pool_key):
            if remaining <= 0:
                break
            if pool.quantity is None:
                continue
            consumed = min(pool.quantity, remaining)
            pool.quantity -= consumed
            remaining -= consumed
        return True

    @staticmethod
    def _add_projected_resource(
        region_key: str | None,
        resource_key: str,
        amount: int,
        projected_pools: dict[str, _ProjectedResourcePool],
    ) -> None:
        identity = resource_state_key(resource_key, region_key, "default")
        pool = projected_pools.get(identity)
        if pool is None:
            pool = _ProjectedResourcePool(
                pool_key="default",
                resource_key=resource_key,
                region_key=region_key,
                facility_key=None,
                quantity=0,
                visibility=ResourcePoolVisibility.VISIBLE,
                availability=ResourcePoolAvailability.AVAILABLE,
                survey_discoverable=False,
            )
            projected_pools[identity] = pool
        if pool.quantity is not None:
            pool.quantity += amount

    def _apply_projected_resource_effects(
        self,
        definition: ScenarioDefinitionV2,
        actor_key: str,
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_actor_locations: dict[str, str],
        projected_pools: dict[str, _ProjectedResourcePool],
        projected_region_knowledge: dict[str, _ProjectedRegionResourceKnowledge],
        projected_resolution_effects: Sequence[EffectV2],
    ) -> None:
        actor_node_key = projected_actor_locations.get(actor_key)
        for effect in projected_resolution_effects:
            if effect.kind == EffectKind.SET_REGION_RESOURCE_VISIBILITY:
                if effect.region_key is not None and effect.visibility is not None:
                    region = projected_region_knowledge.get(effect.region_key)
                    if region is not None:
                        region.visibility = ResourceInventoryVisibility(effect.visibility.value)
            elif effect.kind == EffectKind.SET_RESOURCE_POOL_VISIBILITY:
                if effect.pool_key is not None and effect.visibility is not None:
                    for pool in projected_pools.values():
                        if pool.pool_key == effect.pool_key:
                            pool.visibility = ResourcePoolVisibility(effect.visibility.value)
            elif effect.kind == EffectKind.SET_RESOURCE_POOL_AVAILABILITY:
                if effect.pool_key is not None and effect.availability is not None:
                    for pool in projected_pools.values():
                        if pool.pool_key == effect.pool_key:
                            pool.availability = effect.availability
            elif effect.kind == EffectKind.ADJUST_RESOURCE:
                if (
                    effect.resource_key is None
                    or effect.amount is None
                    or actor_node_key is None
                ):
                    continue
                amount = self._projected_integer_effect(effect.amount, parameters)
                try:
                    scope = resolve_resource_scope(
                        definition,
                        effect.resource_scope,
                        actor_current_node_key=actor_node_key,
                        target_node_key=target_key,
                    )
                except LocalityEngineError as exc:
                    raise GenericAgentError(exc.code, exc.message) from exc
                if amount < 0:
                    self._consume_projected_resource(
                        scope,
                        effect.resource_key,
                        -amount,
                        projected_pools,
                        projected_region_knowledge,
                        require_known=True,
                    )
                elif amount > 0:
                    self._add_projected_resource(
                        scope,
                        effect.resource_key,
                        amount,
                        projected_pools,
                    )

    @staticmethod
    def _projected_integer_effect(expression: object, parameters: dict[str, StrictScalar]) -> int:
        source = getattr(expression, "source", None)
        multiplier = getattr(expression, "multiplier", 1)
        if getattr(source, "value", source) == ValueSource.LITERAL.value:
            literal = getattr(expression, "literal", None)
            if isinstance(literal, int) and not isinstance(literal, bool):
                return literal * multiplier
        parameter_key = getattr(expression, "parameter_key", None)
        value = parameters.get(parameter_key) if isinstance(parameter_key, str) else None
        if isinstance(value, int) and not isinstance(value, bool):
            return value * multiplier
        raise GenericAgentError(
            "RESOURCE_EFFECT_PARAMETER_UNKNOWN",
            "A Resource effect parameter is not available for Plan validation",
        )

    @staticmethod
    def _advance_projected_action_state(
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_actor_locations: dict[str, str],
        projected_known_passability: dict[str, bool],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
        projected_resolution_effects: Sequence[EffectV2],
        *,
        planner_input: PlannerInput | None = None,
        projected_command_reachability: dict[str, CommandReachability] | None = None,
    ) -> None:
        if action.behavior == ActionBehavior.TRAVEL:
            projected_actor_locations[actor_key] = target_key
        location_effects: tuple[dict[str, object], ...]
        if planner_input is None:
            location_effects = tuple(action_planner_effects(action))
        else:
            contract = next(
                (item for item in planner_input.action_contracts if item.action_key == action.key),
                None,
            )
            binding = next(
                (
                    item
                    for item in planner_input.target_bindings
                    if item.action_key == action.key and item.target_key == target_key
                ),
                None,
            )
            location_effects = tuple(
                effect
                for item in (contract, binding)
                if item is not None
                for effect in item.deterministic_effects
            )
            if contract is None:
                location_effects = tuple(action_planner_effects(action))
        if any(
            effect.get("type") == "ACTOR_LOCATION"
            and effect.get("actor") in {"executor", "actor"}
            and effect.get("value") in {"target_key", "target_node"}
            for effect in location_effects
        ):
            projected_actor_locations[actor_key] = target_key
        GenericAgentService._apply_projected_passability_effect(
            definition,
            target_key,
            projected_resolution_effects,
            projected_known_passability,
        )
        GenericAgentService._apply_projected_fact_effects(
            definition,
            action,
            actor_key,
            target_key,
            parameters,
            projected_resolution_effects,
            projected_known_facts,
            projected_known_nodes,
            projected_known_relations,
        )
        if projected_command_reachability is not None:
            GenericAgentService._apply_projected_actor_reachability_effect(
                action,
                actor_key,
                target_key,
                projected_resolution_effects,
                projected_command_reachability,
            )
            if action.behavior == ActionBehavior.RELAY_MESSAGE:
                projected_command_reachability[target_key] = CommandReachability.ONLINE

    def _known_passability(self, definition: ScenarioDefinitionV2) -> dict[str, bool]:
        fact_key = definition.metadata.locality.passability_fact_key
        if fact_key is None:
            return {}
        return {
            row.node_key: row.truth_value
            for row in self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceFactState.fact_key == fact_key,
                    GameInstanceFactState.visibility == Visibility.KNOWN,
                )
            )
            if isinstance(row.truth_value, bool)
        }

    def _known_fact_projection(self) -> dict[tuple[str, str], _ProjectedFact]:
        return {
            (row.node_key, row.fact_key): _ProjectedFact(
                value=row.truth_value,
                visibility=row.visibility,
            )
            for row in self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == self.scope.game_instance_id
                )
            )
        }

    def _known_node_keys(self) -> set[str]:
        return {
            row.node_key
            for row in self.db.scalars(
                select(GameInstanceNodeState).where(
                    GameInstanceNodeState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceNodeState.visibility == Visibility.KNOWN,
                )
            )
        }

    def _known_relation_keys(self, definition: ScenarioDefinitionV2) -> set[str]:
        return {
            key
            for item in SharedKnowledgeProjection(
                self.db,
                self.scope,
                definition,
            ).known_relations()
            if isinstance((key := item.get("relation_key")), str)
        }

    @staticmethod
    def _apply_projected_fact_effects(
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_resolution_effects: Sequence[EffectV2],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> None:
        if action.behavior == ActionBehavior.INSPECT:
            target = definition.world.node(target_key)
            if target is not None:
                for fact in target.facts:
                    current = projected_known_facts.get((target_key, fact.key))
                    if current is not None:
                        current.visibility = Visibility.KNOWN

        fact_values: dict[tuple[str, str], set[StrictScalar]] = {}
        fact_visibility: dict[tuple[str, str], set[Visibility]] = {}
        node_visibility: dict[str, set[Visibility]] = {}
        relation_visibility: dict[str, set[RelationVisibility]] = {}
        for effect in projected_resolution_effects:
            node_key = GenericAgentService._projected_effect_node_key(
                effect.node,
                target_key,
                parameters,
            )
            if effect.kind == EffectKind.SET_FACT and node_key is not None:
                value = GenericAgentService._projected_value(effect.value, parameters)
                if value is not None and effect.fact_key is not None:
                    fact_values.setdefault((node_key, effect.fact_key), set()).add(value)
            elif effect.kind in {EffectKind.REVEAL_FACT, EffectKind.HIDE_FACT} and node_key:
                if effect.fact_key is not None:
                    fact_visibility.setdefault((node_key, effect.fact_key), set()).add(
                        Visibility.KNOWN
                        if effect.kind == EffectKind.REVEAL_FACT
                        else Visibility.HIDDEN
                    )
            elif effect.kind in {EffectKind.REVEAL_NODE, EffectKind.HIDE_NODE} and node_key:
                node_visibility.setdefault(node_key, set()).add(
                    Visibility.KNOWN
                    if effect.kind == EffectKind.REVEAL_NODE
                    else Visibility.HIDDEN
                )
            elif (
                effect.kind == EffectKind.SET_RELATION_VISIBILITY
                and effect.relation_key is not None
                and effect.visibility is not None
            ):
                relation_visibility.setdefault(effect.relation_key, set()).add(
                    RelationVisibility(effect.visibility.value)
                )

        for identity, fact_value_options in fact_values.items():
            if len(fact_value_options) != 1:
                continue
            current = projected_known_facts.get(identity)
            visibility = current.visibility if current is not None else Visibility.HIDDEN
            projected_known_facts[identity] = _ProjectedFact(fact_value_options.pop(), visibility)
        for identity, visibility_options in fact_visibility.items():
            if len(visibility_options) != 1:
                continue
            current = projected_known_facts.get(identity)
            if current is not None:
                current.visibility = visibility_options.pop()
        for node_key, node_visibility_options in node_visibility.items():
            if len(node_visibility_options) != 1:
                continue
            if node_visibility_options.pop() == Visibility.KNOWN:
                projected_known_nodes.add(node_key)
            else:
                projected_known_nodes.discard(node_key)
        for relation_key, relation_visibility_options in relation_visibility.items():
            if len(relation_visibility_options) != 1:
                continue
            if relation_visibility_options.pop() == RelationVisibility.VISIBLE:
                projected_known_relations.add(relation_key)
            else:
                projected_known_relations.discard(relation_key)

    @classmethod
    def _projected_resolution_rules(
        cls,
        definition: ScenarioDefinitionV2,
        rules: Sequence[RuleDefinitionV2],
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> list[RuleDefinitionV2]:
        potential: list[tuple[RuleDefinitionV2, bool | None]] = []
        for rule in rules:
            status = cls._known_condition_status(
                definition,
                rule.condition,
                target_key,
                parameters,
                projected_known_facts,
                projected_known_nodes,
                projected_known_relations,
            )
            if status is not False:
                potential.append((rule, status))
        known_true = [item for item in potential if item[1] is True]
        if not known_true:
            return [item[0] for item in potential]
        highest_true_priority = max(item[0].priority for item in known_true)
        highest_known_winners = [
            rule for rule, status in known_true if rule.priority == highest_true_priority
        ]
        possible_unknown_winners = [
            rule
            for rule, status in potential
            if status is None and rule.priority >= highest_true_priority
        ]
        return [*highest_known_winners, *possible_unknown_winners]

    @classmethod
    def _projected_resolution_effects(
        cls,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        target_key: str,
        parameters: dict[str, StrictScalar],
        projected_known_facts: dict[tuple[str, str], _ProjectedFact],
        projected_known_nodes: set[str],
        projected_known_relations: set[str],
    ) -> tuple[EffectV2, ...]:
        """Return effects certain across all possible winning resolution rules."""

        rules = [
            rule
            for rule in definition.rules
            if rule.phase == RulePhase.RESOLVE and rule.action_key == action.key
        ]
        projected_rules = cls._projected_resolution_rules(
            definition,
            rules,
            target_key,
            parameters,
            projected_known_facts,
            projected_known_nodes,
            projected_known_relations,
        )
        if not projected_rules:
            return ()
        if len(projected_rules) == 1:
            return projected_rules[0].effects

        identities_by_rule = [
            {effect.model_dump_json() for effect in rule.effects} for rule in projected_rules
        ]
        common_identities = set.intersection(*identities_by_rule)
        result: list[EffectV2] = []
        seen: set[str] = set()
        for effect in projected_rules[0].effects:
            identity = effect.model_dump_json()
            if identity in common_identities and identity not in seen:
                result.append(effect)
                seen.add(identity)
        return tuple(result)

    @staticmethod
    def _projected_effect_node_key(
        selector: NodeSelectorV2 | None,
        target_key: str,
        parameters: dict[str, StrictScalar],
    ) -> str | None:
        if selector is None:
            return None
        kind = selector.kind
        if kind == NodeSelectorKind.CURRENT_TARGET:
            return target_key
        if kind == NodeSelectorKind.ACTION_SOURCE:
            source = parameters.get("source_key")
            return source if isinstance(source, str) else None
        if kind == NodeSelectorKind.EXPLICIT:
            return selector.node_key if isinstance(selector.node_key, str) else None
        return None

    @staticmethod
    def _projected_value(
        expression: ValueExpressionV2 | None,
        parameters: dict[str, StrictScalar],
    ) -> StrictScalar | None:
        if expression is None:
            return None
        if getattr(getattr(expression, "source", None), "value", None) == ValueSource.LITERAL.value:
            return getattr(expression, "literal", None)
        parameter_key = getattr(expression, "parameter_key", None)
        value = parameters.get(parameter_key) if isinstance(parameter_key, str) else None
        return value

    @staticmethod
    def _apply_projected_passability_effect(
        definition: ScenarioDefinitionV2,
        target_key: str,
        projected_resolution_effects: Sequence[EffectV2],
        projected_known_passability: dict[str, bool],
    ) -> None:
        fact_key = definition.metadata.locality.passability_fact_key
        if fact_key is None:
            return
        values: set[bool] = set()
        for effect in projected_resolution_effects:
            if (
                effect.kind == EffectKind.SET_FACT
                and effect.node is not None
                and effect.node.kind == NodeSelectorKind.CURRENT_TARGET
                and effect.fact_key == fact_key
                and effect.value is not None
                and effect.value.source == ValueSource.LITERAL
                and isinstance(effect.value.literal, bool)
            ):
                values.add(effect.value.literal)
        if len(values) == 1:
            projected_known_passability[target_key] = values.pop()

    @staticmethod
    def _apply_projected_actor_reachability_effect(
        action: ActionDefinitionV2,
        actor_key: str,
        target_key: str,
        projected_resolution_effects: Sequence[EffectV2],
        projected_command_reachability: dict[str, CommandReachability],
    ) -> None:
        for effect in projected_resolution_effects:
            if (
                effect.kind == EffectKind.SET_ACTOR_COMMAND_REACHABILITY
                and effect.command_reachability is not None
            ):
                recipient = effect.actor_key or (
                    target_key if action.target_kind == ActionTargetKind.ACTOR else actor_key
                )
                projected_command_reachability[recipient] = effect.command_reachability

    def _record_provider_plan_call(
        self,
        task: AgentTask,
        *,
        request: PlanRequest,
        proposal_steps: tuple[object, ...],
        proposal_candidate_ids: tuple[str, ...],
        diagnostics: tuple[PlanViolation, ...],
        proposal_stop_reason: str,
        accepted: bool,
        audit_id: str | None = None,
    ) -> None:
        assert self.provider is not None
        metadata = dict(task.objective_resolution_metadata or {})
        calls = list(metadata.get("provider_calls", []))
        provider_metadata = provider_call_metadata(self.provider)
        started_at = self._provider_call_started_at.get(audit_id or "")
        call_record: dict[str, object] = {
            "audit_id": audit_id,
            "call_type": request.call_type,
            "model": self.provider.model_name,
            "repair_attempt": request.repair_attempt,
            "provider_payload": request.provider_payload(),
            "planning_context": (
                request.planning_context.compact_dump()
                if request.planning_context is not None
                else None
            ),
            "planning_context_bytes": (
                len(
                    json.dumps(
                        request.planning_context.compact_dump(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                if request.planning_context is not None
                else None
            ),
            "candidate_catalog": [
                {
                    "candidate_id": item.candidate_id,
                    "action_key": item.action_key,
                    "actor_key": item.actor_key,
                    "target_key": item.target_key,
                    "currently_executable": item.currently_executable,
                    "known_blockers": list(item.known_blockers),
                }
                for item in request.planning_action_catalog
            ],
            "proposal_steps": [
                {
                    "purpose": getattr(item, "purpose", ""),
                    "action_key": getattr(item, "action_key", None),
                    "actor_key": getattr(item, "actor_key", None),
                    "target_key": getattr(item, "target_key", None),
                    "parameters": dict(getattr(item, "parameters", {}) or {}),
                    "order": index,
                }
                for index, item in enumerate(proposal_steps, start=1)
            ],
            "proposal_candidate_ids": list(proposal_candidate_ids),
            "proposal_stop_reason": proposal_stop_reason,
            "validator_violations": [
                violation.model_dump(
                    mode="json", exclude_none=True, exclude_defaults=True
                )
                for violation in diagnostics
            ],
            "validation": "ACCEPTED" if accepted else "REJECTED",
            "outcome": "SUCCESS",
            "finished_at": datetime.now(UTC).isoformat(),
            "wall_clock_latency_ms": (
                provider_metadata.get("wall_clock_latency_ms")
                if isinstance(provider_metadata.get("wall_clock_latency_ms"), int)
                else (_duration_ms(started_at) if started_at is not None else None)
            ),
            **provider_metadata,
        }
        existing_index = next(
            (
                index
                for index, item in enumerate(calls)
                if isinstance(item, dict) and item.get("audit_id") == audit_id
            ),
            None,
        )
        if existing_index is None:
            calls.append(call_record)
        else:
            existing = calls[existing_index]
            calls[existing_index] = {
                **(existing if isinstance(existing, dict) else {}),
                **call_record,
            }
        task.objective_resolution_metadata = {**metadata, "provider_calls": calls}
        self.db.flush()

    def _successful_proposal_signatures(self, task: AgentTask) -> set[str]:
        operations = self.db.scalars(
            select(WorldOperation).where(
                WorldOperation.game_instance_id == self.scope.game_instance_id,
                WorldOperation.task_id == task.id,
                WorldOperation.status == WorldOperationStatus.RESOLVED,
            )
        )
        signatures: set[str] = set()
        for operation in operations:
            outcome = operation.outcome
            if not isinstance(outcome, dict) or outcome.get("failure") is not None:
                continue
            signatures.add(
                proposal_signature(
                    operation.actor_key,
                    operation.action_key,
                    operation.target_key,
                    dict(operation.parameters),
                )
            )
        return signatures

    def _validated_proposed_step(
        self,
        definition: ScenarioDefinitionV2,
        candidate: PlanningActionCandidate,
        parameters: dict[str, StrictScalar],
        objectives: tuple[ObjectiveDefinitionV2, ...],
        plan_version: int,
        index: int,
        reason: str | None,
        *,
        allow_epistemic: bool = False,
    ) -> list[dict[str, object]]:
        action = next(
            (item for item in definition.actions if item.key == candidate.action_key), None
        )
        actor = self.db.get(GameInstanceActor, (self.scope.game_instance_id, candidate.actor_key))
        if action is None or actor is None:
            raise GenericAgentError("GENERIC_PROVIDER_PLAN_INVALID", "Unknown Action or Actor")
        try:
            parameters = normalize_action_parameters(action, parameters)
        except ValueError as exc:
            raise GenericAgentError(
                "GENERIC_PLAN_PARAMETER_INVALID",
                str(exc),
                details={
                    "dimension": "PARAMETER",
                    "actual_parameters": parameters,
                    "validation_error": str(exc),
                },
            ) from exc
        planning_failure_code = self._planning_action_failure_code(
            definition,
            action,
            actor,
            candidate.target_key,
        )
        if planning_failure_code is not None:
            raise GenericAgentError(
                planning_failure_code,
                "The Action target does not satisfy the declared planning contract",
                details=_planning_failure_details(
                    definition,
                    action,
                    actor,
                    candidate.target_key,
                    planning_failure_code,
                ),
            )
        if not self._validate_planning_action(definition, action, actor, candidate.target_key):
            raise GenericAgentError("GENERIC_PROVIDER_PLAN_INVALID", "Action assignment is invalid")
        objective_refs = {
            (requirement.node_key, requirement.fact_key)
            for objective in objectives
            for requirement in (
                *objective.completion_requirements,
                *(item for group in objective.prerequisites for item in group.requirements),
            )
        }
        projected_refs = {
            (item.node_key, item.fact_key)
            for item in (
                *action.planning.terminal_effects,
                *action.planning.supporting_effects,
            )
        }
        if (
            objective_refs.isdisjoint(projected_refs)
            and not action.planning.supporting_effects
            and not allow_epistemic
        ):
            raise GenericAgentError(
                "OBJECTIVE_IRRELEVANT",
                "Action does not advance the frozen scope",
                details={
                    "dimension": "OBJECTIVE_RELEVANCE",
                    "required": "ADVANCES_FROZEN_OBJECTIVE_SCOPE",
                    "actual": "NO_DECLARED_RELEVANT_EFFECT",
                },
            )
        authority = evaluate_authority(actor, action, parameters)
        if authority.outcome == AuthorityOutcome.DENY:
            raise GenericAgentError(authority.reason_code, "Action authority denied")
        arguments = {
            "action_key": action.key,
            "target_key": candidate.target_key,
            "parameters": parameters,
            "idempotency_key": (
                f"task-{'-'.join(item.key for item in objectives)}-plan-{plan_version}-"
                f"{self.scope.game_instance_id}-{index}-{action.key}"
            )[:160],
        }
        steps: list[dict[str, object]] = [
            {
                "description": f"Execute {action.name}",
                "actor_key": actor.actor_key,
                "execution_type": StepExecutionType.TOOL,
                "action_intent": action.key,
                "arguments": arguments,
                "expected_outcome": {"codes": list(action.planning.success_outcome_codes)},
                "resume_condition": None,
            }
        ]
        if action.execution_mode == ActionExecutionMode.ASYNC:
            steps.append(
                {
                    "description": f"Wait for {action.name}",
                    "actor_key": actor.actor_key,
                    "execution_type": StepExecutionType.WAIT_FOR_WORLD_EVENT,
                    "action_intent": action.key,
                    "arguments": {},
                    "expected_outcome": {"codes": list(action.planning.wait_success_outcome_codes)},
                    "resume_condition": {"action_key": action.key},
                }
            )
        return steps

    def _validate_planning_action(
        self,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor: GameInstanceActor,
        target_key: str,
    ) -> bool:
        """Validate static Plan membership without applying current runtime access gates."""

        return self._planning_action_failure_code(definition, action, actor, target_key) is None

    def _planning_action_failure_code(
        self,
        definition: ScenarioDefinitionV2,
        action: ActionDefinitionV2,
        actor: GameInstanceActor,
        target_key: str,
    ) -> str | None:
        """Return a safe static-contract diagnostic, when one is available.

        The validator deliberately keeps the public boolean contract used by
        runtime and test callers.  Provider-facing plan validation needs one
        additional distinction, though: a real target interaction mismatch is
        actionable repair feedback and must not be mislabeled as an objective
        relevance failure.  This helper reports only non-sensitive static
        contract information; dynamic accessibility and hidden Truth stay in
        the existing boolean/knowledge-aware validation paths.
        """

        if action.target_kind == ActionTargetKind.ACTOR:
            target_actor = self.db.get(
                GameInstanceActor,
                (self.scope.game_instance_id, target_key),
            )
            if target_actor is None:
                return "TARGET_INVALID"
            if target_actor.status != "ACTIVE":
                return "TARGET_INVALID"
            target_interaction_valid = True
            target_visible = True
        else:
            target = definition.world.node(target_key)
            node_state = self.db.get(
                GameInstanceNodeState,
                (self.scope.game_instance_id, target_key),
            )
            if target is None:
                return "TARGET_INVALID"
            target_interaction_valid = bool(
                target is not None and action.required_interaction_key in target.interaction_keys
            )
            target_visible = bool(node_state and node_state.visibility == Visibility.KNOWN)
            if not target_visible:
                return "TARGET_NOT_VISIBLE"
            if not target_interaction_valid:
                return "TARGET_INTERACTION_INVALID"
        if actor.status != "ACTIVE":
            return "ACTOR_NOT_AVAILABLE"
        if action.key not in actor.allowed_action_keys:
            return "ACTOR_NOT_ALLOWED"
        if not {item.value for item in action.allowed_actor_capabilities}.issubset(
            set(actor.capabilities)
        ):
            return "ACTOR_CAPABILITY_MISSING"
        if (
            action.required_actor_role_key is not None
            and actor.role_key != action.required_actor_role_key
        ):
            return "ACTOR_ROLE_MISSING"
        if not actor_binding_matches(definition, actor):
            return "ACTOR_BINDING_INVALID"
        return None

    @staticmethod
    def _default_parameters(action: ActionDefinitionV2) -> dict[str, StrictScalar]:
        try:
            return normalize_action_parameters(action, {})
        except ValueError as exc:
            raise GenericAgentError("GENERIC_PLAN_PARAMETER_REQUIRED", str(exc)) from exc

    def _known_requirement_satisfied(self, requirement) -> bool:  # type: ignore[no-untyped-def]
        row = self.db.get(
            GameInstanceFactState,
            (self.scope.game_instance_id, requirement.node_key, requirement.fact_key),
        )
        return bool(
            row
            and row.visibility == Visibility.KNOWN
            and row.truth_value in requirement.accepted_values
        )

    def _known_requirement_public(self, requirement) -> bool:  # type: ignore[no-untyped-def]
        row = self.db.get(
            GameInstanceFactState,
            (self.scope.game_instance_id, requirement.node_key, requirement.fact_key),
        )
        return bool(row and row.visibility == Visibility.KNOWN)

    def _record_action_failure(
        self,
        task: AgentTask,
        step: AgentStep,
        code: str,
        *,
        retryable: bool,
        replan: bool = True,
    ) -> None:
        step.status = AgentStepStatus.FAILED
        step.failure_code = code
        task.last_error_code = code
        if retryable and replan:
            self.plan(task, reason=code)
        elif not retryable:
            task.status = AgentTaskStatus.BLOCKED

    def _definition(self) -> ScenarioDefinitionV2:
        persisted = GameInstanceService(self.db).load(self.scope.game_instance_id)
        self.scope.assert_compatible(persisted)
        definition = (
            ScenarioVersionRepository(self.db).load(self.scope.scenario_version_id).definition
        )
        if not isinstance(definition, ScenarioDefinitionV2):
            raise GenericAgentError(
                "GENERIC_RUNTIME_SCHEMA_REQUIRED", "Generic Agent requires ScenarioDefinition v2"
            )
        return definition

    def _task_scope(self, task: AgentTask) -> None:
        try:
            objective_scope = ObjectiveScope.create(
                task.objective_scope_keys or [], task.objective_catalog_version or ""
            )
        except ObjectiveScopeError as exc:
            raise GenericAgentError("GENERIC_OBJECTIVE_SCOPE_INVALID", str(exc)) from exc
        if (
            task.game_instance_id != self.scope.game_instance_id
            or task.player_id != self.scope.player_id
            or task.objective_catalog_version
            != f"scenario-version:{self.scope.scenario_version_id}"
            or task.objective_frozen_at is None
            or objective_scope.content_hash != task.objective_scope_hash
        ):
            raise GenericAgentError(
                "GENERIC_TASK_SCOPE_INVALID", "Task does not belong to this exact Version scope"
            )

    @staticmethod
    def _objectives(
        task: AgentTask, definition: ScenarioDefinitionV2
    ) -> tuple[ObjectiveDefinitionV2, ...]:
        keys = tuple(task.objective_scope_keys or ())
        catalog = {item.key: item for item in definition.objectives}
        if not keys or any(key not in catalog for key in keys):
            raise GenericAgentError(
                "GENERIC_OBJECTIVE_SCOPE_INVALID", "Objective is absent from exact Version"
            )
        return tuple(catalog[key] for key in keys)

    def _actor(self, actor_key: str | None) -> GameInstanceActor:
        if actor_key is None:
            raise GenericAgentError("GENERIC_ACTOR_REQUIRED", "Task has no Versioned Actor")
        actor = self.db.get(GameInstanceActor, (self.scope.game_instance_id, actor_key))
        if actor is None:
            raise GenericAgentError("GENERIC_ACTOR_REQUIRED", "Task Actor is not in this Instance")
        return actor

    def _active_plan(self, task: AgentTask) -> AgentPlan:
        plan = self.db.scalar(
            select(AgentPlan).where(
                AgentPlan.task_id == task.id,
                AgentPlan.status == AgentPlanStatus.ACTIVE,
            )
        )
        if plan is None:
            raise GenericAgentError("GENERIC_PLAN_REQUIRED", "Task has no active Plan")
        return plan

    @staticmethod
    def _complete_task(task: AgentTask) -> None:
        task.status = AgentTaskStatus.SUCCEEDED
        task.last_error_code = None
        task.completed_at = datetime.now(UTC)


def normalize_objective_keys(
    definition: ScenarioDefinitionV2,
    objective_keys: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    """Remove objectives fully subsumed by another selected Objective.

    This is applied before ObjectiveScope is frozen.  It follows the authored
    generic ``subsumes`` graph and does not alter the immutable definitions or
    an already persisted scope.
    """

    objectives = {objective.key: objective for objective in definition.objectives}
    selected = set(objective_keys)
    removed: set[str] = set()

    def descendants(key: str, visiting: set[str]) -> set[str]:
        if key in visiting:
            return set()
        visiting.add(key)
        objective = objectives.get(key)
        if objective is None:
            return set()
        result: set[str] = set()
        for child in objective.subsumes:
            result.add(child)
            result.update(descendants(child, visiting))
        visiting.remove(key)
        return result

    for parent in selected:
        removed.update(descendants(parent, set()) & selected)
    return tuple(sorted(selected - removed))


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


def _actor_command_reachability(actor: GameInstanceActor) -> CommandReachability:
    try:
        return CommandReachability(actor.command_reachability)
    except ValueError as exc:
        raise GenericAgentError(
            "RUNTIME_ACTOR_REACHABILITY_INVALID",
            "The Actor command reachability value is invalid",
        ) from exc


def _structured_plan_diagnostic(
    exc: GenericAgentError,
    *,
    action: ActionDefinitionV2,
    step_id: str,
    actor_key: str,
    target_key: str,
    projected_command_reachability: dict[str, CommandReachability],
) -> dict[str, object]:
    """Keep repair diagnostics actionable without exposing hidden Truth."""

    diagnostic: dict[str, object] = {
        "code": _safe_provider_diagnostic(exc.code),
        "failure_code": exc.code,
        "step_id": step_id,
        "action_key": action.key,
        "actor_key": actor_key,
        "target_key": target_key,
    }
    typed_fields = (
        "dimension",
        "required",
        "actual",
        "reason_code",
        "required_interaction_key",
        "actual_interactions",
        "transport_key",
        "source_region",
        "target_region",
        "resource_key",
        "scope_region",
        "required_amount",
        "projected_known_available_amount",
        "deficit",
        "parameter_key",
        "parameter_error",
        "validation_error",
        "actual_parameters",
        "blocking_condition",
        "known_predicate",
    )
    diagnostic.update({key: exc.details[key] for key in typed_fields if key in exc.details})
    if (
        diagnostic.get("code") == "PROPOSAL_INVALID"
        and diagnostic.get("dimension") == "ACTION_PRECONDITION"
    ):
        diagnostic["code"] = "ACTION_PRECONDITION_FAILED"
    if "dimension" not in diagnostic:
        blocker = _diagnostic_blocker(exc.code, action)
        if blocker:
            dimension = blocker.get("type", "ACTION_PRECONDITION")
            diagnostic["dimension"] = dimension
            if "required_value" in blocker:
                diagnostic["required"] = blocker["required_value"]
            if dimension == "COMMAND_REACHABILITY":
                reachability = projected_command_reachability.get(actor_key)
                if reachability is not None:
                    diagnostic["actual"] = reachability.value
    return diagnostic


def _planning_failure_details(
    definition: ScenarioDefinitionV2,
    action: ActionDefinitionV2,
    actor: GameInstanceActor,
    target_key: str,
    failure_code: str,
) -> dict[str, object]:
    """Project only public static assignment facts for a planning failure."""

    if failure_code == "TARGET_INTERACTION_INVALID":
        target = definition.world.node(target_key)
        actual_interactions = tuple(target.interaction_keys) if target is not None else ()
        return {
            "dimension": "TARGET_INTERACTION",
            "required": action.required_interaction_key,
            "actual": list(actual_interactions),
            "required_interaction_key": action.required_interaction_key,
            "actual_interactions": actual_interactions,
        }
    if failure_code == "ACTOR_NOT_ALLOWED":
        return {
            "dimension": "ACTOR_ACTION_ELIGIBILITY",
            "required": action.key,
            "actual": list(actor.allowed_action_keys),
        }
    if failure_code == "ACTOR_CAPABILITY_MISSING":
        return {
            "dimension": "ACTOR_CAPABILITY",
            "required": [item.value for item in action.allowed_actor_capabilities],
            "actual": list(actor.capabilities),
        }
    if failure_code == "ACTOR_ROLE_MISSING":
        return {
            "dimension": "ACTOR_ROLE",
            "required": action.required_actor_role_key,
            "actual": actor.role_key,
        }
    if failure_code == "ACTOR_BINDING_INVALID":
        return {
            "dimension": "ACTOR_BINDING",
            "required": "VALID_VERSION_BINDING",
            "actual": "INVALID",
        }
    if failure_code == "ACTOR_NOT_AVAILABLE":
        return {
            "dimension": "ACTOR_AVAILABILITY",
            "required": "ACTIVE",
            "actual": actor.status,
        }
    if failure_code == "TARGET_NOT_VISIBLE":
        return {"dimension": "TARGET_VISIBILITY", "required": "KNOWN", "actual": "HIDDEN"}
    return {"dimension": "TARGET", "required": "VALID_TARGET", "actual": "INVALID"}


def _diagnostic_with_step_id(
    diagnostic: dict[str, object], proposed_steps: tuple[object, ...]
) -> dict[str, object]:
    result = dict(diagnostic)
    raw_step = result.pop("step", None)
    if isinstance(raw_step, int) and 1 <= raw_step <= len(proposed_steps):
        result["step_id"] = str(getattr(proposed_steps[raw_step - 1], "step_id", ""))
    return result


def _diagnostic_blocker(
    failure_code: str,
    action: ActionDefinitionV2,
) -> dict[str, object] | None:
    if failure_code == "ACTOR_COMMAND_DISCONNECTED":
        return {
            "type": "COMMAND_REACHABILITY",
            "current_value": CommandReachability.DISCONNECTED.value,
            "required_value": CommandReachability.ONLINE.value,
        }
    if failure_code in {
        "RESOURCE_INVENTORY_UNKNOWN",
        "TRANSPORT_RESOURCE_KNOWLEDGE_UNKNOWN",
    }:
        return {
            "type": "RESOURCE_KNOWLEDGE",
            "required_value": "KNOWN_VISIBLE_AVAILABLE",
            "unknown_value": "NOT_USABLE",
        }
    if failure_code in {"KNOWN_RESOURCE_INSUFFICIENT", "TRANSPORT_RESOURCE_INSUFFICIENT"}:
        return {
            "type": "RESOURCE_QUANTITY",
            "current_value": "KNOWN_INSUFFICIENT",
            "required_value": "REQUESTED_AMOUNT",
        }
    if failure_code in {"KNOWN_TRANSPORT_BLOCKED", "TRAVEL_BLOCKED", "TRANSPORT_BLOCKED"}:
        return {
            "type": "TRANSPORT_PASSABILITY",
            "current_value": "KNOWN_BLOCKED",
            "required_value": "PASSABLE",
            "unknown_value": "MAY_ATTEMPT",
        }
    if failure_code.startswith("LOCALITY_"):
        return {
            "type": "LOCALITY",
            "contract": action.locality.value,
        }
    if failure_code == "ACTOR_ROLE_MISSING":
        return {
            "type": "ACTOR_ROLE",
            "required_value": action.required_actor_role_key,
        }
    if failure_code in {
        "SUPPLY_POWER_RELATION_UNKNOWN",
        "SUPPLY_POWER_SOURCE_NOT_OPERATIONAL",
        "SUPPLY_POWER_SOURCE_UNAVAILABLE",
    }:
        return {
            "type": "POWER_SOURCE_REQUIREMENT",
            "required_value": "KNOWN_VALID_SOURCE",
        }
    return {"type": "ACTION_PRECONDITION", "failure_code": failure_code}


def _validate_plan_segment_contract(
    segment: PlanProposal,
    planner_input: PlannerInput,
) -> tuple[PlanViolation, ...]:
    """Validate segment termination without planning a recovery path."""

    step_ids = [step.step_id for step in segment.steps]
    if any(not step_id.strip() for step_id in step_ids):
        return (
            PlanViolation(
                code="STEP_ID_INVALID",
                failure_code="STEP_ID_INVALID",
                dimension="STEP_ID",
                required="NON_BLANK",
                actual="BLANK",
            ),
        )
    if len(step_ids) != len(set(step_ids)):
        return (
            PlanViolation(
                code="STEP_ID_DUPLICATE",
                failure_code="STEP_ID_DUPLICATE",
                dimension="STEP_ID",
                required="UNIQUE",
                actual="DUPLICATE",
                step_ids=tuple(step_ids),
            ),
        )
    if segment.stop_reason == "OBJECTIVE_COMPLETION":
        if segment.boundary_dependency_id is not None:
            return (
                PlanViolation(
                    code="BOUNDARY_DEPENDENCY_NOT_ALLOWED",
                    failure_code="BOUNDARY_DEPENDENCY_NOT_ALLOWED",
                    dimension="SEGMENT_TERMINATION",
                    required="NO_BOUNDARY_DEPENDENCY",
                    actual=segment.boundary_dependency_id,
                    dependency_id=segment.boundary_dependency_id,
                ),
            )
        boundary_violation = _objective_completion_boundary_violation(segment, planner_input)
        if boundary_violation is not None:
            return (boundary_violation,)
        if not segment.steps:
            return (
                PlanViolation(
                    code="NO_STEPS",
                    failure_code="NO_STEPS",
                    dimension="SEGMENT_STEPS",
                    required="AT_LEAST_ONE_STEP",
                    actual=0,
                ),
            )
        return ()
    if segment.stop_reason == "BLOCKED":
        if segment.boundary_dependency_id is not None:
            return (
                PlanViolation(
                    code="BOUNDARY_DEPENDENCY_NOT_ALLOWED",
                    failure_code="BOUNDARY_DEPENDENCY_NOT_ALLOWED",
                    dimension="SEGMENT_TERMINATION",
                    required="NO_BOUNDARY_DEPENDENCY",
                    actual=segment.boundary_dependency_id,
                    dependency_id=segment.boundary_dependency_id,
                ),
            )
        if segment.steps:
            return (
                PlanViolation(
                    code="BLOCKED_SEGMENT_HAS_STEPS",
                    failure_code="BLOCKED_SEGMENT_HAS_STEPS",
                    dimension="SEGMENT_STEPS",
                    required="EMPTY",
                    actual=len(segment.steps),
                    step_ids=tuple(step_ids),
                ),
            )
        if _has_direct_known_progress_option(planner_input):
            return (
                PlanViolation(
                    code="BLOCKED_SEGMENT_HAS_PROGRESS_OPTIONS",
                    failure_code="BLOCKED_SEGMENT_HAS_PROGRESS_OPTIONS",
                    dimension="SEGMENT_TERMINATION",
                    required="NO_DIRECT_KNOWN_LEGAL_PROGRESS_OPTION",
                    actual="DIRECT_KNOWN_LEGAL_PROGRESS_OPTION_EXISTS",
                ),
            )
        return ()

    dependency_id = segment.boundary_dependency_id
    if not isinstance(dependency_id, str) or not dependency_id.strip():
        return (
            PlanViolation(
                code="INFORMATION_BOUNDARY_DEPENDENCY_MISSING",
                failure_code="INFORMATION_BOUNDARY_DEPENDENCY_MISSING",
                dimension="INFORMATION_BOUNDARY",
                required="REGISTERED_UNKNOWN_DEPENDENCY_ID",
                actual="MISSING",
            ),
        )
    matching = next(
        (
            item
            for item in planner_input.known_world.unknown_dependencies
            if item.get("dependency_id") == dependency_id
        ),
        None,
    )
    if matching is None or matching.get("status") != "UNKNOWN" or not matching.get("blocks"):
        return (
            PlanViolation(
                code="INFORMATION_BOUNDARY_NOT_RELEVANT",
                failure_code="INFORMATION_BOUNDARY_NOT_RELEVANT",
                dimension="INFORMATION_BOUNDARY",
                required="ACTIVE_UNKNOWN_BLOCKING_DEPENDENCY",
                actual=dependency_id,
                dependency_id=dependency_id,
            ),
        )
    if not segment.steps:
        return (
            PlanViolation(
                code="NO_STEPS",
                failure_code="NO_STEPS",
                dimension="SEGMENT_STEPS",
                required="AT_LEAST_ONE_STEP",
                actual=0,
            ),
        )
    resolvers = matching.get("resolvable_by_effect_types")
    resolver_types = (
        {str(item) for item in resolvers}
        if isinstance(resolvers, list) and all(isinstance(item, str) for item in resolvers)
        else set()
    )
    action_contracts = {item.action_key: item for item in planner_input.action_contracts}
    target_bindings = {
        (item.action_key, item.target_key): item for item in planner_input.target_bindings
    }
    acquisition_indices: list[int] = []
    for index, step in enumerate(segment.steps):
        contract = action_contracts.get(str(step.action_key))
        binding = target_bindings.get((str(step.action_key), str(step.target_key)))
        effects = (
            *(contract.deterministic_effects if contract is not None else ()),
            *(binding.deterministic_effects if binding is not None else ()),
        )
        if _submitted_step_matches_dependency(
            step,
            matching,
            resolver_types,
            effects,
            planner_input=planner_input,
        ):
            acquisition_indices.append(index)
    if not resolver_types or not acquisition_indices:
        return (
            PlanViolation(
                code="INFORMATION_BOUNDARY_ACQUISITION_MISSING",
                failure_code="INFORMATION_BOUNDARY_ACQUISITION_MISSING",
                dimension="INFORMATION_BOUNDARY_ACQUISITION",
                required="MATCHING_SUBMITTED_KNOWLEDGE_ACQUISITION",
                actual="NO_MATCHING_SUBMITTED_STEP",
                dependency_id=dependency_id,
                required_effect_types=tuple(sorted(resolver_types)),
            ),
        )
    if acquisition_indices[-1] != len(segment.steps) - 1:
        return (
            PlanViolation(
                code="INFORMATION_BOUNDARY_ACQUISITION_NOT_LAST",
                failure_code="INFORMATION_BOUNDARY_ACQUISITION_NOT_LAST",
                dimension="INFORMATION_BOUNDARY_ACQUISITION",
                required="MATCHING_KNOWLEDGE_ACQUISITION_MUST_BE_LAST_STEP",
                actual={
                    "matching_step_indices": acquisition_indices,
                    "segment_step_count": len(segment.steps),
                },
                dependency_id=dependency_id,
                required_effect_types=tuple(sorted(resolver_types)),
            ),
        )
    return ()


def _objective_completion_boundary_violation(
    segment: PlanProposal,
    planner_input: PlannerInput,
) -> PlanViolation | None:
    """Prevent an acquisition step from disguising an information boundary.

    The Validator does not choose an acquisition.  It only observes whether a
    submitted step publicly resolves an active blocking UNKNOWN dependency;
    if so, the segment must stop at that step and name the dependency.
    """

    contracts = {item.action_key: item for item in planner_input.action_contracts}
    bindings = {
        (item.action_key, item.target_key): item for item in planner_input.target_bindings
    }
    for dependency in planner_input.known_world.unknown_dependencies:
        dependency_id = dependency.get("dependency_id")
        if (
            not isinstance(dependency_id, str)
            or dependency.get("status") != "UNKNOWN"
            or not dependency.get("blocks")
            or dependency.get("attempt_policy") == "MAY_ATTEMPT"
        ):
            continue
        raw_types = dependency.get("resolvable_by_effect_types")
        resolver_types = (
            {str(item) for item in raw_types}
            if isinstance(raw_types, list) and all(isinstance(item, str) for item in raw_types)
            else set()
        )
        if not resolver_types:
            continue
        matches: list[int] = []
        for index, step in enumerate(segment.steps):
            contract = contracts.get(str(step.action_key))
            binding = bindings.get((str(step.action_key), str(step.target_key)))
            effects = (
                *(contract.deterministic_effects if contract is not None else ()),
                *(binding.deterministic_effects if binding is not None else ()),
            )
            if _submitted_step_matches_dependency(
                step,
                dependency,
                resolver_types,
                effects,
                planner_input=planner_input,
            ):
                matches.append(index)
        if matches:
            return PlanViolation(
                code="INFORMATION_BOUNDARY_REQUIRED",
                failure_code="INFORMATION_BOUNDARY_REQUIRED",
                dimension="INFORMATION_BOUNDARY",
                required="INFORMATION_BOUNDARY_WITH_ACQUISITION_LAST",
                actual={
                    "stop_reason": segment.stop_reason,
                    "matching_step_indices": matches,
                },
                dependency_id=dependency_id,
                required_effect_types=tuple(sorted(resolver_types)),
            )
    return None


def _has_direct_known_progress_option(planner_input: PlannerInput) -> bool:
    """Prove one immediately legal option without planning or suggesting one."""

    nodes = [
        item
        for item in planner_input.known_world.nodes
        if item.get("access") in {"AVAILABLE", "ENTERED"}
    ]
    resource_knowledge = {
        item.get("region_key"): item for item in planner_input.known_world.resource_knowledge
    }
    bindings = {
        (item.action_key, item.target_key): item for item in planner_input.target_bindings
    }
    for contract in planner_input.action_contracts:
        if not _direct_known_parameters(contract) or not _direct_known_preconditions(contract):
            continue
        requirements = contract.executor_requirements
        required_role = requirements.get("required_role_key")
        required_capabilities = requirements.get("required_capabilities", [])
        if not isinstance(required_capabilities, list):
            continue
        for actor in planner_input.actors:
            if (
                actor.availability != "ACTIVE"
                or contract.action_key not in actor.allowed_action_keys
                or not set(required_capabilities).issubset(actor.capabilities)
                or (isinstance(required_role, str) and actor.role_key != required_role)
                or (
                    requirements.get("command_reachability") == "ONLINE"
                    and actor.command_reachability != "ONLINE"
                )
            ):
                continue
            if _actor_has_direct_known_target(
                actor,
                contract,
                planner_input,
                nodes,
                bindings,
                resource_knowledge,
            ):
                return True
    return False


def _actor_has_direct_known_target(
    actor: PlannerActorState,
    contract: PlannerActionContract,
    planner_input: PlannerInput,
    nodes: list[dict[str, object]],
    bindings: dict[tuple[str, str], PlannerTargetBinding],
    resource_knowledge: dict[object, dict[str, object]],
) -> bool:
    """Check direct target legality only; never search a recovery sequence."""

    target_kind = contract.target_contract.get("kind")
    locality = contract.locality.get("type")
    if target_kind == "ACTOR":
        required_reachability = contract.target_contract.get("command_reachability")
        return any(
            target.actor_key != actor.actor_key
            and target.availability == "ACTIVE"
            and (
                not isinstance(required_reachability, str)
                or target.command_reachability == required_reachability
            )
            and (
                locality != "ACTOR_SAME_REGION"
                or (
                    actor.current_region is not None
                    and target.current_region == actor.current_region
                )
            )
            for target in planner_input.actors
        )
    required_interaction = contract.target_contract.get("required_interaction_key")
    for node in nodes:
        target_key = node.get("key")
        interactions = node.get("interactions")
        if not isinstance(target_key, str) or not isinstance(interactions, list):
            continue
        if isinstance(required_interaction, str) and required_interaction not in interactions:
            continue
        binding = bindings.get((contract.action_key, target_key))
        if binding is not None and not _direct_known_binding_requirements(
            binding,
            planner_input,
            actor.current_region,
        ):
            continue
        if not _direct_known_locality(
            actor,
            contract,
            target_key,
            planner_input,
        ):
            continue
        if any(
            effect.get("type") == "RESOURCE_SURVEY_COMPLETED"
            for effect in contract.deterministic_effects
        ) and resource_knowledge.get(target_key, {}).get("resource_survey_completed") is not False:
            continue
        if not _direct_known_resource_option(
            actor,
            contract,
            binding,
            planner_input,
        ):
            continue
        return True
    return False


def _direct_known_locality(
    actor: PlannerActorState,
    contract: PlannerActionContract,
    target_key: str,
    planner_input: PlannerInput,
) -> bool:
    """Prove one-step locality from the public V2 graph only.

    This intentionally checks existence, not a route or a choice.  Relation
    rows are already part of the canonical Known-world slice, so the helper
    never consults Scenario Truth or constructs a multi-step recovery plan.
    """

    actor_region = actor.current_region
    locality = contract.locality.get("type")
    if not isinstance(actor_region, str):
        return False
    if contract.target_contract.get("kind") == "ACTOR":
        actor_target = next(
            (item for item in planner_input.actors if item.actor_key == target_key),
            None,
        )
        return actor_target is not None and (
            locality not in {"ACTOR_SAME_REGION", "ACTOR_REGION"}
            or actor_target.current_region == actor_region
        )

    nodes = {str(item.get("key")): item for item in planner_input.known_world.nodes}
    target_node = nodes.get(target_key)
    if target_node is None:
        return False
    if locality in {None, "NONE"}:
        return True
    locality_metadata = contract.locality
    region_type = locality_metadata.get("region_node_type_key", "region")
    facility_type = locality_metadata.get("facility_node_type_key", "facility")
    transport_type = locality_metadata.get("transport_node_type_key", "transport")
    located_in_type = locality_metadata.get("located_in_relation_type_key")
    endpoint_type = locality_metadata.get("transport_endpoint_relation_type_key")
    target_type = target_node.get("type")
    if locality in {"REGION", "ACTOR_SAME_REGION"}:
        return target_type == region_type and target_key == actor_region
    if locality in {"FACILITY_REGION", "LOCAL_TARGET", "LOCAL_TARGET_FACILITY_OR_TRANSPORT"}:
        if target_type == facility_type:
            return any(
                relation.get("source_node_key") == target_key
                and relation.get("target_node_key") == actor_region
                and (
                    not isinstance(located_in_type, str)
                    or relation.get("relation_type_key") == located_in_type
                )
                for relation in planner_input.known_world.relations
            )
        if (
            locality in {"LOCAL_TARGET", "LOCAL_TARGET_FACILITY_OR_TRANSPORT"}
            and target_type == transport_type
        ):
            return any(
                relation.get("source_node_key") == target_key
                and relation.get("target_node_key") == actor_region
                and (
                    not isinstance(endpoint_type, str)
                    or relation.get("relation_type_key") == endpoint_type
                )
                for relation in planner_input.known_world.relations
            )
        return False
    if locality == "TRANSPORT_ENDPOINT":
        return any(
            relation.get("source_node_key") == target_key
            and relation.get("target_node_key") == actor_region
            and target_type == transport_type
            and (
                not isinstance(endpoint_type, str)
                or relation.get("relation_type_key") == endpoint_type
            )
            for relation in planner_input.known_world.relations
        )
    if locality == "ONE_HOP_TRANSPORT":
        if target_type != region_type or target_key == actor_region:
            return False
        for transport_key in {
            str(item.get("key"))
            for item in planner_input.known_world.nodes
            if item.get("type") == transport_type and isinstance(item.get("key"), str)
        }:
            endpoints = {
                str(relation.get("target_node_key"))
                for relation in planner_input.known_world.relations
                if relation.get("source_node_key") == transport_key
                and (
                    not isinstance(endpoint_type, str)
                    or relation.get("relation_type_key") == endpoint_type
                )
            }
            if {actor_region, target_key}.issubset(endpoints):
                return _known_transport_route_is_not_blocked(
                    planner_input,
                    transport_key,
                    locality_metadata,
                )
        return False
    return False


def _direct_known_parameters(contract: PlannerActionContract) -> bool:
    for parameter in contract.parameters:
        if parameter.get("required") is not True or "default" in parameter:
            continue
        allowed_values = parameter.get("allowed_values")
        if isinstance(allowed_values, list) and allowed_values:
            continue
        if parameter.get("value_type") in {"BOOLEAN", "INTEGER"}:
            # A numeric/boolean required parameter without a bounded domain is
            # not an immediately provable choice.
            return False
        return False
    return True


def _direct_known_preconditions(contract: PlannerActionContract) -> bool:
    """Accept only preconditions that are publicly proven non-blocking."""

    for precondition in contract.known_preconditions:
        current = precondition.get("current_value")
        failure = precondition.get("failure_condition")
        if current is None or not isinstance(failure, dict):
            return False
        kind = failure.get("kind")
        expected = failure.get("value")
        if kind not in {"FACT_EQUALS", "FACT_NOT_EQUALS", "FACT_IN"}:
            return False
        blocked = (
            kind == "FACT_EQUALS" and current == expected
        ) or (
            kind == "FACT_NOT_EQUALS" and current != expected
        ) or (
            kind == "FACT_IN"
            and isinstance(failure.get("values"), list)
            and current in failure["values"]
        )
        if blocked:
            return False
    return True


def _known_transport_route_is_not_blocked(
    planner_input: PlannerInput,
    transport_key: str,
    locality: dict[str, object],
) -> bool:
    passability_key = locality.get("passability_fact_key")
    if not isinstance(passability_key, str):
        return True
    identity = f"{transport_key}.{passability_key}"
    value = planner_input.known_world.facts.get(identity)
    return value is not False


def _direct_known_resource_option(
    actor: PlannerActorState,
    contract: PlannerActionContract,
    binding: PlannerTargetBinding | None,
    planner_input: PlannerInput,
) -> bool:
    """Reject a direct proof when a negative Resource effect is unknown/short."""

    effects = (*contract.deterministic_effects, *(binding.deterministic_effects if binding else ()))
    requirements: list[dict[str, object]] = []
    if binding is not None:
        requirements.extend(item for item in binding.requirements if isinstance(item, dict))
    for requirement in requirements:
        cost = requirement.get("cost")
        if isinstance(cost, dict):
            for resource_key, amount in cost.items():
                if not isinstance(resource_key, str) or not isinstance(amount, int) or amount <= 0:
                    continue
                if not _known_resource_sufficient(
                    planner_input.known_world.resources.get(resource_key),
                    actor.current_region,
                    amount,
                ):
                    return False
    for effect in effects:
        if effect.get("type") not in {"RESOURCE_DELTA", "RESOURCE_CONSUMPTION"}:
            continue
        resource_key = effect.get("resource_key")
        amount = effect.get("amount")
        if not isinstance(resource_key, str):
            continue
        if isinstance(amount, bool) or not isinstance(amount, int):
            return False
        if amount >= 0:
            continue
        scope = effect.get("scope")
        scope_region = actor.current_region if scope in {None, "ACTOR_CURRENT_REGION"} else None
        if not _known_resource_sufficient(
            planner_input.known_world.resources.get(resource_key),
            scope_region,
            -amount,
        ):
            return False
    return True


def _direct_known_binding_requirements(
    binding: PlannerTargetBinding,
    planner_input: PlannerInput,
    actor_region: str | None,
) -> bool:
    """Prove sparse target requirements from the public Known projection.

    A target binding may carry a known resource cost and/or known Fact
    predicates.  This helper only evaluates those already-projected values;
    it does not infer hidden Truth or construct a recovery sequence.
    """

    for requirement in binding.requirements:
        if not isinstance(requirement, dict):
            return False
        cost = requirement.get("cost")
        if isinstance(cost, dict):
            for resource_key, amount in cost.items():
                if (
                    not isinstance(resource_key, str)
                    or isinstance(amount, bool)
                    or not isinstance(amount, int)
                    or amount <= 0
                    or not _known_resource_sufficient(
                        planner_input.known_world.resources.get(resource_key),
                        actor_region,
                        amount,
                    )
                ):
                    return False
        special_requirements = requirement.get("special_requirements", [])
        if not isinstance(special_requirements, list):
            return False
        for special in special_requirements:
            if not isinstance(special, dict):
                return False
            node_key = special.get("node_key")
            fact_key = special.get("fact_key")
            operator = special.get("operator")
            expected = special.get("value")
            if not isinstance(node_key, str) or not isinstance(fact_key, str):
                return False
            actual = planner_input.known_world.facts.get(f"{node_key}.{fact_key}")
            if actual is None or not _direct_known_predicate_holds(actual, operator, expected):
                return False
    return True


def _direct_known_predicate_holds(actual: object, operator: object, expected: object) -> bool:
    if not isinstance(operator, str):
        return False
    try:
        if operator == "EQ":
            return actual == expected
        if operator == "NE":
            return actual != expected
        if operator == "IN":
            return isinstance(expected, list) and actual in expected
        if operator == "NOT_IN":
            return isinstance(expected, list) and actual not in expected
        if operator in {"LT", "LTE", "GT", "GTE"}:
            if not isinstance(actual, (bool, int, str)) or not isinstance(
                expected, (bool, int, str)
            ):
                return False
            actual_value = cast(Any, actual)
            expected_value = cast(Any, expected)
            if operator == "LT":
                return bool(actual_value < expected_value)
            if operator == "LTE":
                return bool(actual_value <= expected_value)
            if operator == "GT":
                return bool(actual_value > expected_value)
            return bool(actual_value >= expected_value)
        if operator.startswith("NOT_"):
            return not _direct_known_predicate_holds(actual, operator[4:], expected)
    except TypeError:
        return False
    return False


def _known_resource_sufficient(raw: object, region_key: str | None, amount: int) -> bool:
    if not isinstance(raw, dict):
        return False
    scopes = raw.get("scopes")
    if isinstance(scopes, dict) and region_key is not None:
        value = scopes.get(region_key)
        return (
            isinstance(value, dict)
            and isinstance(value.get("known_available"), int)
            and value["known_available"] >= amount
        )
    known_available = raw.get("known_available")
    return isinstance(known_available, int) and known_available >= amount


def _submitted_step_matches_dependency(
    step: object,
    dependency: dict[str, object],
    resolver_types: set[str],
    effects: Sequence[dict[str, object]],
    *,
    planner_input: PlannerInput | None = None,
) -> bool:
    """Judge the submitted acquisition binding; never choose one for the Planner."""

    target_key = getattr(step, "target_key", None)
    dimension = dependency.get("dimension")
    for effect in effects:
        effect_type = effect.get("type")
        if effect_type not in resolver_types:
            continue
        if dimension == "RESOURCE_SOURCE":
            if not isinstance(target_key, str):
                continue
            known_nodes = planner_input.known_world.nodes if planner_input else ()
            public_region = next(
                (item for item in known_nodes if item.get("key") == target_key),
                None,
            )
            if planner_input is not None and known_nodes and public_region is None:
                continue
            if (
                planner_input is not None
                and public_region is not None
                and str(public_region.get("type", "")).casefold() != "region"
            ):
                continue
            if planner_input is not None and not known_nodes:
                scope_region = dependency.get("scope_region")
                if isinstance(scope_region, str) and target_key != scope_region:
                    continue
            knowledge = next(
                (
                    item
                    for item in (
                        planner_input.known_world.resource_knowledge
                        if planner_input
                        else ()
                    )
                    if item.get("region_key") == target_key
                ),
                None,
            )
            if knowledge is not None and knowledge.get("resource_survey_completed") is True:
                continue
            if effect.get("target") == "target_region":
                return True
            if effect.get("region_key") == target_key:
                return True
            continue
        subject_key = dependency.get("subject_key")
        if isinstance(subject_key, str) and target_key == subject_key:
            return True
    return False


_ANTI_REGRESSION_LOCATION_FIELDS: set[str] = {
    "step_id",
    "sequence",
    "message",
    "cascade_from_step_id",
    "step_ids",
}
_ANTI_REGRESSION_OCCURRENCE_FIELDS: set[str] = {
    "first_seen_attempt",
    "last_seen_attempt",
    "seen_count",
}


def _anti_regression_evidence(violation: PlanViolation) -> dict[str, object]:
    return violation.model_dump(
        mode="json",
        exclude_none=True,
        exclude_defaults=True,
        exclude=_ANTI_REGRESSION_LOCATION_FIELDS,
    )


def _anti_regression_fingerprint(evidence: dict[str, object]) -> str:
    return json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _remember_prior_contradictions(
    memory: tuple[AntiRegressionMemoryItem, ...],
    violations: tuple[PlanViolation, ...],
    *,
    seen_attempt: int,
) -> tuple[AntiRegressionMemoryItem, ...]:
    result = list(memory)
    indexes = {
        _anti_regression_fingerprint(
            item.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
                exclude=(
                    _ANTI_REGRESSION_LOCATION_FIELDS
                    | _ANTI_REGRESSION_OCCURRENCE_FIELDS
                ),
            )
        ): index
        for index, item in enumerate(result)
    }
    seen_in_proposal: set[str] = set()
    for violation in violations:
        evidence = _anti_regression_evidence(violation)
        fingerprint = _anti_regression_fingerprint(evidence)
        if fingerprint in seen_in_proposal:
            continue
        seen_in_proposal.add(fingerprint)
        existing_index = indexes.get(fingerprint)
        if existing_index is None:
            indexes[fingerprint] = len(result)
            result.append(
                AntiRegressionMemoryItem.model_validate(
                    {
                        **evidence,
                        "first_seen_attempt": seen_attempt,
                        "last_seen_attempt": seen_attempt,
                        "seen_count": 1,
                    }
                )
            )
            continue
        existing = result[existing_index]
        result[existing_index] = existing.model_copy(
            update={
                "last_seen_attempt": seen_attempt,
                "seen_count": existing.seen_count + 1,
            }
        )
    return tuple(result)


def _safe_provider_diagnostic(code: str) -> str:
    if code in {
        "KNOWN_TRANSPORT_BLOCKED",
        "OBJECTIVE_IRRELEVANT",
        "TARGET_INTERACTION_INVALID",
        "TARGET_INVALID",
        "TARGET_NOT_VISIBLE",
        "ACTOR_NOT_ALLOWED",
        "ACTOR_CAPABILITY_MISSING",
        "ACTOR_ROLE_MISSING",
        "ACTOR_BINDING_INVALID",
        "ACTOR_NOT_AVAILABLE",
        "RESOURCE_SURVEY_ALREADY_COMPLETED",
        "RESOURCE_INVENTORY_UNKNOWN",
        "KNOWN_RESOURCE_INSUFFICIENT",
    }:
        return code
    if code.startswith("LOCALITY_"):
        return "LOCALITY_INVALID"
    return {
        "ACTION_PARAMETERS_INVALID": "PARAMETER_INVALID",
        "GENERIC_PLAN_PARAMETER_INVALID": "PARAMETER_INVALID",
        "TRANSPORT_PARAMETERS_INVALID": "PARAMETER_INVALID",
        "RESOURCE_AMOUNT_INVALID": "PARAMETER_INVALID",
        "AUTHORITY_PARAMETER_INVALID": "PARAMETER_INVALID",
        "ACTION_APPROVAL_REQUIRED": "AUTHORITY_REQUIRED",
        "ACTION_NOT_ALLOWED": "ACTOR_NOT_ALLOWED",
        "ACTOR_COMMAND_DISCONNECTED": "ACTOR_COMMAND_DISCONNECTED",
        "RELAY_TARGET_NOT_DISCONNECTED": "RELAY_TARGET_NOT_DISCONNECTED",
        "RELAY_TARGET_INVALID": "TARGET_INVALID",
        "SUPPLY_POWER_RELATION_UNKNOWN": "SUPPLY_POWER_REQUIREMENT_UNKNOWN",
        "SUPPLY_POWER_SOURCE_NOT_OPERATIONAL": "SUPPLY_POWER_SOURCE_INVALID",
        "SUPPLY_POWER_SOURCE_UNAVAILABLE": "SUPPLY_POWER_SOURCE_INVALID",
        "GENERIC_PROVIDER_PLAN_INVALID": "PROPOSAL_INVALID",
    }.get(code, "PROPOSAL_INVALID")


def _provider_error_category(error: GenericProviderError) -> str:
    cause = error.__cause__
    return type(cause).__name__ if cause is not None else type(error).__name__


def _duration_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def proposal_signature(
    actor_key: str,
    action_key: str,
    target_key: str,
    parameters: dict[str, StrictScalar],
) -> str:
    payload = json.dumps(
        [actor_key, action_key, target_key, parameters],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "PLAN_INVALIDATED_BY_NEW_KNOWLEDGE",
    "GenericAgentError",
    "GenericAgentService",
    "GenericGoalResolution",
    "GenericGoalResolver",
    "GenericObjectiveEvaluation",
    "PlanRevalidationResult",
    "normalize_objective_keys",
    "proposal_signature",
]
