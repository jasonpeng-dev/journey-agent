"""Typed, bounded Objective dependency retrieval for Planner Input V2."""

from __future__ import annotations

import ast
import hashlib
import json
from collections import deque
from dataclasses import dataclass
from itertools import product
from typing import Any

from app.agent.provider import (
    PlannerActionContract,
    PlannerActorState,
    PlannerInput,
    PlannerKnownWorldSlice,
    PlannerTargetBinding,
)
from app.domain.enums import ResourceInventoryVisibility
from app.domain.scenario_v2 import ObjectiveDefinitionV2, ScenarioDefinitionV2
from app.services.knowledge_projection import resource_knowledge_status


class DependencyClosureError(ValueError):
    """Raised when a valid dependency expansion exceeds an explicit bound."""


@dataclass(frozen=True)
class TypedDependency:
    dimension: str
    subject: str
    key: str = ""
    required: str | int = ""


@dataclass(frozen=True)
class DependencyClosureResult:
    planner_input: PlannerInput
    relevance_reason: dict[str, tuple[dict[str, object], ...]]


def _scope_actor_actions_to_contracts(
    actors: tuple[PlannerActorState, ...],
    action_contracts: tuple[PlannerActionContract, ...],
) -> tuple[PlannerActorState, ...]:
    """Project global Actor permissions onto the exposed closure vocabulary."""

    exposed_action_keys = {item.action_key for item in action_contracts}
    return tuple(
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
    )


def _relation_is_public(relation: dict[str, object]) -> bool:
    for key in ("public", "is_public"):
        if key in relation and relation[key] is not True:
            return False
    for key in ("visibility", "relation_visibility"):
        if key not in relation:
            continue
        value = getattr(relation[key], "value", relation[key])
        if not isinstance(value, str) or value.upper() != "VISIBLE":
            return False
    return True


def _public_source_candidates(
    contract: PlannerActionContract,
    target_key: str,
    planner_input: PlannerInput,
) -> tuple[tuple[str, dict[str, object]], ...]:
    relation_type = contract.source_relation_type_key
    if not isinstance(relation_type, str):
        return ()
    known_nodes = {
        str(item["key"])
        for item in planner_input.known_world.nodes
        if isinstance(item.get("key"), str)
    }
    candidates: list[tuple[str, dict[str, object]]] = []
    for relation in planner_input.known_world.relations:
        source_key = relation.get("source_node_key")
        if (
            not isinstance(source_key, str)
            or relation.get("target_node_key") != target_key
            or relation.get("relation_type_key") != relation_type
            or source_key not in known_nodes
            or target_key not in known_nodes
            or not _relation_is_public(relation)
        ):
            continue
        candidates.append((source_key, relation))
    return tuple(
        sorted(
            candidates,
            key=lambda item: (item[0], str(item[1].get("relation_key", ""))),
        )
    )


def _source_condition_status(
    condition: dict[str, object],
    source_key: str,
    known_facts: dict[str, object],
) -> bool | None:
    kind = condition.get("kind")
    if kind in {"ALL", "ANY"}:
        raw_children = condition.get("conditions")
        if not isinstance(raw_children, (list, tuple)):
            return None
        children = [item for item in raw_children if isinstance(item, dict)]
        if len(children) != len(raw_children) or not children:
            return None
        statuses = [_source_condition_status(item, source_key, known_facts) for item in children]
        if kind == "ALL":
            if any(status is False for status in statuses):
                return False
            if all(status is True for status in statuses):
                return True
            return None
        if any(status is True for status in statuses):
            return True
        if all(status is False for status in statuses):
            return False
        return None
    if kind == "NOT":
        child = condition.get("condition")
        if not isinstance(child, dict):
            return None
        status = _source_condition_status(child, source_key, known_facts)
        return None if status is None else not status
    selector = condition.get("selector")
    if selector not in {None, "ACTION_SOURCE"}:
        return None
    fact_key = condition.get("fact_key")
    if not isinstance(fact_key, str):
        return None
    identity = f"{source_key}.{fact_key}"
    if identity not in known_facts:
        return None
    current = known_facts[identity]
    if kind == "FACT_EQUALS":
        return current == condition.get("value")
    if kind == "FACT_NOT_EQUALS":
        return current != condition.get("value")
    if kind == "FACT_IN":
        values = condition.get("values")
        return isinstance(values, (list, tuple)) and current in values
    if kind == "FACT_COMPARE":
        operator = condition.get("operator")
        expected = condition.get("value")
        left: Any = current
        right: Any = expected
        try:
            if operator == "EQ":
                return bool(left == right)
            if operator == "NE":
                return bool(left != right)
            if operator == "LT":
                return bool(left < right)
            if operator == "LTE":
                return bool(left <= right)
            if operator == "GT":
                return bool(left > right)
            if operator == "GTE":
                return bool(left >= right)
        except TypeError:
            return None
    return None


def _source_predicate(condition: dict[str, object]) -> dict[str, object] | None:
    kind = condition.get("kind")
    if kind not in {
        "FACT_EQUALS",
        "FACT_NOT_EQUALS",
        "FACT_IN",
        "FACT_COMPARE",
    }:
        return None
    selector = condition.get("selector")
    if selector not in {None, "ACTION_SOURCE"}:
        return None
    fact_key = condition.get("fact_key")
    if not isinstance(fact_key, str):
        return None
    predicate: dict[str, object] = {"kind": kind, "fact_key": fact_key}
    for key in ("value", "values", "operator"):
        if key in condition:
            predicate[key] = condition[key]
    return predicate


def _source_alternative_product(
    groups: list[tuple[tuple[dict[str, object], ...], ...]],
) -> tuple[tuple[dict[str, object], ...], ...]:
    combined: tuple[tuple[dict[str, object], ...], ...] = ((),)
    for alternatives in groups:
        if not alternatives:
            return ()
        combined = tuple(prefix + suffix for prefix, suffix in product(combined, alternatives))
    return combined


def _source_condition_true_alternatives(
    condition: dict[str, object],
) -> tuple[tuple[dict[str, object], ...], ...]:
    kind = condition.get("kind")
    if kind in {
        "FACT_EQUALS",
        "FACT_NOT_EQUALS",
        "FACT_IN",
        "FACT_COMPARE",
    }:
        predicate = _source_predicate(condition)
        return ((predicate,),) if predicate is not None else ()
    if kind in {"ALL", "ANY"}:
        raw_children = condition.get("conditions")
        if not isinstance(raw_children, (list, tuple)):
            return ()
        groups = [
            _source_condition_true_alternatives(item)
            for item in raw_children
            if isinstance(item, dict)
        ]
        if len(groups) != len(raw_children):
            return ()
        if kind == "ALL":
            return _source_alternative_product(groups)
        return tuple(alternative for group in groups for alternative in group)
    if kind == "NOT":
        child = condition.get("condition")
        return _source_condition_false_alternatives(child) if isinstance(child, dict) else ()
    return ()


def _source_condition_false_alternatives(
    condition: dict[str, object],
) -> tuple[tuple[dict[str, object], ...], ...]:
    kind = condition.get("kind")
    if kind in {
        "FACT_EQUALS",
        "FACT_NOT_EQUALS",
        "FACT_IN",
        "FACT_COMPARE",
    }:
        predicate = _source_predicate(condition)
        if predicate is None:
            return ()
        if kind == "FACT_NOT_EQUALS":
            complement = dict(predicate)
            complement["kind"] = "FACT_EQUALS"
            return ((complement,),)
        if kind == "FACT_COMPARE" and condition.get("operator") == "NE":
            complement = dict(predicate)
            complement["kind"] = "FACT_EQUALS"
            complement.pop("operator", None)
            return ((complement,),)
        return ()
    if kind in {"ALL", "ANY"}:
        raw_children = condition.get("conditions")
        if not isinstance(raw_children, (list, tuple)):
            return ()
        groups = [
            _source_condition_false_alternatives(item)
            for item in raw_children
            if isinstance(item, dict)
        ]
        if len(groups) != len(raw_children):
            return ()
        if kind == "ALL":
            return tuple(alternative for group in groups for alternative in group)
        return _source_alternative_product(groups)
    if kind == "NOT":
        child = condition.get("condition")
        return _source_condition_true_alternatives(child) if isinstance(child, dict) else ()
    return ()


def _source_fact_satisfies(
    predicate: dict[str, object],
    source_key: str,
    known_facts: dict[str, object],
) -> bool:
    fact_key = predicate.get("fact_key")
    if not isinstance(fact_key, str):
        return False
    identity = f"{source_key}.{fact_key}"
    if identity not in known_facts:
        return False
    current = known_facts[identity]
    if predicate.get("kind") == "FACT_EQUALS":
        return current == predicate.get("value")
    if predicate.get("kind") == "FACT_IN":
        values = predicate.get("values")
        return isinstance(values, (list, tuple)) and current in values
    return False


def _source_precondition_dependencies(
    contract: PlannerActionContract,
    source_key: str,
    planner_input: PlannerInput,
) -> tuple[TypedDependency, ...]:
    known_facts = planner_input.known_world.facts
    dependencies: list[TypedDependency] = []
    seen: set[TypedDependency] = set()
    for entry in contract.source_preconditions:
        failure_condition = entry.get("failure_condition")
        if not isinstance(failure_condition, dict):
            continue
        status = _source_condition_status(failure_condition, source_key, known_facts)
        if status is False:
            continue
        for alternative in _source_condition_false_alternatives(failure_condition):
            for predicate in alternative:
                fact_key = predicate.get("fact_key")
                kind = predicate.get("kind")
                if not isinstance(fact_key, str) or kind not in {
                    "FACT_EQUALS",
                    "FACT_IN",
                }:
                    continue
                if _source_fact_satisfies(predicate, source_key, known_facts):
                    continue
                values: tuple[object, ...]
                if kind == "FACT_EQUALS":
                    values = (predicate.get("value"),)
                else:
                    raw_values = predicate.get("values")
                    if not isinstance(raw_values, (list, tuple)) or not raw_values:
                        continue
                    values = tuple(raw_values)
                dependency = TypedDependency(
                    "FACT",
                    source_key,
                    fact_key,
                    repr(values),
                )
                if dependency not in seen:
                    seen.add(dependency)
                    dependencies.append(dependency)
    return tuple(dependencies)


def build_dependency_closure(
    definition: ScenarioDefinitionV2,
    objectives: tuple[ObjectiveDefinitionV2, ...],
    planner_input: PlannerInput,
    *,
    dependency_limit: int = 128,
    action_limit: int = 64,
) -> DependencyClosureResult:
    """Return a fixed-point slice; never choose bindings or order Plan steps."""

    contracts = {item.action_key: item for item in planner_input.action_contracts}
    bindings = {(item.action_key, item.target_key): item for item in planner_input.target_bindings}
    queue: deque[tuple[TypedDependency, tuple[str, ...], str | None]] = deque()
    for objective in objectives:
        requirements = (
            *objective.completion_requirements,
            *(
                requirement
                for group in objective.prerequisites
                for requirement in group.requirements
            ),
        )
        for requirement in requirements:
            queue.append(
                (
                    TypedDependency(
                        "FACT",
                        requirement.node_key,
                        requirement.fact_key,
                        repr(tuple(requirement.accepted_values)),
                    ),
                    (f"objective:{objective.key}", f"requirement:{requirement.key}"),
                    None,
                )
            )

    visited: set[TypedDependency] = set()
    selected_actions: set[str] = set()
    selected_bindings: set[tuple[str, str]] = set()
    relevant_nodes = {
        requirement.node_key
        for objective in objectives
        for requirement in (
            *objective.completion_requirements,
            *(item for group in objective.prerequisites for item in group.requirements),
        )
    }
    relevant_resources: set[str] = set()
    unknowns: dict[str, dict[str, object]] = {}
    audit: dict[str, list[dict[str, object]]] = {}
    selected_actor_targets: set[str] = set()
    selected_diagnostic_actor_keys: set[str] = set()
    expanded_source_targets: set[tuple[str, str]] = set()
    queued_resource_dependencies: set[TypedDependency] = set()
    resource_demand_records: set[tuple[TypedDependency, str, str, str | None]] = set()
    resource_demand: dict[str, dict[tuple[str, str, str | None], int]] = {}
    state_changed = False

    def _dependency_demand_group(dependency: TypedDependency) -> str:
        """Identify one conjunctive dependency requirement for private demand accounting."""

        return repr((dependency.dimension, dependency.subject, dependency.key, dependency.required))

    def _resource_demand_total(resource_key: str, fallback: int) -> int:
        """Aggregate resource demand without summing alternative producer branches."""

        entries = resource_demand.get(resource_key)
        if not entries:
            return fallback
        grouped: dict[
            str,
            tuple[dict[str, int], dict[tuple[str, str], int]],
        ] = {}
        for (group, action_key, binding_target_key), amount in entries.items():
            action_demands, binding_demands = grouped.setdefault(group, ({}, {}))
            if binding_target_key is None:
                action_demands[action_key] = action_demands.get(action_key, 0) + amount
            else:
                binding_key = (action_key, binding_target_key)
                binding_demands[binding_key] = binding_demands.get(binding_key, 0) + amount

        total = 0
        for action_demands, binding_demands in grouped.values():
            branch_totals: list[int] = []
            action_keys = set(action_demands)
            action_keys.update(action_key for action_key, _ in binding_demands)
            for action_key in action_keys:
                action_amount = action_demands.get(action_key, 0)
                binding_branches = [
                    amount
                    for (binding_action, _target_key), amount in binding_demands.items()
                    if binding_action == action_key
                ]
                if binding_branches:
                    branch_totals.extend(action_amount + amount for amount in binding_branches)
                else:
                    branch_totals.append(action_amount)
            total += max(branch_totals, default=0)
        return max(fallback, total)

    def select_action(
        action_key: str,
        path: tuple[str, ...],
        producer_for: str,
        *,
        demand_group: str | None = None,
    ) -> bool:
        demand_group = demand_group or repr(("ACTION", action_key))
        if action_key in selected_actions:
            contract = contracts.get(action_key)
            if contract is not None:
                _queue_action_resource_dependencies(
                    contract,
                    path,
                    action_key,
                    demand_group,
                )
            return False
        if len(selected_actions) >= action_limit:
            raise DependencyClosureError(
                f"dependency closure action bound {action_limit} exceeded at {action_key}; "
                f"path={' -> '.join(path)}"
            )
        selected_actions.add(action_key)
        audit.setdefault(action_key, []).append(
            {"producer_for": producer_for, "dependency_path": list(path)}
        )
        contract = contracts.get(action_key)
        if contract is None:
            return True
        if not action_has_legal_executor(contract):
            diagnostic_actors = _capability_diagnostic_actor_keys(contract, planner_input)
            if diagnostic_actors:
                selected_diagnostic_actor_keys.update(diagnostic_actors)
                return True
            selected_actions.remove(action_key)
            entries = audit.get(action_key)
            if entries:
                entries.pop()
                if not entries:
                    audit.pop(action_key, None)
            return False
        reachability = contract.executor_requirements.get("command_reachability")
        eligible_actors = [
            actor for actor in planner_input.actors if _actor_matches_executor(actor, contract)
        ]
        if reachability == "ONLINE":
            online_actors = [
                actor for actor in eligible_actors if actor.command_reachability == "ONLINE"
            ]
            if not online_actors:
                for actor_state in sorted(eligible_actors, key=lambda item: item.actor_key):
                    if not _has_public_reachability_producer(
                        definition,
                        planner_input,
                        contracts,
                        bindings,
                        actor_state.actor_key,
                        seen_actions=frozenset({action_key}),
                    ):
                        continue
                    queue.append(
                        (
                            TypedDependency(
                                "ACTOR_COMMAND_REACHABILITY",
                                actor_state.actor_key,
                                required="ONLINE",
                            ),
                            (*path, f"action:{action_key}", f"executor:{actor_state.actor_key}"),
                            action_key,
                        )
                    )
                    break

        locality = contract.locality.get("type")
        # A locality contract is a dependency on the executor's current
        # Region, not a choice of executor, destination, or route. Keep a
        # public Actor-location producer (normally one-hop Travel) in the
        # closure whenever an action may need to be performed at a different
        # Region. The closure deliberately does not select a binding or
        # synthesize a relocation step.
        if locality in {
            "ACTOR_SAME_REGION",
            "TARGET_SAME_REGION",
            "ACTOR_REGION",
            "REGION",
            "FACILITY_REGION",
            "TRANSPORT_ENDPOINT",
            "LOCAL_TARGET",
            "LOCAL_TARGET_FACILITY_OR_TRANSPORT",
            "ONE_HOP_TRANSPORT",
        }:
            queue.append(
                (
                    TypedDependency("ACTOR_LOCATION", "executor", required="SAME_REGION"),
                    (*path, f"action:{action_key}", "locality:SAME_REGION"),
                    action_key,
                )
            )
        # A selected Action may have a public, known preflight contradiction
        # (for example, a route is still ACTIVE when the next Action requires
        # it to be DISRUPTED).  Expose deterministic public producers for that
        # contradiction without choosing a target/actor or synthesizing a
        # recovery step.  This is deliberately limited to concrete FACT
        # mutations already present in canonical Action contracts.
        for precondition in contract.known_preconditions:
            if not _known_precondition_is_true(precondition):
                continue
            node_key = precondition.get("node_key")
            fact_key = precondition.get("fact_key")
            current_value = precondition.get("current_value")
            if not isinstance(node_key, str) or not isinstance(fact_key, str):
                continue
            # The contradiction witness is public Known state; retain its
            # node so a selected producer has a legal public target context.
            relevant_nodes.add(node_key)
            for producer_key, producer in contracts.items():
                for effect in producer.deterministic_effects:
                    if effect.get("type") != "FACT_MUTATION" or effect.get("fact_key") != fact_key:
                        continue
                    target = effect.get("target")
                    if target not in {"target_key", "target_node", node_key}:
                        continue
                    effect_value = effect.get("value")
                    if isinstance(effect_value, dict):
                        literal = effect_value.get("literal")
                        if literal is None or literal == current_value:
                            continue
                        effect_value = literal
                    if effect_value == current_value:
                        continue
                    select_action(
                        producer_key,
                        (*path, f"known_precondition:{node_key}.{fact_key}"),
                        repr(TypedDependency("FACT", node_key, fact_key)),
                        demand_group=demand_group,
                    )
                    break

        # Resource requirements declared by the canonical Action contract are
        # source-agnostic. Queue the knowledge dependency without selecting a
        # source Region; the Planner may choose any public acquisition target.
        _queue_action_resource_dependencies(contract, path, action_key, demand_group)
        return True

    def select_binding(
        binding_key: tuple[str, str],
        path: tuple[str, ...],
        producer_for: str,
        *,
        demand_group: str | None = None,
    ) -> bool:
        """Select and expand one Action/Target binding exactly once."""

        binding = bindings.get(binding_key)
        if binding is None or binding_key in selected_bindings:
            return False
        action_key, target_key = binding_key
        demand_group = demand_group or repr(("BINDING", action_key, target_key))
        if action_key not in selected_actions and not select_action(
            action_key,
            path,
            producer_for,
            demand_group=demand_group,
        ):
            return False
        selected_bindings.add(binding_key)
        audit.setdefault(action_key, []).append(
            {
                "producer_for": producer_for,
                "dependency_path": list(path),
                "target_binding": target_key,
            }
        )
        relevant_nodes.add(target_key)
        _queue_binding_resource_dependencies(binding, path, action_key, demand_group)
        expand_source_dependencies(contracts[action_key], target_key, path)
        return True

    def action_has_legal_executor(contract: PlannerActionContract) -> bool:
        return _has_legal_executor(
            definition,
            planner_input,
            contracts,
            bindings,
            contract,
        )

    def _queue_resource_dependency(
        resource_key: str,
        required_amount: int,
        target_key: str,
        path: tuple[str, ...],
        action_key: str,
        demand_group: str,
        binding_target_key: str | None = None,
    ) -> None:
        relevant_resources.add(resource_key)
        dependency = TypedDependency(
            "RESOURCE_SOURCE",
            resource_key,
            key=target_key,
            required=required_amount,
        )
        demand_key = (dependency, demand_group, action_key, binding_target_key)
        if demand_key not in resource_demand_records:
            resource_demand_records.add(demand_key)
            demands = resource_demand.setdefault(resource_key, {})
            aggregate_key = (demand_group, action_key, binding_target_key)
            demands[aggregate_key] = demands.get(aggregate_key, 0) + required_amount
        if dependency in queued_resource_dependencies:
            return
        queued_resource_dependencies.add(dependency)
        queue.append(
            (
                dependency,
                (*path, f"action:{action_key}", f"resource:{resource_key}"),
                action_key,
            )
        )

    def _queue_action_resource_dependencies(
        contract: PlannerActionContract,
        path: tuple[str, ...],
        action_key: str,
        demand_group: str,
    ) -> None:
        for effect in contract.deterministic_effects:
            resource_key = effect.get("resource_key")
            amount = effect.get("amount")
            if (
                effect.get("type") not in {"RESOURCE_DELTA", "RESOURCE_CONSUMPTION"}
                or not isinstance(resource_key, str)
                or isinstance(amount, bool)
                or not isinstance(amount, int)
                or amount >= 0
            ):
                continue
            _queue_resource_dependency(
                resource_key,
                -amount,
                "",
                path,
                action_key,
                demand_group,
            )

    def _queue_binding_resource_dependencies(
        binding: PlannerTargetBinding,
        path: tuple[str, ...],
        action_key: str,
        demand_group: str,
    ) -> None:
        """Queue source knowledge for one selected target binding only."""

        for requirement in binding.requirements:
            cost = requirement.get("cost")
            if not isinstance(cost, dict):
                continue
            for resource_key, required_amount in cost.items():
                if (
                    not isinstance(resource_key, str)
                    or isinstance(required_amount, bool)
                    or not isinstance(required_amount, int)
                    or required_amount <= 0
                ):
                    continue
                _queue_resource_dependency(
                    resource_key,
                    required_amount,
                    binding.target_key,
                    path,
                    action_key,
                    demand_group,
                    binding.target_key,
                )

        for effect in binding.deterministic_effects:
            resource_key = effect.get("resource_key")
            amount = effect.get("amount")
            if (
                effect.get("type") not in {"RESOURCE_DELTA", "RESOURCE_CONSUMPTION"}
                or not isinstance(resource_key, str)
                or isinstance(amount, bool)
                or not isinstance(amount, int)
                or amount >= 0
            ):
                continue
            _queue_resource_dependency(
                resource_key,
                -amount,
                binding.target_key,
                path,
                action_key,
                demand_group,
                binding.target_key,
            )

    def expand_source_dependencies(
        contract: PlannerActionContract,
        target_key: str,
        path: tuple[str, ...],
    ) -> None:
        relation_type = contract.source_relation_type_key
        if not isinstance(relation_type, str):
            return
        expansion_key = (contract.action_key, target_key)
        if expansion_key in expanded_source_targets:
            return
        expanded_source_targets.add(expansion_key)
        for source_key, relation in _public_source_candidates(
            contract,
            target_key,
            planner_input,
        ):
            relation_path = (
                *path,
                f"action:{contract.action_key}",
                f"source_relation:{relation_type}",
                f"source:{source_key}",
            )
            relevant_nodes.add(source_key)
            audit.setdefault(contract.action_key, []).append(
                {
                    "producer_for": f"{contract.action_key}:{target_key}",
                    "dependency_path": list(relation_path),
                    "source_candidate": source_key,
                    "relation_key": relation.get("relation_key"),
                }
            )
            for dependency in _source_precondition_dependencies(
                contract,
                source_key,
                planner_input,
            ):
                queue.append((dependency, relation_path, contract.action_key))

    def process_dependency(
        dependency: TypedDependency,
        path: tuple[str, ...],
        consumer_action: str | None,
    ) -> None:
        nonlocal state_changed
        demand_group = _dependency_demand_group(dependency)
        if dependency in visited:
            return
        if len(visited) >= dependency_limit:
            raise DependencyClosureError(
                f"dependency closure bound {dependency_limit} exceeded at {dependency}; "
                f"path={' -> '.join(path)}"
            )
        visited.add(dependency)
        state_changed = True
        if dependency.dimension == "FACT":
            fact_identity = f"{dependency.subject}.{dependency.key}"
            if fact_identity not in planner_input.known_world.facts:
                objective_unknown: dict[str, object] = {
                    "dimension": "OBJECTIVE_FACT_KNOWLEDGE",
                    "subject_key": dependency.subject,
                    "fact_key": dependency.key,
                    "required": dependency.required,
                    "status": "UNKNOWN",
                    "blocks": "OBJECTIVE_PROGRESSION",
                    "resolvable_by_effect_types": ["KNOWLEDGE_REVEAL"],
                }
                objective_unknown["dependency_id"] = _dependency_id(
                    "OBJECTIVE_FACT_KNOWLEDGE",
                    subject_key=dependency.subject,
                    fact_key=dependency.key,
                )
                unknowns[str(objective_unknown["dependency_id"])] = objective_unknown
                for _action_key, contract in contracts.items():
                    if not action_has_legal_executor(contract):
                        continue
                    binding_key = _knowledge_producer_binding_for_target(
                        contract,
                        dependency,
                        planner_input,
                        bindings,
                    )
                    if binding_key is None:
                        continue
                    if binding_key not in bindings:
                        bindings[binding_key] = PlannerTargetBinding(
                            action_key=binding_key[0],
                            target_key=binding_key[1],
                        )
                    relevant_nodes.add(dependency.subject)
                    select_binding(
                        binding_key,
                        path,
                        repr(dependency),
                        demand_group=demand_group,
                    )
            for action_key, contract in contracts.items():
                if not _contract_can_produce_fact_for_target(
                    contract,
                    dependency,
                    planner_input,
                ):
                    continue
                select_action(
                    action_key,
                    path,
                    repr(dependency),
                    demand_group=demand_group,
                )
                if action_key in selected_actions:
                    expand_source_dependencies(
                        contract,
                        dependency.subject,
                        path,
                    )
            for binding_key, binding in bindings.items():
                for effect in binding.deterministic_effects:
                    if (
                        effect.get("type") == "FACT_MUTATION"
                        and effect.get("fact_key") == dependency.key
                        and binding.target_key == dependency.subject
                    ):
                        select_binding(
                            binding_key,
                            path,
                            repr(dependency),
                            demand_group=demand_group,
                        )
        elif dependency.dimension in {"ACTOR_COMMAND_REACHABILITY", "ACTOR_LOCATION"}:
            effect_type = dependency.dimension
            if dependency.dimension == "ACTOR_COMMAND_REACHABILITY":
                selected_actor_targets.add(dependency.subject)
            for action_key, contract in contracts.items():
                if action_key == consumer_action:
                    continue
                if not _contract_is_frontier_eligible(contract):
                    continue
                if any(
                    effect.get("type") == effect_type
                    and (
                        not dependency.required
                        or effect.get("value") in {dependency.required, "target_key"}
                    )
                    for effect in contract.deterministic_effects
                ):
                    select_action(
                        action_key,
                        path,
                        repr(dependency),
                        demand_group=demand_group,
                    )
        elif dependency.dimension == "RESOURCE_SOURCE":
            known_resource = planner_input.known_world.resources.get(dependency.subject)
            required_amount = int(dependency.required or 0)
            known_available_amount = _known_available_resource_amount(known_resource)
            # A resource may be needed by several already relevant bindings.
            # Accumulated demand is only a closure-relevance signal: it does
            # not assign quantities to Pools or choose a source for Planner.
            relevant_demand = _resource_demand_total(dependency.subject, required_amount)
            if known_available_amount < relevant_demand:
                for unlock_dependency in _known_linked_pool_unlock_dependencies(
                    known_resource,
                    planner_input,
                ):
                    queue.append(
                        (
                            unlock_dependency,
                            (
                                *path,
                                f"resource_unlock:{dependency.subject}",
                            ),
                            consumer_action,
                        )
                    )
            destination_region = _known_region_for_node(
                definition,
                planner_input,
                dependency.key,
            )
            inventory_status = _resource_inventory_status(
                known_resource,
                planner_input,
                required_amount=required_amount,
            )
            if inventory_status == "UNKNOWN":
                unknown: dict[str, object] = {
                    "dimension": "RESOURCE_SOURCE",
                    "resource_key": dependency.subject,
                    "target_key": dependency.key,
                    "required_amount": required_amount,
                    "known_available_amount": known_available_amount,
                    "deficit": required_amount - known_available_amount,
                    "source_knowledge_status": "UNKNOWN",
                    "status": "UNKNOWN",
                    "blocks": "SOURCE_SELECTION",
                    "resolvable_by_effect_types": [
                        "REGION_RESOURCE_KNOWLEDGE",
                        "RESOURCE_POOL_KNOWLEDGE",
                    ],
                }
                if destination_region is not None:
                    unknown["destination_region"] = destination_region
                unknown["dependency_id"] = _dependency_id(
                    "RESOURCE_SOURCE",
                    resource_key=dependency.subject,
                    target_key=dependency.key,
                    required_amount=required_amount,
                )
                unknowns[str(unknown["dependency_id"])] = unknown
                for action_key, contract in contracts.items():
                    if action_key == consumer_action:
                        continue
                    if any(
                        effect.get("type")
                        in {"REGION_RESOURCE_KNOWLEDGE", "RESOURCE_POOL_KNOWLEDGE"}
                        for effect in contract.deterministic_effects
                    ):
                        select_action(
                            action_key,
                            path,
                            repr(dependency),
                            demand_group=demand_group,
                        )
            elif known_available_amount < required_amount:
                # A known quantity deficit is a deterministic contradiction,
                # not an UNKNOWN dependency.  Keep it in the canonical
                # resource summary/precondition and let Validator emit the
                # typed shortage diagnostic for a submitted proposal.
                return
            elif not _has_known_available_resource_at(
                known_resource, destination_region, required_amount
            ):
                for action_key, contract in contracts.items():
                    if action_key == consumer_action:
                        continue
                    if any(
                        effect.get("type") == "RESOURCE_TRANSFER"
                        for effect in contract.deterministic_effects
                    ) and action_has_legal_executor(contract):
                        select_action(
                            action_key,
                            path,
                            repr(dependency),
                            demand_group=demand_group,
                        )

    selected_actor_keys: set[str] = set()
    actor_states = {item.actor_key: item for item in planner_input.actors}
    passability_key = definition.metadata.locality.passability_fact_key

    def actor_matches_executor(
        actor: PlannerActorState,
        contract: PlannerActionContract,
    ) -> bool:
        return _actor_matches_executor(actor, contract)

    def refresh_actor_slice() -> None:
        for action_key in sorted(selected_actions):
            contract = contracts.get(action_key)
            if contract is None:
                continue
            all_candidates = [
                actor for actor in planner_input.actors if actor_matches_executor(actor, contract)
            ]
            candidates = all_candidates
            if contract.executor_requirements.get("command_reachability") == "ONLINE":
                online = [
                    actor for actor in all_candidates if actor.command_reachability == "ONLINE"
                ]
                if online:
                    candidates = online
                else:
                    targeted = [
                        actor
                        for actor in all_candidates
                        if actor.actor_key in selected_actor_targets
                    ]
                    if targeted:
                        candidates = targeted
                    else:
                        recoverable = [
                            actor
                            for actor in all_candidates
                            if _has_public_reachability_producer(
                                definition,
                                planner_input,
                                contracts,
                                bindings,
                                actor.actor_key,
                                seen_actions=frozenset({action_key}),
                            )
                        ]
                        if recoverable:
                            witness = sorted(recoverable, key=lambda item: item.actor_key)[0]
                            candidates = [witness]
                            selected_actor_targets.add(witness.actor_key)
                        else:
                            candidates = []
            for actor in candidates:
                selected_actor_keys.add(actor.actor_key)
                if actor.current_region:
                    relevant_nodes.add(actor.current_region)

        for actor_key in sorted(selected_actor_targets):
            target_actor = actor_states.get(actor_key)
            if target_actor is None or target_actor.availability != "ACTIVE":
                continue
            selected_actor_keys.add(actor_key)
            if target_actor.current_region:
                relevant_nodes.add(target_actor.current_region)

    state_changed = True
    while state_changed or queue:
        state_changed = False
        before = (
            len(visited),
            len(selected_actions),
            len(selected_bindings),
            len(relevant_nodes),
            len(relevant_resources),
            len(unknowns),
            len(selected_actor_keys),
            len(selected_actor_targets),
        )
        while queue:
            process_dependency(*queue.popleft())

        for binding_key in tuple(selected_bindings):
            relevant_nodes.add(binding_key[1])
            if binding_key[1] in actor_states:
                selected_actor_targets.add(binding_key[1])
        refresh_actor_slice()
        for actor_key in sorted(selected_diagnostic_actor_keys):
            actor = actor_states.get(actor_key)
            if actor is None:
                continue
            selected_actor_keys.add(actor_key)
            if actor.current_region:
                relevant_nodes.add(actor.current_region)
        _include_one_hop_locality(
            definition,
            planner_input,
            selected_actions,
            relevant_nodes,
            unknowns,
        )
        _include_public_resource_acquisition_regions(
            definition,
            planner_input,
            selected_actions,
            relevant_nodes,
        )

        if passability_key is not None:
            for node_key in tuple(sorted(relevant_nodes)):
                if (
                    planner_input.known_world.facts.get(f"{node_key}.{passability_key}")
                    is not False
                ):
                    continue
                for action_key, contract in sorted(contracts.items()):
                    if not any(
                        effect.get("type") == "FACT_MUTATION"
                        and effect.get("fact_key") == passability_key
                        and effect.get("value") is True
                        for effect in contract.deterministic_effects
                    ):
                        continue
                    binding_key = (action_key, node_key)
                    if binding_key in bindings:
                        select_binding(
                            binding_key,
                            (f"known_fact:{node_key}.{passability_key}=false",),
                            "KNOWN_BLOCKED_TRANSPORT",
                            demand_group=_dependency_demand_group(
                                TypedDependency(
                                    "FACT",
                                    node_key,
                                    passability_key,
                                    repr((False,)),
                                )
                            ),
                        )
                    else:
                        select_action(
                            action_key,
                            (f"known_fact:{node_key}.{passability_key}=false",),
                            "KNOWN_BLOCKED_TRANSPORT",
                            demand_group=_dependency_demand_group(
                                TypedDependency(
                                    "FACT",
                                    node_key,
                                    passability_key,
                                    repr((False,)),
                                )
                            ),
                        )

        after = (
            len(visited),
            len(selected_actions),
            len(selected_bindings),
            len(relevant_nodes),
            len(relevant_resources),
            len(unknowns),
            len(selected_actor_keys),
            len(selected_actor_targets),
        )
        if after != before or queue:
            state_changed = True
    known_world = _slice_known_world(
        planner_input.known_world,
        relevant_nodes,
        relevant_resources,
        tuple(unknowns.values()),
    )
    sliced_action_contracts = tuple(
        item for item in planner_input.action_contracts if item.action_key in selected_actions
    )
    selected_actors = tuple(
        item for item in planner_input.actors if item.actor_key in selected_actor_keys
    )
    sliced = planner_input.model_copy(
        update={
            "actors": _scope_actor_actions_to_contracts(
                selected_actors,
                sliced_action_contracts,
            ),
            "action_contracts": sliced_action_contracts,
            "target_bindings": tuple(
                item
                for binding_key, item in sorted(bindings.items())
                if binding_key in selected_bindings
            ),
            "known_world": known_world,
        }
    )
    return DependencyClosureResult(
        planner_input=sliced,
        relevance_reason={key: tuple(value) for key, value in audit.items()},
    )


def _contract_can_produce_fact_for_target(
    contract: PlannerActionContract,
    dependency: TypedDependency,
    planner_input: PlannerInput,
) -> bool:
    """Check an action-level FACT producer without inventing a binding."""

    target_key = dependency.subject
    target = next(
        (item for item in planner_input.known_world.nodes if item.get("key") == target_key),
        None,
    )
    if target is None:
        return False
    required_interaction = contract.target_contract.get("required_interaction_key")
    interactions = target.get("interactions")
    if isinstance(required_interaction, str) and (
        not isinstance(interactions, list) or required_interaction not in interactions
    ):
        return False
    accepted_values: object = dependency.required
    if isinstance(dependency.required, str):
        try:
            accepted_values = ast.literal_eval(dependency.required)
        except (SyntaxError, ValueError):
            accepted_values = dependency.required
    if not isinstance(accepted_values, (tuple, list, set, frozenset)):
        accepted_values = (accepted_values,)
    for effect in contract.deterministic_effects:
        effect_value = effect.get("value")
        if isinstance(effect_value, dict) and isinstance(effect_value.get("from_parameter"), str):
            parameter = next(
                (
                    item
                    for item in contract.parameters
                    if item.get("key") == effect_value.get("from_parameter")
                ),
                None,
            )
            allowed_values = parameter.get("allowed_values", []) if parameter else []
            if (
                effect.get("type") == "FACT_MUTATION"
                and effect.get("fact_key") == dependency.key
                and effect.get("target") in {"target_key", "target_node", target_key}
                and isinstance(allowed_values, list)
                and any(value in accepted_values for value in allowed_values)
            ):
                return True
        if (
            effect.get("type") == "FACT_MUTATION"
            and effect.get("fact_key") == dependency.key
            and effect.get("target") in {"target_key", "target_node", target_key}
            and effect.get("value") in accepted_values
        ):
            return True
    return False


def _knowledge_producer_binding_for_target(
    contract: PlannerActionContract,
    dependency: TypedDependency,
    planner_input: PlannerInput,
    bindings: dict[tuple[str, str], PlannerTargetBinding],
) -> tuple[str, str] | None:
    """Return a legal target binding for an objective-relevant knowledge producer.

    Knowledge acquisition is separate from FACT_MUTATION.  The action must
    declare a knowledge contract and a corresponding public KNOWLEDGE_REVEAL
    effect.  Direct inspection may use a sparse binding because its target has
    no hidden cost or target-specific Truth requirement.  A broader region
    reveal is accepted only when an existing target binding proves that the
    action can be attempted from the current public state.
    """

    target_key = dependency.subject
    target = next(
        (item for item in planner_input.known_world.nodes if item.get("key") == target_key),
        None,
    )
    if target is None or target.get("access") not in {"AVAILABLE", "ENTERED"}:
        return None
    required_interaction = contract.target_contract.get("required_interaction_key")
    interactions = target.get("interactions")
    if isinstance(required_interaction, str) and (
        not isinstance(interactions, list) or required_interaction not in interactions
    ):
        return None
    if not any(
        effect.get("type") == "KNOWLEDGE_REVEAL" for effect in contract.deterministic_effects
    ):
        return None

    for semantics in contract.knowledge_semantics:
        if semantics.get("reveals") != "NON_RESOURCE_STATE":
            continue
        target_scope = semantics.get("target")
        if target_scope == "INSPECT_TARGET":
            if semantics.get("type") != "FACILITY_OR_ROUTE_KNOWLEDGE":
                continue
            return (contract.action_key, target_key)
        if target_scope != "TARGET_REGION_FACILITIES":
            continue
        if semantics.get("type") != "REGION_FACILITY_KNOWLEDGE":
            continue
        # A region-wide reveal cannot bypass hidden target requirements.
        candidate = (contract.action_key, target_key)
        if candidate in bindings:
            return candidate
    return None


def _actor_matches_executor(
    actor: PlannerActorState,
    contract: PlannerActionContract,
) -> bool:
    requirements = contract.executor_requirements
    required_role = requirements.get("required_role_key")
    required_capabilities = requirements.get("required_capabilities", [])
    return (
        actor.availability == "ACTIVE"
        and contract.action_key in actor.allowed_action_keys
        and (not isinstance(required_role, str) or actor.role_key == required_role)
        and isinstance(required_capabilities, (list, tuple))
        and all(
            isinstance(item, str) and item in actor.capabilities for item in required_capabilities
        )
    )


def _has_legal_executor(
    definition: ScenarioDefinitionV2,
    planner_input: PlannerInput,
    contracts: dict[str, PlannerActionContract],
    bindings: dict[tuple[str, str], PlannerTargetBinding],
    contract: PlannerActionContract,
    *,
    seen_actions: frozenset[str] = frozenset(),
) -> bool:
    """Prove current or publicly recoverable execution without choosing an Actor."""

    for actor in planner_input.actors:
        if not _actor_matches_executor(actor, contract):
            continue
        if contract.executor_requirements.get("command_reachability") != "ONLINE":
            return True
        if actor.command_reachability == "ONLINE":
            return True
        if _has_public_reachability_producer(
            definition,
            planner_input,
            contracts,
            bindings,
            actor.actor_key,
            seen_actions=seen_actions | {contract.action_key},
        ):
            return True
    return False


def _capability_diagnostic_actor_keys(
    contract: PlannerActionContract,
    planner_input: PlannerInput,
) -> set[str]:
    """Keep public role-matching Actors for the existing validator diagnostic."""

    requirements = contract.executor_requirements
    required_role = requirements.get("required_role_key")
    required_capabilities = requirements.get("required_capabilities", [])
    if not isinstance(required_capabilities, (list, tuple)) or not all(
        isinstance(item, str) for item in required_capabilities
    ):
        return set()
    return {
        actor.actor_key
        for actor in planner_input.actors
        if actor.availability == "ACTIVE"
        and contract.action_key in actor.allowed_action_keys
        and (not isinstance(required_role, str) or actor.role_key == required_role)
        and not set(required_capabilities).issubset(actor.capabilities)
    }


def _has_public_reachability_producer(
    definition: ScenarioDefinitionV2,
    planner_input: PlannerInput,
    contracts: dict[str, PlannerActionContract],
    bindings: dict[tuple[str, str], PlannerTargetBinding],
    actor_key: str,
    *,
    seen_actions: frozenset[str],
) -> bool:
    """Return whether a public Action can restore one known Actor to ONLINE."""

    target_actor = next(
        (item for item in planner_input.actors if item.actor_key == actor_key), None
    )
    if target_actor is None:
        return False
    for producer in sorted(contracts.values(), key=lambda item: item.action_key):
        if producer.action_key in seen_actions:
            continue
        if producer.target_contract.get("kind") != "ACTOR":
            continue
        if producer.target_contract.get("command_reachability") not in {
            None,
            target_actor.command_reachability,
        }:
            continue
        if not any(
            effect.get("type") == "ACTOR_COMMAND_REACHABILITY"
            and effect.get("value") == "ONLINE"
            and effect.get("target") in {"target_actor", "target_key", actor_key}
            for effect in producer.deterministic_effects
        ):
            continue
        if _has_legal_executor(
            definition,
            planner_input,
            contracts,
            bindings,
            producer,
            seen_actions=seen_actions,
        ):
            return True
    return False


def _known_available_resource_amount(raw: object) -> int:
    if not isinstance(raw, dict):
        return 0
    known_available = raw.get("known_available")
    if isinstance(known_available, int) and not isinstance(known_available, bool):
        return max(0, known_available)
    scopes = raw.get("scopes")
    if not isinstance(scopes, dict):
        return 0
    return sum(
        max(0, int(value["known_available"]))
        for value in scopes.values()
        if isinstance(value, dict)
        and isinstance(value.get("known_available"), int)
        and not isinstance(value.get("known_available"), bool)
    )


def _known_linked_pool_unlock_dependencies(
    raw_resource: object,
    planner_input: PlannerInput,
) -> tuple[TypedDependency, ...]:
    """Return public, unsatisfied unlock Facts for known unavailable Pools.

    ``PlannerInput`` contains only the public Pool projection.  A requirement
    is safe to expand only when its referenced Fact is also present in the
    public Fact projection; a requirement definition alone must not reveal a
    hidden Fact.  The helper deliberately returns Facts only.  Existing
    producer/binding expansion remains the sole authority for deciding which
    Action can satisfy them.
    """

    if not isinstance(raw_resource, dict):
        return ()
    scopes = raw_resource.get("scopes")
    if not isinstance(scopes, dict):
        return ()
    known_facts = planner_input.known_world.facts
    dependencies: set[TypedDependency] = set()
    for scope in scopes.values():
        if not isinstance(scope, dict):
            continue
        pools = scope.get("pools")
        if not isinstance(pools, (list, tuple)):
            continue
        for pool in pools:
            if not isinstance(pool, dict):
                continue
            visibility = pool.get("visibility")
            if visibility is not None:
                visibility_value = getattr(visibility, "value", visibility)
                if visibility_value != "VISIBLE":
                    continue
            availability = getattr(pool.get("availability"), "value", pool.get("availability"))
            if availability != "UNAVAILABLE":
                continue
            requirement_status = pool.get("availability_requirement_status")
            if requirement_status is not None:
                requirement_status = getattr(requirement_status, "value", requirement_status)
                if requirement_status != "KNOWN":
                    continue
            requirement = pool.get("availability_requirement")
            if not isinstance(requirement, dict):
                continue
            node_key = requirement.get("node_key")
            fact_key = requirement.get("fact_key")
            if not isinstance(node_key, str) or not isinstance(fact_key, str):
                continue
            operator = requirement.get("operator")
            if operator not in {None, "EQ"}:
                continue
            identity = f"{node_key}.{fact_key}"
            if identity not in known_facts:
                continue
            required_value = requirement.get("value")
            if known_facts[identity] == required_value:
                continue
            dependencies.add(
                TypedDependency(
                    "FACT",
                    node_key,
                    fact_key,
                    repr((required_value,)),
                )
            )
    return tuple(
        sorted(
            dependencies,
            key=lambda item: (item.subject, item.key, str(item.required)),
        )
    )


def _known_precondition_is_true(precondition: dict[str, object]) -> bool:
    """Return whether a public projected preflight failure is currently true."""

    condition = precondition.get("failure_condition")
    current = precondition.get("current_value")
    if not isinstance(condition, dict):
        return False
    kind = condition.get("kind")
    if kind == "FACT_EQUALS":
        return current == condition.get("value")
    if kind == "FACT_NOT_EQUALS":
        return current != condition.get("value")
    if kind == "FACT_IN":
        values = condition.get("values")
        return isinstance(values, list) and current in values
    return False


def _resource_inventory_status(
    raw: object,
    planner_input: PlannerInput,
    *,
    required_amount: int | None = None,
) -> str:
    """Return the shared public inventory status for a resource source.

    Region scopes explicitly present in the canonical projection are known,
    including a zero summary.  A missing scope is unknown only while that
    Region's inventory is incomplete/hidden; a fully surveyed visible Region
    with no Pool is known zero.
    """

    if not isinstance(raw, dict):
        return "UNKNOWN"
    if required_amount is not None and _known_available_resource_amount(raw) >= required_amount:
        return "KNOWN"
    scopes = raw.get("scopes")
    if not isinstance(scopes, dict):
        return "KNOWN" if "global" in raw or "known_total" in raw else "UNKNOWN"
    regional_scopes = {key for key in scopes if key != "global"}
    if not regional_scopes:
        return "KNOWN" if "global" in scopes else "UNKNOWN"
    if any(
        isinstance(entry, dict) and entry.get("knowledge_status") == "UNKNOWN"
        for entry in scopes.values()
    ):
        return "UNKNOWN"
    knowledge_by_region = {
        item.get("region_key"): item
        for item in planner_input.known_world.resource_knowledge
        if isinstance(item, dict) and isinstance(item.get("region_key"), str)
    }
    for region_key in sorted(regional_scopes):
        entry = scopes.get(region_key)
        if not isinstance(entry, dict):
            return "UNKNOWN"
        knowledge = knowledge_by_region.get(region_key)
        visibility = entry.get("resource_inventory_visibility")
        survey_completed = entry.get("resource_survey_completed")
        if knowledge is not None:
            visibility = knowledge.get("resource_inventory_visibility", visibility)
            survey_completed = knowledge.get("resource_survey_completed", survey_completed)
        if not isinstance(visibility, str) or not isinstance(survey_completed, bool):
            return "UNKNOWN"
        if (
            ResourceInventoryVisibility(visibility) != ResourceInventoryVisibility.VISIBLE
            or not survey_completed
        ):
            # A visible Pool can be consumed when it is sufficient, but a
            # shortfall in an incomplete inventory is still UNKNOWN because
            # an undiscovered Pool may exist.
            return "UNKNOWN"
        status = resource_knowledge_status(
            inventory_visibility=ResourceInventoryVisibility(visibility),
            survey_completed=survey_completed,
            has_visible_pool=bool(entry.get("pools")),
        )
        if status == "UNKNOWN":
            return "UNKNOWN"
    for region_key, entry in knowledge_by_region.items():
        if region_key in regional_scopes:
            continue
        visibility = entry.get("resource_inventory_visibility")
        survey_completed = entry.get("resource_survey_completed")
        if not isinstance(visibility, str) or not isinstance(survey_completed, bool):
            return "UNKNOWN"
        status = resource_knowledge_status(
            inventory_visibility=ResourceInventoryVisibility(visibility),
            survey_completed=survey_completed,
            has_visible_pool=False,
        )
        if status == "UNKNOWN":
            return "UNKNOWN"
    return "KNOWN"


def _has_known_available_resource_at(
    raw: object, region_key: str | None, required_amount: int
) -> bool:
    if region_key is None or not isinstance(raw, dict):
        return False
    scopes = raw.get("scopes")
    if not isinstance(scopes, dict):
        return False
    value = scopes.get(region_key)
    return (
        isinstance(value, dict)
        and isinstance(value.get("known_available"), int)
        and not isinstance(value.get("known_available"), bool)
        and value["known_available"] >= required_amount
    )


def _known_region_for_node(
    definition: ScenarioDefinitionV2,
    planner_input: PlannerInput,
    node_key: str,
) -> str | None:
    node = definition.world.node(node_key)
    locality = definition.metadata.locality
    if node is None or not locality.enabled:
        return None
    if node.node_type_key == locality.region_node_type_key:
        return node.key
    relation_type = locality.located_in_relation_type_key
    for relation in planner_input.known_world.relations:
        if (
            relation.get("source_node_key") == node_key
            and relation.get("relation_type_key") == relation_type
            and isinstance(relation.get("target_node_key"), str)
        ):
            return str(relation["target_node_key"])
    return None


def _contract_is_frontier_eligible(contract: object) -> bool:
    knowledge_semantics = getattr(contract, "knowledge_semantics", ())
    return not any(
        isinstance(item, dict) and item.get("type") == "SOURCE_INVENTORY"
        for item in knowledge_semantics
    )


def _include_one_hop_locality(
    definition: ScenarioDefinitionV2,
    planner_input: PlannerInput,
    selected_actions: set[str],
    relevant_nodes: set[str],
    unknowns: dict[str, dict[str, object]],
) -> None:
    if not any(
        contract.action_key in selected_actions
        and contract.locality.get("type") == "ONE_HOP_TRANSPORT"
        for contract in planner_input.action_contracts
    ):
        return
    locality = definition.metadata.locality
    endpoint_key = locality.transport_endpoint_relation_type_key
    transport_type = locality.transport_node_type_key
    passability_key = locality.passability_fact_key
    if endpoint_key is None or transport_type is None:
        return
    known_node_keys = {str(item.get("key")) for item in planner_input.known_world.nodes}
    known_relations = [
        item
        for item in planner_input.known_world.relations
        if item.get("relation_type_key") == endpoint_key
    ]
    transport_nodes = {
        str(item.get("key"))
        for item in planner_input.known_world.nodes
        if item.get("type") == transport_type
    }
    origin_regions = set(relevant_nodes)
    for transport_key in transport_nodes:
        endpoints = {
            str(item.get("target_node_key"))
            for item in known_relations
            if item.get("source_node_key") == transport_key
        }
        if not endpoints & origin_regions:
            continue
        relevant_nodes.update(endpoints)
        relevant_nodes.add(transport_key)
        if passability_key:
            identity = f"{transport_key}.{passability_key}"
            if identity not in planner_input.known_world.facts and transport_key in known_node_keys:
                unknown: dict[str, object] = {
                    "dimension": "TRANSPORT_PASSABILITY",
                    "subject_key": transport_key,
                    "fact_key": passability_key,
                    "status": "UNKNOWN",
                    "attempt_policy": "MAY_ATTEMPT",
                }
                unknown["dependency_id"] = _dependency_id(
                    "TRANSPORT_PASSABILITY",
                    subject_key=transport_key,
                    fact_key=passability_key,
                )
                unknowns[str(unknown["dependency_id"])] = unknown


def _include_public_resource_acquisition_regions(
    definition: ScenarioDefinitionV2,
    planner_input: PlannerInput,
    selected_actions: set[str],
    relevant_nodes: set[str],
) -> None:
    """Keep public Region choices for source-knowledge acquisition available.

    A RESOURCE_SOURCE dependency deliberately does not identify a source
    Region.  Once a public knowledge-acquisition Action is in the closure,
    expose the bounded set of currently public, accessible Region Nodes so the
    Planner can choose among them.  This is a Region slice only; it never
    creates Actor x Action x Target candidates or selects a source.
    """

    if not definition.metadata.locality.enabled:
        return
    region_type = definition.metadata.locality.region_node_type_key
    acquisition_actions = {
        contract.action_key
        for contract in planner_input.action_contracts
        if contract.action_key in selected_actions
        and any(
            effect.get("type") in {"REGION_RESOURCE_KNOWLEDGE", "RESOURCE_POOL_KNOWLEDGE"}
            for effect in contract.deterministic_effects
        )
    }
    if not acquisition_actions:
        return
    relevant_nodes.update(
        str(item["key"])
        for item in planner_input.known_world.nodes
        if item.get("type") == region_type
        and item.get("access") in {"AVAILABLE", "ENTERED"}
        and isinstance(item.get("key"), str)
    )


def _dependency_id(dimension: str, **identity: object) -> str:
    """Return a stable ID from typed semantic keys, never display/scenario text."""

    canonical_input: dict[str, object] = {"dimension": dimension}
    canonical_input.update(identity)
    canonical = json.dumps(
        canonical_input,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"dependency-{dimension.lower().replace('_', '-')}-{digest}"


def _slice_known_world(
    known_world: PlannerKnownWorldSlice,
    node_keys: set[str],
    resource_keys: set[str],
    unknown_dependencies: tuple[dict[str, object], ...],
) -> PlannerKnownWorldSlice:
    nodes = tuple(item for item in known_world.nodes if item.get("key") in node_keys)
    facts = {
        identity: value
        for identity, value in known_world.facts.items()
        if identity.split(".", 1)[0] in node_keys
    }
    relations = tuple(
        item
        for item in known_world.relations
        if item.get("source_node_key") in node_keys and item.get("target_node_key") in node_keys
    )
    resources = {key: value for key, value in known_world.resources.items() if key in resource_keys}
    resource_knowledge = tuple(
        item for item in known_world.resource_knowledge if item.get("region_key") in node_keys
    )
    return PlannerKnownWorldSlice(
        nodes=nodes,
        facts=facts,
        relations=relations,
        resources=resources,
        resource_knowledge=resource_knowledge,
        unknown_dependencies=unknown_dependencies,
    )


__all__ = [
    "DependencyClosureError",
    "DependencyClosureResult",
    "TypedDependency",
    "build_dependency_closure",
]
