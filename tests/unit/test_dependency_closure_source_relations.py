from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.agent.dependency_closure import build_dependency_closure
from app.agent.planning_context import _canonical_planner_input
from app.agent.provider import (
    PlannerActionContract,
    PlannerActorState,
    PlannerInput,
    PlannerKnownWorldSlice,
    PlannerTargetBinding,
    PlanningContext,
)


def _definition(node_keys: tuple[str, ...]) -> Any:
    nodes = tuple(SimpleNamespace(key=key, node_type_key="synthetic_node") for key in node_keys)
    node_by_key = {node.key: node for node in nodes}
    world = SimpleNamespace(nodes=nodes, node=lambda key: node_by_key.get(key))
    metadata = SimpleNamespace(locality=SimpleNamespace(enabled=False, passability_fact_key=None))
    return SimpleNamespace(world=world, metadata=metadata)


def _objective(*requirements: tuple[str, str, str, tuple[object, ...]]) -> Any:
    return SimpleNamespace(
        key="synthetic_objective",
        completion_requirements=tuple(
            SimpleNamespace(
                key=key,
                node_key=node_key,
                fact_key=fact_key,
                accepted_values=accepted_values,
            )
            for key, node_key, fact_key, accepted_values in requirements
        ),
        prerequisites=(),
    )


def _relation(
    relation_key: str,
    source_key: str,
    target_key: str,
    *,
    visibility: str | None = None,
) -> dict[str, object]:
    relation: dict[str, object] = {
        "relation_key": relation_key,
        "source_node_key": source_key,
        "relation_type_key": "feeds",
        "target_node_key": target_key,
    }
    if visibility is not None:
        relation["visibility"] = visibility
    return relation


def _relation_planner_input(
    *,
    source_nodes: tuple[str, ...],
    relations: tuple[dict[str, object], ...],
    facts: dict[str, object],
    prepare_targets: tuple[str, ...],
) -> PlannerInput:
    nodes = (
        {
            "key": "target",
            "access": "AVAILABLE",
            "interactions": ["targetable"],
        },
        *(
            {
                "key": source_key,
                "access": "AVAILABLE",
                "interactions": ["preparable"],
            }
            for source_key in source_nodes
        ),
    )
    actor = PlannerActorState(
        actor_key="operator",
        role_key="operator",
        capabilities=("EXECUTE",),
        allowed_action_keys=("use_source", "prepare_source"),
        availability="ACTIVE",
        current_region=None,
        command_reachability="ONLINE",
    )
    use_source = PlannerActionContract(
        action_key="use_source",
        executor_requirements={
            "command_reachability": "ONLINE",
            "required_capabilities": ["EXECUTE"],
        },
        target_contract={
            "kind": "NODE",
            "required_interaction_key": "targetable",
        },
        source_relation_type_key="feeds",
        source_preconditions=(
            {
                "failure_condition": {
                    "kind": "FACT_NOT_EQUALS",
                    "selector": "ACTION_SOURCE",
                    "fact_key": "ready",
                    "value": True,
                }
            },
        ),
        deterministic_effects=(
            {
                "type": "FACT_MUTATION",
                "target": "target_key",
                "fact_key": "complete",
                "value": True,
            },
        ),
    )
    prepare_source = PlannerActionContract(
        action_key="prepare_source",
        executor_requirements={
            "command_reachability": "ONLINE",
            "required_capabilities": ["EXECUTE"],
        },
        target_contract={
            "kind": "NODE",
            "required_interaction_key": "preparable",
        },
        deterministic_effects=(
            {
                "type": "FACT_MUTATION",
                "target": "target_key",
                "fact_key": "ready",
                "value": True,
            },
        ),
    )
    bindings = tuple(
        PlannerTargetBinding(
            action_key="prepare_source",
            target_key=source_key,
            deterministic_effects=(
                {
                    "type": "FACT_MUTATION",
                    "target": "target_key",
                    "fact_key": "ready",
                    "value": True,
                },
            ),
        )
        for source_key in prepare_targets
    )
    return PlannerInput(
        actors=(actor,),
        action_contracts=(use_source, prepare_source),
        target_bindings=bindings,
        known_world=PlannerKnownWorldSlice(
            nodes=nodes,
            facts=facts,
            relations=relations,
        ),
    )


def _run_relation_closure(
    *,
    source_nodes: tuple[str, ...],
    relations: tuple[dict[str, object], ...],
    facts: dict[str, object],
    prepare_targets: tuple[str, ...],
) -> Any:
    planner_input = _relation_planner_input(
        source_nodes=source_nodes,
        relations=relations,
        facts=facts,
        prepare_targets=prepare_targets,
    )
    definition = _definition(tuple(item["key"] for item in planner_input.known_world.nodes))
    return build_dependency_closure(
        definition,
        (_objective(("complete_target", "target", "complete", (True,))),),
        planner_input,
    )


def test_direct_public_source_expands_source_prerequisite() -> None:
    result = _run_relation_closure(
        source_nodes=("source_a",),
        relations=(_relation("link_a", "source_a", "target"),),
        facts={"target.complete": False, "source_a.ready": False},
        prepare_targets=("source_a",),
    )
    assert {item.action_key for item in result.planner_input.action_contracts} == {
        "use_source",
        "prepare_source",
    }
    assert {item["key"] for item in result.planner_input.known_world.nodes} == {
        "target",
        "source_a",
    }
    assert {
        (item["source_node_key"], item["target_node_key"])
        for item in result.planner_input.known_world.relations
    } == {("source_a", "target")}
    assert ("prepare_source", "source_a") in {
        (item.action_key, item.target_key) for item in result.planner_input.target_bindings
    }
    assert any(
        "source_relation:feeds" in item["dependency_path"]
        for item in result.relevance_reason["use_source"]
    )


def test_multiple_direct_public_sources_are_all_retained() -> None:
    result = _run_relation_closure(
        source_nodes=("source_a", "source_b"),
        relations=(
            _relation("link_a", "source_a", "target"),
            _relation("link_b", "source_b", "target"),
        ),
        facts={
            "target.complete": False,
            "source_a.ready": False,
            "source_b.ready": False,
        },
        prepare_targets=("source_a", "source_b"),
    )
    assert {
        (item["source_node_key"], item["target_node_key"])
        for item in result.planner_input.known_world.relations
    } == {("source_a", "target"), ("source_b", "target")}
    assert {
        (item.action_key, item.target_key) for item in result.planner_input.target_bindings
    } == {("prepare_source", "source_a"), ("prepare_source", "source_b")}


def test_hidden_relation_is_excluded_without_hidden_fact_leak() -> None:
    result = _run_relation_closure(
        source_nodes=("source_a", "hidden_source"),
        relations=(
            _relation("link_a", "source_a", "target"),
            _relation("link_hidden", "hidden_source", "target", visibility="HIDDEN"),
        ),
        facts={"target.complete": False, "source_a.ready": False},
        prepare_targets=("source_a", "hidden_source"),
    )
    node_keys = {item["key"] for item in result.planner_input.known_world.nodes}
    assert "hidden_source" not in node_keys
    assert all(
        item["source_node_key"] != "hidden_source"
        for item in result.planner_input.known_world.relations
    )
    assert "hidden_source.ready" not in result.planner_input.known_world.facts
    assert all(item.target_key != "hidden_source" for item in result.planner_input.target_bindings)


def test_missing_direct_source_does_not_fabricate_one() -> None:
    result = _run_relation_closure(
        source_nodes=("source_a",),
        relations=(),
        facts={"target.complete": False, "source_a.ready": False},
        prepare_targets=("source_a",),
    )
    assert {item.action_key for item in result.planner_input.action_contracts} == {"use_source"}
    assert {item["key"] for item in result.planner_input.known_world.nodes} == {"target"}
    assert result.planner_input.known_world.relations == ()
    assert result.planner_input.target_bindings == ()


def test_source_expansion_is_direct_only() -> None:
    result = _run_relation_closure(
        source_nodes=("source_a", "far_source"),
        relations=(
            _relation("link_a", "source_a", "target"),
            _relation("link_far", "far_source", "source_a"),
        ),
        facts={"target.complete": False, "source_a.ready": False},
        prepare_targets=("source_a", "far_source"),
    )
    assert {item["key"] for item in result.planner_input.known_world.nodes} == {
        "target",
        "source_a",
    }
    assert all(
        item["source_node_key"] != "far_source"
        for item in result.planner_input.known_world.relations
    )
    assert ("prepare_source", "far_source") not in {
        (item.action_key, item.target_key) for item in result.planner_input.target_bindings
    }


def test_canonical_planner_input_preserves_source_contract() -> None:
    source_precondition = {
        "failure_condition": {
            "kind": "FACT_NOT_EQUALS",
            "selector": "ACTION_SOURCE",
            "fact_key": "ready",
            "value": True,
        }
    }
    context = PlanningContext(
        relevant_actions=(
            {
                "action_key": "use_source",
                "planner_constraints": {
                    "executor": {},
                    "target": {},
                    "locality": {},
                    "source_relation_type_key": "feeds",
                    "source_preconditions": [source_precondition],
                },
                "parameter_schema": [],
                "planner_effects": [],
            },
        ),
        relevant_actors=(
            {
                "actor_key": "operator",
                "role_key": "operator",
                "capabilities": [],
                "allowed_action_keys": ["use_source"],
                "current_known_state": {
                    "availability": "ACTIVE",
                    "current_region": None,
                    "command_reachability": "ONLINE",
                },
            },
        ),
    )
    result = _canonical_planner_input(context)
    contract = result.action_contracts[0]
    assert contract.source_relation_type_key == "feeds"
    assert contract.source_preconditions == (source_precondition,)


def _regression_planner_input() -> PlannerInput:
    actor_online = PlannerActorState(
        actor_key="online_operator",
        role_key="operator",
        capabilities=("EXECUTE",),
        allowed_action_keys=("complete_fact", "travel", "transport", "relay"),
        availability="ACTIVE",
        current_region=None,
        command_reachability="ONLINE",
    )
    actor_disconnected = PlannerActorState(
        actor_key="disconnected_operator",
        role_key="operator",
        capabilities=("EXECUTE",),
        allowed_action_keys=("recover_fact",),
        availability="ACTIVE",
        current_region=None,
        command_reachability="DISCONNECTED",
    )
    return PlannerInput(
        actors=(actor_online, actor_disconnected),
        action_contracts=(
            PlannerActionContract(
                action_key="complete_fact",
                executor_requirements={
                    "command_reachability": "ONLINE",
                    "required_capabilities": ["EXECUTE"],
                },
                target_contract={"required_interaction_key": "targetable"},
                locality={"type": "LOCAL_TARGET"},
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "target_key",
                        "fact_key": "complete",
                        "value": True,
                    },
                    {
                        "type": "RESOURCE_CONSUMPTION",
                        "resource_key": "support_parts",
                        "amount": -1,
                    },
                ),
            ),
            PlannerActionContract(
                action_key="recover_fact",
                executor_requirements={
                    "command_reachability": "ONLINE",
                    "required_capabilities": ["EXECUTE"],
                },
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "target_key",
                        "fact_key": "recovered",
                        "value": True,
                    },
                ),
            ),
            PlannerActionContract(
                action_key="travel",
                executor_requirements={
                    "command_reachability": "ONLINE",
                    "required_capabilities": ["EXECUTE"],
                },
                deterministic_effects=(
                    {
                        "type": "ACTOR_LOCATION",
                        "actor": "executor",
                        "value": "target_key",
                    },
                ),
            ),
            PlannerActionContract(
                action_key="transport",
                executor_requirements={
                    "command_reachability": "ONLINE",
                    "required_capabilities": ["EXECUTE"],
                },
                deterministic_effects=({"type": "RESOURCE_TRANSFER"},),
            ),
            PlannerActionContract(
                action_key="relay",
                executor_requirements={
                    "command_reachability": "ONLINE",
                    "required_capabilities": ["EXECUTE"],
                },
                target_contract={
                    "kind": "ACTOR",
                    "command_reachability": "DISCONNECTED",
                },
                deterministic_effects=(
                    {
                        "type": "ACTOR_COMMAND_REACHABILITY",
                        "target": "target_actor",
                        "value": "ONLINE",
                    },
                ),
            ),
        ),
        known_world=PlannerKnownWorldSlice(
            nodes=(
                {
                    "key": "target",
                    "access": "AVAILABLE",
                    "interactions": ["targetable"],
                },
                {
                    "key": "recovery_target",
                    "access": "AVAILABLE",
                    "interactions": [],
                },
            ),
            facts={
                "target.complete": False,
                "recovery_target.recovered": False,
            },
            resources={"support_parts": {"known_available": 1, "known_total": 1}},
        ),
    )


def test_existing_typed_dependency_dimensions_remain_generic() -> None:
    result = build_dependency_closure(
        _definition(("target", "recovery_target")),
        (
            _objective(
                ("complete_target", "target", "complete", (True,)),
                ("recover_target", "recovery_target", "recovered", (True,)),
            ),
        ),
        _regression_planner_input(),
    )
    action_keys = {item.action_key for item in result.planner_input.action_contracts}
    assert {"complete_fact", "recover_fact", "travel", "transport", "relay"} <= action_keys
    assert "support_parts" in result.planner_input.known_world.resources
    actor_keys = {item.actor_key for item in result.planner_input.actors}
    assert {"online_operator", "disconnected_operator"} <= actor_keys
