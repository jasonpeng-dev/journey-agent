from copy import deepcopy
from types import SimpleNamespace

from app.agent.dependency_closure import build_dependency_closure
from app.agent.planner_contract import planner_known_preconditions
from app.agent.planning_context import _action_planning_is_public
from app.agent.provider import (
    PlannerActionContract,
    PlannerActorState,
    PlannerInput,
    PlannerKnownWorldSlice,
    PlannerTargetBinding,
)
from app.domain.scenario_v2 import ScenarioDefinitionV2
from tests.unit.test_scenario_definition_v2 import _contract_scenario_document


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


def test_unknown_explicit_fact_precondition_is_projected_without_truth_leak() -> None:
    document = deepcopy(_contract_scenario_document())
    document["world"]["nodes"][1]["facts"] = [
        {
            "key": "ready",
            "name": "Ready",
            "value_type": "BOOLEAN",
            "initial_value": True,
            "initial_visibility": "HIDDEN",
        }
    ]
    document["rules"].insert(
        0,
        {
            "key": "ready_required",
            "phase": "PREFLIGHT",
            "action_key": "treat_patient",
            "priority": 100,
            "condition": {
                "kind": "FACT_NOT_EQUALS",
                "node": {"kind": "EXPLICIT", "node_key": "triage_room"},
                "fact_key": "ready",
                "value": True,
            },
            "effects": [
                {
                    "kind": "EMIT_FAILURE",
                    "failure_code": "READY_REQUIRED",
                    "message": "The treatment room must be ready.",
                    "retryable": True,
                }
            ],
        },
    )
    definition = ScenarioDefinitionV2.model_validate(document)
    action = next(item for item in definition.actions if item.key == "treat_patient")

    projected = planner_known_preconditions(
        definition,
        action,
        known_facts={("patient_one", "stable"): False},
        known_node_keys={"patient_one", "triage_room"},
    )

    ready = next(item for item in projected if item["fact_key"] == "ready")
    assert ready["node_key"] == "triage_room"
    assert ready["knowledge_status"] == "UNKNOWN"
    assert "current_value" not in ready


def test_unknown_fact_precondition_reenters_fixed_point_knowledge_and_fact_producers() -> None:
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
    executor = {
        "command_reachability": "ONLINE",
        "required_capabilities": ["EXECUTE"],
    }
    finish = PlannerActionContract(
        action_key="finish_goal",
        executor_requirements=executor,
        target_contract={"kind": "NODE", "required_interaction_key": "finishable"},
        locality={"type": "NONE"},
        known_preconditions=(
            {
                "selector": "EXPLICIT",
                "node_key": "support_node",
                "fact_key": "ready",
                "knowledge_status": "UNKNOWN",
                "failure_condition": {
                    "kind": "FACT_NOT_EQUALS",
                    "value": True,
                },
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
    inspect = PlannerActionContract(
        action_key="inspect_support",
        executor_requirements=executor,
        target_contract={"kind": "NODE", "required_interaction_key": "inspectable"},
        locality={"type": "NONE"},
        deterministic_effects=(
            {
                "type": "KNOWLEDGE_REVEAL",
                "target": "target_key",
                "fact_key": "ready",
            },
        ),
        knowledge_semantics=(
            {
                "type": "FACILITY_OR_ROUTE_KNOWLEDGE",
                "target": "INSPECT_TARGET",
                "reveals": "NON_RESOURCE_STATE",
            },
        ),
    )
    repair = PlannerActionContract(
        action_key="prepare_support",
        executor_requirements=executor,
        target_contract={"kind": "NODE", "required_interaction_key": "repairable"},
        locality={"type": "NONE"},
        deterministic_effects=(
            {
                "type": "FACT_MUTATION",
                "target": "target_key",
                "fact_key": "ready",
                "value": True,
            },
        ),
    )
    planner_input = PlannerInput(
        actors=(
            PlannerActorState(
                actor_key="operator",
                role_key="operator",
                capabilities=("EXECUTE",),
                allowed_action_keys=("finish_goal", "inspect_support", "prepare_support"),
                availability="ACTIVE",
                current_region=None,
                command_reachability="ONLINE",
            ),
        ),
        action_contracts=(finish, inspect, repair),
        target_bindings=(
            PlannerTargetBinding(action_key="finish_goal", target_key="goal_node"),
            PlannerTargetBinding(action_key="inspect_support", target_key="support_node"),
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
                {"key": "goal_node", "access": "AVAILABLE", "interactions": ["finishable"]},
                {
                    "key": "support_node",
                    "access": "AVAILABLE",
                    "interactions": ["inspectable", "repairable"],
                },
            ),
            facts={"goal_node.complete": False},
        ),
    )

    result = build_dependency_closure(definition, (objective,), planner_input)

    selected_actions = {item.action_key for item in result.planner_input.action_contracts}
    selected_bindings = {
        (item.action_key, item.target_key) for item in result.planner_input.target_bindings
    }
    assert {"finish_goal", "inspect_support", "prepare_support"}.issubset(selected_actions)
    assert {("inspect_support", "support_node"), ("prepare_support", "support_node")} <= (
        selected_bindings
    )
    unknown = result.planner_input.known_world.unknown_dependencies
    assert any(
        item.get("subject_key") == "support_node" and item.get("fact_key") == "ready"
        for item in unknown
    )
