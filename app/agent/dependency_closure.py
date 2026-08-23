"""Typed, bounded Objective dependency retrieval for Planner Input V2."""

from __future__ import annotations

import ast
import hashlib
import json
from collections import deque
from dataclasses import dataclass

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
        for binding_key, binding in bindings.items():
            if binding_key[0] != action_key or binding_key not in selected_bindings:
                continue
            _queue_binding_resource_dependencies(binding, path, action_key)

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
                    )
                    break

        # Resource requirements declared by the canonical Action contract are
        # source-agnostic. Queue the knowledge dependency without selecting a
        # source Region; the Planner may choose any public acquisition target.
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
            _queue_resource_dependency(resource_key, -amount, "", path, action_key)

    def _queue_resource_dependency(
        resource_key: str,
        required_amount: int,
        target_key: str,
        path: tuple[str, ...],
        action_key: str,
    ) -> None:
        relevant_resources.add(resource_key)
        queue.append(
            (
                TypedDependency(
                    "RESOURCE_SOURCE",
                    resource_key,
                    key=target_key,
                    required=required_amount,
                ),
                (*path, f"action:{action_key}", f"resource:{resource_key}"),
                action_key,
            )
        )

    def _queue_binding_resource_dependencies(
        binding: PlannerTargetBinding,
        path: tuple[str, ...],
        action_key: str,
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
            for action_key, contract in contracts.items():
                if not _contract_can_produce_fact_for_target(
                    contract,
                    dependency,
                    planner_input,
                ):
                    continue
                select_action(action_key, path, repr(dependency))
            for binding_key, binding in bindings.items():
                for effect in binding.deterministic_effects:
                    if (
                        effect.get("type") == "FACT_MUTATION"
                        and effect.get("fact_key") == dependency.key
                        and binding.target_key == dependency.subject
                    ):
                        selected_bindings.add(binding_key)
                        select_action(binding.action_key, path, repr(dependency))
                        _queue_binding_resource_dependencies(binding, path, binding.action_key)
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
                        select_action(action_key, path, repr(dependency))
            elif known_available_amount < required_amount:
                # A known quantity deficit is a deterministic contradiction,
                # not an UNKNOWN dependency.  Keep it in the canonical
                # resource summary/precondition and let Validator emit the
                # typed shortage diagnostic for a submitted proposal.
                continue
            elif not _has_known_available_resource_at(
                known_resource, destination_region, required_amount
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
    explicit_effect_actor_keys = {
        str(effect.get("target"))
        for contract in contracts.values()
        if contract.action_key in selected_actions
        for effect in contract.deterministic_effects
        if effect.get("type") == "ACTOR_COMMAND_REACHABILITY"
        and isinstance(effect.get("target"), str)
        and effect.get("target") in actor_states
    }
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
            # Keep online executors, role-bound executors, and Actors named by
            # a public deterministic reachability effect.  A disconnected
            # Actor is otherwise not an executable candidate; retaining every
            # disconnected profile would turn the sparse closure into a
            # scenario-wide Actor catalog.
            if (
                state.command_reachability != "ONLINE"
                and not isinstance(role_key, str)
                and profile.key not in explicit_effect_actor_keys
            ):
                continue
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
    _include_public_resource_acquisition_regions(
        definition,
        planner_input,
        selected_actions,
        relevant_nodes,
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
            if (
                state.command_reachability != "ONLINE"
                and not isinstance(role_key, str)
                and profile.key not in explicit_effect_actor_keys
            ):
                continue
            selected_actor_keys.add(profile.key)
            if state.current_region:
                relevant_nodes.add(state.current_region)
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
