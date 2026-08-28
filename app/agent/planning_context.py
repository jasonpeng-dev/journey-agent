"""Knowledge-safe PlanningContext V1 and the legacy catalog compatibility view."""

from __future__ import annotations

import hashlib
from typing import cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import actor_binding_matches
from app.agent.dependency_closure import DependencyClosureResult, build_dependency_closure
from app.agent.planner_contract import (
    action_planner_constraints,
    action_planner_effects,
    actor_execution_state,
    declarative_action_effects,
    planner_known_preconditions,
    planner_source_preconditions,
    planner_target_contracts,
)
from app.agent.provider import (
    ContinuityPlan,
    ContinuityStep,
    PlannerActionContract,
    PlannerActorState,
    PlannerInput,
    PlannerKnownWorldSlice,
    PlannerTargetBinding,
    PlanningActionCandidate,
    PlanningContext,
    PlanningContinuity,
)
from app.domain.enums import NodeStatus, WorldOperationStatus
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import (
    ActionBehavior,
    ActionDefinitionV2,
    ActionLocality,
    ActionTargetKind,
    ObjectiveDefinitionV2,
    ScenarioDefinitionV2,
)
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    AgentPlan,
    AgentStep,
    AgentTask,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceResourceState,
    WorldOperation,
)
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
    raw_requirements = current.get("known_action_requirements", [])
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
        ),
        execution_context=dict(context.previous_execution_context),
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
        prior_plans = tuple(self._plan_projection(plan, operations_by_step) for plan in plans)
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
        return ContinuityPlan(
            plan_summary=plan.strategy_summary,
            stop_reason=plan.stop_reason,
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
    Action/Target, chooses a route, or orders steps.  The old
    ``PlanningActionCatalogBuilder`` below remains as a compatibility view for
    in-process callers while migration completes; it is deliberately not used
    by the OpenAI-compatible provider payload.
    """

    def __init__(self, db: Session, scope: RuntimeScope, *, retrieval_hops: int = 3) -> None:
        self.db = db
        self.scope = scope
        self.retrieval_hops = max(1, retrieval_hops)

    def build(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        *,
        task: AgentTask,
        replan_reason: str | None,
    ) -> PlanningContext:
        legacy = PlanningActionCatalogBuilder(self.db, self.scope)
        known_refs = legacy.known_fact_refs()
        known_world = legacy.known_world(definition)
        knowledge_projection = SharedKnowledgeProjection(self.db, self.scope, definition)
        planner_action_requirements = knowledge_projection.planner_action_requirements()
        known_pool_keys = {item.pool_key for item in knowledge_projection.visible_resource_pools()}
        relevant_action_keys = self._retrieve_action_keys(definition, objectives, known_refs)
        relevant_targets = self._targets(definition, relevant_action_keys, known_world)
        relevant_actions = self._actions(
            definition,
            objectives,
            relevant_action_keys,
            known_refs,
            known_world,
            planner_action_requirements,
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
                )
                for action in definition.actions
            },
        )
        relevant_actors = self._actors(definition, relevant_action_keys)
        return PlanningContext(
            goal=self._goal(definition, objectives, known_refs),
            current_knowledge={
                **known_world,
                "known_action_requirements": list(planner_action_requirements),
                "observations": self._observations(task),
            },
            relevant_actions=tuple(relevant_actions),
            relevant_actors=tuple(relevant_actors),
            relevant_targets=tuple(relevant_targets),
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
                        "Target-specific known requirements are in current_knowledge."
                        "known_action_requirements; target_contracts adds target effects."
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
        objectives: tuple[ObjectiveDefinitionV2, ...],
        *,
        task: AgentTask,
        replan_reason: str | None,
    ) -> PlannerInput:
        """Build canonical V2 while V1 remains an internal Validator adapter."""

        return self.build_v2_closure(
            definition,
            objectives,
            task=task,
            replan_reason=replan_reason,
        ).planner_input

    def build_v2_closure(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        *,
        task: AgentTask,
        replan_reason: str | None,
    ) -> DependencyClosureResult:
        """Build the typed, bounded dependency closure and its internal audit."""

        base = _canonical_planner_input(
            self.build(
                definition,
                objectives,
                task=task,
                replan_reason=replan_reason,
            )
        )
        return build_dependency_closure(definition, objectives, base)

    def _retrieve_action_keys(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        known_refs: set[tuple[str, str]],
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

        objective_refs = _objective_refs(objectives)
        selected: set[str] = set()
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
                visible_effects = {
                    (item.node_key, item.fact_key)
                    for item in (
                        *action.planning.terminal_effects,
                        *action.planning.supporting_effects,
                    )
                    if (item.node_key, item.fact_key) in known_refs
                }
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
    ) -> dict[str, object]:
        completion = [
            item.model_dump(mode="json")
            for objective in objectives
            for item in objective.completion_requirements
            if (item.node_key, item.fact_key) in known_refs
        ]
        prerequisites = [
            {
                "objective_key": objective.key,
                "key": group.key,
                "description": group.description,
                "requirements": [
                    item.model_dump(mode="json")
                    for item in group.requirements
                    if (item.node_key, item.fact_key) in known_refs
                ],
            }
            for objective in objectives
            for group in objective.prerequisites
            if any((item.node_key, item.fact_key) in known_refs for item in group.requirements)
        ]
        return {
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

    def _actions(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        action_keys: set[str],
        known_refs: set[tuple[str, str]],
        known_world: dict[str, object],
        planner_action_requirements: tuple[dict[str, object], ...],
        known_pool_keys: set[str],
        known_preconditions_by_action: dict[str, tuple[dict[str, object], ...]],
    ) -> list[dict[str, object]]:
        objective_refs = _objective_refs(objectives)
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
        raw_facts = known_world.get("facts", {})
        known_facts: dict[tuple[str, str], object] = {}
        if isinstance(raw_facts, dict):
            for identity, value in raw_facts.items():
                if not isinstance(identity, str) or "." not in identity:
                    continue
                node_key, fact_key = identity.split(".", 1)
                known_facts[(node_key, fact_key)] = value
        result: list[dict[str, object]] = []
        for action in sorted(definition.actions, key=lambda item: item.key):
            if action.key not in action_keys:
                continue
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
            target_contracts = planner_target_contracts(
                definition,
                action,
                known_node_keys=known_node_keys,
                known_facts=known_fact_values,
                known_relation_keys=known_relation_keys,
                known_pool_keys=known_pool_keys,
            )
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


class PlanningActionCatalogBuilder:
    """Expose known, statically legal action bindings, including future steps."""

    def __init__(self, db: Session, scope: RuntimeScope) -> None:
        self.db = db
        self.scope = scope

    def build(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        *,
        task: AgentTask,
        replan_reason: str | None,
        planner_input: PlannerInput | None = None,
    ) -> tuple[PlanningActionCandidate, ...]:
        actors = tuple(
            self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == self.scope.game_instance_id,
                    GameInstanceActor.status == "ACTIVE",
                )
            )
        )
        node_states = tuple(
            self.db.scalars(
                select(GameInstanceNodeState).where(
                    GameInstanceNodeState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceNodeState.visibility == Visibility.KNOWN,
                )
            )
        )
        known_fact_refs = self.known_fact_refs()
        objective_refs = _objective_refs(objectives)
        successful_bindings = {
            (item.action_key, item.target_key)
            for item in self.db.scalars(
                select(WorldOperation).where(
                    WorldOperation.game_instance_id == self.scope.game_instance_id,
                    WorldOperation.task_id == task.id,
                    WorldOperation.status == WorldOperationStatus.RESOLVED,
                )
            )
            if isinstance(item.outcome, dict) and item.outcome.get("failure") is None
        }
        needed_refs = _unsatisfied_objective_refs(self.db, self.scope, objectives)
        public_prerequisites = _public_prerequisites(objectives)
        candidates: list[PlanningActionCandidate] = []
        canonical_contracts = (
            {item.action_key: item for item in planner_input.action_contracts}
            if planner_input is not None
            else None
        )
        canonical_bindings = (
            {(item.action_key, item.target_key): item for item in planner_input.target_bindings}
            if planner_input is not None
            else None
        )
        canonical_actors = (
            {item.actor_key: item for item in planner_input.actors}
            if planner_input is not None
            else None
        )
        canonical_nodes = (
            tuple(
                item for item in planner_input.known_world.nodes if isinstance(item.get("key"), str)
            )
            if planner_input is not None
            else None
        )
        actor_by_key = {item.actor_key: item for item in actors}
        target_rows: tuple[tuple[str, dict[str, object] | None, str], ...] = (
            tuple(
                (str(item["key"]), item, ActionTargetKind.NODE.value)
                for item in canonical_nodes or ()
                if isinstance(item.get("key"), str)
            )
            if canonical_nodes is not None
            else tuple((item.node_key, None, ActionTargetKind.NODE.value) for item in node_states)
        )
        target_rows += tuple(
            (actor_key, None, ActionTargetKind.ACTOR.value) for actor_key in sorted(actor_by_key)
        )
        binding_targets: dict[str, set[str]] = {}
        if planner_input is not None:
            for binding in planner_input.target_bindings:
                binding_targets.setdefault(binding.action_key, set()).add(binding.target_key)
        for target_key, target_projection, target_kind in sorted(
            target_rows, key=lambda item: (item[0], item[2])
        ):
            node_state = next(
                (item for item in node_states if item.node_key == target_key),
                None,
            )
            target = definition.world.node(target_key)
            target_actor = actor_by_key.get(target_key)
            if target_kind == ActionTargetKind.NODE.value and target is None:
                continue
            for action in sorted(definition.actions, key=lambda item: item.key):
                if action.target_kind.value != target_kind:
                    continue
                if canonical_contracts is not None and action.key not in canonical_contracts:
                    continue
                if action.key in binding_targets and target_key not in binding_targets[action.key]:
                    continue
                canonical_contract = (
                    canonical_contracts.get(action.key) if canonical_contracts is not None else None
                )
                if canonical_contracts is not None and canonical_contract is None:
                    continue
                canonical_binding = (
                    canonical_bindings.get((action.key, target_key))
                    if canonical_bindings is not None
                    else None
                )
                target_interaction = (
                    canonical_contract.target_contract.get("required_interaction_key")
                    if canonical_contract is not None
                    else action.required_interaction_key
                )
                if (
                    target_kind == ActionTargetKind.NODE.value
                    and target is not None
                    and isinstance(target_interaction, str)
                    and target_interaction not in target.interaction_keys
                ):
                    continue
                if target_kind == ActionTargetKind.ACTOR.value and target_actor is None:
                    continue
                visible_effects = tuple(
                    item
                    for item in (
                        *action.planning.terminal_effects,
                        *action.planning.supporting_effects,
                    )
                    if (item.node_key, item.fact_key) in known_fact_refs
                )
                if canonical_contract is not None:
                    canonical_effects = (
                        *canonical_contract.deterministic_effects,
                        *(
                            canonical_binding.deterministic_effects
                            if canonical_binding is not None
                            else ()
                        ),
                    )
                    projected_refs: set[tuple[str, str]] = set()
                    for effect in canonical_effects:
                        if effect.get("type") != "FACT_MUTATION":
                            continue
                        fact_key = effect.get("fact_key")
                        if not isinstance(fact_key, str):
                            continue
                        resolved_target = (
                            target_key
                            if effect.get("target") in {"target_key", "target_node"}
                            else effect.get("target")
                        )
                        if isinstance(resolved_target, str):
                            projected_refs.add((resolved_target, fact_key))
                else:
                    projected_refs = {(item.node_key, item.fact_key) for item in visible_effects}
                relevant = projected_refs & objective_refs
                if (
                    not relevant
                    and not action.planning.supporting_effects
                    and (
                        action.behavior == ActionBehavior.RULE
                        and action.locality == ActionLocality.NONE
                    )
                ):
                    continue
                for actor in sorted(actors, key=lambda item: item.actor_key):
                    if not _actor_can_execute(definition, actor, action.key):
                        continue
                    canonical_actor = (
                        canonical_actors.get(actor.actor_key)
                        if canonical_actors is not None
                        else None
                    )
                    if canonical_actors is not None and canonical_actor is None:
                        continue
                    if (
                        canonical_actor is not None
                        and action.key not in canonical_actor.allowed_action_keys
                    ):
                        continue
                    if (action.key, target_key) in successful_bindings and not (
                        projected_refs & needed_refs
                    ):
                        continue
                    if (
                        target_kind == ActionTargetKind.ACTOR.value
                        and action.behavior == ActionBehavior.RELAY_MESSAGE
                        and target_key == actor.actor_key
                    ):
                        continue
                    candidate_id = legal_candidate_id(action.key, actor.actor_key, target_key)
                    blockers = _known_blockers(
                        node_state.status if node_state is not None else NodeStatus.AVAILABLE,
                        projected_refs,
                        objectives,
                        needed_refs,
                    )
                    if canonical_actor is not None and canonical_contracts is not None:
                        required_reachability = canonical_contracts[
                            action.key
                        ].executor_requirements.get("command_reachability")
                        if (
                            required_reachability == "ONLINE"
                            and canonical_actor.command_reachability != "ONLINE"
                        ):
                            blockers = (*blockers, {"code": "ACTOR_COMMAND_DISCONNECTED"})
                        locality = canonical_contracts[action.key].locality.get("type")
                        canonical_target_actor = (
                            canonical_actors.get(target_key)
                            if target_kind == ActionTargetKind.ACTOR.value
                            and canonical_actors is not None
                            else None
                        )
                        target_region = (
                            canonical_target_actor.current_region
                            if canonical_target_actor is not None
                            else _canonical_node_region(target_projection, definition, target_key)
                        )
                        if (
                            locality
                            in {
                                "ACTOR_SAME_REGION",
                                "TARGET_SAME_REGION",
                                "FACILITY_REGION",
                                "LOCAL_TARGET",
                                "LOCAL_TARGET_FACILITY_OR_TRANSPORT",
                                "REGION",
                            }
                            and canonical_actor.current_region
                            and target_region
                            and (canonical_actor.current_region != target_region)
                        ):
                            blockers = (*blockers, {"code": "LOCALITY_INVALID"})
                        target_contract = canonical_contracts[action.key].target_contract
                        target_reachability = target_contract.get("command_reachability")
                        if (
                            isinstance(target_reachability, str)
                            and canonical_target_actor is not None
                            and canonical_target_actor.command_reachability != target_reachability
                        ):
                            blockers = (
                                *blockers,
                                {"code": "TARGET_COMMAND_REACHABILITY_INVALID"},
                            )
                    candidates.append(
                        PlanningActionCandidate(
                            candidate_id=candidate_id,
                            action_key=action.key,
                            action_name=action.name,
                            actor_key=actor.actor_key,
                            actor_name=actor.name,
                            target_key=target_key,
                            target_name=(
                                target.name
                                if target is not None
                                else (target_actor.name if target_actor is not None else target_key)
                            ),
                            target_kind=action.target_kind.value,
                            parameter_domain=tuple(
                                canonical_contract.parameters
                                if canonical_contract is not None
                                else tuple(
                                    item.model_dump(mode="json") for item in action.parameters
                                )
                            ),
                            public_effects=tuple(
                                {
                                    "kind": "DECLARED",
                                    "node_key": node_key,
                                    "fact_key": fact_key,
                                }
                                for node_key, fact_key in sorted(projected_refs)
                                if (node_key, fact_key) in known_fact_refs
                            ),
                            objective_relevance=tuple(
                                {"node_key": node_key, "fact_key": fact_key}
                                for node_key, fact_key in sorted(projected_refs & objective_refs)
                            ),
                            currently_executable=not blockers,
                            known_blockers=blockers,
                            public_prerequisites=public_prerequisites,
                            authority={
                                "actor_policy": actor.authority_policy,
                                "action_policy": action.authority_policy.model_dump(mode="json"),
                            },
                            action_behavior=action.behavior.value,
                            action_locality=action.locality.value,
                        )
                    )
        return tuple(candidates)

    def known_world(self, definition: ScenarioDefinitionV2) -> dict[str, object]:
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
                        {
                            "knowledge_status": region_summary["knowledge_status"],
                        }
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


def _group_resources(
    rows: tuple[GameInstanceResourceState, ...],
) -> dict[str, list[GameInstanceResourceState]]:
    grouped: dict[str, list[GameInstanceResourceState]] = {}
    for row in rows:
        grouped.setdefault(row.resource_key, []).append(row)
    return grouped


def _resource_context(rows: list[GameInstanceResourceState]) -> dict[str, object]:
    if len(rows) == 1 and rows[0].scope_node_key is None:
        row = rows[0]
        return {"value": row.value, "reserved": row.reserved_value}
    scopes: dict[str, dict[str, int]] = {}
    for row in sorted(rows, key=lambda item: item.scope_node_key or ""):
        scope_key = row.scope_node_key or "global"
        scopes[scope_key] = {"value": row.value, "reserved": row.reserved_value}
    return {"scopes": scopes}


def objective_context(
    objectives: tuple[ObjectiveDefinitionV2, ...],
    *,
    known_fact_refs: set[tuple[str, str]],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "key": objective.key,
            "name": objective.name,
            "description": objective.description,
            **(
                {"planning_guidance": objective.planning_guidance}
                if objective.planning_guidance is not None
                else {}
            ),
            "completion_requirements": [
                item.model_dump(mode="json")
                for item in objective.completion_requirements
                if (item.node_key, item.fact_key) in known_fact_refs
            ],
            "prerequisites": [
                {
                    **item.model_dump(mode="json", exclude={"requirements"}),
                    "requirements": [
                        requirement.model_dump(mode="json")
                        for requirement in item.requirements
                        if (requirement.node_key, requirement.fact_key) in known_fact_refs
                    ],
                }
                for item in objective.prerequisites
                if any(
                    (requirement.node_key, requirement.fact_key) in known_fact_refs
                    for requirement in item.requirements
                )
            ],
        }
        for objective in objectives
    )


def _objective_refs(
    objectives: tuple[ObjectiveDefinitionV2, ...],
) -> set[tuple[str, str]]:
    return {
        (item.node_key, item.fact_key)
        for objective in objectives
        for item in (
            *objective.completion_requirements,
            *(
                requirement
                for group in objective.prerequisites
                for requirement in group.requirements
            ),
        )
    }


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


def _unsatisfied_objective_refs(
    db: Session,
    scope: RuntimeScope,
    objectives: tuple[ObjectiveDefinitionV2, ...],
) -> set[tuple[str, str]]:
    needed: set[tuple[str, str]] = set()
    for objective in objectives:
        requirements = (
            *objective.completion_requirements,
            *(item for group in objective.prerequisites for item in group.requirements),
        )
        for requirement in requirements:
            state = db.get(
                GameInstanceFactState,
                (scope.game_instance_id, requirement.node_key, requirement.fact_key),
            )
            if (
                state is None
                or state.visibility != Visibility.KNOWN
                or state.truth_value not in requirement.accepted_values
            ):
                needed.add((requirement.node_key, requirement.fact_key))
    return needed


def _public_prerequisites(
    objectives: tuple[ObjectiveDefinitionV2, ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "objective_key": objective.key,
            "key": group.key,
            "description": group.description,
            "requirements": [item.model_dump(mode="json") for item in group.requirements],
        }
        for objective in objectives
        for group in objective.prerequisites
    )


def _known_blockers(
    access: NodeStatus,
    projected_refs: set[tuple[str, str]],
    objectives: tuple[ObjectiveDefinitionV2, ...],
    needed_refs: set[tuple[str, str]],
) -> tuple[dict[str, object], ...]:
    blockers: list[dict[str, object]] = []
    if access == NodeStatus.LOCKED:
        blockers.append({"code": "TARGET_CURRENTLY_LOCKED"})
    completion_refs = {
        (item.node_key, item.fact_key)
        for objective in objectives
        for item in objective.completion_requirements
    }
    if projected_refs & completion_refs:
        for objective in objectives:
            for group in objective.prerequisites:
                unmet = [
                    item.model_dump(mode="json")
                    for item in group.requirements
                    if (item.node_key, item.fact_key) in needed_refs
                ]
                if unmet:
                    blockers.append(
                        {
                            "code": "PUBLIC_PREREQUISITE_UNSATISFIED",
                            "prerequisite_key": group.key,
                            "requirements": unmet,
                        }
                    )
    return tuple(blockers)


def _actor_can_execute(
    definition: ScenarioDefinitionV2,
    actor: GameInstanceActor,
    action_key: str,
) -> bool:
    action = next((item for item in definition.actions if item.key == action_key), None)
    return bool(
        action is not None
        and actor_binding_matches(definition, actor)
        and action.key in actor.allowed_action_keys
        and {item.value for item in action.allowed_actor_capabilities}.issubset(
            set(actor.capabilities)
        )
    )


def legal_candidate_id(action_key: str, actor_key: str, target_key: str) -> str:
    digest = hashlib.sha256(f"{action_key}|{actor_key}|{target_key}".encode()).hexdigest()[:12]
    return f"candidate_{digest}"


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


def _canonical_node_region(
    projection: dict[str, object] | None,
    definition: ScenarioDefinitionV2,
    target_key: str,
) -> str | None:
    if isinstance(projection, dict) and isinstance(projection.get("region_key"), str):
        return str(projection["region_key"])
    locality = definition.metadata.locality
    target = definition.world.node(target_key)
    if target is not None and target.node_type_key == locality.region_node_type_key:
        return target.key
    return None


__all__ = [
    "PlanningActionCatalogBuilder",
    "PlanningContext",
    "PlanningContextBuilder",
    "PlanningContinuityBuilder",
    "legal_candidate_id",
    "objective_context",
]
