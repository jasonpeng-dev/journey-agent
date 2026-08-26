from __future__ import annotations

from copy import deepcopy

from sqlalchemy.orm import Session

from app.domain.enums import (
    CommandReachability,
    ResourceInventoryVisibility,
    ResourcePoolVisibility,
)
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.domain.world import Visibility
from app.engine.locality import region_for_node
from app.infrastructure.db.models import (
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceResourceState,
    Player,
)
from app.scenarios.builtin import load_builtin_scenario
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.generic_game import GenericGameService
from app.services.knowledge_projection import SharedKnowledgeProjection
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService

V2_0 = load_builtin_scenario("linjiang_infrastructure_recovery_v2_0.yaml")


def _definition_with_pool(
    key: str,
    *,
    pool_key: str,
    resource_key: str,
    region_key: str,
    quantity: int,
) -> ScenarioDefinitionV2:
    document = deepcopy(V2_0.model_dump(mode="json"))
    document["metadata"]["key"] = key
    document["metadata"]["name"] = key
    document["world"]["key"] = key
    document["world"]["name"] = key
    document["initialization"]["resource_pools"].append(
        {
            "pool_key": pool_key,
            "resource_key": resource_key,
            "region_key": region_key,
            "quantity": quantity,
            "visibility": "VISIBLE",
            "availability": "AVAILABLE",
        }
    )
    return ScenarioDefinitionV2.model_validate(document)


def _runtime(
    session: Session,
    definition: ScenarioDefinitionV2,
    key: str,
):
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = (
        ScenarioService(session)
        .publish_draft(
            scenario.id,
            expected_revision=1,
        )
        .version
    )
    player = Player(name=key)
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=key,
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return runtime, scope


def _set_actor(
    session: Session,
    instance_id,
    actor_key: str,
    node_key: str,
) -> None:
    actor = session.get(GameInstanceActor, (instance_id, actor_key))
    assert actor is not None
    actor.current_node_key = node_key
    actor.command_reachability = CommandReachability.ONLINE.value
    session.flush()


def _fact(session: Session, instance_id, node_key: str, fact_key: str):
    row = session.get(GameInstanceFactState, (instance_id, node_key, fact_key))
    assert row is not None
    return row


def test_v3_initializes_facility_and_route_knowledge_boundaries(session: Session) -> None:
    definition = V2_0
    facilities = [
        node
        for node in definition.world.nodes
        if node.node_type_key == definition.metadata.locality.facility_node_type_key
    ]
    transports = [
        node
        for node in definition.world.nodes
        if node.node_type_key == definition.metadata.locality.transport_node_type_key
    ]
    assert len(facilities) == 30
    assert len(transports) == 6
    expected_communication = {
        "central_district": ("central_telecom_hub", False, Visibility.HIDDEN),
        "east_residential_district": ("east_telecom_station", True, Visibility.KNOWN),
        "west_logistics_district": ("west_communication_relay", True, Visibility.KNOWN),
        "north_industrial_district": ("north_communication_relay", False, Visibility.HIDDEN),
        "south_waterfront_district": ("south_communication_core", False, Visibility.HIDDEN),
        "southeast_heights_district": ("southeast_telecom_relay", True, Visibility.KNOWN),
    }
    for region, (communication_key, operational, visibility) in expected_communication.items():
        communication = definition.world.node(communication_key)
        assert communication is not None
        assert region_for_node(definition, communication_key) == region
        assert communication.fact("operational").initial_value is operational
        assert communication.fact("operational").initial_visibility == visibility
    for facility in facilities:
        region = region_for_node(definition, facility.key)
        expected_visibility = expected_communication[region][2]
        assert {fact.key for fact in facility.facts} >= {
            "operational",
            "power_supply",
        }
        assert facility.initial_visibility == Visibility.KNOWN
        assert all(fact.initial_visibility == expected_visibility for fact in facility.facts)
    for transport in transports:
        passable = next(fact for fact in transport.facts if fact.key == "passable")
        assert passable.initial_visibility == Visibility.HIDDEN


def test_inspect_reveals_non_resource_facility_facts_only(session: Session) -> None:
    runtime, scope = _runtime(session, V2_0, "linjiang-v2_0-inspect")
    _set_actor(session, runtime.instance.id, "logistics_team_alpha", "north_industrial_district")
    result = GenericGameService(session, scope).execute(
        actor_key="logistics_team_alpha",
        action_key="inspect",
        target_node_key="utility_service_depot",
        parameters={},
    )
    assert result.outcome.failure is None
    definition = V2_0
    facility = definition.world.node("utility_service_depot")
    assert facility is not None
    assert all(
        _fact(session, runtime.instance.id, facility.key, fact.key).visibility == Visibility.KNOWN
        for fact in facility.facts
    )
    region_knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "north_industrial_district"),
    )
    assert region_knowledge is not None
    assert region_knowledge.resource_survey_completed is False
    pool = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            "general_engineering_parts@north_industrial_district@north_heavy_equipment_stock",
        ),
    )
    assert pool is not None and pool.visibility == ResourcePoolVisibility.HIDDEN


def test_resource_pool_and_facility_knowledge_is_order_independent(session: Session) -> None:
    survey_definition = _definition_with_pool(
        "linjiang_v2_0_survey_then_inspect_definition",
        pool_key="order_test_survey_pool",
        resource_key="general_engineering_parts",
        region_key="north_industrial_district",
        quantity=1,
    )
    inspect_definition = _definition_with_pool(
        "linjiang_v2_0_inspect_then_survey_definition",
        pool_key="order_test_inspect_pool",
        resource_key="general_engineering_parts",
        region_key="north_industrial_district",
        quantity=1,
    )

    def discovered_pool(scope, definition):
        pool = next(
            (
                item
                for item in SharedKnowledgeProjection(
                    session, scope, definition
                ).visible_resource_pools()
                if item.pool_key == "north_heavy_equipment_stock"
            ),
            None,
        )
        return pool

    survey_first_runtime, survey_first_scope = _runtime(
        session,
        survey_definition,
        "linjiang-v2_0-survey-then-inspect",
    )
    _set_actor(
        session,
        survey_first_runtime.instance.id,
        "logistics_team_alpha",
        "north_industrial_district",
    )
    survey_first_game = GenericGameService(session, survey_first_scope)
    surveyed = survey_first_game.execute(
        actor_key="logistics_team_alpha",
        action_key="survey_resources",
        target_node_key="north_industrial_district",
        parameters={},
    )
    assert surveyed.outcome.failure is None
    before_inspect = discovered_pool(survey_first_scope, survey_definition)
    assert before_inspect is not None
    assert before_inspect.availability_requirement == {
        "node_key": "heavy_equipment_yard",
        "fact_key": "operational",
        "value": True,
    }
    assert "known_value" not in before_inspect.availability_requirement
    assert (
        _fact(
            session,
            survey_first_runtime.instance.id,
            "heavy_equipment_yard",
            "operational",
        ).visibility
        == Visibility.HIDDEN
    )
    inspected = survey_first_game.execute(
        actor_key="logistics_team_alpha",
        action_key="inspect",
        target_node_key="heavy_equipment_yard",
        parameters={},
    )
    assert inspected.outcome.failure is None
    survey_then_inspect = discovered_pool(survey_first_scope, survey_definition)
    assert survey_then_inspect is not None
    assert survey_then_inspect.availability_requirement["known_value"] is False

    inspect_first_runtime, inspect_first_scope = _runtime(
        session,
        inspect_definition,
        "linjiang-v2_0-inspect-then-survey",
    )
    _set_actor(
        session,
        inspect_first_runtime.instance.id,
        "logistics_team_alpha",
        "north_industrial_district",
    )
    inspect_first_game = GenericGameService(session, inspect_first_scope)
    inspected_first = inspect_first_game.execute(
        actor_key="logistics_team_alpha",
        action_key="inspect",
        target_node_key="heavy_equipment_yard",
        parameters={},
    )
    assert inspected_first.outcome.failure is None
    assert discovered_pool(inspect_first_scope, inspect_definition) is None
    surveyed_second = inspect_first_game.execute(
        actor_key="logistics_team_alpha",
        action_key="survey_resources",
        target_node_key="north_industrial_district",
        parameters={},
    )
    assert surveyed_second.outcome.failure is None
    inspect_then_survey = discovered_pool(inspect_first_scope, inspect_definition)
    assert inspect_then_survey is not None
    assert (
        inspect_then_survey.availability_requirement == survey_then_inspect.availability_requirement
    )


def test_repair_communications_reveals_target_region_facilities_not_resources(
    session: Session,
) -> None:
    definition = _definition_with_pool(
        "linjiang_v2_0_repair_communications",
        pool_key="north_communication_test",
        resource_key="communication_equipment",
        region_key="north_industrial_district",
        quantity=10,
    )
    runtime, scope = _runtime(session, definition, "linjiang_v2_0_repair_communications")
    _set_actor(
        session,
        runtime.instance.id,
        "communications_repair_team_alpha",
        "north_industrial_district",
    )
    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "north_industrial_district"),
    )
    assert knowledge is not None
    knowledge.resource_inventory_visibility = ResourceInventoryVisibility.VISIBLE
    knowledge.resource_survey_completed = False
    general = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            "general_engineering_parts@north_industrial_district@north_emergency_engineering_stock",
        ),
    )
    assert general is not None
    general.value = 20
    session.flush()

    result = GenericGameService(session, scope).execute(
        actor_key="communications_repair_team_alpha",
        action_key="repair_communications",
        target_node_key="north_communication_relay",
        parameters={},
    )
    assert result.outcome.failure is None
    assert result.outcome.outcome_code == "COMMUNICATIONS_REPAIRED"
    for node in definition.world.nodes:
        if node.node_type_key != definition.metadata.locality.facility_node_type_key:
            continue
        if region_for_node(definition, node.key) != "north_industrial_district":
            continue
        assert all(
            _fact(session, runtime.instance.id, node.key, fact.key).visibility == Visibility.KNOWN
            for fact in node.facts
        )
    assert knowledge.resource_survey_completed is False
    hidden_pool = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            "general_engineering_parts@north_industrial_district@north_heavy_equipment_stock",
        ),
    )
    assert hidden_pool is not None
    assert hidden_pool.visibility == ResourcePoolVisibility.HIDDEN


def test_route_attempts_reveal_truth_and_clear_requires_known_blocked(
    session: Session,
) -> None:
    definition = _definition_with_pool(
        "linjiang_v2_0_route_reveal",
        pool_key="central_municipal_test",
        resource_key="municipal_repair_materials",
        region_key="central_district",
        quantity=10,
    )
    runtime, scope = _runtime(session, definition, "linjiang_v2_0_route_reveal")
    game = GenericGameService(session, scope)
    _set_actor(session, runtime.instance.id, "logistics_team_alpha", "south_waterfront_district")
    travelled = game.execute(
        actor_key="logistics_team_alpha",
        action_key="travel",
        target_node_key="southeast_heights_district",
        parameters={},
    )
    assert travelled.outcome.failure is None
    assert (
        _fact(
            session,
            runtime.instance.id,
            "southeast_access_corridor",
            "passable",
        ).visibility
        == Visibility.KNOWN
    )
    assert (
        _fact(
            session,
            runtime.instance.id,
            "southeast_access_corridor",
            "passable",
        ).truth_value
        is True
    )

    _set_actor(session, runtime.instance.id, "logistics_team_alpha", "central_district")
    blocked = game.execute(
        actor_key="logistics_team_alpha",
        action_key="travel",
        target_node_key="east_residential_district",
        parameters={},
    )
    assert blocked.outcome.failure is not None
    assert blocked.outcome.failure.code == "TRAVEL_BLOCKED"
    central_route = _fact(
        session,
        runtime.instance.id,
        "central_river_tunnel",
        "passable",
    )
    assert central_route.visibility == Visibility.KNOWN
    assert central_route.truth_value is False

    _set_actor(session, runtime.instance.id, "municipal_transport_team", "central_district")
    cleared = game.execute(
        actor_key="municipal_transport_team",
        action_key="clear_transport",
        target_node_key="central_river_tunnel",
        parameters={},
    )
    assert cleared.outcome.failure is None
    assert cleared.outcome.outcome_code == "CLEARED"
    assert central_route.visibility == Visibility.KNOWN
    assert central_route.truth_value is True
    municipal = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            "municipal_repair_materials@central_district@central_municipal_test",
        ),
    )
    assert municipal is not None and municipal.value == 0


def test_clear_transport_rejects_unknown_route(session: Session) -> None:
    runtime, scope = _runtime(session, V2_0, "linjiang-v2_0-clear-unknown")
    _set_actor(session, runtime.instance.id, "municipal_transport_team", "central_district")
    result = GenericGameService(session, scope).execute(
        actor_key="municipal_transport_team",
        action_key="clear_transport",
        target_node_key="central_river_tunnel",
        parameters={},
    )
    assert result.outcome.failure is not None
    assert result.outcome.failure.code == "TRANSPORT_NOT_CONFIRMED_BLOCKED"
