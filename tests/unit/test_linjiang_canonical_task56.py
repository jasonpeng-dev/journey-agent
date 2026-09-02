from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.agent.planning_context import PlanningContextBuilder
from app.domain.enums import (
    AgentTaskStatus,
    CommandReachability,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import (
    DerivedDependencyKind,
    EffectKind,
    NodeSelectorKind,
    ObjectiveRequirementKind,
    ScenarioDefinitionV2,
    ValueSource,
)
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceResourceState,
    Player,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.validation import ScenarioDefinitionValidator
from app.services.derived_state import evaluate_derived_states
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService
from tests.scenario_fixtures import LINJIANG_V2_TEST, predefined_goal_resolution


def _definition() -> ScenarioDefinitionV2:
    return LINJIANG_V2_TEST


def _initial_fact(definition: ScenarioDefinitionV2, node_key: str, fact_key: str):  # type: ignore[no-untyped-def]
    node = definition.world.node(node_key)
    assert node is not None
    return next(item for item in node.facts if item.key == fact_key)


def _runtime(
    session: Session, creation_key: str
) -> tuple[object, object, GenericAgentService, ScenarioDefinitionV2]:
    definition = _definition()
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name=creation_key)
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=creation_key,
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return runtime, scope, GenericAgentService(session, scope), definition


def _set_fact(
    session: Session,
    game_instance_id: Any,
    node_key: str,
    fact_key: str,
    value: object,
    *,
    visibility: Visibility | None = None,
) -> None:
    row = session.get(GameInstanceFactState, (game_instance_id, node_key, fact_key))
    assert row is not None
    row.truth_value = value
    if visibility is not None:
        row.visibility = visibility


def _known_inflow(
    session: Session,
    game_instance_id: Any,
    region_key: str,
    resource_key: str,
    value: int,
) -> GameInstanceResourceState:
    pool_key = "__runtime_known_inflow__"
    row = GameInstanceResourceState(
        game_instance_id=game_instance_id,
        resource_identity=f"{resource_key}@{region_key}@{pool_key}",
        resource_key=resource_key,
        scope_node_key=region_key,
        pool_key=pool_key,
        facility_key=None,
        value=value,
        reserved_value=0,
        visibility=ResourcePoolVisibility.VISIBLE,
        availability=ResourcePoolAvailability.AVAILABLE,
    )
    session.add(row)
    return row


def _reveal_region_resources(
    session: Session,
    game_instance_id: Any,
    *region_keys: str,
) -> None:
    for region_key in region_keys:
        row = session.get(
            GameInstanceRegionResourceKnowledge,
            (game_instance_id, region_key),
        )
        assert row is not None
        row.resource_inventory_visibility = "VISIBLE"
        row.resource_survey_completed = True


def _derived_fact_dependencies(
    definition: ScenarioDefinitionV2,
    derived_key: str,
) -> tuple[object, ...]:
    return tuple(
        item
        for item in definition.derived_state_definitions[derived_key].dependencies
        if item.kind == DerivedDependencyKind.FACT
    )


def _opposite_value(value: object) -> object:
    if value is True:
        return False
    if value is False:
        return True
    if value == "AVAILABLE":
        return "UNAVAILABLE"
    if value == "UNAVAILABLE":
        return "AVAILABLE"
    raise AssertionError(f"No test opposite for {value!r}")


def _condition_facts(condition: object | None) -> tuple[tuple[str | None, str], ...]:
    if condition is None:
        return ()
    refs: list[tuple[str | None, str]] = []
    fact_key = getattr(condition, "fact_key", None)
    node = getattr(condition, "node", None)
    if fact_key is not None and node is not None:
        refs.append((node.node_key, fact_key))
    refs.extend(
        ref for child in getattr(condition, "conditions", ()) for ref in _condition_facts(child)
    )
    nested = getattr(condition, "condition", None)
    if nested is not None:
        refs.extend(_condition_facts(nested))
    return tuple(refs)


def _set_derived_facts(
    session: Session,
    game_instance_id: Any,
    definition: ScenarioDefinitionV2,
    derived_key: str,
    *,
    missing: tuple[str, str] | None = None,
) -> None:
    for dependency in _derived_fact_dependencies(definition, derived_key):
        assert dependency.node_key is not None and dependency.fact_key is not None
        value = dependency.accepted_values[0]
        if missing == (dependency.node_key, dependency.fact_key):
            value = _opposite_value(value)
        node = session.get(GameInstanceNodeState, (game_instance_id, dependency.node_key))
        assert node is not None
        node.visibility = Visibility.KNOWN
        _set_fact(
            session,
            game_instance_id,
            dependency.node_key,
            dependency.fact_key,
            value,
            visibility=Visibility.KNOWN,
        )
    session.flush()


def test_task5_and_task6_objective_contracts_are_exact() -> None:
    definition = _definition()
    by_key = {item.key: item for item in definition.objectives}
    task5 = by_key["establish_citywide_sustained_emergency_support"]
    task6 = by_key["establish_sustained_emergency_generation"]

    assert len(task5.completion_requirements) == 1
    task5_requirement = task5.completion_requirements[0]
    assert task5_requirement.kind == ObjectiveRequirementKind.DERIVED_STATE
    assert task5_requirement.derived_key == "citywide_sustained_emergency_support"
    task5_state = definition.derived_state_definitions[task5_requirement.derived_key]
    assert {
        (item.region_key, item.resource_key, item.minimum)
        for item in task5_state.dependencies
        if item.kind == DerivedDependencyKind.RESOURCE_AT_LEAST
    } == {
        ("central_district", "emergency_relief_supplies", 30),
        ("east_residential_district", "emergency_relief_supplies", 30),
        ("southeast_heights_district", "emergency_relief_supplies", 30),
    }
    assert len(task6.completion_requirements) == 1
    task6_requirement = task6.completion_requirements[0]
    assert task6_requirement.kind == ObjectiveRequirementKind.DERIVED_STATE
    assert task6_requirement.derived_key == "southeast_sustained_emergency_generation"
    task6_state = definition.derived_state_definitions[task6_requirement.derived_key]
    reserve = next(
        item
        for item in task6_state.dependencies
        if item.kind == DerivedDependencyKind.RESOURCE_AT_LEAST
    )
    assert (reserve.region_key, reserve.resource_key, reserve.minimum) == (
        "southeast_heights_district",
        "emergency_fuel",
        100,
    )
    gated_dependencies = tuple(
        item for item in task6_state.dependencies if item.knowledge_gate is not None
    )
    assert {
        (item.kind, item.node_key, item.fact_key, item.region_key, item.resource_key)
        for item in gated_dependencies
    } == {
        (DerivedDependencyKind.FACT, "river_port", "operational", None, None),
        (DerivedDependencyKind.FACT, "south_fuel_terminal", "operational", None, None),
        (
            DerivedDependencyKind.RESOURCE_AT_LEAST,
            None,
            None,
            "southeast_heights_district",
            "emergency_fuel",
        ),
    }
    assert {
        (item.knowledge_gate.node_key, item.knowledge_gate.fact_key)
        for item in gated_dependencies
        if item.knowledge_gate is not None
    } == {("southeast_fuel_emergency_power_plant", "sustained_requirements_discovered")}


def test_task56_resource_budget_and_hidden_supply_contract() -> None:
    definition = _definition()
    pools = {item.pool_key: item for item in definition.initialization.resource_pools}

    assert pools["north_service_depot_stock"].quantity == 100
    assert "north_heavy_equipment_stock" not in pools
    assert pools["warehouse_aid_general"].quantity == 20
    assert pools["warehouse_aid_municipal"].quantity == 60
    assert pools["warehouse_relief_supply"].quantity == 100
    assert pools["north_emergency_fuel"].quantity == 50
    assert pools["south_emergency_fuel"].quantity == 120
    assert pools["south_emergency_fuel"].visibility.value == "HIDDEN"


def test_task5_warehouse_bootstrap_unlocks_aid_and_supports_completion(
    session: Session,
) -> None:
    runtime, scope, agent, _definition_value = _runtime(session, "task56-warehouse-bootstrap")
    game_id = runtime.instance.id  # type: ignore[attr-defined]
    industrial = session.get(
        GameInstanceActor,
        (game_id, "industrial_repair_team_alpha"),
    )
    assert industrial is not None
    industrial.current_node_key = "west_logistics_district"
    industrial.command_reachability = CommandReachability.ONLINE.value
    _known_inflow(session, game_id, "west_logistics_district", "general_engineering_parts", 5)
    session.flush()

    pools = {
        row.pool_key: row
        for row in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == game_id
            )
        )
    }
    assert pools["warehouse_aid_general"].availability == ResourcePoolAvailability.UNAVAILABLE
    assert pools["warehouse_aid_municipal"].availability == ResourcePoolAvailability.UNAVAILABLE

    actions = GenericActionService(session, scope)
    warehouse = actions.execute_action(
        actor_key=industrial.actor_key,
        action_key="repair_industrial_facility",
        target_key="emergency_supply_warehouse",
        parameters={},
        idempotency_key="task56-repair-warehouse",
    )
    assert warehouse.applied is not None and warehouse.applied.outcome.failure is None

    session.refresh(pools["warehouse_aid_general"])
    session.refresh(pools["warehouse_aid_municipal"])
    assert pools["warehouse_aid_general"].availability == ResourcePoolAvailability.AVAILABLE
    assert pools["warehouse_aid_municipal"].availability == ResourcePoolAvailability.AVAILABLE
    assert pools["warehouse_aid_general"].value == 20
    assert pools["warehouse_aid_municipal"].value == 60
    warehouse_operational = session.get(
        GameInstanceFactState,
        (game_id, "emergency_supply_warehouse", "operational"),
    )
    assert warehouse_operational is not None and warehouse_operational.truth_value is True
    session.refresh(pools["warehouse_relief_supply"])
    assert pools["warehouse_relief_supply"].availability == ResourcePoolAvailability.UNAVAILABLE

    _set_derived_facts(
        session,
        game_id,
        _definition_value,
        "citywide_sustained_emergency_support",
    )
    for region in (
        "central_district",
        "east_residential_district",
        "southeast_heights_district",
    ):
        _known_inflow(session, game_id, region, "emergency_relief_supplies", 30)
    _reveal_region_resources(
        session,
        game_id,
        "central_district",
        "east_residential_district",
        "southeast_heights_district",
    )
    session.flush()
    task = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "建立全城灾后持续应急保障网络",
        resolved_goal=predefined_goal_resolution("establish_citywide_sustained_emergency_support"),
        initialize_plan=False,
    )
    assert task.status == AgentTaskStatus.SUCCEEDED


def test_task56_world_cleanup_bridge_and_reference_integrity() -> None:
    definition = _definition()
    node_keys = {item.key for item in definition.world.nodes}
    action_keys = {item.key for item in definition.actions}

    assert "central_fire_rescue_station" not in node_keys
    assert "hill_reservoir" not in node_keys
    assert "railway_freight_yard" not in node_keys
    assert "emergency_vehicle_base" not in node_keys
    assert {"rail_freight_yard", "vehicle_depot"}.issubset(node_keys)
    bridge = _initial_fact(definition, "south_bridge", "passable")
    assert bridge.initial_value is False
    assert bridge.initial_visibility.value == "HIDDEN"
    assert {"receive_external_relief_supplies", "generate_power"}.issubset(action_keys)
    assert "establish_sustained_humanitarian_logistics" not in action_keys
    assert "commission_sustained_generation" not in action_keys
    assert ScenarioDefinitionValidator().validate(definition.model_dump(mode="json")).passed


def test_task5_public_dependency_and_transport_contract_is_complete() -> None:
    definition = _definition()
    nodes = {item.key: item for item in definition.world.nodes}
    actions = {item.key: item for item in definition.actions}
    transport_keys = {
        item.key for item in definition.world.nodes if item.node_type_key == "transport"
    }
    assert transport_keys == {
        "central_river_tunnel",
        "north_service_corridor",
        "south_bridge",
        "southeast_access_corridor",
        "waterfront_access_corridor",
        "west_freight_corridor",
    }
    assert "clearable" in nodes["south_bridge"].interaction_keys
    assert actions["receive_external_relief_supplies"].required_interaction_key == (
        "external_relief_intake"
    )
    task5 = definition.derived_state_definitions["citywide_sustained_emergency_support"]
    assert {
        (
            item.kind,
            item.node_key,
            item.fact_key,
            item.region_key,
            item.resource_key,
            item.minimum,
        )
        for item in task5.dependencies
    } == {
        (
            DerivedDependencyKind.FACT,
            "rail_freight_yard",
            "rail_freight_capability",
            None,
            None,
            None,
        ),
        (
            DerivedDependencyKind.FACT,
            "emergency_supply_warehouse",
            "operational",
            None,
            None,
            None,
        ),
        (
            DerivedDependencyKind.FACT,
            "vehicle_depot",
            "emergency_delivery_support",
            None,
            None,
            None,
        ),
        (DerivedDependencyKind.FACT, "city_distribution_center", "operational", None, None, None),
        *{
            (DerivedDependencyKind.FACT, node_key, "passable", None, None, None)
            for node_key in transport_keys
        },
        (
            DerivedDependencyKind.RESOURCE_AT_LEAST,
            None,
            None,
            "central_district",
            "emergency_relief_supplies",
            30,
        ),
        (
            DerivedDependencyKind.RESOURCE_AT_LEAST,
            None,
            None,
            "east_residential_district",
            "emergency_relief_supplies",
            30,
        ),
        (
            DerivedDependencyKind.RESOURCE_AT_LEAST,
            None,
            None,
            "southeast_heights_district",
            "emergency_relief_supplies",
            30,
        ),
    }
    passability_nodes = {
        item.node_key
        for item in task5.dependencies
        if item.kind == DerivedDependencyKind.FACT and item.fact_key == "passable"
    }
    assert passability_nodes == transport_keys


def test_task5_capability_facts_are_real_base_contracts() -> None:
    definition = _definition()
    task5 = definition.derived_state_definitions["citywide_sustained_emergency_support"]
    expected = {
        "rail_freight_capability": (
            "rail_freight_yard",
            "repair_industrial_facility_rail_freight_yard_task56_resolution",
            {"receive_relief_requires_rail"},
        ),
        "emergency_delivery_support": (
            "vehicle_depot",
            "repair_industrial_facility_vehicle_depot_task56_resolution",
            {"repair_industrial_facility_city_distribution_center_prerequisite_1"},
        ),
    }

    for fact_key, (node_key, producer_key, consumer_keys) in expected.items():
        fact = _initial_fact(definition, node_key, fact_key)
        assert fact.goal_addressable is True
        assert fact.initial_value == "UNAVAILABLE"
        assert fact.allowed_values == ("AVAILABLE", "UNAVAILABLE")

        producer = next(rule for rule in definition.rules if rule.key == producer_key)
        writes = tuple(
            effect
            for effect in producer.effects
            if effect.kind == EffectKind.SET_FACT and effect.fact_key == fact_key
        )
        assert len(writes) == 1
        assert writes[0].node is not None
        assert writes[0].node.kind == NodeSelectorKind.CURRENT_TARGET
        assert writes[0].value is not None
        assert writes[0].value.source == ValueSource.LITERAL
        assert writes[0].value.literal == "AVAILABLE"
        assert any(
            effect.kind == EffectKind.SET_FACT
            and effect.fact_key == "operational"
            and effect.value is not None
            and effect.value.literal is True
            for effect in producer.effects
        )

        consumers = {
            rule.key
            for rule in definition.rules
            if (node_key, fact_key) in _condition_facts(rule.condition)
        }
        assert consumers == consumer_keys

        assert any(
            item.kind == DerivedDependencyKind.FACT
            and item.node_key == node_key
            and item.fact_key == fact_key
            for item in task5.dependencies
        )


def test_task5_unknown_bridge_precondition_is_blocking_and_knowledge_safe(
    session: Session,
) -> None:
    runtime, scope, agent, definition = _runtime(session, "task56-unknown-bridge")
    task = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "建立全城灾后持续应急保障网络",
        resolved_goal=predefined_goal_resolution("establish_citywide_sustained_emergency_support"),
        initialize_plan=False,
    )

    closure = PlanningContextBuilder(session, scope).build_v2_closure(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason=None,
    )
    payload = closure.planner_input.model_dump(mode="json")
    unknowns = payload["known_world"]["unknown_dependencies"]
    assert any(
        item.get("subject_key") == "south_bridge"
        and item.get("fact_key") == "passable"
        and item.get("status") == "UNKNOWN"
        for item in unknowns
    )
    action_keys = {item["action_key"] for item in payload["action_contracts"]}
    assert "inspect" in action_keys
    assert any(
        item["action_key"] == "inspect" and item["target_key"] == "south_bridge"
        for item in payload["target_bindings"]
    )


def test_task5_and_task6_resource_budget_is_solvable_without_artificial_costs() -> None:
    definition = _definition()
    pools = {item.pool_key: item for item in definition.initialization.resource_pools}
    assert (
        sum(
            item.quantity
            for item in pools.values()
            if item.resource_key == "general_engineering_parts"
        )
        == 145
    )
    assert (
        sum(
            item.quantity
            for item in pools.values()
            if item.resource_key == "municipal_repair_materials"
        )
        == 160
    )
    assert (
        sum(
            item.quantity
            for item in pools.values()
            if item.resource_key == "electrical_repair_parts"
        )
        == 70
    )
    assert (145 - 40, 160 - 60, 70 - 15) == (105, 100, 55)
    assert pools["warehouse_relief_supply"].quantity == 100
    assert pools["north_emergency_fuel"].quantity == 50
    assert pools["south_emergency_fuel"].quantity == 120


def test_task5_completion_and_same_objective_reissue_use_current_truth(
    session: Session,
) -> None:
    runtime, _scope, agent, definition = _runtime(session, "task56-reissue")
    game_id = runtime.instance.id  # type: ignore[attr-defined]
    _set_derived_facts(
        session,
        game_id,
        definition,
        "citywide_sustained_emergency_support",
    )
    pools = [
        _known_inflow(session, game_id, region, "emergency_relief_supplies", 30)
        for region in (
            "central_district",
            "east_residential_district",
            "southeast_heights_district",
        )
    ]
    _reveal_region_resources(
        session,
        game_id,
        "central_district",
        "east_residential_district",
        "southeast_heights_district",
    )
    session.flush()

    first = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "建立全城灾后持续应急保障网络",
        resolved_goal=predefined_goal_resolution("establish_citywide_sustained_emergency_support"),
        initialize_plan=False,
    )
    assert first.status == AgentTaskStatus.SUCCEEDED

    pools[0].value = 29
    session.flush()
    second = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "建立全城灾后持续应急保障网络",
        resolved_goal=predefined_goal_resolution("establish_citywide_sustained_emergency_support"),
        initialize_plan=False,
    )
    assert second.status == AgentTaskStatus.ACTIVE
    assert not agent.evaluate(second).completed
    assert first.status == AgentTaskStatus.SUCCEEDED


def test_task5_then_task6_complete_sequentially_on_one_runtime(session: Session) -> None:
    runtime, _scope, agent, definition = _runtime(session, "task56-sequential")
    game_id = runtime.instance.id  # type: ignore[attr-defined]
    _set_derived_facts(
        session,
        game_id,
        definition,
        "citywide_sustained_emergency_support",
    )
    for region in (
        "central_district",
        "east_residential_district",
        "southeast_heights_district",
    ):
        _known_inflow(session, game_id, region, "emergency_relief_supplies", 30)
    _reveal_region_resources(
        session,
        game_id,
        "central_district",
        "east_residential_district",
        "southeast_heights_district",
    )
    session.flush()

    task5 = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "建立全城灾后持续应急保障网络",
        resolved_goal=predefined_goal_resolution("establish_citywide_sustained_emergency_support"),
        initialize_plan=False,
    )
    assert task5.status == AgentTaskStatus.SUCCEEDED

    task6 = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "建立持续应急发电保障",
        resolved_goal=predefined_goal_resolution("establish_sustained_emergency_generation"),
        initialize_plan=False,
    )
    assert task6.status == AgentTaskStatus.ACTIVE
    _set_fact(
        session,
        game_id,
        "southeast_fuel_emergency_power_plant",
        "generating",
        True,
        visibility=Visibility.KNOWN,
    )
    _set_fact(
        session,
        game_id,
        "river_port",
        "operational",
        True,
        visibility=Visibility.KNOWN,
    )
    _set_fact(
        session,
        game_id,
        "south_fuel_terminal",
        "operational",
        True,
        visibility=Visibility.KNOWN,
    )
    fuel = _known_inflow(
        session,
        game_id,
        "southeast_heights_district",
        "emergency_fuel",
        99,
    )
    _reveal_region_resources(session, game_id, "southeast_heights_district")
    session.flush()
    assert not agent.evaluate(task6).completed
    fuel.value = 100
    session.flush()
    evaluation = agent.evaluate(task6)
    assert evaluation.authoritative_completed is True
    assert evaluation.completed is False

    gate = session.get(
        GameInstanceFactState,
        (
            game_id,
            "southeast_fuel_emergency_power_plant",
            "sustained_requirements_discovered",
        ),
    )
    assert gate is not None
    gate.truth_value = True
    gate.visibility = Visibility.KNOWN
    session.flush()
    assert agent.evaluate(task6).completed is True


def test_task6_hidden_requirement_and_supply_chain_reveal_are_knowledge_safe(
    session: Session,
) -> None:
    runtime, scope, agent, definition = _runtime(session, "task56-hidden-gate")
    game_id = runtime.instance.id  # type: ignore[attr-defined]
    task = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "建立持续应急发电保障",
        resolved_goal=predefined_goal_resolution("establish_sustained_emergency_generation"),
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
    requirements = initial_payload["objective"]["completion_requirements"]
    assert len(requirements) == 1
    assert requirements[0]["kind"] == ObjectiveRequirementKind.DERIVED_STATE.value
    assert requirements[0]["derived_key"] == "southeast_sustained_emergency_generation"
    assert '"minimum": 100' not in initial_serialized
    assert "south_emergency_fuel" not in initial_serialized
    for node_key in ("river_port", "south_fuel_terminal"):
        node = session.get(GameInstanceNodeState, (game_id, node_key))
        assert node is not None and node.visibility == Visibility.KNOWN
    initial_actions = {item["action_key"] for item in initial_payload["action_contracts"]}
    assert "generate_power" in initial_actions
    assert "commission_sustained_generation" not in initial_actions
    assert {
        item["target_key"]
        for item in initial_payload["target_bindings"]
        if item["target_key"] in {"river_port", "south_fuel_terminal"}
    } == set()

    plant = session.get(
        GameInstanceFactState,
        (game_id, "southeast_fuel_emergency_power_plant", "operational"),
    )
    actor = session.get(GameInstanceActor, (game_id, "electrical_repair_team_alpha"))
    assert plant is not None and actor is not None
    plant.truth_value = True
    actor.current_node_key = "southeast_heights_district"
    actor.command_reachability = CommandReachability.ONLINE.value
    fuel = _known_inflow(
        session,
        game_id,
        "southeast_heights_district",
        "emergency_fuel",
        50,
    )
    session.flush()

    result = GenericActionService(session, scope).execute_action(
        actor_key=actor.actor_key,
        action_key="generate_power",
        target_key="southeast_fuel_emergency_power_plant",
        parameters={},
        idempotency_key="task56-generate-power",
        task_id=task.id,
    )
    assert result.applied is not None and result.applied.outcome.failure is None
    assert fuel.value == 0
    gate = session.get(
        GameInstanceFactState,
        (
            game_id,
            "southeast_fuel_emergency_power_plant",
            "sustained_requirements_discovered",
        ),
    )
    assert gate is not None and gate.truth_value is True and gate.visibility == Visibility.KNOWN
    for node_key in ("river_port", "south_fuel_terminal"):
        node = session.get(GameInstanceNodeState, (game_id, node_key))
        assert node is not None and node.visibility == Visibility.KNOWN
    south_fuel = next(
        item
        for item in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == game_id
            )
        )
        if item.pool_key == "south_emergency_fuel"
    )
    assert south_fuel.visibility == ResourcePoolVisibility.VISIBLE

    revealed = PlanningContextBuilder(session, scope).build_v2_closure(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason="INFORMATION_BOUNDARY",
    )
    revealed_payload = revealed.planner_input.model_dump(mode="json")
    revealed_requirements = revealed_payload["objective"]["completion_requirements"]
    assert len(revealed_requirements) == 1
    assert revealed_requirements[0]["kind"] == ObjectiveRequirementKind.DERIVED_STATE.value
    assert revealed_requirements[0]["derived_key"] == ("southeast_sustained_emergency_generation")
    assert any(
        item.get("resource_key") == "emergency_fuel" and item.get("required_amount") == 100
        for item in revealed_payload["known_world"]["unknown_dependencies"]
    )
    revealed_nodes = {item["key"] for item in revealed_payload["known_world"]["nodes"]}
    assert {"river_port", "south_fuel_terminal"} <= revealed_nodes
    revealed_actions = {item["action_key"] for item in revealed_payload["action_contracts"]}
    assert "commission_sustained_generation" not in revealed_actions


def test_task6_generate_power_requires_operational_power_and_startup_fuel() -> None:
    definition = _definition()
    rules = [
        item
        for item in definition.rules
        if item.action_key == "generate_power" and item.phase.value == "PREFLIGHT"
    ]
    facts = {
        (item.condition.node.node_key, item.condition.fact_key, item.condition.value)
        for item in rules
        if item.condition is not None and item.condition.node is not None
    }
    assert facts == {
        ("southeast_fuel_emergency_power_plant", "operational", True),
        ("southeast_fuel_emergency_power_plant", "power_supply", "AVAILABLE"),
    }
    fuel_rules = [
        item
        for item in rules
        if item.condition is not None and item.condition.resource_key == "emergency_fuel"
    ]
    assert len(fuel_rules) == 1
    assert fuel_rules[0].condition.value == 50


def test_task6_fuel_repairs_explicitly_unlock_north_and_south_pools(
    session: Session,
) -> None:
    runtime, scope, _agent, _definition_value = _runtime(session, "task56-fuel-unlocks")
    game_id = runtime.instance.id  # type: ignore[attr-defined]
    industrial = session.get(
        GameInstanceActor,
        (game_id, "industrial_repair_team_alpha"),
    )
    assert industrial is not None
    industrial.command_reachability = CommandReachability.ONLINE.value
    north_node = session.get(GameInstanceNodeState, (game_id, "north_fuel_depot"))
    south_node = session.get(GameInstanceNodeState, (game_id, "south_fuel_terminal"))
    river_port = session.get(GameInstanceNodeState, (game_id, "river_port"))
    assert north_node is not None and south_node is not None and river_port is not None
    north_node.visibility = Visibility.KNOWN
    south_node.visibility = Visibility.KNOWN
    river_port.visibility = Visibility.KNOWN
    _reveal_region_resources(
        session,
        game_id,
        "north_industrial_district",
        "south_waterfront_district",
    )

    pools = {
        row.pool_key: row
        for row in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == game_id
            )
        )
    }
    assert pools["north_emergency_fuel"].value == 50
    assert pools["north_emergency_fuel"].availability == ResourcePoolAvailability.UNAVAILABLE
    assert pools["south_emergency_fuel"].value == 120
    assert pools["south_emergency_fuel"].availability == ResourcePoolAvailability.UNAVAILABLE

    north_operational = session.get(
        GameInstanceFactState,
        (game_id, "north_fuel_depot", "operational"),
    )
    assert north_operational is not None and north_operational.truth_value is False
    north_operational.truth_value = True
    session.flush()
    session.refresh(pools["north_emergency_fuel"])
    assert pools["north_emergency_fuel"].availability == ResourcePoolAvailability.UNAVAILABLE
    north_operational.truth_value = False

    industrial.current_node_key = "north_industrial_district"
    _known_inflow(
        session,
        game_id,
        "north_industrial_district",
        "municipal_repair_materials",
        5,
    )
    session.flush()

    actions = GenericActionService(session, scope)
    north_result = actions.execute_action(
        actor_key=industrial.actor_key,
        action_key="repair_industrial_facility",
        target_key="north_fuel_depot",
        parameters={},
        idempotency_key="task56-repair-north-fuel-depot",
    )
    assert north_result.applied is not None and north_result.applied.outcome.failure is None

    session.refresh(pools["north_emergency_fuel"])
    north_operational = session.get(
        GameInstanceFactState,
        (game_id, "north_fuel_depot", "operational"),
    )
    assert north_operational is not None and north_operational.truth_value is True
    assert pools["north_emergency_fuel"].availability == ResourcePoolAvailability.AVAILABLE
    assert pools["north_emergency_fuel"].value == 50

    industrial.current_node_key = "south_waterfront_district"
    _set_fact(
        session,
        game_id,
        "river_port",
        "operational",
        True,
        visibility=Visibility.KNOWN,
    )
    _known_inflow(
        session,
        game_id,
        "south_waterfront_district",
        "general_engineering_parts",
        5,
    )
    _known_inflow(
        session,
        game_id,
        "south_waterfront_district",
        "municipal_repair_materials",
        10,
    )
    session.flush()

    south_result = actions.execute_action(
        actor_key=industrial.actor_key,
        action_key="repair_industrial_facility",
        target_key="south_fuel_terminal",
        parameters={},
        idempotency_key="task56-repair-south-fuel-terminal",
    )
    assert south_result.applied is not None and south_result.applied.outcome.failure is None

    session.refresh(pools["south_emergency_fuel"])
    south_operational = session.get(
        GameInstanceFactState,
        (game_id, "south_fuel_terminal", "operational"),
    )
    assert south_operational is not None and south_operational.truth_value is True
    assert pools["south_emergency_fuel"].availability == ResourcePoolAvailability.AVAILABLE
    assert pools["south_emergency_fuel"].value == 120
    assert pools["south_emergency_fuel"].visibility == ResourcePoolVisibility.HIDDEN


def test_task6_unlocked_fuel_supports_generation_and_commission_chain(
    session: Session,
) -> None:
    runtime, scope, _agent, definition = _runtime(session, "task56-fuel-chain")
    game_id = runtime.instance.id  # type: ignore[attr-defined]
    industrial = session.get(
        GameInstanceActor,
        (game_id, "industrial_repair_team_alpha"),
    )
    logistics = session.get(
        GameInstanceActor,
        (game_id, "logistics_team_alpha"),
    )
    electrical = session.get(
        GameInstanceActor,
        (game_id, "electrical_repair_team_alpha"),
    )
    assert industrial is not None and logistics is not None and electrical is not None
    industrial.command_reachability = CommandReachability.ONLINE.value
    electrical.command_reachability = CommandReachability.ONLINE.value
    north_node = session.get(GameInstanceNodeState, (game_id, "north_fuel_depot"))
    assert north_node is not None
    north_node.visibility = Visibility.KNOWN
    for transport in definition.world.nodes:
        if transport.node_type_key != "transport":
            continue
        transport_state = session.get(GameInstanceNodeState, (game_id, transport.key))
        passability = session.get(GameInstanceFactState, (game_id, transport.key, "passable"))
        assert transport_state is not None and passability is not None
        transport_state.visibility = Visibility.KNOWN
        passability.visibility = Visibility.KNOWN
        passability.truth_value = True
    _reveal_region_resources(
        session,
        game_id,
        "north_industrial_district",
        "central_district",
        "east_residential_district",
        "south_waterfront_district",
        "southeast_heights_district",
    )

    industrial.current_node_key = "north_industrial_district"
    _known_inflow(
        session,
        game_id,
        "north_industrial_district",
        "municipal_repair_materials",
        5,
    )
    session.flush()
    actions = GenericActionService(session, scope)

    north_repair = actions.execute_action(
        actor_key=industrial.actor_key,
        action_key="repair_industrial_facility",
        target_key="north_fuel_depot",
        parameters={},
        idempotency_key="task56-chain-repair-north",
    )
    assert north_repair.applied is not None and north_repair.applied.outcome.failure is None

    logistics.current_node_key = "north_industrial_district"
    for target_key, suffix in (
        ("central_district", "central"),
        ("east_residential_district", "east"),
        ("south_waterfront_district", "south"),
        ("southeast_heights_district", "southeast"),
    ):
        transported = actions.execute_action(
            actor_key=logistics.actor_key,
            action_key="transport_resource",
            target_key=target_key,
            parameters={"resource_key": "emergency_fuel", "amount": 50},
            idempotency_key=f"task56-chain-transport-{suffix}",
        )
        assert transported.applied is not None and transported.applied.outcome.failure is None

    electrical.current_node_key = "southeast_heights_district"
    _known_inflow(
        session,
        game_id,
        "southeast_heights_district",
        "general_engineering_parts",
        5,
    )
    session.flush()
    plant_repair = actions.execute_action(
        actor_key=electrical.actor_key,
        action_key="repair_electrical",
        target_key="southeast_fuel_emergency_power_plant",
        parameters={},
        idempotency_key="task56-chain-repair-power-plant",
    )
    assert plant_repair.applied is not None and plant_repair.applied.outcome.failure is None

    generated = actions.execute_action(
        actor_key=electrical.actor_key,
        action_key="generate_power",
        target_key="southeast_fuel_emergency_power_plant",
        parameters={},
        idempotency_key="task56-chain-generate-power",
    )
    assert generated.applied is not None and generated.applied.outcome.failure is None

    industrial.current_node_key = "south_waterfront_district"
    logistics.current_node_key = "south_waterfront_district"
    south_node = session.get(GameInstanceNodeState, (game_id, "south_fuel_terminal"))
    river_port = session.get(GameInstanceNodeState, (game_id, "river_port"))
    assert south_node is not None and river_port is not None
    south_node.visibility = Visibility.KNOWN
    river_port.visibility = Visibility.KNOWN
    _set_fact(
        session,
        game_id,
        "river_port",
        "operational",
        True,
        visibility=Visibility.KNOWN,
    )
    _known_inflow(
        session,
        game_id,
        "south_waterfront_district",
        "general_engineering_parts",
        5,
    )
    _known_inflow(
        session,
        game_id,
        "south_waterfront_district",
        "municipal_repair_materials",
        10,
    )
    session.flush()
    south_repair = actions.execute_action(
        actor_key=industrial.actor_key,
        action_key="repair_industrial_facility",
        target_key="south_fuel_terminal",
        parameters={},
        idempotency_key="task56-chain-repair-south-terminal",
    )
    assert south_repair.applied is not None and south_repair.applied.outcome.failure is None
    south_operational = session.get(
        GameInstanceFactState,
        (game_id, "south_fuel_terminal", "operational"),
    )
    assert south_operational is not None
    south_operational.visibility = Visibility.KNOWN

    transported_south_fuel = actions.execute_action(
        actor_key=logistics.actor_key,
        action_key="transport_resource",
        target_key="southeast_heights_district",
        parameters={"resource_key": "emergency_fuel", "amount": 100},
        idempotency_key="task56-chain-transport-south-fuel",
    )
    assert (
        transported_south_fuel.applied is not None
        and transported_south_fuel.applied.outcome.failure is None
    )

    electrical.current_node_key = "southeast_heights_district"
    session.flush()
    evaluation = evaluate_derived_states(session, scope, definition)
    capability = evaluation.values["southeast_sustained_emergency_generation"]
    assert capability.truth_value == "AVAILABLE"
    assert capability.knowledge_value == "AVAILABLE"
