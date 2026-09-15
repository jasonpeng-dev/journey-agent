from __future__ import annotations

from app.domain.action_invocation import (
    action_invocation_from_operation,
    action_invocation_from_tool_arguments,
    canonical_action_invocation,
    transport_bindings_from_outcome,
)
from app.domain.scenario_v2 import (
    ActionBehavior,
    ActionDefinitionV2,
    ActionExecutionMode,
    ActionLocality,
    ActionParameterType,
    ActionParameterV2,
    ActionPlanningProjectionV2,
    EngineCapability,
    ExpectedOutcomeV2,
)


def _transport_action() -> ActionDefinitionV2:
    return ActionDefinitionV2(
        key="transport_resource",
        name="Transport Resource",
        description="Carry resources.",
        required_interaction_key="transport_destination",
        execution_mode=ActionExecutionMode.IMMEDIATE,
        parameters=(
            ActionParameterV2(
                key="resource_key",
                name="Resource",
                value_type=ActionParameterType.STRING,
            ),
            ActionParameterV2(
                key="amount",
                name="Amount",
                value_type=ActionParameterType.INTEGER,
                minimum=1,
            ),
        ),
        allowed_actor_capabilities=(EngineCapability.EXECUTE_ACTION,),
        expected_outcomes=(
            ExpectedOutcomeV2(code="TRANSPORTED", name="Transported", success=True),
        ),
        planning=ActionPlanningProjectionV2(success_outcome_codes=("TRANSPORTED",)),
        behavior=ActionBehavior.TRANSPORT_RESOURCE,
        locality=ActionLocality.TRANSPORT_ENDPOINT,
    )


def test_transport_invocation_identity_is_order_independent_and_normalizes_legacy_input() -> None:
    action = _transport_action()
    structured = canonical_action_invocation(
        action,
        actor_key="carrier",
        target_key="region_b",
        parameters={
            "resources": [
                {"resource_key": "cargo_beta", "amount": 15},
                {"resource_key": "cargo_alpha", "amount": 10},
            ]
        },
        bindings={"destination_region": "region_b", "source_region": "region_a"},
    )
    reordered = canonical_action_invocation(
        action,
        actor_key="carrier",
        target_key="region_b",
        parameters={
            "resources": [
                {"resource_key": "cargo_alpha", "amount": 10},
                {"resource_key": "cargo_beta", "amount": 15},
            ]
        },
        bindings={"source_region": "region_a", "destination_region": "region_b"},
    )
    legacy = canonical_action_invocation(
        action,
        actor_key="carrier",
        target_key="region_b",
        parameters={"resource_key": "cargo_alpha", "amount": 10},
    )
    one_item = canonical_action_invocation(
        action,
        actor_key="carrier",
        target_key="region_b",
        parameters={"resources": [{"resource_key": "cargo_alpha", "amount": 10}]},
    )

    assert structured == reordered
    assert structured.signature == reordered.signature
    assert legacy == one_item


def test_invocation_identity_changes_for_actor_target_and_amount() -> None:
    action = _transport_action()
    base = canonical_action_invocation(
        action,
        actor_key="carrier",
        target_key="region_b",
        parameters={"resource_key": "cargo_alpha", "amount": 10},
    )

    assert base != base.model_copy(update={"actor_key": "other_carrier"})
    assert base != base.model_copy(update={"target_key": "region_c"})
    assert base != base.model_copy(
        update={"parameters": {"resources": [{"resource_key": "cargo_alpha", "amount": 11}]}}
    )


def test_runtime_transport_binding_uses_authoritative_resource_deltas() -> None:
    action = _transport_action()
    outcome = {
        "resource_mutations": [
            {
                "resource_key": "cargo_alpha",
                "amount": -10,
                "scope_node_key": "region_a",
                "pool_key": "source_pool",
            },
            {
                "resource_key": "cargo_alpha",
                "amount": 10,
                "scope_node_key": "region_b",
                "pool_key": "__runtime_known_inflow__",
            },
        ]
    }

    assert transport_bindings_from_outcome(action, outcome) == {
        "source_region": "region_a",
        "destination_region": "region_b",
    }
    invocation = action_invocation_from_operation(
        action,
        actor_key="carrier",
        target_key="region_b",
        parameters={"resource_key": "cargo_alpha", "amount": 10},
        outcome=outcome,
    )
    assert invocation.bindings[0].role == "destination_region"
    assert invocation.bindings[1].role == "source_region"


def test_persisted_tool_arguments_adapt_to_the_same_canonical_value() -> None:
    action = _transport_action()
    invocation = action_invocation_from_tool_arguments(
        action,
        actor_key="carrier",
        tool_arguments={
            "action_key": "transport_resource",
            "target_key": "region_b",
            "parameters": {"resources": [{"resource_key": "cargo_alpha", "amount": 10}]},
        },
    )

    assert invocation.action_key == "transport_resource"
    assert invocation.parameters == {"resources": [{"resource_key": "cargo_alpha", "amount": 10}]}
