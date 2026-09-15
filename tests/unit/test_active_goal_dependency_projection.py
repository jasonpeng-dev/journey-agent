from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from app.agent.dependency_closure import build_dependency_closure
from app.agent.formal_goal_projection import formal_goal_planning_objectives
from app.agent.provider import PlannerInput, PlannerKnownWorldSlice, PlanRequest
from app.domain.formal_goal import (
    FormalGoalContractV1,
    FormalGoalObjectiveSourceV1,
    FormalGoalRequirementV1,
    FormalGoalScenarioProofV1,
    FormalGoalSourceKind,
    canonical_requirement_identity,
)
from app.domain.scenario_v2 import ObjectiveRequirementV2


def _definition(*, derived_states: Mapping[str, object] | None = None) -> Any:
    nodes = tuple(
        SimpleNamespace(key=key, node_type_key="synthetic_node")
        for key in ("goal_node", "region_a", "region_b", "gate_node")
    )
    node_by_key = {node.key: node for node in nodes}
    return SimpleNamespace(
        world=SimpleNamespace(
            nodes=nodes,
            node=lambda key: node_by_key.get(key),
        ),
        metadata=SimpleNamespace(
            locality=SimpleNamespace(
                enabled=False,
                passability_fact_key=None,
                region_node_type_key="region",
            )
        ),
        actions=(),
        objective_definitions={},
        derived_state_definitions=derived_states or {},
    )


def _objective(*requirements: object) -> Any:
    return SimpleNamespace(
        key="synthetic_goal",
        completion_requirements=tuple(requirements),
        prerequisites=(),
    )


def _fact_requirement(*, accepted_values: tuple[object, ...] = (True,), gate: object = None) -> Any:
    return SimpleNamespace(
        key="fact_requirement",
        kind="FACT",
        node_key="goal_node",
        fact_key="ready",
        accepted_values=accepted_values,
        knowledge_gate=gate,
    )


def _resource_requirement(*, minimum: int = 100) -> Any:
    return SimpleNamespace(
        key="resource_requirement",
        kind="RESOURCE_AT_LEAST",
        region_key="region_a",
        resource_key="fuel",
        minimum=minimum,
        knowledge_gate=None,
    )


def _derived_requirement(
    *,
    derived_key: str = "goal_state",
    accepted_values: tuple[object, ...] = (True,),
) -> Any:
    return SimpleNamespace(
        key="derived_requirement",
        kind="DERIVED_STATE",
        derived_key=derived_key,
        accepted_values=accepted_values,
        knowledge_gate=None,
    )


def _planner_input(
    *,
    facts: dict[str, object] | None = None,
    fuel: int | None = None,
    other_region_fuel: int | None = None,
    include_resource_knowledge: bool = True,
) -> PlannerInput:
    resources: dict[str, object] = {}
    if fuel is not None:
        scopes: dict[str, object] = {
            "region_a": {
                "known_total": fuel,
                "known_available": fuel,
                "pools": [],
            }
        }
        if other_region_fuel is not None:
            scopes["region_b"] = {
                "known_total": other_region_fuel,
                "known_available": other_region_fuel,
                "pools": [],
            }
        resources["fuel"] = {
            "known_total": fuel + (other_region_fuel or 0),
            "known_available": fuel + (other_region_fuel or 0),
            "scopes": scopes,
        }
    return PlannerInput(
        known_world=PlannerKnownWorldSlice(
            nodes=tuple(
                {"key": key, "access": "AVAILABLE"}
                for key in ("goal_node", "region_a", "region_b", "gate_node")
            ),
            facts=facts or {},
            resources=resources,
            resource_knowledge=(
                {
                    "region_key": "region_a",
                    "resource_inventory_visibility": "VISIBLE",
                    "resource_survey_completed": True,
                },
                {
                    "region_key": "region_b",
                    "resource_inventory_visibility": "VISIBLE",
                    "resource_survey_completed": True,
                },
            )
            if include_resource_knowledge
            else (),
        )
    )


def _active(result: Any) -> tuple[dict[str, Any], ...]:
    return tuple(
        item.model_dump(mode="json", exclude_none=False)
        for item in result.planner_input.active_goal_dependencies
    )


def test_fact_projection_transitions_unknown_to_known_unsatisfied_to_satisfied() -> None:
    definition = _definition()
    objective = _objective(_fact_requirement())

    unknown = build_dependency_closure(definition, (objective,), _planner_input())
    unknown_payload = _active(unknown)
    assert len(unknown_payload) == 1
    assert unknown_payload[0]["kind"] == "FACT"
    assert unknown_payload[0]["knowledge_status"] == "UNKNOWN"
    assert unknown_payload[0]["satisfaction_status"] == "UNKNOWN"
    assert "current_known_value" not in unknown_payload[0]
    dependency_id = unknown_payload[0]["dependency_id"]

    unsatisfied = build_dependency_closure(
        definition,
        (objective,),
        _planner_input(facts={"goal_node.ready": False}),
    )
    unsatisfied_payload = _active(unsatisfied)
    assert unsatisfied_payload[0]["dependency_id"] == dependency_id
    assert unsatisfied_payload[0]["knowledge_status"] == "KNOWN"
    assert unsatisfied_payload[0]["satisfaction_status"] == "UNSATISFIED"
    assert unsatisfied_payload[0]["current_known_value"] is False

    satisfied = build_dependency_closure(
        definition,
        (objective,),
        _planner_input(facts={"goal_node.ready": True}),
    )
    assert satisfied.planner_input.active_goal_dependencies == ()


def test_resource_projection_exposes_public_shortfall_and_dedupes_same_typed_dependency() -> None:
    definition = _definition()
    objective = _objective(_resource_requirement(), _resource_requirement())

    unknown = build_dependency_closure(
        definition,
        (objective,),
        _planner_input(fuel=70, include_resource_knowledge=False),
    )
    unknown_payload = _active(unknown)
    assert len(unknown_payload) == 1
    assert unknown_payload[0]["kind"] == "RESOURCE_AT_LEAST"
    assert unknown_payload[0]["knowledge_status"] == "UNKNOWN"
    assert "current_known_available" not in unknown_payload[0]
    assert "deficit" not in unknown_payload[0]

    unsatisfied = build_dependency_closure(
        definition,
        (objective,),
        _planner_input(fuel=70),
    )
    unsatisfied_payload = _active(unsatisfied)
    assert len(unsatisfied_payload) == 1
    assert unsatisfied_payload[0]["knowledge_status"] == "KNOWN"
    assert unsatisfied_payload[0]["current_known_available"] == 70
    assert unsatisfied_payload[0]["minimum"] == 100
    assert unsatisfied_payload[0]["deficit"] == 30

    satisfied = build_dependency_closure(
        definition,
        (objective,),
        _planner_input(fuel=100),
    )
    assert satisfied.planner_input.active_goal_dependencies == ()


def test_resource_projection_keeps_target_shortfall_for_known_30_unit_recovery() -> None:
    definition = _definition()
    objective = _objective(_resource_requirement())
    result = build_dependency_closure(
        definition,
        (objective,),
        _planner_input(fuel=70, other_region_fuel=30),
    )

    active = _active(result)
    assert len(active) == 1
    assert active[0]["kind"] == "RESOURCE_AT_LEAST"
    assert active[0]["region_key"] == "region_a"
    assert active[0]["resource_key"] == "fuel"
    assert active[0]["minimum"] == 100
    assert active[0]["current_known_available"] == 70
    assert active[0]["deficit"] == 30
    fuel_resource = cast(dict[str, Any], result.planner_input.known_world.resources["fuel"])
    fuel_scopes = cast(dict[str, Any], fuel_resource["scopes"])
    assert fuel_scopes["region_b"]["known_available"] == 30


def test_nested_derived_projection_keeps_parent_link_and_respects_gate() -> None:
    child = SimpleNamespace(
        kind="FACT",
        node_key="goal_node",
        fact_key="ready",
        accepted_values=(True,),
        knowledge_gate=None,
    )
    child_derived = SimpleNamespace(
        kind="DERIVED_STATE",
        derived_key="child_state",
        accepted_values=("AVAILABLE",),
        knowledge_gate=None,
    )
    gated_child = SimpleNamespace(
        kind="FACT",
        node_key="goal_node",
        fact_key="ready",
        accepted_values=(True,),
        knowledge_gate=SimpleNamespace(
            node_key="gate_node",
            fact_key="revealed",
            accepted_values=(True,),
        ),
    )
    states = {
        "child_state": SimpleNamespace(
            key="child_state",
            dependencies=(child,),
            available_value="AVAILABLE",
            unavailable_value="UNAVAILABLE",
        ),
        "parent_state": SimpleNamespace(
            key="parent_state",
            dependencies=(child_derived,),
            available_value="AVAILABLE",
            unavailable_value="UNAVAILABLE",
        ),
        "gated_state": SimpleNamespace(
            key="gated_state",
            dependencies=(gated_child,),
            available_value=True,
            unavailable_value=False,
        ),
    }
    definition = _definition(derived_states=states)

    unknown = build_dependency_closure(
        definition,
        (
            _objective(
                _derived_requirement(
                    derived_key="parent_state",
                    accepted_values=("AVAILABLE",),
                )
            ),
        ),
        _planner_input(),
    )
    unknown_payload = _active(unknown)
    assert {item["kind"] for item in unknown_payload} == {"DERIVED_STATE", "FACT"}
    assert sum(item["kind"] == "DERIVED_STATE" for item in unknown_payload) == 2
    parent = next(item for item in unknown_payload if item["derived_key"] == "parent_state")
    child_projection = next(
        item for item in unknown_payload if item["derived_key"] == "child_state"
    )
    fact = next(item for item in unknown_payload if item["kind"] == "FACT")
    assert child_projection["parent_dependency_id"] == parent["dependency_id"]
    assert fact["parent_dependency_id"] == child_projection["dependency_id"]

    false_value = build_dependency_closure(
        definition,
        (
            _objective(
                _derived_requirement(
                    derived_key="parent_state",
                    accepted_values=("AVAILABLE",),
                )
            ),
        ),
        _planner_input(facts={"goal_node.ready": False}),
    )
    false_payload = _active(false_value)
    assert all(item["knowledge_status"] == "KNOWN" for item in false_payload)
    assert {item["current_known_value"] for item in false_payload} == {
        False,
        "UNAVAILABLE",
    }

    true_value = build_dependency_closure(
        definition,
        (
            _objective(
                _derived_requirement(
                    derived_key="parent_state",
                    accepted_values=("AVAILABLE",),
                )
            ),
        ),
        _planner_input(facts={"goal_node.ready": True}),
    )
    assert true_value.planner_input.active_goal_dependencies == ()

    gated = build_dependency_closure(
        definition,
        (
            _objective(
                _derived_requirement(
                    derived_key="gated_state",
                    accepted_values=(True,),
                )
            ),
        ),
        _planner_input(facts={"gate_node.revealed": False}),
    )
    gated_payload = _active(gated)
    assert len(gated_payload) == 1
    assert gated_payload[0]["kind"] == "DERIVED_STATE"
    assert not any(item["kind"] == "FACT" for item in gated_payload)

    revealed = build_dependency_closure(
        definition,
        (
            _objective(
                _derived_requirement(
                    derived_key="gated_state",
                    accepted_values=(True,),
                )
            ),
        ),
        _planner_input(facts={"gate_node.revealed": True}),
    )
    revealed_payload = _active(revealed)
    assert {item["kind"] for item in revealed_payload} == {"DERIVED_STATE", "FACT"}


def test_predefined_and_dynamic_formal_goals_use_the_same_projection_path() -> None:
    requirement = ObjectiveRequirementV2(
        key="ready_requirement",
        node_key="goal_node",
        fact_key="ready",
        accepted_values=(True,),
        description="The synthetic target is ready",
    )
    scenario = FormalGoalScenarioProofV1(
        scenario_version_id=uuid4(),
        scenario_content_hash="0" * 64,
    )
    predefined = FormalGoalContractV1(
        source_kind=FormalGoalSourceKind.PREDEFINED,
        scenario=scenario,
        completion_requirements=(
            FormalGoalRequirementV1(
                identity="synthetic_objective:ready_requirement",
                requirement=requirement,
                source_objective_key="synthetic_objective",
                source_requirement_key="ready_requirement",
            ),
        ),
        predefined_objectives=(FormalGoalObjectiveSourceV1(objective_key="synthetic_objective"),),
        compiler_version="synthetic-test",
    )
    dynamic = FormalGoalContractV1(
        source_kind=FormalGoalSourceKind.AD_HOC_DYNAMIC,
        scenario=scenario,
        completion_requirements=(
            FormalGoalRequirementV1(
                identity=canonical_requirement_identity(requirement),
                requirement=requirement,
            ),
        ),
        compiler_version="synthetic-test",
    )
    definition = _definition()
    planner_input = _planner_input()

    predefined_objectives = formal_goal_planning_objectives(predefined, definition)
    dynamic_objectives = formal_goal_planning_objectives(dynamic, definition)
    predefined_projection = build_dependency_closure(
        definition,
        predefined_objectives,
        planner_input,
        formal_goal=predefined,
    )
    dynamic_projection = build_dependency_closure(
        definition,
        dynamic_objectives,
        planner_input,
        formal_goal=dynamic,
    )

    assert _active(predefined_projection) == _active(dynamic_projection)


def test_active_goal_projection_is_additive_to_legacy_unknown_dependencies_and_payload() -> None:
    definition = _definition()
    objective = _objective(_fact_requirement())
    result = build_dependency_closure(definition, (objective,), _planner_input())
    payload = result.planner_input.model_dump(mode="json")

    assert "active_goal_dependencies" in payload
    assert payload["active_goal_dependencies"][0]["kind"] == "FACT"
    assert payload["known_world"]["unknown_dependencies"][0]["status"] == "UNKNOWN"

    provider_payload = PlanRequest(
        call_type="INITIAL_PLAN",
        planner_input=result.planner_input,
    ).provider_payload()
    provider_input = cast(dict[str, Any], provider_payload["planner_input"])
    assert provider_input["active_goal_dependencies"][0]["kind"] == "FACT"
