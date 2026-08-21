"""Typed, bounded Objective dependency retrieval for Planner Input V2."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass

from app.agent.provider import PlannerInput, PlannerKnownWorldSlice
from app.domain.scenario_v2 import ObjectiveDefinitionV2, ScenarioDefinitionV2


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
        for requirement in objective.completion_requirements:
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
        for requirement in objective.completion_requirements
    }
    relevant_resources: set[str] = set()
    unknowns: dict[str, dict[str, object]] = {}
    audit: dict[str, list[dict[str, object]]] = {}

    def select_action(action_key: str, path: tuple[str, ...], producer_for: str) -> None:
        if action_key in selected_actions:
            return
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
            return
        reachability = contract.executor_requirements.get("command_reachability")
        role_key = contract.executor_requirements.get("required_role_key")
        eligible_profiles = [
            profile
            for profile in definition.actors.actor_profiles
            if action_key in profile.allowed_action_keys
            and (not isinstance(role_key, str) or profile.role_key == role_key)
        ]
        for profile in eligible_profiles:
            actor_state = next(
                (item for item in planner_input.actors if item.actor_key == profile.key), None
            )
            if (
                reachability == "ONLINE"
                and actor_state is not None
                and actor_state.command_reachability != "ONLINE"
            ):
                queue.append(
                    (
                        TypedDependency(
                            "ACTOR_COMMAND_REACHABILITY", profile.key, required="ONLINE"
                        ),
                        (*path, f"action:{action_key}", f"executor:{profile.key}"),
                        action_key,
                    )
                )

        locality = contract.locality.get("type")
        if locality in {"ACTOR_SAME_REGION", "TARGET_SAME_REGION"}:
            queue.append(
                (
                    TypedDependency("ACTOR_LOCATION", "executor", required="SAME_REGION"),
                    (*path, f"action:{action_key}", "locality:SAME_REGION"),
                    action_key,
                )
            )
        for binding_key, binding in bindings.items():
            if binding_key[0] != action_key or binding_key not in selected_bindings:
                continue
            for requirement in binding.requirements:
                cost = requirement.get("cost")
                if isinstance(cost, dict):
                    for resource_key, required_amount in cost.items():
                        if (
                            not isinstance(resource_key, str)
                            or isinstance(required_amount, bool)
                            or not isinstance(required_amount, int)
                            or required_amount <= 0
                        ):
                            continue
                        relevant_resources.add(resource_key)
                        queue.append(
                            (
                                TypedDependency(
                                    "RESOURCE_SOURCE",
                                    resource_key,
                                    key=binding.target_key,
                                    required=required_amount,
                                ),
                                (*path, f"action:{action_key}", f"resource:{resource_key}"),
                                action_key,
                            )
                        )

    while queue:
        dependency, path, consumer_action = queue.popleft()
        if dependency in visited:
            continue
        if len(visited) >= dependency_limit:
            raise DependencyClosureError(
                f"dependency closure bound {dependency_limit} exceeded at {dependency}; "
                f"path={' -> '.join(path)}"
            )
        visited.add(dependency)
        if dependency.dimension == "FACT":
            for binding_key, binding in bindings.items():
                for effect in binding.deterministic_effects:
                    if (
                        effect.get("type") == "FACT_MUTATION"
                        and effect.get("fact_key") == dependency.key
                        and binding.target_key == dependency.subject
                    ):
                        selected_bindings.add(binding_key)
                        select_action(binding.action_key, path, repr(dependency))
        elif dependency.dimension in {"ACTOR_COMMAND_REACHABILITY", "ACTOR_LOCATION"}:
            effect_type = dependency.dimension
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
                    select_action(action_key, path, repr(dependency))
        elif dependency.dimension == "RESOURCE_SOURCE":
            known_resource = planner_input.known_world.resources.get(dependency.subject)
            required_amount = int(dependency.required or 0)
            known_available_amount = _known_available_resource_amount(known_resource)
            target_region = _known_region_for_node(
                definition,
                planner_input,
                dependency.key,
            )
            if known_available_amount < required_amount:
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
                        select_action(action_key, path, repr(dependency))
            elif not _has_known_available_resource_at(
                known_resource, target_region, required_amount
            ):
                for action_key, contract in contracts.items():
                    if action_key == consumer_action:
                        continue
                    if any(
                        effect.get("type") == "RESOURCE_TRANSFER"
                        for effect in contract.deterministic_effects
                    ):
                        select_action(action_key, path, repr(dependency))

    selected_actor_keys: set[str] = set()
    actor_states = {item.actor_key: item for item in planner_input.actors}
    for action_key in selected_actions:
        selected_contract = contracts.get(action_key)
        if selected_contract is None:
            continue
        role_key = selected_contract.executor_requirements.get("required_role_key")
        for profile in definition.actors.actor_profiles:
            state = actor_states.get(profile.key)
            if state is None or action_key not in profile.allowed_action_keys:
                continue
            if isinstance(role_key, str) and profile.role_key != role_key:
                continue
            if role_key or state.command_reachability == "ONLINE":
                selected_actor_keys.add(profile.key)
                if state.current_region:
                    relevant_nodes.add(state.current_region)

    for binding_key in selected_bindings:
        relevant_nodes.add(binding_key[1])
    _include_one_hop_locality(
        definition,
        planner_input,
        selected_actions,
        relevant_nodes,
        unknowns,
    )
    passability_key = definition.metadata.locality.passability_fact_key
    if passability_key is not None:
        for node_key in tuple(relevant_nodes):
            if planner_input.known_world.facts.get(f"{node_key}.{passability_key}") is not False:
                continue
            for action_key, contract in contracts.items():
                if any(
                    effect.get("type") == "FACT_MUTATION"
                    and effect.get("fact_key") == passability_key
                    and effect.get("value") is True
                    for effect in contract.deterministic_effects
                ):
                    binding_key = (action_key, node_key)
                    if binding_key in bindings:
                        selected_bindings.add(binding_key)
                    select_action(
                        action_key,
                        (f"known_fact:{node_key}.{passability_key}=false",),
                        "KNOWN_BLOCKED_TRANSPORT",
                    )

    # A newly selected blocked-route producer can introduce another legal
    # executor. Recompute the actor slice without choosing which actor to use.
    for action_key in selected_actions:
        selected_contract = contracts.get(action_key)
        if selected_contract is None:
            continue
        role_key = selected_contract.executor_requirements.get("required_role_key")
        for profile in definition.actors.actor_profiles:
            state = actor_states.get(profile.key)
            if state is None or action_key not in profile.allowed_action_keys:
                continue
            if isinstance(role_key, str) and profile.role_key != role_key:
                continue
            if role_key or state.command_reachability == "ONLINE":
                selected_actor_keys.add(profile.key)
                if state.current_region:
                    relevant_nodes.add(state.current_region)
    known_world = _slice_known_world(
        planner_input.known_world,
        relevant_nodes,
        relevant_resources,
        tuple(unknowns.values()),
    )
    sliced = planner_input.model_copy(
        update={
            "actors": tuple(
                item for item in planner_input.actors if item.actor_key in selected_actor_keys
            ),
            "action_contracts": tuple(
                item
                for item in planner_input.action_contracts
                if item.action_key in selected_actions
            ),
            "target_bindings": tuple(
                item
                for item in planner_input.target_bindings
                if (item.action_key, item.target_key) in selected_bindings
            ),
            "known_world": known_world,
        }
    )
    return DependencyClosureResult(
        planner_input=sliced,
        relevance_reason={key: tuple(value) for key, value in audit.items()},
    )


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
        max(0, int(value["value"]))
        for value in scopes.values()
        if isinstance(value, dict)
        and isinstance(value.get("value"), int)
        and not isinstance(value.get("value"), bool)
    )


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
        and isinstance(value.get("value"), int)
        and not isinstance(value.get("value"), bool)
        and value["value"] >= required_amount
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
    resources = {
        key: value for key, value in known_world.resources.items() if key in resource_keys
    }
    resource_knowledge = tuple(
        item
        for item in known_world.resource_knowledge
        if item.get("region_key") in node_keys
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
