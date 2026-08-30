from types import SimpleNamespace

from app.agent.dependency_closure import build_dependency_closure
from app.agent.planning_context import _action_planning_is_public
from app.agent.provider import (
    PlannerActionContract,
    PlannerActorState,
    PlannerInput,
    PlannerKnownWorldSlice,
    PlannerTargetBinding,
)


def _gate() -> SimpleNamespace:
    return SimpleNamespace(
        node_key="discovery_node",
        fact_key="discovered",
        accepted_values=(True,),
    )


def _gated_action() -> SimpleNamespace:
    return SimpleNamespace(
        key="finish_goal",
        planning=SimpleNamespace(
            knowledge_gate=_gate(),
            terminal_effects=(SimpleNamespace(node_key="goal_node", fact_key="complete"),),
            supporting_effects=(),
        ),
    )


def test_action_planning_gate_requires_public_accepted_knowledge() -> None:
    action = _gated_action()

    assert _action_planning_is_public(action, {}) is False
    assert _action_planning_is_public(action, {("discovery_node", "discovered"): False}) is False
    assert _action_planning_is_public(action, {("discovery_node", "discovered"): True}) is True


def test_gated_producer_expands_public_discovery_without_exposing_action() -> None:
    nodes = (
        SimpleNamespace(key="goal_node", node_type_key="facility"),
        SimpleNamespace(key="discovery_node", node_type_key="facility"),
    )
    node_by_key = {node.key: node for node in nodes}
    definition = SimpleNamespace(
        actions=(_gated_action(),),
        world=SimpleNamespace(nodes=nodes, node=lambda key: node_by_key.get(key)),
        metadata=SimpleNamespace(
            locality=SimpleNamespace(enabled=False, passability_fact_key=None),
        ),
    )
    objective = SimpleNamespace(
        key="synthetic_goal",
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
    planner_input = PlannerInput(
        actors=(
            PlannerActorState(
                actor_key="operator",
                role_key="operator",
                capabilities=("EXECUTE",),
                allowed_action_keys=("discover_requirement",),
                availability="ACTIVE",
                current_region=None,
                command_reachability="ONLINE",
            ),
        ),
        action_contracts=(
            PlannerActionContract(
                action_key="discover_requirement",
                executor_requirements={
                    "command_reachability": "ONLINE",
                    "required_capabilities": ["EXECUTE"],
                },
                target_contract={"kind": "NODE"},
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "discovery_node",
                        "fact_key": "discovered",
                        "value": True,
                    },
                ),
            ),
        ),
        known_world=PlannerKnownWorldSlice(
            nodes=(
                {"key": "goal_node", "access": "AVAILABLE", "interactions": []},
                {"key": "discovery_node", "access": "AVAILABLE", "interactions": []},
            ),
            facts={
                "goal_node.complete": False,
                "discovery_node.discovered": False,
            },
        ),
    )

    result = build_dependency_closure(definition, (objective,), planner_input)

    assert {item.action_key for item in result.planner_input.action_contracts} == {
        "discover_requirement"
    }
    assert "finish_goal" not in result.relevance_reason


def test_target_binding_fact_requirement_reenters_fixed_point_producer_expansion() -> None:
    nodes = (
        SimpleNamespace(key="goal_node", node_type_key="facility"),
        SimpleNamespace(key="support_node", node_type_key="facility"),
    )
    node_by_key = {node.key: node for node in nodes}
    definition = SimpleNamespace(
        actions=(),
        world=SimpleNamespace(nodes=nodes, node=lambda key: node_by_key.get(key)),
        metadata=SimpleNamespace(
            locality=SimpleNamespace(enabled=False, passability_fact_key=None),
        ),
    )
    objective = SimpleNamespace(
        key="synthetic_goal",
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
    contracts = (
        PlannerActionContract(
            action_key="finish_goal",
            executor_requirements={
                "command_reachability": "ONLINE",
                "required_capabilities": ["EXECUTE"],
            },
            target_contract={"kind": "NODE", "required_interaction_key": "finishable"},
        ),
        PlannerActionContract(
            action_key="prepare_support",
            executor_requirements={
                "command_reachability": "ONLINE",
                "required_capabilities": ["EXECUTE"],
            },
            target_contract={"kind": "NODE", "required_interaction_key": "preparable"},
        ),
    )
    planner_input = PlannerInput(
        actors=(
            PlannerActorState(
                actor_key="operator",
                role_key="operator",
                capabilities=("EXECUTE",),
                allowed_action_keys=("finish_goal", "prepare_support"),
                availability="ACTIVE",
                current_region=None,
                command_reachability="ONLINE",
            ),
        ),
        action_contracts=contracts,
        target_bindings=(
            PlannerTargetBinding(
                action_key="finish_goal",
                target_key="goal_node",
                requirements=(
                    {
                        "special_requirements": [
                            {
                                "node_key": "support_node",
                                "fact_key": "ready",
                                "operator": "EQ",
                                "value": True,
                            }
                        ]
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
            ),
            PlannerTargetBinding(
                action_key="prepare_support",
                target_key="support_node",
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "target_key",
                        "fact_key": "ready",
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
                    "interactions": ["finishable"],
                },
                {
                    "key": "support_node",
                    "access": "AVAILABLE",
                    "interactions": ["preparable"],
                },
            ),
            facts={"goal_node.complete": False, "support_node.ready": False},
        ),
    )

    result = build_dependency_closure(definition, (objective,), planner_input)

    assert {
        (item.action_key, item.target_key) for item in result.planner_input.target_bindings
    } == {("finish_goal", "goal_node"), ("prepare_support", "support_node")}
