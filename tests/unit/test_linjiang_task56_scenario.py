from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.agent.planning_context import PlanningContextBuilder
from app.domain.enums import AgentTaskStatus, ResourcePoolAvailability, ResourcePoolVisibility
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ObjectiveRequirementKind, ScenarioDefinitionV2
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceResourceState,
    Player,
)
from app.scenarios.builtin import load_builtin_scenario
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.validation import ScenarioDefinitionValidator
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService

FILE = "linjiang_infrastructure_recovery_v2_0_task4_completed_task56_test.yaml"


def _definition() -> ScenarioDefinitionV2:
    return load_builtin_scenario(FILE)


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


def test_task4_completed_baseline_and_task56_initial_status() -> None:
    definition = _definition()
    by_key = {item.key: item for item in definition.objectives}

    for objective_key in (
        "restore_central_communication_capability",
        "restore_north_basic_engineering_support",
        "restore_east_emergency_power_network",
        "restore_east_emergency_water_supply",
    ):
        objective = by_key[objective_key]
        assert all(
            requirement.kind == ObjectiveRequirementKind.FACT
            and _initial_fact(
                definition,
                requirement.node_key,
                requirement.fact_key,
            ).initial_value
            in requirement.accepted_values
            for requirement in objective.completion_requirements
        )

    assert (
        _initial_fact(
            definition, "city_distribution_center", "sustained_humanitarian_logistics"
        ).initial_value
        == "UNAVAILABLE"
    )


def test_task1_through_task4_contracts_match_the_task2_completed_baseline() -> None:
    definition = _definition()
    baseline = load_builtin_scenario("linjiang_infrastructure_recovery_v2_0_task3_baseline.yaml")
    objective_keys = {
        "restore_central_communication_capability",
        "restore_north_basic_engineering_support",
        "restore_east_emergency_power_network",
        "restore_east_emergency_water_supply",
    }

    assert {
        item.key: item.model_dump(mode="json")
        for item in definition.objectives
        if item.key in objective_keys
    } == {
        item.key: item.model_dump(mode="json")
        for item in baseline.objectives
        if item.key in objective_keys
    }
    assert (
        _initial_fact(
            definition,
            "southeast_fuel_emergency_power_plant",
            "sustained_generation_capability",
        ).initial_value
        == "UNAVAILABLE"
    )


def test_task5_and_task6_objective_contracts_are_exact() -> None:
    definition = _definition()
    by_key = {item.key: item for item in definition.objectives}
    task5 = by_key["establish_citywide_sustained_emergency_support"]
    task6 = by_key["establish_sustained_emergency_generation"]

    assert len(task5.completion_requirements) == 4
    assert {
        (item.region_key, item.resource_key, item.minimum)
        for item in task5.completion_requirements
        if item.kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST
    } == {
        ("central_district", "emergency_relief_supplies", 30),
        ("east_residential_district", "emergency_relief_supplies", 30),
        ("southeast_heights_district", "emergency_relief_supplies", 30),
    }
    assert len(task6.completion_requirements) == 2
    reserve = next(
        item
        for item in task6.completion_requirements
        if item.kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST
    )
    assert (reserve.region_key, reserve.resource_key, reserve.minimum) == (
        "southeast_heights_district",
        "emergency_fuel",
        100,
    )
    assert reserve.knowledge_gate is not None
    assert reserve.knowledge_gate.fact_key == "sustained_requirements_discovered"


def test_task56_resource_budget_and_hidden_supply_contract() -> None:
    definition = _definition()
    pools = {item.pool_key: item for item in definition.initialization.resource_pools}

    assert pools["north_service_depot_stock"].quantity == 25
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

    for target_key, suffix in (
        ("rail_freight_yard", "rail"),
        ("vehicle_depot", "vehicle"),
        ("city_distribution_center", "distribution"),
    ):
        repaired = actions.execute_action(
            actor_key=industrial.actor_key,
            action_key="repair_industrial_facility",
            target_key=target_key,
            parameters={},
            idempotency_key=f"task56-repair-{suffix}",
        )
        assert repaired.applied is not None and repaired.applied.outcome.failure is None

    _set_fact(
        session,
        game_id,
        "city_distribution_center",
        "sustained_humanitarian_logistics",
        "AVAILABLE",
    )
    for region in (
        "central_district",
        "east_residential_district",
        "southeast_heights_district",
    ):
        _known_inflow(session, game_id, region, "emergency_relief_supplies", 30)
    session.flush()
    task = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "establish_citywide_sustained_emergency_support",
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
    assert {
        "receive_external_relief_supplies",
        "establish_sustained_humanitarian_logistics",
        "generate_power",
        "commission_sustained_generation",
    }.issubset(action_keys)
    assert ScenarioDefinitionValidator().validate(definition.model_dump(mode="json")).passed


def test_task4_completed_authoritative_state_and_intentional_differences() -> None:
    definition = _definition()
    actors = {item.key: item for item in definition.actors.actor_profiles}
    assert {
        key: (item.initial_node_key, item.command_reachability.value)
        for key, item in actors.items()
    } == {
        "communications_repair_team_alpha": ("south_waterfront_district", "ONLINE"),
        "electrical_repair_team_alpha": ("south_waterfront_district", "ONLINE"),
        "industrial_repair_team_alpha": ("south_waterfront_district", "ONLINE"),
        "logistics_team_alpha": ("southeast_heights_district", "ONLINE"),
        "municipal_transport_team": ("central_district", "ONLINE"),
        "water_repair_team_alpha": ("south_waterfront_district", "ONLINE"),
    }
    assert all(
        item.resource_survey_completed and item.resource_inventory_visibility.value == "VISIBLE"
        for item in definition.initialization.region_resource_knowledge
    )
    for node_key, fact_key, value in (
        ("district_service_center", "operational", True),
        ("heavy_equipment_yard", "operational", True),
        ("heavy_equipment_yard", "heavy_engineering_support", "AVAILABLE"),
        ("south_communication_core", "operational", True),
        ("south_pump_station", "operational", True),
        ("south_substation", "operational", True),
        ("south_substation", "power_supply", "AVAILABLE"),
        ("water_treatment_plant", "operational", True),
        ("water_treatment_plant", "heavy_engineering_support_ready", True),
    ):
        fact = _initial_fact(definition, node_key, fact_key)
        assert fact.initial_value == value
        assert fact.initial_visibility == Visibility.KNOWN


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
    assert (
        actions["establish_sustained_humanitarian_logistics"].required_interaction_key
        == "sustained_logistics_control"
    )

    final_rules = [
        item
        for item in definition.rules
        if item.action_key == "establish_sustained_humanitarian_logistics"
        and item.phase.value == "PREFLIGHT"
    ]
    passability_nodes = {
        item.condition.node.node_key
        for item in final_rules
        if item.condition is not None
        and item.condition.fact_key == "passable"
        and item.condition.node is not None
    }
    assert passability_nodes == transport_keys


def test_task5_and_task6_resource_budget_is_solvable_without_artificial_costs() -> None:
    definition = _definition()
    pools = {item.pool_key: item for item in definition.initialization.resource_pools}
    assert (
        sum(
            item.quantity
            for item in pools.values()
            if item.resource_key == "general_engineering_parts"
        )
        == 45
    )
    assert (
        sum(
            item.quantity
            for item in pools.values()
            if item.resource_key == "municipal_repair_materials"
        )
        == 70
    )
    assert (
        sum(
            item.quantity
            for item in pools.values()
            if item.resource_key == "electrical_repair_parts"
        )
        == 20
    )
    assert (45 - 40, 70 - 60, 20 - 15) == (5, 10, 5)
    assert pools["warehouse_relief_supply"].quantity == 100
    assert pools["north_emergency_fuel"].quantity == 50
    assert pools["south_emergency_fuel"].quantity == 120


def test_task5_completion_and_same_objective_reissue_use_current_truth(
    session: Session,
) -> None:
    runtime, _scope, agent, _definition_value = _runtime(session, "task56-reissue")
    game_id = runtime.instance.id  # type: ignore[attr-defined]
    _set_fact(
        session,
        game_id,
        "city_distribution_center",
        "sustained_humanitarian_logistics",
        "AVAILABLE",
    )
    pools = [
        _known_inflow(session, game_id, region, "emergency_relief_supplies", 30)
        for region in (
            "central_district",
            "east_residential_district",
            "southeast_heights_district",
        )
    ]
    session.flush()

    first = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "establish_citywide_sustained_emergency_support",
        initialize_plan=False,
    )
    assert first.status == AgentTaskStatus.SUCCEEDED

    pools[0].value = 29
    session.flush()
    second = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "establish_citywide_sustained_emergency_support",
        initialize_plan=False,
    )
    assert second.status == AgentTaskStatus.ACTIVE
    assert not agent.evaluate(second).completed
    assert first.status == AgentTaskStatus.SUCCEEDED


def test_task5_then_task6_complete_sequentially_on_one_runtime(session: Session) -> None:
    runtime, _scope, agent, _definition_value = _runtime(session, "task56-sequential")
    game_id = runtime.instance.id  # type: ignore[attr-defined]
    _set_fact(
        session,
        game_id,
        "city_distribution_center",
        "sustained_humanitarian_logistics",
        "AVAILABLE",
    )
    for region in (
        "central_district",
        "east_residential_district",
        "southeast_heights_district",
    ):
        _known_inflow(session, game_id, region, "emergency_relief_supplies", 30)
    session.flush()

    task5 = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "establish_citywide_sustained_emergency_support",
        initialize_plan=False,
    )
    assert task5.status == AgentTaskStatus.SUCCEEDED

    task6 = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "establish_sustained_emergency_generation",
        initialize_plan=False,
    )
    assert task6.status == AgentTaskStatus.ACTIVE
    _set_fact(
        session,
        game_id,
        "southeast_fuel_emergency_power_plant",
        "sustained_generation_capability",
        "AVAILABLE",
    )
    fuel = _known_inflow(
        session,
        game_id,
        "southeast_heights_district",
        "emergency_fuel",
        99,
    )
    session.flush()
    assert not agent.evaluate(task6).completed
    fuel.value = 100
    session.flush()
    assert agent.evaluate(task6).completed


def test_task6_hidden_requirement_and_supply_chain_reveal_are_knowledge_safe(
    session: Session,
) -> None:
    runtime, scope, agent, definition = _runtime(session, "task56-hidden-gate")
    game_id = runtime.instance.id  # type: ignore[attr-defined]
    task = agent.create_task(
        runtime.session,  # type: ignore[attr-defined]
        "establish_sustained_emergency_generation",
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
    assert [item["key"] for item in requirements] == ["generation_capability"]
    assert '"minimum": 100' not in initial_serialized
    assert "south_emergency_fuel" not in initial_serialized
    for node_key in ("river_port", "south_fuel_terminal"):
        node = session.get(GameInstanceNodeState, (game_id, node_key))
        assert node is not None and node.visibility == Visibility.KNOWN
    initial_actions = {item["action_key"] for item in initial_payload["action_contracts"]}
    assert "generate_power" in initial_actions
    assert "commission_sustained_generation" not in initial_actions
    assert not {
        item["target_key"]
        for item in initial_payload["target_bindings"]
        if item["target_key"] in {"river_port", "south_fuel_terminal"}
    }

    plant = session.get(
        GameInstanceFactState,
        (game_id, "southeast_fuel_emergency_power_plant", "operational"),
    )
    actor = session.get(GameInstanceActor, (game_id, "electrical_repair_team_alpha"))
    assert plant is not None and actor is not None
    plant.truth_value = True
    actor.current_node_key = "southeast_heights_district"
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
    assert {item["key"] for item in revealed_requirements} == {
        "generation_capability",
        "southeast_fuel_reserve",
    }
    assert (
        next(item for item in revealed_requirements if item["key"] == "southeast_fuel_reserve")[
            "minimum"
        ]
        == 100
    )
    revealed_nodes = {item["key"] for item in revealed_payload["known_world"]["nodes"]}
    assert {"river_port", "south_fuel_terminal"}.issubset(revealed_nodes)
    revealed_actions = {item["action_key"] for item in revealed_payload["action_contracts"]}
    assert "commission_sustained_generation" in revealed_actions
    assert {
        "river_port",
        "south_fuel_terminal",
    }.issubset({item["target_key"] for item in revealed_payload["target_bindings"]})


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
