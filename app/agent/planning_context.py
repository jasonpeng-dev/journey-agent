"""Knowledge-safe PlanningContext and canonical PlannerInput construction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.dependency_closure import DependencyClosureResult, build_dependency_closure
from app.agent.formal_goal_projection import (
    formal_goal_operation_goal,
    formal_goal_planning_objectives,
)
from app.agent.planner_contract import (
    action_planner_constraints,
    action_planner_effects,
    actor_execution_state,
    declarative_action_effects,
    planner_known_preconditions,
    planner_source_preconditions,
)
from app.agent.provider import (
    ContinuityPlan,
    ContinuityStep,
    OperationGoalProjection,
    PlannerActionContract,
    PlannerActorState,
    PlannerInput,
    PlannerKnownWorldSlice,
    PlannerResourceSourceHint,
    PlannerTargetBinding,
    PlanningContext,
    PlanningContinuity,
)
from app.domain.formal_goal import FormalGoalContract
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import (
    ActionBehavior,
    ActionDefinitionV2,
    ActionLocality,
    ActionTargetKind,
    ObjectiveDefinitionV2,
    ObjectiveRequirementKind,
    ObjectiveRequirementV2,
    ScenarioDefinitionV2,
    knowledge_gate_is_revealed,
)
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    AgentPlan,
    AgentStep,
    AgentTask,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    PlanningAttempt,
    WorldOperation,
)
from app.services.derived_state import evaluate_derived_states
from app.services.knowledge_projection import SharedKnowledgeProjection


def _planner_parameter_schema(action: ActionDefinitionV2) -> list[dict[str, object]]:
    """Project the preferred structured transport input without changing YAML."""

    if action.behavior != ActionBehavior.TRANSPORT_RESOURCE:
        return [item.model_dump(mode="json") for item in action.parameters]
    return [
        {
            "key": "resources",
            "name": "Resource cargo",
            "value_type": "OBJECT_ARRAY",
            "required": True,
            "minimum_items": 1,
            "unique_by": "resource_key",
            "item_schema": {
                "resource_key": "STRING",
                "amount": "POSITIVE_INTEGER",
            },
            "legacy_parameters": [item.model_dump(mode="json") for item in action.parameters],
        }
    ]


def _canonical_resource_knowledge(raw: object) -> tuple[dict[str, object], ...]:
    """Normalize the V1 region-keyed view into sparse canonical V2 entries."""

    candidates: list[tuple[str | None, object]]
    if isinstance(raw, dict):
        candidates = [(str(region_key), value) for region_key, value in raw.items()]
    elif isinstance(raw, (list, tuple)):
        candidates = [
            (
                str(value["region_key"])
                if isinstance(value, dict) and isinstance(value.get("region_key"), str)
                else None,
                value,
            )
            for value in raw
        ]
    else:
        candidates = []

    result: list[dict[str, object]] = []
    for region_key, value in candidates:
        if region_key is None or not isinstance(value, dict):
            continue
        entry: dict[str, object] = {"region_key": region_key}
        visibility = value.get("resource_inventory_visibility")
        if isinstance(visibility, str):
            entry["resource_inventory_visibility"] = visibility
        survey_completed = value.get("resource_survey_completed")
        if isinstance(survey_completed, bool):
            entry["resource_survey_completed"] = survey_completed
        if len(entry) > 1:
            result.append(entry)
    return tuple(sorted(result, key=lambda item: str(item["region_key"])))


def _canonical_resource_source_hints(raw: object) -> tuple[PlannerResourceSourceHint, ...]:
    """Normalize authored public source guidance into the V2 Planner shape."""

    candidates: object = raw
    if isinstance(raw, dict):
        candidates = raw.get("resource_source_hints", ())
    if not isinstance(candidates, (list, tuple)):
        return ()
    result: list[PlannerResourceSourceHint] = []
    for value in candidates:
        if not isinstance(value, dict) or not isinstance(value.get("resource_key"), str):
            continue
        primary_region_key = value.get("primary_region_key")
        raw_candidate_regions = value.get("candidate_region_keys", ())
        candidate_region_keys = (
            tuple(item for item in raw_candidate_regions if isinstance(item, str))
            if isinstance(raw_candidate_regions, (list, tuple))
            else ()
        )
        result.append(
            PlannerResourceSourceHint(
                resource_key=value["resource_key"],
                primary_region_key=(
                    primary_region_key if isinstance(primary_region_key, str) else None
                ),
                candidate_region_keys=candidate_region_keys,
            )
        )
    return tuple(sorted(result, key=lambda item: item.resource_key))


def _canonical_planner_input(context: PlanningContext) -> PlannerInput:
    """Normalize the internal V1 view into one Provider-facing semantic source."""

    current = context.current_knowledge
    raw_locality = current.get("locality")
    locality_projection = (
        {
            key: value
            for key, value in cast(dict[str, object], raw_locality).items()
            if isinstance(value, (str, int, bool))
        }
        if isinstance(raw_locality, dict)
        else {}
    )
    actors: list[PlannerActorState] = []
    for raw in context.relevant_actors:
        actor_current = raw.get("current_known_state")
        state = actor_current if isinstance(actor_current, dict) else {}
        actors.append(
            PlannerActorState(
                actor_key=str(raw["actor_key"]),
                role_key=str(raw.get("role_key", "")),
                capabilities=tuple(
                    str(item)
                    for item in cast(list[object], raw.get("capabilities", []))
                    if isinstance(item, str)
                ),
                allowed_action_keys=tuple(
                    str(item)
                    for item in cast(list[object], raw.get("allowed_action_keys", []))
                    if isinstance(item, str)
                ),
                availability=str(state.get("availability", "UNKNOWN")),
                current_region=(
                    str(state["current_region"])
                    if isinstance(state.get("current_region"), str)
                    else None
                ),
                command_reachability=str(state.get("command_reachability", "UNKNOWN")),
                execution_state=(
                    dict(cast(dict[str, object], raw["execution_state"]))
                    if isinstance(raw.get("execution_state"), dict)
                    else {"status": "UNKNOWN"}
                ),
            )
        )

    action_contracts: list[PlannerActionContract] = []
    bindings: dict[tuple[str, str], dict[str, list[dict[str, object]]]] = {}
    for raw in context.relevant_actions:
        constraints = raw.get("planner_constraints")
        contract = constraints if isinstance(constraints, dict) else {}
        effects = raw.get("planner_effects")
        action_key = str(raw["action_key"])
        raw_contract_locality = contract.get("locality")
        contract_locality = (
            dict(cast(dict[str, object], raw_contract_locality))
            if isinstance(raw_contract_locality, dict)
            else {}
        )
        # The action-level locality contract remains authoritative for the
        # Planner.  These generic schema keys make the one-step BLOCKED
        # legality proof able to interpret Region/Facility/Transport
        # relations without consulting Scenario Truth.
        contract_locality.update(locality_projection)
        raw_source_relation_type_key = contract.get("source_relation_type_key")
        if not isinstance(raw_source_relation_type_key, str):
            raw_target_requirements = raw.get("target_requirements")
            if isinstance(raw_target_requirements, dict):
                raw_source_relation_type_key = raw_target_requirements.get(
                    "source_relation_type_key"
                )
        raw_source_preconditions = contract.get("source_preconditions")
        action_contracts.append(
            PlannerActionContract(
                action_key=action_key,
                executor_requirements=(
                    dict(cast(dict[str, object], contract["executor"]))
                    if isinstance(contract.get("executor"), dict)
                    else {}
                ),
                target_contract=(
                    dict(cast(dict[str, object], contract["target"]))
                    if isinstance(contract.get("target"), dict)
                    else {}
                ),
                source_relation_type_key=(
                    raw_source_relation_type_key
                    if isinstance(raw_source_relation_type_key, str)
                    else None
                ),
                source_preconditions=(
                    tuple(dict(item) for item in raw_source_preconditions if isinstance(item, dict))
                    if isinstance(raw_source_preconditions, (list, tuple))
                    else ()
                ),
                locality=contract_locality,
                parameters=tuple(
                    dict(item)
                    for item in cast(list[object], raw.get("parameter_schema", []))
                    if isinstance(item, dict)
                ),
                known_preconditions=tuple(
                    dict(item)
                    for item in cast(list[object], contract.get("known_preconditions", []))
                    if isinstance(item, dict)
                ),
                resource_requirements=tuple(
                    dict(item)
                    for item in cast(list[object], contract.get("resource_requirements", []))
                    if isinstance(item, dict)
                ),
                deterministic_effects=tuple(
                    dict(item)
                    for item in cast(list[object], effects or [])
                    if isinstance(item, dict)
                ),
                knowledge_semantics=tuple(
                    dict(item)
                    for item in cast(list[object], contract.get("knowledge", []))
                    if isinstance(item, dict)
                ),
            )
        )
        raw_contracts = raw.get("target_contracts")
        if isinstance(raw_contracts, dict):
            for target_key, target_contract in raw_contracts.items():
                if not isinstance(target_key, str) or not isinstance(target_contract, dict):
                    continue
                entry = bindings.setdefault(
                    (action_key, target_key), {"requirements": [], "effects": []}
                )
                entry["effects"].extend(
                    dict(item)
                    for item in cast(list[object], target_contract.get("effects", []))
                    if isinstance(item, dict)
                )

    current = context.current_knowledge
    # This is the Planner-shaped adapter of the same shared target Knowledge
    # projection used by the Player API.  There is deliberately no private
    # authored-hidden fallback here.
    raw_requirements = current.get("known_target_action_requirements", [])
    if isinstance(raw_requirements, list):
        for target in raw_requirements:
            if not isinstance(target, dict) or not isinstance(target.get("target_key"), str):
                continue
            for requirement in cast(list[object], target.get("requirements", [])):
                if not isinstance(requirement, dict) or not isinstance(
                    requirement.get("action_key"), str
                ):
                    continue
                action_key = str(requirement["action_key"])
                target_key = str(target["target_key"])
                entry = bindings.setdefault(
                    (action_key, target_key), {"requirements": [], "effects": []}
                )
                entry["requirements"].append(
                    {key: value for key, value in requirement.items() if key != "action_key"}
                )

    target_bindings = tuple(
        PlannerTargetBinding(
            action_key=action_key,
            target_key=target_key,
            requirements=tuple(value["requirements"]),
            resource_requirements=tuple(
                dict(item)
                for requirement in value["requirements"]
                if isinstance(requirement.get("resource_requirements"), (list, tuple))
                for item in cast(list[object], requirement["resource_requirements"])
                if isinstance(item, dict)
            ),
            deterministic_effects=tuple(value["effects"]),
        )
        for (action_key, target_key), value in sorted(bindings.items())
    )
    exposed_action_keys = {item.action_key for item in action_contracts}
    actors = [
        actor.model_copy(
            update={
                "allowed_action_keys": tuple(
                    action_key
                    for action_key in actor.allowed_action_keys
                    if action_key in exposed_action_keys
                )
            }
        )
        for actor in actors
    ]
    return PlannerInput(
        objective=dict(context.goal),
        actors=tuple(actors),
        action_contracts=tuple(action_contracts),
        target_bindings=target_bindings,
        operation_goal=context.operation_goal,
        known_world=PlannerKnownWorldSlice(
            nodes=tuple(
                dict(item)
                for item in cast(list[object], current.get("nodes", []))
                if isinstance(item, dict)
            ),
            facts=(
                dict(cast(dict[str, object], current["facts"]))
                if isinstance(current.get("facts"), dict)
                else {}
            ),
            relations=tuple(
                dict(item)
                for item in cast(list[object], current.get("relations", []))
                if isinstance(item, dict)
            ),
            resources=(
                dict(cast(dict[str, object], current["resources"]))
                if isinstance(current.get("resources"), dict)
                else {}
            ),
            resource_knowledge=_canonical_resource_knowledge(
                current.get("region_resource_knowledge", {})
            ),
            resource_source_hints=_canonical_resource_source_hints(
                current.get("resource_source_hints", ())
            ),
        ),
        execution_context=dict(context.previous_execution_context),
    )


_SEGMENT_INTENT_MAX_LENGTH = 240


def _bounded_intent_text(value: object, fallback: str) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text[:_SEGMENT_INTENT_MAX_LENGTH]
    return fallback[:_SEGMENT_INTENT_MAX_LENGTH]


def _plan_intent(
    plan: AgentPlan,
    proposal: dict[str, object] | None,
) -> tuple[str, str, str]:
    """Read short intent from the accepted proposal with legacy fallbacks."""

    fallback_goal = _bounded_intent_text(
        plan.strategy_summary,
        "Advance the frozen Objective",
    )
    fallback_link = "Re-evaluate against the frozen Formal Goal and active dependency"
    if plan.stop_reason == "OBJECTIVE_COMPLETION":
        fallback_continuation = "No continuation; the frozen Objective was completed"
    elif plan.stop_reason == "INFORMATION_BOUNDARY":
        fallback_continuation = "Re-evaluate the unresolved dependency after new Knowledge"
    elif plan.stop_reason == "BLOCKED":
        fallback_continuation = "Resume when a legal progress or Knowledge path appears"
    else:
        fallback_continuation = "Continue the unfinished mainline toward the frozen Objective"
    return (
        _bounded_intent_text(
            proposal.get("segment_goal") if proposal is not None else None,
            fallback_goal,
        ),
        _bounded_intent_text(
            proposal.get("goal_link") if proposal is not None else None,
            fallback_link,
        ),
        _bounded_intent_text(
            proposal.get("continuation_intent") if proposal is not None else None,
            fallback_continuation,
        ),
    )


class PlanningContinuityBuilder:
    """Project compact public history for one task's next REPLAN."""

    def __init__(self, db: Session, scope: RuntimeScope) -> None:
        self.db = db
        self.scope = scope

    def build(
        self,
        task: AgentTask,
        *,
        replan_reason: str | None = None,
        trigger_step_id: UUID | None = None,
    ) -> PlanningContinuity | None:
        plans = list(
            self.db.scalars(
                select(AgentPlan)
                .where(
                    AgentPlan.task_id == task.id,
                    AgentPlan.validation_status == "PASSED",
                )
                .order_by(AgentPlan.version.desc())
                .limit(3)
            )
        )
        plans.reverse()
        operations = tuple(
            self.db.scalars(
                select(WorldOperation)
                .where(
                    WorldOperation.game_instance_id == self.scope.game_instance_id,
                    WorldOperation.task_id == task.id,
                )
                .order_by(WorldOperation.created_at.asc())
            )
        )
        operations_by_step: dict[object, WorldOperation] = {
            operation.source_step_id: operation
            for operation in operations
            if operation.source_step_id is not None
        }
        accepted_attempts: dict[object, dict[str, object]] = {}
        cycle_ids = tuple(
            plan.planning_cycle_id for plan in plans if plan.planning_cycle_id is not None
        )
        if cycle_ids:
            for attempt in self.db.scalars(
                select(PlanningAttempt)
                .where(
                    PlanningAttempt.cycle_id.in_(cycle_ids),
                    PlanningAttempt.status == "ACCEPTED",
                )
                .order_by(PlanningAttempt.attempt_index.desc())
            ):
                proposal = attempt.proposal
                if isinstance(proposal, dict) and attempt.cycle_id not in accepted_attempts:
                    accepted_attempts[attempt.cycle_id] = proposal
        prior_plans = tuple(
            self._plan_projection(
                plan,
                operations_by_step,
                proposal=accepted_attempts.get(plan.planning_cycle_id),
            )
            for plan in plans
        )
        latest_new_knowledge = self._trigger_knowledge(
            task,
            trigger_step_id=trigger_step_id,
            operations_by_step=operations_by_step,
        )
        trigger = (replan_reason or task.last_error_code or "").strip() or None
        if not prior_plans and trigger is None and not latest_new_knowledge:
            return None
        return PlanningContinuity(
            prior_plans=prior_plans,
            latest_replan_trigger=trigger,
            latest_new_knowledge=latest_new_knowledge,
        )

    def _plan_projection(
        self,
        plan: AgentPlan,
        operations_by_step: dict[object, WorldOperation],
        *,
        proposal: dict[str, object] | None = None,
    ) -> ContinuityPlan:
        steps: list[ContinuityStep] = []
        for step in self.db.scalars(
            select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
        ):
            constraints = step.constraints if isinstance(step.constraints, dict) else {}
            arguments = step.tool_arguments if isinstance(step.tool_arguments, dict) else {}
            target = arguments.get("target_key")
            purpose = constraints.get("planner_purpose")
            if not isinstance(purpose, str) or not purpose.strip():
                purpose = step.description
            short_actor_reason = constraints.get("short_actor_reason")
            if not isinstance(short_actor_reason, str) or not short_actor_reason.strip():
                short_actor_reason = None
            actual = step.actual_result if isinstance(step.actual_result, dict) else {}
            outcome = actual.get("outcome", actual)
            if not isinstance(outcome, dict):
                outcome = {}
            operation = operations_by_step.get(step.id)
            if not outcome and operation is not None and isinstance(operation.outcome, dict):
                outcome = operation.outcome
            failure = outcome.get("failure")
            failure_code = (
                str(failure.get("code"))
                if isinstance(failure, dict) and failure.get("code") is not None
                else step.failure_code
            )
            outcome_code = (
                str(outcome.get("outcome_code"))
                if outcome.get("outcome_code") is not None
                else None
            )
            raw_changes = outcome.get("knowledge_changes", [])
            knowledge_changes = (
                tuple(dict(item) for item in raw_changes if isinstance(item, dict))
                if isinstance(raw_changes, list)
                else ()
            )
            action_key = step.action_intent or arguments.get("action_key") or ""
            steps.append(
                ContinuityStep(
                    action_key=str(action_key),
                    actor_key=step.assigned_actor_key,
                    target_key=str(target) if isinstance(target, str) else None,
                    purpose=str(purpose),
                    short_actor_reason=short_actor_reason,
                    execution_status=str(getattr(step.status, "value", step.status)),
                    outcome_code=outcome_code,
                    failure_code=failure_code,
                    knowledge_changes=knowledge_changes,
                )
            )
        segment_goal, goal_link, continuation_intent = _plan_intent(plan, proposal)
        return ContinuityPlan(
            plan_summary=plan.strategy_summary,
            stop_reason=plan.stop_reason,
            segment_goal=segment_goal,
            goal_link=goal_link,
            continuation_intent=continuation_intent,
            steps=tuple(steps),
        )

    def _trigger_knowledge(
        self,
        task: AgentTask,
        *,
        trigger_step_id: UUID | None,
        operations_by_step: dict[object, WorldOperation],
    ) -> tuple[dict[str, JsonValue], ...]:
        """Return only the public delta produced by the triggering execution."""

        if trigger_step_id is None:
            return ()
        trigger_step = self.db.scalar(
            select(AgentStep)
            .join(AgentPlan, AgentPlan.id == AgentStep.plan_id)
            .where(
                AgentStep.id == trigger_step_id,
                AgentPlan.task_id == task.id,
            )
        )
        if trigger_step is not None:
            changes = self._knowledge_changes_from_payload(trigger_step.actual_result)
            if changes is not None:
                return changes
        operation = operations_by_step.get(trigger_step_id)
        if operation is None:
            return ()
        changes = self._knowledge_changes_from_payload(operation.outcome)
        return changes if changes is not None else ()

    @staticmethod
    def _knowledge_changes_from_payload(
        payload: object,
    ) -> tuple[dict[str, JsonValue], ...] | None:
        if not isinstance(payload, dict):
            return None
        outcome = payload.get("outcome", payload)
        if not isinstance(outcome, dict) or "knowledge_changes" not in outcome:
            return None
        changes = outcome.get("knowledge_changes")
        if not isinstance(changes, list):
            return ()
        return tuple(dict(item) for item in changes if isinstance(item, dict))


class PlanningContextBuilder:
    """Build the provider's entity-once, high-recall PlanningContext.

    This class performs only knowledge filtering, relevance retrieval,
    semantic normalization, and compression.  It never binds an Actor to an
    Action/Target, chooses a route, or orders steps.
    """

    def __init__(self, db: Session, scope: RuntimeScope, *, retrieval_hops: int = 3) -> None:
        self.db = db
        self.scope = scope
        self.retrieval_hops = max(1, retrieval_hops)

    def build(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...] | None = None,
        *,
        task: AgentTask,
        replan_reason: str | None,
        formal_goal: FormalGoalContract | None = None,
    ) -> PlanningContext:
        operation_goal = (
            formal_goal_operation_goal(formal_goal) if formal_goal is not None else None
        )
        if formal_goal is not None:
            objectives = formal_goal_planning_objectives(
                formal_goal,
                definition,
                goal_description=task.goal_description,
            )
        if objectives is None:
            raise ValueError("PlanningContext needs a Formal Goal or Objective projection")
        known_refs = self.known_fact_refs()
        known_world = self.known_world(definition)
        knowledge_projection = SharedKnowledgeProjection(self.db, self.scope, definition)
        target_knowledge_contracts = knowledge_projection.target_knowledge_contracts()
        target_action_requirements = knowledge_projection.planner_action_requirements()
        global_action_resource_requirements = (
            knowledge_projection.global_action_resource_requirements()
        )
        known_pool_keys = {item.pool_key for item in knowledge_projection.visible_resource_pools()}
        known_derived = _public_derived_knowledge(
            definition,
            evaluate_derived_states(self.db, self.scope, definition).knowledge_values
            if definition.derived_states
            else {},
        )
        relevant_action_keys = self._retrieve_action_keys(
            definition,
            objectives,
            known_refs,
            _known_world_facts(known_world),
            operation_goal=operation_goal,
        )
        relevant_targets = self._targets(definition, relevant_action_keys, known_world)
        known_node_keys = {
            str(item["key"])
            for item in cast(list[object], known_world.get("nodes", []))
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }
        relevant_actions = self._actions(
            definition,
            objectives,
            relevant_action_keys,
            known_refs,
            known_world,
            target_knowledge_contracts,
            known_pool_keys,
            {
                action.key: planner_known_preconditions(
                    definition,
                    action,
                    known_facts={
                        identity: value
                        for identity, value in _known_world_facts(known_world).items()
                        if isinstance(value, (str, int, bool))
                    },
                    known_node_keys=known_node_keys,
                )
                for action in definition.actions
            },
            global_action_resource_requirements,
        )
        relevant_actors = self._actors(definition, relevant_action_keys)
        return PlanningContext(
            goal=self._goal(
                definition,
                objectives,
                known_refs,
                known_world,
                formal_goal=formal_goal,
                operation_goal=operation_goal,
                known_derived=known_derived,
            ),
            current_knowledge={
                **known_world,
                "derived_states": known_derived,
                # Keep this historical context key target-oriented for the
                # Planner compatibility view.  Player's action-oriented DTO
                # is produced separately by PlayerProjectionService.
                "known_action_requirements": list(target_action_requirements),
                "known_target_action_requirements": list(target_action_requirements),
                "observations": self._observations(task),
            },
            relevant_actions=tuple(relevant_actions),
            relevant_actors=tuple(relevant_actors),
            relevant_targets=tuple(relevant_targets),
            operation_goal=operation_goal,
            previous_execution_context=self._previous_execution(task, replan_reason),
            scenario_planning_hints={
                "instructions": list(definition.planning.instructions),
                "recovery_hints": [
                    item.model_dump(mode="json") for item in definition.planning.recovery_hints
                ],
                "generic_rules": [
                    (
                        "allowed_action_keys are static capability/role permission, "
                        "not current executability."
                    ),
                    "KNOWN_BLOCKED conditions must be resolved before execution.",
                    "UNKNOWN is not equivalent to false, zero, or unavailable.",
                    "Do not consume or transport resources whose availability is not known.",
                    (
                        "Target-specific known requirements are in the shared target Knowledge "
                        "projection and its known_target_action_requirements adapter."
                    ),
                    (
                        "Use planner_constraints, planner_effects, and target_contracts "
                        "to order steps."
                    ),
                ],
            },
        )

    build_context = build

    def build_v2(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...] | None = None,
        *,
        task: AgentTask,
        replan_reason: str | None,
        formal_goal: FormalGoalContract | None = None,
    ) -> PlannerInput:
        """Build canonical V2 while V1 remains an internal Validator adapter."""

        return self.build_v2_closure(
            definition,
            objectives,
            task=task,
            replan_reason=replan_reason,
            formal_goal=formal_goal,
        ).planner_input

    def build_v2_closure(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...] | None = None,
        *,
        task: AgentTask,
        replan_reason: str | None,
        formal_goal: FormalGoalContract | None = None,
    ) -> DependencyClosureResult:
        """Build the typed, bounded dependency closure and its internal audit."""

        base = _canonical_planner_input(
            self.build(
                definition,
                objectives,
                task=task,
                replan_reason=replan_reason,
                formal_goal=formal_goal,
            )
        )
        return build_dependency_closure(
            definition,
            objectives,
            base,
            formal_goal=formal_goal,
        )

    def _retrieve_action_keys(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        known_refs: set[tuple[str, str]],
        known_facts: dict[tuple[str, str], object],
        *,
        operation_goal: OperationGoalProjection | None = None,
    ) -> set[str]:
        """Retrieve a bounded, high-recall action set from public projections.

        V2 has no executable Action-prerequisite field, so retrieval follows
        authored objective requirements and then retains every action whose
        public projection is visible on a known target.  This intentionally
        keeps epistemic/supporting actions (recon/inspect/probe) even when they
        do not directly satisfy a completion requirement.  ``retrieval_hops``
        is a safety bound for future schemas that add public prerequisite
        references; it never ranks or removes a hard-valid alternative.
        """

        objective_refs = _objective_refs(
            objectives,
            definition=definition,
            known_facts=known_facts,
        )
        selected: set[str] = set()
        if operation_goal is not None:
            action = next(
                (item for item in definition.actions if item.key == operation_goal.action_key),
                None,
            )
            if action is not None and _action_planning_is_public(action, known_facts):
                selected.add(action.key)
        frontier = set(objective_refs)
        known_nodes = {
            item.node_key
            for item in self.db.scalars(
                select(GameInstanceNodeState).where(
                    GameInstanceNodeState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceNodeState.visibility == Visibility.KNOWN,
                )
            )
        }
        active_actor_count = self.db.scalar(
            select(GameInstanceActor.actor_key).where(
                GameInstanceActor.game_instance_id == self.scope.game_instance_id,
                GameInstanceActor.status == "ACTIVE",
            )
        )
        for _hop in range(self.retrieval_hops):
            changed = False
            for action in definition.actions:
                if not _action_planning_is_public(action, known_facts):
                    continue
                visible_effects = {
                    (item.node_key, item.fact_key)
                    for item in (
                        *action.planning.terminal_effects,
                        *action.planning.supporting_effects,
                    )
                    if (item.node_key, item.fact_key) in known_refs
                }
                visible_effects.update(
                    (node.key, fact_key)
                    for node in definition.world.nodes
                    if node.key in known_nodes
                    and action.required_interaction_key in node.interaction_keys
                    and (
                        not action.target_node_type_keys
                        or node.node_type_key in action.target_node_type_keys
                    )
                    for effect in action.planning.target_terminal_effects
                    for fact_key in (effect.fact_key,)
                    if (node.key, fact_key) in known_refs
                )
                if not visible_effects and (
                    action.behavior == ActionBehavior.RULE
                    and action.locality == ActionLocality.NONE
                ):
                    continue
                has_known_target = any(
                    node.key in known_nodes
                    and action.required_interaction_key in node.interaction_keys
                    for node in definition.world.nodes
                ) or (
                    action.target_kind == ActionTargetKind.ACTOR and active_actor_count is not None
                )
                if not has_known_target:
                    continue
                if action.key not in selected and (not frontier or visible_effects & frontier):
                    selected.add(action.key)
                    changed = True
                    frontier.update(visible_effects)
            if not changed:
                break

        # High recall is more important than an early backend guess.  Include
        # every additional action with a visible public effect and a known
        # target affordance, including actions that are currently locked.
        for action in definition.actions:
            if not _action_planning_is_public(action, known_facts):
                continue
            known_target = any(
                node.key in known_nodes and action.required_interaction_key in node.interaction_keys
                for node in definition.world.nodes
            ) or (action.target_kind == ActionTargetKind.ACTOR and active_actor_count is not None)
            visible_effect = any(
                (effect.node_key, effect.fact_key) in known_refs
                for effect in (
                    *action.planning.terminal_effects,
                    *action.planning.supporting_effects,
                )
            )
            visible_effect = visible_effect or any(
                node.key in known_nodes
                and action.required_interaction_key in node.interaction_keys
                and (
                    not action.target_node_type_keys
                    or node.node_type_key in action.target_node_type_keys
                )
                and (node.key, fact_key) in known_refs
                for node in definition.world.nodes
                for effect in action.planning.target_terminal_effects
                for fact_key in (effect.fact_key,)
            )
            operational = (
                action.behavior != ActionBehavior.RULE or action.locality != ActionLocality.NONE
            )
            if known_target and (visible_effect or operational):
                selected.add(action.key)
        return selected

    def _goal(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        known_refs: set[tuple[str, str]],
        known_world: dict[str, object],
        *,
        formal_goal: FormalGoalContract | None = None,
        operation_goal: OperationGoalProjection | None = None,
        known_derived: dict[str, object] | None = None,
    ) -> dict[str, object]:
        known_facts = _known_world_facts(known_world)
        derived_values = known_derived or {}
        completion = [
            _planning_goal_requirement_payload(item, derived_values)
            for objective in objectives
            for item in objective.completion_requirements
            if _requirement_is_public(item, known_refs, known_facts, definition)
        ]
        prerequisites = [
            {
                "objective_key": objective.key,
                "key": group.key,
                "description": group.description,
                "requirements": [
                    item.model_dump(mode="json")
                    for item in group.requirements
                    if _requirement_is_public(item, known_refs, known_facts, definition)
                ],
            }
            for objective in objectives
            for group in objective.prerequisites
            if any(
                _requirement_is_public(item, known_refs, known_facts, definition)
                for item in group.requirements
            )
        ]
        result: dict[str, object] = {
            "exact_scenario_version": str(self.scope.scenario_version_id),
            "objective_scope": [item.key for item in objectives],
            "objectives": [
                {
                    "key": item.key,
                    "display": item.name,
                    "description": item.description,
                    **(
                        {"planning_guidance": item.planning_guidance}
                        if item.planning_guidance is not None
                        else {}
                    ),
                }
                for item in objectives
            ],
            "completion_requirements": completion,
            "public_prerequisites": prerequisites,
        }
        if formal_goal is not None:
            formal_payload: dict[str, object] = {
                "schema_version": formal_goal.schema_version,
                "source_kind": formal_goal.source_kind.value,
                "contract_hash": formal_goal.content_hash,
                "requirements": [
                    {
                        "identity": item.identity,
                        **_planning_goal_requirement_payload(item.requirement, derived_values),
                    }
                    for item in formal_goal.completion_requirements
                    if isinstance(item.requirement, ObjectiveRequirementV2)
                    and _requirement_is_public(
                        item.requirement,
                        known_refs,
                        known_facts,
                        definition,
                    )
                ],
            }
            if operation_goal is not None:
                formal_payload["operation_goal"] = operation_goal.model_dump(mode="json")
            result["formal_goal"] = formal_payload
        return result

    def _actions(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        action_keys: set[str],
        known_refs: set[tuple[str, str]],
        known_world: dict[str, object],
        target_knowledge_contracts: tuple[dict[str, object], ...],
        known_pool_keys: set[str],
        known_preconditions_by_action: dict[str, tuple[dict[str, object], ...]],
        global_action_resource_requirements: dict[str, tuple[dict[str, object], ...]],
    ) -> list[dict[str, object]]:
        known_facts = _known_world_facts(known_world)
        objective_refs = _objective_refs(
            objectives,
            definition=definition,
            known_facts=known_facts,
        )
        objective_nodes = {node_key for node_key, _fact_key in objective_refs}
        raw_nodes = known_world.get("nodes", [])
        node_rows = cast(list[dict[str, object]], raw_nodes) if isinstance(raw_nodes, list) else []
        known_node_keys: set[str] = {
            cast(str, item["key"])
            for item in node_rows
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }
        raw_relations = known_world.get("relations", [])
        relation_rows = (
            cast(list[dict[str, object]], raw_relations) if isinstance(raw_relations, list) else []
        )
        known_relation_keys: set[str] = {
            cast(str, item["relation_key"])
            for item in relation_rows
            if isinstance(item, dict) and isinstance(item.get("relation_key"), str)
        }
        target_contracts_by_action: dict[str, list[dict[str, object]]] = {}
        for contract in target_knowledge_contracts:
            action_key = contract.get("action_key")
            if isinstance(action_key, str):
                target_contracts_by_action.setdefault(action_key, []).append(contract)
        result: list[dict[str, object]] = []
        for action in sorted(definition.actions, key=lambda item: item.key):
            if action.key not in action_keys:
                continue
            if not _action_planning_is_public(action, known_facts):
                continue
            safe_target_contracts = target_contracts_by_action.get(action.key, [])
            safe_target_roles = tuple(
                {
                    "target_key": contract["target_key"],
                    "required_role_key": contract["required_actor_role_key"],
                }
                for contract in safe_target_contracts
                if isinstance(contract.get("target_key"), str)
                and isinstance(contract.get("required_actor_role_key"), str)
            )
            terminal = [
                item.model_dump(mode="json")
                for item in action.planning.terminal_effects
                if (item.node_key, item.fact_key) in known_refs
            ]
            supporting = [
                item.model_dump(mode="json")
                for item in action.planning.supporting_effects
                if (item.node_key, item.fact_key) in known_refs
            ]
            relevance = [
                item
                for item in (*terminal, *supporting)
                if (item["node_key"], item["fact_key"]) in objective_refs
            ]
            relevance.extend(
                {
                    "node_key": item["node_key"],
                    "fact_key": item["fact_key"],
                    "relation": "RELATED_OBJECTIVE_NODE",
                }
                for item in (*terminal, *supporting)
                if item["node_key"] in objective_nodes
                and (item["node_key"], item["fact_key"]) not in objective_refs
            )
            action_context: dict[str, object] = {
                "action_key": action.key,
                "display": action.name,
                "description": action.description,
                "declared_world_effects": terminal,
                "declared_knowledge_effects": supporting,
                "objective_relevance": relevance,
                "target_requirements": {
                    "required_interaction_key": action.required_interaction_key,
                    **(
                        {"source_relation_type_key": action.source_relation_type_key}
                        if action.source_relation_type_key is not None
                        else {}
                    ),
                },
                "parameter_schema": _planner_parameter_schema(action),
                "parameter_defaults": {
                    item.key: item.default for item in action.parameters if item.default is not None
                },
                "hard_constraints": {
                    "required_actor_capabilities": [
                        item.value for item in action.allowed_actor_capabilities
                    ],
                    **(
                        {"required_actor_role_key": action.required_actor_role_key}
                        if action.required_actor_role_key is not None
                        else {}
                    ),
                    **(
                        {"target_actor_roles": [dict(item) for item in safe_target_roles]}
                        if safe_target_roles
                        else {}
                    ),
                    "static_authority": action.authority_policy.model_dump(mode="json"),
                },
                "execution_mode": action.execution_mode.value,
                "behavior": action.behavior.value,
                "locality": action.locality.value,
                "target_kind": action.target_kind.value,
                "planner_constraints": action_planner_constraints(
                    action,
                    known_preconditions=known_preconditions_by_action.get(action.key, ()),
                    source_preconditions=planner_source_preconditions(definition, action),
                    resource_requirements=global_action_resource_requirements.get(
                        action.key, ()
                    ),
                    target_role_requirements=safe_target_roles,
                ),
            }
            planner_effects = action_planner_effects(action)
            known_fact_values = {
                key: value
                for key, value in known_facts.items()
                if isinstance(value, (str, int, bool))
            }
            planner_effects.extend(
                declarative_action_effects(
                    definition,
                    action,
                    known_node_keys=known_node_keys,
                    known_relation_keys=known_relation_keys,
                    known_pool_keys=known_pool_keys,
                    known_facts=known_fact_values,
                )
            )
            if planner_effects:
                action_context["planner_effects"] = planner_effects
            target_contracts = {
                str(contract["target_key"]): {
                    "effects": [
                        dict(effect)
                        for effect in cast(list[object], contract.get("effects", []))
                        if isinstance(effect, dict)
                    ]
                }
                for contract in safe_target_contracts
                if isinstance(contract.get("target_key"), str)
                and isinstance(contract.get("effects"), list)
                and contract.get("effects")
            }
            if target_contracts:
                action_context["target_contracts"] = target_contracts
            hints = list(action.planning.hints)
            if action.behavior == ActionBehavior.SURVEY_RESOURCES:
                hints.extend(
                    [
                        "Use when ordinary Region inventory is unknown.",
                        "A visible inventory can still have an incomplete full survey.",
                        "A survey may discover Facility-bound hidden stock.",
                        "Discovered stock may remain unavailable until its "
                        "requirement is satisfied.",
                        "Do not repeat a survey after resource_survey_completed is true.",
                    ]
                )
            elif action.behavior == ActionBehavior.SUPPLY_POWER:
                hints.extend(
                    [
                        (
                            "Choose the explicit source_key and target for one known direct "
                            "power relation."
                        ),
                        (
                            "Use only a source whose known power requirements are satisfied; "
                            "do not infer hidden power state."
                        ),
                        "Do not search for or invent an automatic power route.",
                    ]
                )
            elif action.behavior == ActionBehavior.DEPLOY_HEAVY_ENGINEERING_SUPPORT:
                hints.extend(
                    [
                        (
                            "Use only when the known heavy engineering support capability is "
                            "available."
                        ),
                        (
                            "Deploy at the explicit Facility or Transport target before "
                            "specialist repair."
                        ),
                    ]
                )
            elif action.behavior == ActionBehavior.INSPECT:
                hints.extend(
                    [
                        (
                            "Use to actively investigate one known but not yet inspected "
                            "Facility or Transport."
                        ),
                        (
                            "Facility inspection reveals non-resource facility information "
                            "and repair requirements."
                        ),
                        "Transport inspection reveals the true passability state.",
                        "Inspection does not reveal resource inventory or hidden resource pools.",
                    ]
                )
            elif action.behavior == ActionBehavior.REPAIR_COMMUNICATIONS:
                hints.extend(
                    [
                        (
                            "After successful communication restoration, Facility information "
                            "in the target Region becomes known."
                        ),
                        (
                            "This reveal covers non-resource Facility state and repair "
                            "requirements only."
                        ),
                        (
                            "It does not survey resources, reveal hidden resource pools, "
                            "or create new power relations."
                        ),
                    ]
                )
            elif action.behavior == ActionBehavior.CLEAR_TRANSPORT:
                hints.extend(
                    [
                        (
                            "Use only for a Transport whose passability is already known "
                            "to be blocked."
                        ),
                        "A successful clear makes that Transport known passable.",
                    ]
                )
            elif action.behavior in {ActionBehavior.TRAVEL, ActionBehavior.TRANSPORT_RESOURCE}:
                hints.extend(
                    [
                        (
                            "UNKNOWN Transport passability may be attempted and is not "
                            "the same as blocked."
                        ),
                        (
                            "A successful attempt confirms the Transport is passable; a "
                            "blocked failure confirms it needs repair."
                        ),
                    ]
                )
                if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
                    hints.extend(
                        [
                            (
                                "transport_resource carries one or more resource entries; "
                                "prefer the resources[] format with unique resource_key values "
                                "and positive amounts."
                            ),
                            (
                                "The source is the executing Actor's projected current Region; "
                                "the Action crosses exactly one legal Transport edge and moves "
                                "that Actor to the destination on success."
                            ),
                            (
                                "Prefer efficient logistics: minimize unnecessary travel and "
                                "duplicate transport actions. When multiple required resources "
                                "share the same downstream route, consider consolidating them "
                                "into one transport action at a common region. Prefer shorter "
                                "legal routes when reasonable. UNKNOWN passability is "
                                "MAY_ATTEMPT, not blocked."
                            ),
                        ]
                    )
            if hints:
                action_context["soft_signals"] = {"hints": hints}
            result.append(action_context)
        return result

    def _actors(
        self,
        definition: ScenarioDefinitionV2,
        action_keys: set[str],
    ) -> list[dict[str, object]]:
        roles = {item.key: item for item in definition.actors.roles}
        actors = SharedKnowledgeProjection(self.db, self.scope, definition).actor_rows()
        result: list[dict[str, object]] = []
        for actor in sorted(actors, key=lambda item: item.actor_key):
            role = roles.get(actor.role_key)
            current_region: str | None = None
            if definition.metadata.locality.enabled:
                from app.engine.locality import LocalityEngineError, region_for_node

                try:
                    current_region = region_for_node(definition, actor.current_node_key)
                except LocalityEngineError:
                    current_region = None
            current_known_state: dict[str, object] = {
                "availability": actor.status,
                "current_node_key": actor.current_node_key,
                "command_reachability": actor.command_reachability,
            }
            if current_region is not None:
                current_known_state["current_region"] = current_region
            result.append(
                {
                    "actor_key": actor.actor_key,
                    "display": actor.name,
                    "role_key": actor.role_key,
                    "role_display": role.name if role is not None else actor.role_key,
                    "capabilities": list(actor.capabilities),
                    "static_authority": actor.authority_policy,
                    "current_known_state": current_known_state,
                    "execution_state": actor_execution_state(
                        status=actor.status,
                        command_reachability=actor.command_reachability,
                    ),
                    "allowed_action_keys": [
                        key for key in actor.allowed_action_keys if key in action_keys
                    ],
                    "soft_signals": {"doctrine": actor.doctrine, "persona": actor.persona},
                }
            )
        return result

    def _targets(
        self,
        definition: ScenarioDefinitionV2,
        action_keys: set[str],
        known_world: dict[str, object],
    ) -> list[dict[str, object]]:
        interaction_keys = {
            action.required_interaction_key
            for action in definition.actions
            if action.key in action_keys and action.target_kind == ActionTargetKind.NODE
        }
        raw_nodes = known_world.get("nodes", [])
        node_rows = cast(list[dict[str, object]], raw_nodes) if isinstance(raw_nodes, list) else []
        known_nodes = {item["key"]: item for item in node_rows if isinstance(item.get("key"), str)}
        result: list[dict[str, object]] = []
        for node in sorted(definition.world.nodes, key=lambda item: item.key):
            if node.key not in known_nodes or not set(node.interaction_keys).intersection(
                interaction_keys
            ):
                continue
            result.append({"target_key": node.key})
        actor_target_actions = {
            action.key
            for action in definition.actions
            if action.key in action_keys and action.target_kind == ActionTargetKind.ACTOR
        }
        if actor_target_actions:
            result.extend(
                {"target_key": actor.actor_key}
                for actor in sorted(
                    SharedKnowledgeProjection(self.db, self.scope, definition).actor_rows(),
                    key=lambda item: item.actor_key,
                )
            )
        return result

    def _previous_execution(self, task: AgentTask, replan_reason: str | None) -> dict[str, object]:
        plan = self.db.scalar(
            select(AgentPlan).where(AgentPlan.task_id == task.id).order_by(AgentPlan.version.desc())
        )
        completed: list[dict[str, object]] = []
        failed: dict[str, object] | None = None
        player_visible_result: dict[str, object] | None = None
        newly_learned_knowledge: list[object] = []
        operation = self.db.scalar(
            select(WorldOperation)
            .where(
                WorldOperation.game_instance_id == self.scope.game_instance_id,
                WorldOperation.task_id == task.id,
            )
            .order_by(WorldOperation.created_at.desc())
        )
        if operation is not None and isinstance(operation.outcome, dict):
            outcome = operation.outcome
            raw_knowledge_changes = outcome.get("knowledge_changes", [])
            if isinstance(raw_knowledge_changes, list):
                newly_learned_knowledge = list(raw_knowledge_changes)
            failure = outcome.get("failure")
            if isinstance(failure, dict):
                player_visible_result = {
                    "outcome_code": outcome.get("outcome_code"),
                    "knowledge_changes": newly_learned_knowledge,
                    "failure": {
                        "code": failure.get("code"),
                        "message": failure.get("message"),
                        "retryable": failure.get("retryable"),
                    },
                }
            else:
                player_visible_result = {
                    "outcome_code": outcome.get("outcome_code"),
                    "knowledge_changes": outcome.get("knowledge_changes", []),
                }
        if plan is not None:
            for step in self.db.scalars(
                select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
            ):
                if step.status.value in {"SUCCEEDED", "FAILED", "SKIPPED"}:
                    item: dict[str, object] = {
                        "sequence": step.sequence,
                        "action_key": step.action_intent,
                        "status": step.status.value,
                    }
                    if step.status.value == "FAILED":
                        failed = {**item, "failure_code": step.failure_code}
                    else:
                        completed.append(item)
        if (
            plan is None
            and operation is None
            and replan_reason is None
            and task.last_error_code is None
        ):
            return {}
        return {
            "previous_plan_summary": plan.strategy_summary if plan is not None else None,
            "previous_plan_version": plan.version if plan is not None else None,
            "failed_or_current_step": failed,
            "player_visible_result": player_visible_result or failed,
            "newly_learned_knowledge": newly_learned_knowledge,
            "relevant_blocker": replan_reason or task.last_error_code,
            "completed_steps": completed,
        }

    @staticmethod
    def _observations(task: AgentTask) -> list[dict[str, object]]:
        metadata = task.objective_resolution_metadata or {}
        values = metadata.get("observations", [])
        return (
            [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []
        )


    def known_world(self, definition: ScenarioDefinitionV2) -> dict[str, object]:
        """Return the shared knowledge projection used to build PlannerInput."""

        knowledge_projection = SharedKnowledgeProjection(self.db, self.scope, definition)
        node_states = knowledge_projection.known_node_rows()
        known_keys = {item.node_key for item in node_states}
        facts = knowledge_projection.known_fact_rows()
        resource_projection = knowledge_projection.planner_resources()
        resources: dict[str, object] = {}
        for resource_key, summary in resource_projection["resources"].items():
            scopes = {
                region_key: {
                    "value": region_summary["known_total"],
                    "known_total": region_summary["known_total"],
                    "known_available": region_summary["known_available"],
                    "pools": region_summary["pools"],
                    **(
                        {"knowledge_status": region_summary["knowledge_status"]}
                        if isinstance(region_summary.get("knowledge_status"), str)
                        else {}
                    ),
                }
                for region_key, region_summary in summary.get("regions", {}).items()
            }
            if "global" in summary:
                scopes["global"] = summary["global"]
            resources[resource_key] = {
                "known_total": summary["known_total"],
                "known_available": summary["known_available"],
                "scopes": scopes,
            }
        return {
            "nodes": [
                _node_context(definition, item)
                for item in sorted(node_states, key=lambda row: row.node_key)
            ],
            "facts": {
                f"{item.node_key}.{item.fact_key}": item.truth_value
                for item in facts
                if item.node_key in known_keys
            },
            "relations": list(knowledge_projection.known_relations()),
            "resources": {resource_key: value for resource_key, value in resources.items()},
            "region_resource_knowledge": resource_projection["regions"],
            "resource_source_hints": knowledge_projection.public_resource_source_hints(),
            **(
                {"locality": definition.metadata.locality.model_dump(mode="json")}
                if definition.metadata.locality.enabled
                else {}
            ),
        }

    def known_fact_refs(self) -> set[tuple[str, str]]:
        return {
            (item.node_key, item.fact_key)
            for item in self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceFactState.visibility == Visibility.KNOWN,
                )
            )
        }


def _objective_refs(
    objectives: tuple[ObjectiveDefinitionV2, ...],
    *,
    definition: ScenarioDefinitionV2 | None = None,
    known_facts: Mapping[tuple[str, str], object] | None = None,
) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    visited_derived: set[str] = set()
    public_known_facts = known_facts or {}

    def add_derived_dependencies(derived_key: str) -> None:
        if definition is None or derived_key in visited_derived:
            return
        state = definition.derived_state_definitions.get(derived_key)
        if state is None:
            return
        visited_derived.add(derived_key)
        for dependency in state.dependencies:
            gate = dependency.knowledge_gate
            if not knowledge_gate_is_revealed(
                gate,
                public_known_facts.get((gate.node_key, gate.fact_key))
                if gate is not None
                else None,
            ):
                continue
            if dependency.kind.value == "FACT":
                assert dependency.node_key is not None and dependency.fact_key is not None
                result.add((dependency.node_key, dependency.fact_key))
            elif dependency.kind.value == "DERIVED_STATE":
                assert dependency.derived_key is not None
                add_derived_dependencies(dependency.derived_key)

    for objective in objectives:
        for item in (
            *objective.completion_requirements,
            *(
                requirement
                for group in objective.prerequisites
                for requirement in group.requirements
            ),
        ):
            gate = item.knowledge_gate
            if not knowledge_gate_is_revealed(
                gate,
                public_known_facts.get((gate.node_key, gate.fact_key))
                if gate is not None
                else None,
            ):
                continue
            if item.fact_ref is not None:
                result.add(item.fact_ref)
            if item.derived_ref is not None:
                add_derived_dependencies(item.derived_ref)
    return result


def _public_derived_knowledge(
    definition: ScenarioDefinitionV2,
    values: Mapping[str, object],
) -> dict[str, object]:
    return {
        key: values.get(key)
        for key, state in definition.derived_state_definitions.items()
        if state.goal_addressable
    }


def _planning_goal_requirement_payload(
    requirement: ObjectiveRequirementV2,
    known_derived: dict[str, object],
) -> dict[str, object]:
    payload = requirement.model_dump(mode="json")
    if requirement.kind == ObjectiveRequirementKind.DERIVED_STATE:
        assert requirement.derived_key is not None
        current = known_derived.get(requirement.derived_key)
        payload["current_known_value"] = current
        payload["knowledge_status"] = "UNKNOWN" if current is None else "KNOWN"
    return payload


def _requirement_is_public(
    requirement: ObjectiveRequirementV2,
    known_fact_refs: set[tuple[str, str]],
    known_facts: dict[tuple[str, str], object],
    definition: ScenarioDefinitionV2 | None = None,
) -> bool:
    gate = requirement.knowledge_gate
    if not knowledge_gate_is_revealed(
        gate,
        known_facts.get((gate.node_key, gate.fact_key)) if gate is not None else None,
    ):
        return False
    if requirement.kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST:
        return True
    if requirement.kind == ObjectiveRequirementKind.DERIVED_STATE:
        return bool(
            definition is not None
            and requirement.derived_key is not None
            and (state := definition.derived_state_definitions.get(requirement.derived_key))
            is not None
            and state.goal_addressable
        )
    assert requirement.fact_ref is not None
    if requirement.fact_ref in known_fact_refs:
        return True
    if definition is None:
        return False
    node = definition.world.node(requirement.fact_ref[0])
    fact = node.fact(requirement.fact_ref[1]) if node is not None else None
    return bool(fact is not None and fact.goal_addressable)


def _action_planning_is_public(
    action: ActionDefinitionV2,
    known_facts: dict[tuple[str, str], object],
) -> bool:
    """Keep gated Action relevance out of Planner projections until discovered."""

    gate = action.planning.knowledge_gate
    return knowledge_gate_is_revealed(
        gate,
        known_facts.get((gate.node_key, gate.fact_key)) if gate is not None else None,
    )


def _known_world_facts(known_world: dict[str, object]) -> dict[tuple[str, str], object]:
    raw_facts = known_world.get("facts", {})
    if not isinstance(raw_facts, dict):
        return {}
    result: dict[tuple[str, str], object] = {}
    for identity, value in raw_facts.items():
        if not isinstance(identity, str) or "." not in identity:
            continue
        node_key, fact_key = identity.split(".", 1)
        result[(node_key, fact_key)] = value
    return result


def _node_context(
    definition: ScenarioDefinitionV2,
    state: GameInstanceNodeState,
) -> dict[str, object]:
    node = definition.world.node(state.node_key)
    return {
        "key": state.node_key,
        "name": node.name if node is not None else state.node_key,
        "type": node.node_type_key if node is not None else None,
        "access": state.status.value,
        "interactions": list(node.interaction_keys) if node is not None else [],
    }


__all__ = [
    "PlanningContext",
    "PlanningContextBuilder",
    "PlanningContinuityBuilder",
]
