from __future__ import annotations

from types import SimpleNamespace

from app.agent.generic import (
    GenericAgentService,
    _ProjectedFact,
    _validate_plan_segment_contract,
)
from app.agent.planner_contract import planner_resource_requirements
from app.agent.provider import (
    PlannerActionContract,
    PlannerInput,
    PlanProposal,
    PlanStepProposal,
)
from app.domain.scenario_v2 import (
    ComparisonOperator,
    ConditionKind,
    ObjectiveDefinitionV2,
    ObjectiveRequirementV2,
    RulePhase,
)
from app.domain.world import Visibility


def test_generic_resource_compare_becomes_a_typed_public_requirement() -> None:
    action = SimpleNamespace(key="assemble_bridge")
    condition = SimpleNamespace(
        kind=ConditionKind.RESOURCE_COMPARE,
        node=None,
        resource_key="bridge_parts",
        resource_scope=SimpleNamespace(
            model_dump=lambda mode: {"kind": "EXPLICIT", "node_key": "parts_yard"}
        ),
        operator=ComparisonOperator.LT,
        value=4,
    )
    definition = SimpleNamespace(
        rules=(
            SimpleNamespace(
                action_key="assemble_bridge",
                phase=RulePhase.PREFLIGHT,
                condition=condition,
            ),
        )
    )

    requirements = planner_resource_requirements(
        definition,
        action,
        known_resources={
            "bridge_parts": {
                "scopes": {
                    "parts_yard": {
                        "knowledge_status": "KNOWN",
                        "known_available": 2,
                    }
                }
            }
        },
    )

    assert requirements == (
        {
            "resource_key": "bridge_parts",
            "scope": {"kind": "EXPLICIT", "node_key": "parts_yard"},
            "minimum": 4,
            "known_status": "KNOWN",
            "known_available": 2,
        },
    )


def test_action_contract_keeps_resource_requirement_separate_from_consumption_effect() -> None:
    contract = PlannerActionContract(
        action_key="generate_power",
        resource_requirements=(
            {
                "resource_key": "emergency_fuel",
                "scope": {
                    "kind": "EXPLICIT",
                    "node_key": "southeast_heights_district",
                },
                "minimum": 50,
            },
        ),
        deterministic_effects=(
            {
                "type": "RESOURCE_DELTA",
                "resource_key": "emergency_fuel",
                "amount": -50,
            },
        ),
    )

    payload = contract.model_dump(mode="json")
    assert payload["resource_requirements"] == [
        {
            "resource_key": "emergency_fuel",
            "scope": {
                "kind": "EXPLICIT",
                "node_key": "southeast_heights_district",
            },
            "minimum": 50,
        }
    ]
    assert payload["deterministic_effects"] == [
        {
            "type": "RESOURCE_DELTA",
            "resource_key": "emergency_fuel",
            "amount": -50,
        }
    ]


def test_objective_completion_requires_projected_completion() -> None:
    objective = ObjectiveDefinitionV2(
        key="restore_bridge",
        name="Restore bridge",
        description="Restore bridge",
        completion_requirements=(
            ObjectiveRequirementV2(
                key="bridge_operational",
                node_key="bridge",
                fact_key="operational",
                accepted_values=(True,),
                description="Bridge is operational",
            ),
        ),
    )
    definition = SimpleNamespace(derived_state_definitions={})

    incomplete = GenericAgentService._projected_objective_completion_violation(
        definition,
        (objective,),
        projected_known_facts={("bridge", "operational"): _ProjectedFact(False, Visibility.KNOWN)},
        projected_pools={},
        projected_region_knowledge={},
        projected_known_resource_balance={},
    )
    assert incomplete is not None
    assert incomplete["code"] == "OBJECTIVE_COMPLETION_NOT_PROVEN"

    complete = GenericAgentService._projected_objective_completion_violation(
        definition,
        (objective,),
        projected_known_facts={("bridge", "operational"): _ProjectedFact(True, Visibility.KNOWN)},
        projected_pools={},
        projected_region_knowledge={},
        projected_known_resource_balance={},
    )
    assert complete is None


def test_segment_complete_is_a_valid_non_final_stop() -> None:
    diagnostics = _validate_plan_segment_contract(
        PlanProposal(
            segment_goal="advance the current objective",
            goal_link="supports the frozen objective",
            continuation_intent="continue the unfinished objective mainline",
            stop_reason="SEGMENT_COMPLETE",
            steps=(
                PlanStepProposal(
                    action_key="support_action",
                    actor_key="actor",
                    target_key="target",
                ),
            ),
        ),
        PlannerInput(),
    )

    assert diagnostics == ()
