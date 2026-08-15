"""Knowledge-safe PlanningContext V1 and the legacy catalog compatibility view."""

from __future__ import annotations

import hashlib
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import actor_binding_matches
from app.agent.provider import PlanningActionCandidate, PlanningContext
from app.domain.enums import NodeStatus, WorldOperationStatus
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import ObjectiveDefinitionV2, ScenarioDefinitionV2
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

PlanningContextV1 = PlanningContext


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
        relevant_action_keys = self._retrieve_action_keys(definition, objectives, known_refs)
        relevant_targets = self._targets(definition, relevant_action_keys, known_world)
        relevant_actions = self._actions(definition, objectives, relevant_action_keys, known_refs)
        relevant_actors = self._actors(definition, relevant_action_keys)
        return PlanningContext(
            goal=self._goal(definition, objectives, known_refs),
            current_knowledge={
                **known_world,
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
            },
        )

    build_context = build

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
                if not visible_effects:
                    continue
                if not any(
                    node.key in known_nodes
                    and action.required_interaction_key in node.interaction_keys
                    for node in definition.world.nodes
                ):
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
            if any(
                node.key in known_nodes
                and action.required_interaction_key in node.interaction_keys
                and any(
                    (effect.node_key, effect.fact_key) in known_refs
                    for effect in (
                        *action.planning.terminal_effects,
                        *action.planning.supporting_effects,
                    )
                )
                for node in definition.world.nodes
            ):
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
                }
                for item in objectives
            ],
            "desired_state": completion,
            "completion_requirements": completion,
            "public_prerequisites": prerequisites,
        }

    def _actions(
        self,
        definition: ScenarioDefinitionV2,
        objectives: tuple[ObjectiveDefinitionV2, ...],
        action_keys: set[str],
        known_refs: set[tuple[str, str]],
    ) -> list[dict[str, object]]:
        objective_refs = _objective_refs(objectives)
        objective_nodes = {node_key for node_key, _fact_key in objective_refs}
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
            result.append(
                {
                    "action_key": action.key,
                    "display": action.name,
                    "description": action.description,
                    "declared_world_effects": terminal,
                    "declared_knowledge_effects": supporting,
                    "objective_relevance": relevance,
                    "public_prerequisites": [],
                    "target_requirements": {
                        "required_interaction_key": action.required_interaction_key,
                    },
                    "parameter_schema": [
                        item.model_dump(mode="json") for item in action.parameters
                    ],
                    "parameter_defaults": {
                        item.key: item.default
                        for item in action.parameters
                        if item.default is not None
                    },
                    "hard_constraints": {
                        "required_actor_capabilities": [
                            item.value for item in action.allowed_actor_capabilities
                        ],
                        "static_authority": action.authority_policy.model_dump(mode="json"),
                    },
                    "cost_risk": {},
                    "soft_signals": {"hints": list(action.planning.hints)},
                    "execution_mode": action.execution_mode.value,
                }
            )
        return result

    def _actors(
        self,
        definition: ScenarioDefinitionV2,
        action_keys: set[str],
    ) -> list[dict[str, object]]:
        roles = {item.key: item for item in definition.actors.roles}
        actors = self.db.scalars(
            select(GameInstanceActor).where(
                GameInstanceActor.game_instance_id == self.scope.game_instance_id,
                GameInstanceActor.status == "ACTIVE",
            )
        )
        result: list[dict[str, object]] = []
        for actor in sorted(actors, key=lambda item: item.actor_key):
            if not set(actor.allowed_action_keys).intersection(action_keys):
                continue
            role = roles.get(actor.role_key)
            result.append(
                {
                    "actor_key": actor.actor_key,
                    "display": actor.name,
                    "role_key": actor.role_key,
                    "role_display": role.name if role is not None else actor.role_key,
                    "capabilities": list(actor.capabilities),
                    "static_authority": actor.authority_policy,
                    "current_known_state": {
                        "availability": actor.status,
                        "current_node_key": actor.current_node_key,
                    },
                    "allowed_action_keys": [
                        key for key in actor.allowed_action_keys if key in action_keys
                    ],
                    "cost_risk": {},
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
            if action.key in action_keys
        }
        raw_nodes = known_world.get("nodes", [])
        node_rows = cast(list[dict[str, object]], raw_nodes) if isinstance(raw_nodes, list) else []
        known_nodes = {item["key"]: item for item in node_rows if isinstance(item.get("key"), str)}
        raw_facts = known_world.get("facts", {})
        facts = cast(dict[str, object], raw_facts) if isinstance(raw_facts, dict) else {}
        result: list[dict[str, object]] = []
        for node in sorted(definition.world.nodes, key=lambda item: item.key):
            if node.key not in known_nodes or not set(node.interaction_keys).intersection(
                interaction_keys
            ):
                continue
            node_facts = {
                key.split(".", 1)[1]: value
                for key, value in facts.items()
                if isinstance(key, str) and key.startswith(f"{node.key}.")
            }
            result.append(
                {
                    "target_key": node.key,
                    "display": node.name,
                    "type": node.node_type_key,
                    "current_known_state": {
                        "access": known_nodes[node.key].get("access"),
                        "facts": node_facts,
                    },
                    "affordances": list(node.interaction_keys),
                    "public_relationships": [
                        item.model_dump(mode="json")
                        for item in definition.world.relations
                        if (
                            (item.source_node_key == node.key or item.target_node_key == node.key)
                            and item.source_node_key in known_nodes
                            and item.target_node_key in known_nodes
                        )
                    ],
                }
            )
        return result

    def _previous_execution(self, task: AgentTask, replan_reason: str | None) -> dict[str, object]:
        plan = self.db.scalar(
            select(AgentPlan).where(AgentPlan.task_id == task.id).order_by(AgentPlan.version.desc())
        )
        completed: list[dict[str, object]] = []
        failed: dict[str, object] | None = None
        player_visible_result: dict[str, object] | None = None
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
            failure = outcome.get("failure")
            if isinstance(failure, dict):
                player_visible_result = {
                    "outcome_code": outcome.get("outcome_code"),
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
        return {
            "previous_plan_summary": plan.strategy_summary if plan is not None else None,
            "previous_plan_version": plan.version if plan is not None else None,
            "failed_or_current_step": failed,
            "player_visible_result": player_visible_result or failed,
            "newly_learned_knowledge": [],
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
        for node_state in sorted(node_states, key=lambda item: item.node_key):
            target = definition.world.node(node_state.node_key)
            if target is None:
                continue
            for action in sorted(definition.actions, key=lambda item: item.key):
                if action.required_interaction_key not in target.interaction_keys:
                    continue
                visible_effects = tuple(
                    item
                    for item in (
                        *action.planning.terminal_effects,
                        *action.planning.supporting_effects,
                    )
                    if (item.node_key, item.fact_key) in known_fact_refs
                )
                relevant = tuple(
                    item
                    for item in visible_effects
                    if (item.node_key, item.fact_key) in objective_refs
                )
                if not relevant and not action.planning.supporting_effects:
                    continue
                for actor in sorted(actors, key=lambda item: item.actor_key):
                    if not _actor_can_execute(definition, actor, action.key):
                        continue
                    projected_refs = {(item.node_key, item.fact_key) for item in visible_effects}
                    if (action.key, target.key) in successful_bindings and not (
                        projected_refs & needed_refs
                    ):
                        continue
                    candidate_id = legal_candidate_id(action.key, actor.actor_key, target.key)
                    blockers = _known_blockers(
                        node_state.status,
                        projected_refs,
                        objectives,
                        needed_refs,
                    )
                    candidates.append(
                        PlanningActionCandidate(
                            candidate_id=candidate_id,
                            action_key=action.key,
                            action_name=action.name,
                            actor_key=actor.actor_key,
                            actor_name=actor.name,
                            target_key=target.key,
                            target_name=target.name,
                            parameter_domain=tuple(
                                item.model_dump(mode="json") for item in action.parameters
                            ),
                            public_effects=tuple(
                                {
                                    "kind": (
                                        "TERMINAL"
                                        if item in action.planning.terminal_effects
                                        else "SUPPORTING"
                                    ),
                                    "node_key": item.node_key,
                                    "fact_key": item.fact_key,
                                }
                                for item in visible_effects
                            ),
                            objective_relevance=tuple(
                                {"node_key": item.node_key, "fact_key": item.fact_key}
                                for item in relevant
                            ),
                            currently_executable=not blockers,
                            known_blockers=blockers,
                            public_prerequisites=public_prerequisites,
                            authority={
                                "actor_policy": actor.authority_policy,
                                "action_policy": action.authority_policy.model_dump(mode="json"),
                            },
                        )
                    )
        return tuple(candidates)

    def known_world(self, definition: ScenarioDefinitionV2) -> dict[str, object]:
        node_states = tuple(
            self.db.scalars(
                select(GameInstanceNodeState).where(
                    GameInstanceNodeState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceNodeState.visibility == Visibility.KNOWN,
                )
            )
        )
        known_keys = {item.node_key for item in node_states}
        facts = tuple(
            self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceFactState.visibility == Visibility.KNOWN,
                )
            )
        )
        resources = tuple(
            self.db.scalars(
                select(GameInstanceResourceState).where(
                    GameInstanceResourceState.game_instance_id == self.scope.game_instance_id
                )
            )
        )
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
            "relations": [
                item.model_dump(mode="json")
                for item in definition.world.relations
                if item.source_node_key in known_keys and item.target_node_key in known_keys
            ],
            "resources": {
                item.resource_key: {"value": item.value, "reserved": item.reserved_value}
                for item in resources
            },
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
        "access": state.status.value,
        "interactions": list(node.interaction_keys) if node is not None else [],
    }


__all__ = [
    "PlanningActionCatalogBuilder",
    "PlanningContext",
    "PlanningContextBuilder",
    "PlanningContextV1",
    "legal_candidate_id",
    "objective_context",
]
