from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.agent.dependency_closure import build_dependency_closure
from app.agent.provider import (
    PlannerActionContract,
    PlannerActorState,
    PlannerInput,
    PlannerKnownWorldSlice,
    PlannerTargetBinding,
)


def _definition() -> Any:
    keys = ("goal_node", "unlock_facility_a", "unlock_facility_b", "synthetic_region")
    nodes = tuple(SimpleNamespace(key=key, node_type_key="synthetic_node") for key in keys)
    node_by_key = {node.key: node for node in nodes}
    return SimpleNamespace(
        world=SimpleNamespace(nodes=nodes, node=lambda key: node_by_key.get(key)),
        metadata=SimpleNamespace(
            locality=SimpleNamespace(enabled=False, passability_fact_key=None),
        ),
    )


def _objective() -> Any:
    return SimpleNamespace(
        key="synthetic_objective",
        completion_requirements=(
            SimpleNamespace(
                key="complete_goal",
                node_key="goal_node",
                fact_key="complete",
                accepted_values=(True,),
            ),
        ),
        prerequisites=(),
    )


def _resource_objective(*, gated: bool = False) -> Any:
    return SimpleNamespace(
        key="resource_objective",
        completion_requirements=(
            SimpleNamespace(
                key="keep_stock",
                kind="RESOURCE_AT_LEAST",
                region_key="synthetic_region",
                resource_key="support_material",
                minimum=5,
                knowledge_gate=(
                    SimpleNamespace(
                        node_key="goal_node",
                        fact_key="complete",
                        accepted_values=(True,),
                    )
                    if gated
                    else None
                ),
            ),
        ),
        prerequisites=(),
    )


def _planner_input(
    *,
    available_amount: int = 0,
    pools: tuple[dict[str, object], ...] | None = None,
    known_facts: dict[str, object] | None = None,
) -> PlannerInput:
    if pools is None:
        pools = (
            {
                "visibility": "VISIBLE",
                "availability": "UNAVAILABLE",
                "availability_requirement_status": "KNOWN",
                "availability_requirement": {
                    "node_key": "unlock_facility_a",
                    "fact_key": "operational",
                    "value": True,
                },
            },
        )
    if known_facts is None:
        known_facts = {
            "goal_node.complete": False,
            "unlock_facility_a.operational": False,
            "unlock_facility_b.operational": False,
        }
    return PlannerInput(
        actors=(
            PlannerActorState(
                actor_key="online_operator",
                role_key="operator",
                capabilities=("EXECUTE",),
                allowed_action_keys=("complete_goal", "reconnect_operator"),
                availability="ACTIVE",
                current_region=None,
                command_reachability="ONLINE",
            ),
            PlannerActorState(
                actor_key="facility_worker",
                role_key="technician",
                capabilities=("EXECUTE",),
                allowed_action_keys=("unlock_facility",),
                availability="ACTIVE",
                current_region=None,
                command_reachability="DISCONNECTED",
            ),
        ),
        action_contracts=(
            PlannerActionContract(
                action_key="complete_goal",
                executor_requirements={
                    "command_reachability": "ONLINE",
                    "required_capabilities": ["EXECUTE"],
                },
                target_contract={
                    "kind": "NODE",
                    "required_interaction_key": "completeable",
                },
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "target_key",
                        "fact_key": "complete",
                        "value": True,
                    },
                ),
            ),
            PlannerActionContract(
                action_key="unlock_facility",
                executor_requirements={
                    "command_reachability": "ONLINE",
                    "required_capabilities": ["EXECUTE"],
                },
                target_contract={
                    "kind": "NODE",
                    "required_interaction_key": "repairable",
                },
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "target_key",
                        "fact_key": "operational",
                        "value": True,
                    },
                    {
                        "type": "RESOURCE_CONSUMPTION",
                        "resource_key": "unlock_material",
                        "amount": -1,
                    },
                ),
            ),
            PlannerActionContract(
                action_key="reconnect_operator",
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
        target_bindings=(
            PlannerTargetBinding(
                action_key="complete_goal",
                target_key="goal_node",
                requirements=({"cost": {"support_material": 5}},),
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "target_key",
                        "fact_key": "complete",
                        "value": True,
                    },
                ),
            ),
            PlannerTargetBinding(
                action_key="unlock_facility",
                target_key="unlock_facility_a",
                requirements=({"cost": {"unlock_material": 1}},),
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "target_key",
                        "fact_key": "operational",
                        "value": True,
                    },
                ),
            ),
            PlannerTargetBinding(
                action_key="unlock_facility",
                target_key="unlock_facility_b",
                requirements=({"cost": {"unlock_material": 1}},),
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "target_key",
                        "fact_key": "operational",
                        "value": True,
                    },
                ),
            ),
        ),
        known_world=PlannerKnownWorldSlice(
            nodes=(
                {
                    "key": "goal_node",
                    "access": "AVAILABLE",
                    "interactions": ["completeable"],
                },
                {
                    "key": "unlock_facility_a",
                    "access": "AVAILABLE",
                    "interactions": ["repairable"],
                },
                {
                    "key": "unlock_facility_b",
                    "access": "AVAILABLE",
                    "interactions": ["repairable"],
                },
                {
                    "key": "synthetic_region",
                    "access": "AVAILABLE",
                    "interactions": [],
                },
            ),
            facts=known_facts,
            resources={
                "support_material": {
                    "known_total": available_amount,
                    "known_available": available_amount,
                    "scopes": {
                        "synthetic_region": {
                            "known_total": available_amount,
                            "known_available": available_amount,
                            "resource_inventory_visibility": "VISIBLE",
                            "resource_survey_completed": True,
                            "pools": list(pools),
                        }
                    },
                },
                "unlock_material": {
                    "known_total": 1,
                    "known_available": 1,
                    "scopes": {
                        "global": {
                            "known_total": 1,
                            "known_available": 1,
                        }
                    },
                },
            },
            resource_knowledge=(
                {
                    "region_key": "synthetic_region",
                    "resource_inventory_visibility": "VISIBLE",
                    "resource_survey_completed": True,
                },
            ),
        ),
    )


def _run_closure(
    *,
    available_amount: int = 0,
    pools: tuple[dict[str, object], ...] | None = None,
    known_facts: dict[str, object] | None = None,
) -> Any:
    return build_dependency_closure(
        _definition(),
        (_objective(),),
        _planner_input(
            available_amount=available_amount,
            pools=pools,
            known_facts=known_facts,
        ),
    )


def _action_keys(result: Any) -> set[str]:
    return {item.action_key for item in result.planner_input.action_contracts}


def _node_keys(result: Any) -> set[str]:
    return {item["key"] for item in result.planner_input.known_world.nodes}


def test_known_unavailable_pool_expands_public_unlock_fact_and_producer() -> None:
    result = _run_closure()

    assert "unlock_facility" in _action_keys(result)
    assert "unlock_facility_a" in _node_keys(result)
    assert ("unlock_facility", "unlock_facility_a") in {
        (item.action_key, item.target_key) for item in result.planner_input.target_bindings
    }


def test_resource_objective_enters_closure_without_selecting_a_source() -> None:
    result = build_dependency_closure(
        _definition(),
        (_resource_objective(),),
        _planner_input(available_amount=5),
    )

    assert "support_material" in result.planner_input.known_world.resources
    assert "synthetic_region" in _node_keys(result)


def test_resource_objective_unknown_inventory_is_not_reported_as_unknown_source() -> None:
    base = _planner_input(available_amount=0)
    resources = {
        "support_material": {
            "scopes": {
                "synthetic_region": {
                    "knowledge_status": "UNKNOWN",
                }
            }
        }
    }
    planner_input = base.model_copy(
        update={"known_world": base.known_world.model_copy(update={"resources": resources})}
    )

    result = build_dependency_closure(
        _definition(),
        (_resource_objective(),),
        planner_input,
    )

    unknown = next(
        item
        for item in result.planner_input.known_world.unknown_dependencies
        if item.get("dimension") == "RESOURCE_SOURCE"
    )
    assert unknown["source_knowledge_status"] == "KNOWN"
    assert unknown["inventory_knowledge_status"] == "UNKNOWN"
    assert unknown["knowledge_status_code"] == "RESOURCE_INVENTORY_UNKNOWN"
    assert "known_available_amount" not in unknown
    assert "deficit" not in unknown


def test_gated_resource_objective_is_absent_before_public_gate_reveal() -> None:
    result = build_dependency_closure(
        _definition(),
        (_resource_objective(gated=True),),
        _planner_input(available_amount=5),
    )

    assert "support_material" not in result.planner_input.known_world.resources


def test_unlock_fact_expansion_reaches_actor_and_resource_prerequisites() -> None:
    result = _run_closure()

    assert {"unlock_facility", "reconnect_operator"} <= _action_keys(result)
    assert {item.actor_key for item in result.planner_input.actors} == {
        "online_operator",
        "facility_worker",
    }
    assert "unlock_material" in result.planner_input.known_world.resources


def test_unknown_unlock_requirement_is_not_expanded_or_leaked() -> None:
    result = _run_closure(known_facts={"goal_node.complete": False})
    assert "unlock_facility" not in _action_keys(result)
    assert "unlock_facility_a" not in _node_keys(result)
    assert "unlock_facility_a.operational" not in result.planner_input.known_world.facts

    unknown_status_pool = (
        {
            "visibility": "VISIBLE",
            "availability": "UNAVAILABLE",
            "availability_requirement_status": "UNKNOWN",
            "availability_requirement": {
                "node_key": "unlock_facility_a",
                "fact_key": "operational",
                "value": True,
            },
        },
    )
    result_with_unknown_status = _run_closure(pools=unknown_status_pool)
    assert "unlock_facility" not in _action_keys(result_with_unknown_status)

    hidden_pool = (
        {
            "visibility": "HIDDEN",
            "availability": "UNAVAILABLE",
            "availability_requirement_status": "KNOWN",
            "availability_requirement": {
                "node_key": "unlock_facility_a",
                "fact_key": "operational",
                "value": True,
            },
        },
    )
    result_with_hidden_pool = _run_closure(pools=hidden_pool)
    assert "unlock_facility" not in _action_keys(result_with_hidden_pool)


def test_multiple_public_unavailable_pools_are_all_retained() -> None:
    pools = (
        {
            "visibility": "VISIBLE",
            "availability": "UNAVAILABLE",
            "availability_requirement_status": "KNOWN",
            "availability_requirement": {
                "node_key": "unlock_facility_a",
                "fact_key": "operational",
                "value": True,
            },
        },
        {
            "visibility": "VISIBLE",
            "availability": "UNAVAILABLE",
            "availability_requirement_status": "KNOWN",
            "availability_requirement": {
                "node_key": "unlock_facility_b",
                "fact_key": "operational",
                "value": True,
            },
        },
    )
    result = _run_closure(pools=pools)

    assert {
        (item.action_key, item.target_key)
        for item in result.planner_input.target_bindings
        if item.action_key == "unlock_facility"
    } == {
        ("unlock_facility", "unlock_facility_a"),
        ("unlock_facility", "unlock_facility_b"),
    }
    support_pools = result.planner_input.known_world.resources["support_material"]["scopes"][
        "synthetic_region"
    ]["pools"]
    assert len(support_pools) == 2


def test_available_pool_does_not_create_unlock_dependency() -> None:
    available_pool = (
        {
            "visibility": "VISIBLE",
            "availability": "AVAILABLE",
            "availability_requirement_status": "KNOWN",
            "availability_requirement": {
                "node_key": "unlock_facility_a",
                "fact_key": "operational",
                "value": True,
            },
        },
    )
    result = _run_closure(available_amount=5, pools=available_pool)
    assert "unlock_facility" not in _action_keys(result)


def test_satisfied_unlock_requirement_does_not_retain_duplicate_producer() -> None:
    result = _run_closure(
        known_facts={
            "goal_node.complete": False,
            "unlock_facility_a.operational": True,
        },
    )
    assert "unlock_facility" not in _action_keys(result)
    assert "unlock_facility_a" not in _node_keys(result)


def test_no_public_unlock_candidate_does_not_fabricate_one() -> None:
    result = _run_closure(pools=())
    assert "unlock_facility" not in _action_keys(result)
