"""Generic exact-Version goal resolution, planning, validation and execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import actor_binding_matches, evaluate_authority
from app.agent.objective_scope import ObjectiveScope, ObjectiveScopeError
from app.agent.planning_context import (
    PlanningActionCatalogBuilder,
    PlanningContextBuilder,
    objective_context,
)
from app.agent.provider import (
    GenericModelProvider,
    GoalSelectionRequest,
    PlanningActionCandidate,
    PlanningContext,
    PlanRequest,
    provider_call_metadata,
)
from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    AuthorityOutcome,
    DecisionStatus,
    NodeStatus,
    StepExecutionType,
    WorldOperationStatus,
)
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import (
    ActionDefinitionV2,
    ActionExecutionMode,
    ObjectiveDefinitionV2,
    ScenarioDefinitionV2,
    StrictScalar,
    normalize_action_parameters,
)
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentPlan,
    AgentStep,
    AgentTask,
    ConversationSession,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
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

ObjectiveSelector = Callable[[str, tuple[ObjectiveDefinitionV2, ...]], str | None]

_NON_TERMINAL_TASK_STATUSES = (
    AgentTaskStatus.ACTIVE,
    AgentTaskStatus.REQUIRES_PLAYER_DECISION,
    AgentTaskStatus.WAITING_FOR_PLAYER_ACTION,
    AgentTaskStatus.WAITING_FOR_WORLD_EVENT,
)


class GenericAgentError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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
    MAX_PROVIDER_REPAIR_ATTEMPTS = 2

    def __init__(
        self,
        db: Session,
        scope: RuntimeScope,
        *,
        goal_resolver: GenericGoalResolver | None = None,
        provider: GenericModelProvider | None = None,
    ) -> None:
        self.db = db
        self.scope = scope
        self.provider = provider
        self.goal_resolver = goal_resolver or GenericGoalResolver(provider=provider)
        self._last_provider_plan_summary: str | None = None

    def create_task(
        self,
        session: ConversationSession,
        goal: str,
        *,
        resolved_goal: GenericGoalResolution | None = None,
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
        actor = self._actor(task.owner_actor_key)
        old_plan = self.db.scalar(
            select(AgentPlan).where(
                AgentPlan.task_id == task.id,
                AgentPlan.status == AgentPlanStatus.ACTIVE,
            )
        )
        if old_plan is not None:
            old_plan.status = AgentPlanStatus.SUPERSEDED
        next_version = task.current_plan_version + 1
        steps = self._candidate_steps(
            definition,
            objectives,
            task=task,
            reason=reason,
            plan_version=next_version,
        )
        if self.provider is not None:
            steps = self._provider_steps(task, definition, objectives, reason, next_version)
        if not steps and not self.evaluate(task).completed:
            raise GenericAgentError(
                "GENERIC_PLAN_NOT_FOUND",
                "No exact-Version Action can advance the frozen Objective from current Knowledge",
            )
        plan = AgentPlan(
            task_id=task.id,
            version=next_version,
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
        )
        self.db.add(plan)
        self.db.flush()
        for sequence, candidate in enumerate(steps, start=1):
            self.db.add(
                AgentStep(
                    plan_id=plan.id,
                    sequence=sequence,
                    description=candidate["description"],
                    execution_type=candidate["execution_type"],
                    assigned_actor_key=str(candidate["actor_key"]),
                    action_intent=candidate["action_intent"],
                    constraints={"scenario_version_id": str(self.scope.scenario_version_id)},
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
        if reason is not None:
            task.replan_count += 1
        self.db.flush()
        return plan

    def execute_next(self, task: AgentTask) -> AgentStep | None:
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
            self.plan(task, reason="PLAN_EXHAUSTED")
            return self.execute_next(task)
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
                self._record_action_failure(task, step, exc.code, retryable=exc.retryable)
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
                )
                self.db.flush()
                return step
            if result.operation.status == WorldOperationStatus.PENDING:
                step.status = AgentStepStatus.SUCCEEDED
                step.completed_at = datetime.now(UTC)
            else:
                step.status = AgentStepStatus.SUCCEEDED
                step.completed_at = datetime.now(UTC)
        if self.evaluate(task).completed:
            self._complete_task(task)
        self.db.flush()
        return step

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
    ) -> list[dict[str, object]]:
        assert self.provider is not None
        context_builder = PlanningContextBuilder(self.db, self.scope)
        planning_context = context_builder.build(
            definition,
            objectives,
            task=task,
            replan_reason=reason,
        )
        # The old catalog is retained only as a compatibility projection for
        # existing in-process FakeProviders.  It is never serialized by the
        # OpenAI-compatible provider when ``planning_context`` is present.
        catalog_builder = PlanningActionCatalogBuilder(self.db, self.scope)
        catalog = catalog_builder.build(
            definition,
            objectives,
            task=task,
            replan_reason=reason,
        )
        if not planning_context.relevant_actions and not self.evaluate(task).completed:
            raise GenericAgentError(
                "GENERIC_PLAN_NOT_FOUND",
                "No known public Action can advance the frozen ObjectiveScope",
            )
        diagnostics: tuple[dict[str, object], ...] = ()
        call_type = "INITIAL_PLAN" if reason is None else "REPLAN"
        for repair_attempt in range(self.MAX_PROVIDER_REPAIR_ATTEMPTS + 1):
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
                repair_attempt=repair_attempt,
                repair_diagnostics=diagnostics,
            )
            proposal = self.provider.propose_plan(request)
            steps, diagnostics = self._validate_provider_proposal_v1(
                task,
                definition,
                objectives,
                reason,
                plan_version,
                catalog,
                proposal.steps,
                planning_context,
            )
            self._record_provider_plan_call(
                task,
                request=request,
                proposal_steps=proposal.steps,
                proposal_candidate_ids=tuple(
                    item.candidate_id or "" for item in proposal.steps if item.candidate_id
                ),
                diagnostics=diagnostics,
                accepted=not diagnostics,
            )
            if not diagnostics:
                self._last_provider_plan_summary = proposal.plan_summary.strip() or None
                return steps
        raise GenericAgentError(
            "MODEL_PLAN_REJECTED",
            "The model provider could not produce a backend-valid current Plan",
        )

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
    ) -> tuple[list[dict[str, object]], tuple[dict[str, object], ...]]:
        """Validate direct V1 bindings while accepting legacy candidate IDs.

        Only hard constraints are enforced here.  Current access, resources,
        Rule preflight, and dynamic approval remain execution-time concerns in
        the existing Generic Action service.  A future locked target is thus a
        valid Plan member when its static visibility/interaction contract is
        valid.
        """

        candidates = {item.candidate_id: item for item in catalog}
        actions = {item.key: item for item in definition.actions}
        actors = {
            item.actor_key: item
            for item in self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == self.scope.game_instance_id
                )
            )
        }
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
            )
        }
        diagnostics: list[dict[str, object]] = []
        result: list[dict[str, object]] = []
        step_effects: list[set[tuple[str, str]]] = []
        successful_signatures = self._successful_proposal_signatures(task)
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
        if not proposed_steps:
            return [], ({"code": "NO_STEPS", "message": "Proposal contains no steps"},)

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
                        "step": index,
                        "candidate_id": candidate_id,
                    }
                )
                continue
            if candidate is not None:
                action_key = action_key or candidate.action_key
                actor_key = actor_key or candidate.actor_key
                target_key = target_key or candidate.target_key
            if not isinstance(action_key, str) or not action_key:
                diagnostics.append({"code": "UNKNOWN_ACTION", "step": index})
                continue
            if not isinstance(actor_key, str) or not actor_key:
                diagnostics.append({"code": "UNKNOWN_ACTOR", "step": index})
                continue
            if not isinstance(target_key, str) or not target_key:
                diagnostics.append({"code": "UNKNOWN_TARGET", "step": index})
                continue
            action = actions.get(action_key)
            actor = actors.get(actor_key)
            if action is None:
                diagnostics.append(
                    {"code": "UNKNOWN_ACTION", "step": index, "action_key": action_key}
                )
                continue
            if actor is None:
                diagnostics.append({"code": "UNKNOWN_ACTOR", "step": index, "actor_key": actor_key})
                continue
            if target_key not in target_keys:
                diagnostics.append(
                    {"code": "UNKNOWN_TARGET", "step": index, "target_key": target_key}
                )
                continue
            try:
                parameters = normalize_action_parameters(
                    action, dict(getattr(raw_step, "parameters", {}) or {})
                )
            except ValueError:
                diagnostics.append(
                    {
                        "code": "PARAMETER_INVALID",
                        "step": index,
                        "action_key": action_key,
                    }
                )
                continue
            signature = proposal_signature(actor_key, action_key, target_key, parameters)
            if signature in set(task.rejected_proposal_signatures):
                diagnostics.append(
                    {"code": "REJECTED_PROPOSAL", "step": index, "action_key": action_key}
                )
                continue
            effect_refs = self._context_effect_refs(planning_context, action_key)
            if signature in successful_signatures and not effect_refs.intersection(
                objective_needed
            ):
                diagnostics.append(
                    {"code": "OBJECTIVE_IRRELEVANT", "step": index, "action_key": action_key}
                )
                continue
            target_definition = definition.world.node(target_key)
            target_name = target_definition.name if target_definition is not None else target_key
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
                    if (item.node_key, item.fact_key)
                    in self._context_effect_refs(planning_context, action_key)
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
                    allow_epistemic=action_key in context_action_keys,
                )
            except GenericAgentError as exc:
                diagnostics.append(
                    {
                        "code": _safe_provider_diagnostic(exc.code),
                        "step": index,
                        "action_key": action_key,
                    }
                )
                continue
            purpose = getattr(raw_step, "purpose", "")
            if isinstance(purpose, str) and purpose.strip() and generated:
                generated[0]["description"] = purpose.strip()[:400]
            result.extend(generated)
            step_effects.append(effect_refs)

        if diagnostics:
            return [], tuple(diagnostics)

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
                            "step": index,
                            "missing_prior_public_requirements": [
                                {"node_key": node_key, "fact_key": fact_key}
                                for node_key, fact_key in sorted(missing_before)
                            ],
                        },
                    )
            covered_before.update(effects)
        missing_refs = objective_needed - set().union(*step_effects)
        if missing_refs:
            return [], (
                {
                    "code": "OBJECTIVE_COVERAGE_INCOMPLETE",
                    "missing_public_requirements": [
                        {"node_key": node_key, "fact_key": fact_key}
                        for node_key, fact_key in sorted(missing_refs)
                    ],
                },
            )
        return result, ()

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

    def _record_provider_plan_call(
        self,
        task: AgentTask,
        *,
        request: PlanRequest,
        proposal_steps: tuple[object, ...],
        proposal_candidate_ids: tuple[str, ...],
        diagnostics: tuple[dict[str, object], ...],
        accepted: bool,
    ) -> None:
        assert self.provider is not None
        metadata = dict(task.objective_resolution_metadata or {})
        calls = list(metadata.get("provider_calls", []))
        calls.append(
            {
                "call_type": request.call_type,
                "model": self.provider.model_name,
                "repair_attempt": request.repair_attempt,
                "planning_context": (
                    request.planning_context.model_dump(mode="json")
                    if request.planning_context is not None
                    else None
                ),
                "planning_context_bytes": (
                    len(
                        json.dumps(
                            request.planning_context.model_dump(mode="json"),
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
                "validation": "ACCEPTED" if accepted else "REJECTED",
                "diagnostics": list(diagnostics),
                **provider_call_metadata(self.provider),
            }
        )
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
            raise GenericAgentError("GENERIC_PLAN_PARAMETER_INVALID", str(exc)) from exc
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
                "GENERIC_PROVIDER_PLAN_INVALID", "Action does not advance the frozen scope"
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

        target = definition.world.node(target_key)
        node_state = self.db.get(GameInstanceNodeState, (self.scope.game_instance_id, target_key))
        return bool(
            target
            and node_state
            and actor_binding_matches(definition, actor)
            and node_state.visibility == Visibility.KNOWN
            and action.required_interaction_key in target.interaction_keys
            and action.key in actor.allowed_action_keys
            and {item.value for item in action.allowed_actor_capabilities}.issubset(
                set(actor.capabilities)
            )
        )

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
    ) -> None:
        step.status = AgentStepStatus.FAILED
        step.failure_code = code
        task.last_error_code = code
        if retryable:
            self.plan(task, reason=code)
        else:
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


def _safe_provider_diagnostic(code: str) -> str:
    return {
        "ACTION_PARAMETERS_INVALID": "PARAMETER_INVALID",
        "GENERIC_PLAN_PARAMETER_INVALID": "PARAMETER_INVALID",
        "AUTHORITY_PARAMETER_INVALID": "PARAMETER_INVALID",
        "ACTION_APPROVAL_REQUIRED": "AUTHORITY_REQUIRED",
        "ACTION_NOT_ALLOWED": "ACTOR_NOT_ALLOWED",
        "ACTOR_CAPABILITY_MISSING": "ACTOR_NOT_ALLOWED",
        "GENERIC_PROVIDER_PLAN_INVALID": "OBJECTIVE_IRRELEVANT",
    }.get(code, "PROPOSAL_INVALID")


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
    "GenericAgentError",
    "GenericAgentService",
    "GenericGoalResolution",
    "GenericGoalResolver",
    "GenericObjectiveEvaluation",
    "normalize_objective_keys",
    "proposal_signature",
]
