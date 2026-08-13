"""Generic exact-Version goal resolution, planning, validation and execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import actor_binding_matches, evaluate_authority
from app.agent.objective_scope import ObjectiveScope, ObjectiveScopeError
from app.agent.provider import (
    GenericModelProvider,
    GoalSelectionRequest,
    PlanRequest,
    PlanStepProposal,
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
from app.services.generic_actions import (
    GenericActionError,
    GenericActionService,
    GenericApprovalRequired,
)

ObjectiveSelector = Callable[[str, tuple[ObjectiveDefinitionV2, ...]], str | None]


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
            if keys and set(keys).issubset(valid):
                return GenericGoalResolution(
                    "RESOLVED",
                    keys[0] if len(keys) == 1 else None,
                    keys,
                    source="MODEL_VALIDATED",
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

    def create_task(self, session: ConversationSession, goal: str) -> AgentTask:
        definition = self._definition()
        if session.game_instance_id != self.scope.game_instance_id or not session.actor_key:
            raise GenericAgentError(
                "GENERIC_SESSION_SCOPE_INVALID",
                "Generic task creation requires the Instance primary Actor session",
            )
        resolution = self.goal_resolver.resolve(goal, definition)
        if resolution.status != "RESOLVED" or not resolution.objective_keys:
            raise GenericAgentError(
                f"GOAL_{resolution.status}",
                resolution.clarification_prompt or "Goal does not resolve in the exact Version",
            )
        now = datetime.now(UTC)
        catalog_version = f"scenario-version:{self.scope.scenario_version_id}"
        objective_scope = ObjectiveScope.create(resolution.objective_keys, catalog_version)
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
            objective_resolution_metadata={"exact_version": str(self.scope.scenario_version_id)},
            objective_resolved_at=now,
            objective_confirmed_at=now,
            objective_confirmation_source="EXACT_MATCH",
            objective_frozen_at=now,
            objective_freeze_source="GENERIC_AGENT",
            planning_mode="GENERIC",
        )
        self.db.add(task)
        self.db.flush()
        self.plan(task)
        return task

    def plan(self, task: AgentTask, *, reason: str | None = None) -> AgentPlan:
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
            strategy_summary="Execute exact-Version actions for the frozen objective scope",
            replan_reason=reason,
            supersedes_plan_id=old_plan.id if old_plan else None,
            created_by_actor_key=actor.actor_key,
            source="GENERIC",
            planner_model=None,
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
                step.status = AgentStepStatus.FAILED
                step.failure_code = failure_code
                task.last_error_code = failure_code
                self.plan(task, reason=failure_code)
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
                step.status = AgentStepStatus.FAILED
                step.failure_code = exc.code
                task.last_error_code = exc.code
                self.plan(task, reason=exc.code)
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
                step.status = AgentStepStatus.FAILED
                step.failure_code = failure.code
                task.last_error_code = failure.code
                self.plan(task, reason=failure.code)
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
        reason: str | None,
        plan_version: int,
    ) -> list[dict[str, object]]:
        needed = [
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
        actors = self.db.scalars(
            select(GameInstanceActor).where(
                GameInstanceActor.game_instance_id == self.scope.game_instance_id,
                GameInstanceActor.status == "ACTIVE",
            )
        ).all()
        known_nodes = self.db.scalars(
            select(GameInstanceNodeState).where(
                GameInstanceNodeState.game_instance_id == self.scope.game_instance_id,
                GameInstanceNodeState.visibility == Visibility.KNOWN,
            )
        ).all()
        known_keys = {node.node_key for node in known_nodes}
        known_facts = self.db.scalars(
            select(GameInstanceFactState).where(
                GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                GameInstanceFactState.visibility == Visibility.KNOWN,
            )
        ).all()
        proposal = self.provider.propose_plan(
            PlanRequest(
                goal=task.goal_description,
                objective_keys=tuple(item.key for item in objectives),
                replan_reason=reason,
                known_world={
                    "nodes": sorted(known_keys),
                    "facts": {
                        f"{item.node_key}.{item.fact_key}": item.truth_value
                        for item in known_facts
                        if item.node_key in known_keys
                    },
                },
                actors=tuple(
                    {
                        "key": item.actor_key,
                        "role_key": item.role_key,
                        "capabilities": item.capabilities,
                        "allowed_actions": item.allowed_action_keys,
                    }
                    for item in actors
                ),
                actions=tuple(
                    {
                        "key": item.key,
                        "execution_mode": item.execution_mode.value,
                        "parameters": [
                            parameter.model_dump(mode="json") for parameter in item.parameters
                        ],
                        "planning": item.planning.model_dump(mode="json"),
                    }
                    for item in definition.actions
                ),
            )
        )
        result: list[dict[str, object]] = []
        for index, proposed in enumerate(proposal.steps, start=1):
            result.extend(
                self._validated_proposed_step(
                    definition, proposed, objectives, plan_version, index, reason
                )
            )
        if not result and not self.evaluate(task).completed:
            raise GenericAgentError(
                "GENERIC_PROVIDER_PLAN_INVALID", "Provider returned no valid steps"
            )
        return result

    def _validated_proposed_step(
        self,
        definition: ScenarioDefinitionV2,
        proposed: PlanStepProposal,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        plan_version: int,
        index: int,
        reason: str | None,
    ) -> list[dict[str, object]]:
        action = next(
            (item for item in definition.actions if item.key == proposed.action_key), None
        )
        actor = self.db.get(GameInstanceActor, (self.scope.game_instance_id, proposed.actor_key))
        if action is None or actor is None:
            raise GenericAgentError("GENERIC_PROVIDER_PLAN_INVALID", "Unknown Action or Actor")
        if not self._validate_known_action(definition, action, actor, proposed.target_key):
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
        if objective_refs.isdisjoint(projected_refs) and not (
            reason is not None and action.planning.supporting_effects
        ):
            raise GenericAgentError(
                "GENERIC_PROVIDER_PLAN_INVALID", "Action does not advance the frozen scope"
            )
        authority = evaluate_authority(actor, action, proposed.parameters)
        if authority.outcome == AuthorityOutcome.DENY:
            raise GenericAgentError("GENERIC_PROVIDER_PLAN_INVALID", "Action authority denied")
        arguments = {
            "action_key": action.key,
            "target_key": proposed.target_key,
            "parameters": proposed.parameters,
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

    @staticmethod
    def _default_parameters(action: ActionDefinitionV2) -> dict[str, StrictScalar]:
        parameters: dict[str, StrictScalar] = {}
        for parameter in action.parameters:
            if parameter.default is not None:
                parameters[parameter.key] = parameter.default
            elif parameter.required:
                raise GenericAgentError(
                    "GENERIC_PLAN_PARAMETER_REQUIRED",
                    f"Action {action.key} needs a player/model-selected parameter",
                )
        return parameters

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
        task.completed_at = datetime.now(UTC)


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


__all__ = [
    "GenericAgentError",
    "GenericAgentService",
    "GenericGoalResolution",
    "GenericGoalResolver",
    "GenericObjectiveEvaluation",
]
