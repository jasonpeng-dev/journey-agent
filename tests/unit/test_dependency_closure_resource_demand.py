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


def _definition(node_keys: tuple[str, ...]) -> Any:
    nodes = tuple(SimpleNamespace(key=key, node_type_key="synthetic_node") for key in node_keys)
    node_by_key = {node.key: node for node in nodes}
    return SimpleNamespace(
        world=SimpleNamespace(nodes=nodes, node=lambda key: node_by_key.get(key)),
        metadata=SimpleNamespace(
            locality=SimpleNamespace(enabled=False, passability_fact_key=None),
        ),
    )


def _objective(requirements: tuple[tuple[str, str], ...]) -> Any:
    return SimpleNamespace(
        key="synthetic_objective",
        completion_requirements=tuple(
            SimpleNamespace(
                key=requirement_key,
                node_key=node_key,
                fact_key="complete",
                accepted_values=(True,),
            )
            for requirement_key, node_key in requirements
        ),
        prerequisites=(),
    )


def _planner_input(
    *,
    goals: tuple[str, ...],
    producer_costs: dict[str, tuple[int, ...]],
    producer_targets: dict[str, str] | None = None,
    available_amount: int,
    duplicate_cost: bool = False,
) -> PlannerInput:
    unlock_node = "unlock_node"
    actions: list[PlannerActionContract] = []
    bindings: list[PlannerTargetBinding] = []
    allowed_actions = ["unlock_facility"]
    for producer_key, costs in producer_costs.items():
        allowed_actions.append(producer_key)
        actions.append(
            PlannerActionContract(
                action_key=producer_key,
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
            )
        )
        target_key = (producer_targets or {}).get(producer_key, goals[0])
        requirements = tuple({"cost": {"support_material": amount}} for amount in costs)
        if duplicate_cost:
            requirements = (
                {"cost": {"support_material": costs[0]}},
                {"cost": {"support_material": costs[0]}},
            )
        bindings.append(
            PlannerTargetBinding(
                action_key=producer_key,
                target_key=target_key,
                requirements=requirements,
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "target_key",
                        "fact_key": "complete",
                        "value": True,
                    },
                ),
            )
        )

    actions.append(
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
            ),
        )
    )
    bindings.append(
        PlannerTargetBinding(
            action_key="unlock_facility",
            target_key=unlock_node,
            requirements=({"cost": {"unlock_material": 1}},),
            deterministic_effects=(
                {
                    "type": "FACT_MUTATION",
                    "target": "target_key",
                    "fact_key": "operational",
                    "value": True,
                },
            ),
        )
    )

    known_facts = {f"{goal}.complete": False for goal in goals}
    known_facts[f"{unlock_node}.operational"] = False
    nodes = (
        *(
            {
                "key": goal,
                "access": "AVAILABLE",
                "interactions": ["completeable"],
            }
            for goal in goals
        ),
        {
            "key": unlock_node,
            "access": "AVAILABLE",
            "interactions": ["repairable"],
        },
    )
    locked_pool = {
        "visibility": "VISIBLE",
        "availability": "UNAVAILABLE",
        "availability_requirement_status": "KNOWN",
        "availability_requirement": {
            "node_key": unlock_node,
            "fact_key": "operational",
            "value": True,
        },
    }
    return PlannerInput(
        actors=(
            PlannerActorState(
                actor_key="operator",
                role_key="operator",
                capabilities=("EXECUTE",),
                allowed_action_keys=tuple(allowed_actions),
                availability="ACTIVE",
                current_region=None,
                command_reachability="ONLINE",
            ),
        ),
        action_contracts=tuple(actions),
        target_bindings=tuple(bindings),
        known_world=PlannerKnownWorldSlice(
            nodes=nodes,
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
                            "pools": [locked_pool],
                        }
                    },
                },
                "unlock_material": {
                    "known_total": 1,
                    "known_available": 1,
                    "scopes": {"global": {"known_total": 1, "known_available": 1}},
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
    goals: tuple[str, ...] = ("goal_node",),
    producer_costs: dict[str, tuple[int, ...]] | None = None,
    producer_targets: dict[str, str] | None = None,
    available_amount: int = 10,
    duplicate_cost: bool = False,
) -> Any:
    if producer_costs is None:
        producer_costs = {"producer_a": (10,), "producer_b": (10,)}
    planner_input = _planner_input(
        goals=goals,
        producer_costs=producer_costs,
        producer_targets=producer_targets,
        available_amount=available_amount,
        duplicate_cost=duplicate_cost,
    )
    return build_dependency_closure(
        _definition((*goals, "unlock_node")),
        (_objective(tuple((f"complete_{goal}", goal) for goal in goals)),),
        planner_input,
    )


def _action_keys(result: Any) -> set[str]:
    return {item.action_key for item in result.planner_input.action_contracts}


def _binding_keys(result: Any) -> set[tuple[str, str]]:
    return {(item.action_key, item.target_key) for item in result.planner_input.target_bindings}


def test_alternative_branches_use_maximum_demand_and_remain_retained() -> None:
    result = _run_closure()

    assert {"producer_a", "producer_b"} <= _action_keys(result)
    assert {
        ("producer_a", "goal_node"),
        ("producer_b", "goal_node"),
    } <= _binding_keys(result)
    assert "unlock_facility" not in _action_keys(result)


def test_alternative_branch_with_larger_demand_retains_locked_pool_support() -> None:
    result = _run_closure(producer_costs={"producer_a": (20,), "producer_b": (5,)})

    assert {"producer_a", "producer_b", "unlock_facility"} <= _action_keys(result)
    assert ("unlock_facility", "unlock_node") in _binding_keys(result)


def test_independent_objective_requirements_sum_demand() -> None:
    result = _run_closure(
        goals=("goal_a", "goal_b"),
        producer_costs={"producer_a": (20,), "producer_b": (10,)},
        producer_targets={"producer_a": "goal_a", "producer_b": "goal_b"},
        available_amount=20,
    )

    assert "unlock_facility" in _action_keys(result)
    assert ("unlock_facility", "unlock_node") in _binding_keys(result)


def test_independent_requirements_with_equal_amounts_still_sum() -> None:
    result = _run_closure(
        goals=("goal_a", "goal_b"),
        producer_costs={"producer_a": (20,), "producer_b": (20,)},
        producer_targets={"producer_a": "goal_a", "producer_b": "goal_b"},
        available_amount=20,
    )

    assert "unlock_facility" in _action_keys(result)


def test_same_branch_multiple_resource_requirements_sum() -> None:
    result = _run_closure(
        producer_costs={"producer_a": (10, 5)},
        available_amount=10,
    )

    assert "unlock_facility" in _action_keys(result)


def test_exact_duplicate_resource_dependency_is_deduplicated() -> None:
    result = _run_closure(
        producer_costs={"producer_a": (10,)},
        available_amount=10,
        duplicate_cost=True,
    )

    assert "producer_a" in _action_keys(result)
    assert "unlock_facility" not in _action_keys(result)


def test_multiple_or_groups_are_summed_conservatively() -> None:
    result = _run_closure(
        goals=("goal_a", "goal_b"),
        producer_costs={
            "producer_a1": (10,),
            "producer_a2": (5,),
            "producer_b1": (8,),
            "producer_b2": (4,),
        },
        producer_targets={
            "producer_a1": "goal_a",
            "producer_a2": "goal_a",
            "producer_b1": "goal_b",
            "producer_b2": "goal_b",
        },
        available_amount=15,
    )

    assert {
        "producer_a1",
        "producer_a2",
        "producer_b1",
        "producer_b2",
        "unlock_facility",
    } <= _action_keys(result)
