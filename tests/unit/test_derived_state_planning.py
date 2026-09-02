from __future__ import annotations

import json
from typing import cast

from sqlalchemy.orm import Session

from app.agent.planning_context import PlanningContextBuilder
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import DerivedDependencyKind, ObjectiveRequirementKind
from app.domain.world import Visibility
from app.infrastructure.db.models import GameInstanceFactState
from app.services.runtime_initialization import InitializedRuntime
from tests.unit.test_linjiang_canonical_task56 import _runtime


def test_derived_goal_remains_visible_and_closure_expands_base_dependencies(
    session: Session,
) -> None:
    runtime, scope, agent, definition = _runtime(session, "derived-planning-task4")
    runtime = cast(InitializedRuntime, runtime)
    scope = cast(RuntimeScope, scope)
    state = definition.derived_state_definitions["east_emergency_water_supply"]
    fact_dependencies = tuple(
        item for item in state.dependencies if item.kind == DerivedDependencyKind.FACT
    )
    for item in fact_dependencies:
        assert item.node_key is not None and item.fact_key is not None
        row = session.get(
            GameInstanceFactState,
            (runtime.instance.id, item.node_key, item.fact_key),
        )
        assert row is not None
        row.truth_value = item.accepted_values[0]
        row.visibility = Visibility.KNOWN
    dependency = next(item for item in fact_dependencies if item.kind == DerivedDependencyKind.FACT)
    assert dependency.node_key is not None and dependency.fact_key is not None
    hidden = session.get(
        GameInstanceFactState,
        (runtime.instance.id, dependency.node_key, dependency.fact_key),
    )
    assert hidden is not None
    hidden.visibility = Visibility.HIDDEN
    session.flush()
    task = agent.create_task(
        runtime.session,
        "恢复东部应急供水",
        initialize_plan=False,
    )

    closure = PlanningContextBuilder(session, scope).build_v2_closure(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason=None,
    )
    payload = closure.planner_input.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False)

    completion = payload["objective"]["completion_requirements"]
    assert len(completion) == 1
    assert completion[0]["kind"] == ObjectiveRequirementKind.DERIVED_STATE.value
    assert completion[0]["derived_key"] == "east_emergency_water_supply"
    assert completion[0]["knowledge_status"] == "UNKNOWN"
    assert completion[0]["current_known_value"] is None
    assert "derived_states" not in payload["known_world"]

    fact_dependencies = {
        f"{dependency.node_key}.{dependency.fact_key}"
        for dependency in state.dependencies
        if dependency.kind == DerivedDependencyKind.FACT
    }
    unknown_fact_dependencies = {
        f"{item['subject_key']}.{item['fact_key']}"
        for item in payload["known_world"]["unknown_dependencies"]
        if item.get("dimension") == "OBJECTIVE_FACT_KNOWLEDGE"
    }
    hidden_identity = f"{dependency.node_key}.{dependency.fact_key}"
    assert hidden_identity in unknown_fact_dependencies
    known_fact_dependencies = set(payload["known_world"]["facts"])
    assert fact_dependencies <= known_fact_dependencies | unknown_fact_dependencies
    assert "derived:east_emergency_water_supply" in closure.relevance_reason
    assert "south_bridge.passable" not in serialized


def test_task6_gated_derived_dependency_is_not_exposed_before_reveal(
    session: Session,
) -> None:
    runtime, scope, agent, definition = _runtime(session, "derived-planning-task6-gate")
    runtime = cast(InitializedRuntime, runtime)
    scope = cast(RuntimeScope, scope)
    task = agent.create_task(
        runtime.session,
        "建立持续应急发电保障",
        initialize_plan=False,
    )

    initial = PlanningContextBuilder(session, scope).build_v2_closure(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason=None,
    )
    initial_payload = initial.planner_input.model_dump(mode="json")
    initial_serialized = json.dumps(initial_payload, ensure_ascii=False)
    assert not any(
        item.get("resource_key") == "emergency_fuel" and item.get("required_amount") == 100
        for item in initial_payload["known_world"]["unknown_dependencies"]
    )
    assert "south_emergency_fuel" not in initial_serialized
    assert '"minimum": 100' not in initial_serialized
    assert "river_port.operational" not in initial_serialized
    assert "south_fuel_terminal.operational" not in initial_serialized
    assert initial_payload["objective"]["completion_requirements"][0]["derived_key"] == (
        "southeast_sustained_emergency_generation"
    )

    gate = session.get(
        GameInstanceFactState,
        (
            runtime.instance.id,
            "southeast_fuel_emergency_power_plant",
            "sustained_requirements_discovered",
        ),
    )
    assert gate is not None
    gate.truth_value = True
    gate.visibility = Visibility.KNOWN
    session.flush()

    revealed = PlanningContextBuilder(session, scope).build_v2_closure(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason="INFORMATION_BOUNDARY",
    )
    revealed_payload = revealed.planner_input.model_dump(mode="json")
    revealed_dependencies = {
        item.get("resource_key")
        for item in revealed_payload["known_world"]["unknown_dependencies"]
        if item.get("dimension") == "RESOURCE_SOURCE"
    }
    assert "emergency_fuel" in revealed_dependencies
    revealed_facts = {
        item.get("subject_key")
        for item in revealed_payload["known_world"]["unknown_dependencies"]
        if item.get("dimension") == "OBJECTIVE_FACT_KNOWLEDGE"
    }
    assert {"river_port", "south_fuel_terminal"} <= revealed_facts
    assert revealed_payload["objective"]["completion_requirements"][0]["derived_key"] == (
        "southeast_sustained_emergency_generation"
    )
