from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.dependency_closure import (
    _has_known_available_resource_at,
    build_dependency_closure,
)
from app.agent.generic import (
    GenericAgentError,
    GenericAgentService,
    PlanningActionCatalogBuilder,
    _ProjectedRegionResourceKnowledge,
    _ProjectedResourcePool,
)
from app.agent.planning_context import PlanningContextBuilder, objective_context
from app.agent.provider import (
    PlannerActionContract,
    PlannerInput,
    PlannerKnownWorldSlice,
    PlanRequest,
)
from app.domain.enums import (
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.resources import RUNTIME_KNOWN_INFLOW_POOL_KEY, resource_state_key
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ActionBehavior, ScenarioDefinitionV2
from app.domain.world import Visibility
from app.engine.rules import GenericRuleOutcome, ResourceMutation
from app.infrastructure.db.models import (
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceResourceState,
    Player,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import GameLifecycleService
from app.services.generic_game import GenericGameError, GenericGameService
from app.services.knowledge_projection import SharedKnowledgeProjection
from app.services.player_projection import PlayerProjectionService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.runtime_recovery import RuntimeRecoveryService
from app.services.scenarios import ScenarioService
from tests.scenario_fixtures import LINJIANG_V2_TEST, predefined_goal_resolution

LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0 = LINJIANG_V2_TEST


def _remove_unknown_pool_effects(document: dict[str, Any]) -> None:
    """Keep synthetic pool fixtures valid when the canonical pool set evolves."""

    known_pool_keys = {pool["pool_key"] for pool in document["initialization"]["resource_pools"]}
    retained_rules: list[dict[str, Any]] = []
    for rule in document["rules"]:
        effects = [
            effect
            for effect in rule.get("effects", [])
            if not (
                effect.get("pool_key") is not None and effect.get("pool_key") not in known_pool_keys
            )
        ]
        if not effects:
            continue
        rule["effects"] = effects
        retained_rules.append(rule)
    document["rules"] = retained_rules


def _pool_definition() -> ScenarioDefinitionV2:
    document: dict[str, Any] = deepcopy(LINJIANG_V2_TEST.model_dump(mode="json"))
    document["metadata"]["key"] = "resource_pool_architecture_test"
    document["metadata"]["name"] = "Resource Pool Architecture Test"
    document["world"]["key"] = "resource_pool_architecture_test"
    document["world"]["name"] = "Resource Pool Architecture Test"
    document["initialization"]["resource_initial_states"] = []
    document["initialization"]["resource_pools"] = [
        {
            "pool_key": "west_pool_a",
            "resource_key": "electrical_repair_parts",
            "region_key": "west_logistics_district",
            "quantity": 5,
            "visibility": "VISIBLE",
            "availability": "AVAILABLE",
        },
        {
            "pool_key": "west_pool_b",
            "resource_key": "electrical_repair_parts",
            "region_key": "west_logistics_district",
            "quantity": 50,
            "visibility": "VISIBLE",
            "availability": "AVAILABLE",
        },
        {
            "pool_key": "north_hidden_pool",
            "resource_key": "electrical_repair_parts",
            "region_key": "north_industrial_district",
            "facility_key": "north_power_substation",
            "quantity": 20,
            "visibility": "HIDDEN",
            "availability": "UNAVAILABLE",
            "survey_discoverable": True,
            "availability_requirement": {
                "node_key": "north_power_substation",
                "fact_key": "operational",
                "value": True,
            },
        },
        {
            "pool_key": "southeast_district_service_stock",
            "resource_key": "electrical_repair_parts",
            "region_key": "southeast_heights_district",
            "facility_key": "district_service_center",
            "quantity": 0,
            "visibility": "HIDDEN",
            "availability": "UNAVAILABLE",
            "survey_discoverable": True,
            "availability_requirement": {
                "node_key": "district_service_center",
                "fact_key": "operational",
                "value": True,
            },
        },
        {
            "pool_key": "north_service_depot_stock",
            "resource_key": "general_engineering_parts",
            "region_key": "north_industrial_district",
            "facility_key": "utility_service_depot",
            "quantity": 0,
            "visibility": "HIDDEN",
            "availability": "UNAVAILABLE",
            "survey_discoverable": True,
            "availability_requirement": {
                "node_key": "utility_service_depot",
                "fact_key": "operational",
                "value": True,
            },
        },
        {
            "pool_key": "north_heavy_equipment_stock",
            "resource_key": "general_engineering_parts",
            "region_key": "north_industrial_district",
            "facility_key": "heavy_equipment_yard",
            "quantity": 0,
            "visibility": "HIDDEN",
            "availability": "UNAVAILABLE",
            "survey_discoverable": True,
            "availability_requirement": {
                "node_key": "heavy_equipment_yard",
                "fact_key": "operational",
                "value": True,
            },
        },
    ]
    document["initialization"]["region_resource_knowledge"] = [
        {
            "region_key": "north_industrial_district",
            "resource_inventory_visibility": "HIDDEN",
            "resource_survey_completed": False,
        }
    ]
    for node in document["world"]["nodes"]:
        if node["key"] == "north_power_substation":
            node["facts"] = [
                {
                    "key": "operational",
                    "name": "Operational",
                    "description": "Whether the substation is operational.",
                    "value_type": "BOOLEAN",
                    "initial_value": False,
                    "initial_visibility": "KNOWN",
                }
            ]
    for actor in document["actors"]["actor_profiles"]:
        if actor["key"] == "electrical_repair_team_alpha":
            actor["initial_node_key"] = "east_residential_district"
            actor["command_reachability"] = "ONLINE"
    _remove_unknown_pool_effects(document)
    return ScenarioDefinitionV2.model_validate(document)


def _legacy_balance_definition() -> ScenarioDefinitionV2:
    document: dict[str, Any] = deepcopy(LINJIANG_V2_TEST.model_dump(mode="json"))
    document["metadata"]["key"] = "resource_pool_legacy_balance_test"
    document["metadata"]["name"] = "Resource Pool Legacy Balance Test"
    document["world"]["key"] = "resource_pool_legacy_balance_test"
    document["world"]["name"] = "Resource Pool Legacy Balance Test"
    document["initialization"]["region_resource_knowledge"] = []
    document["initialization"]["resource_pools"] = []
    document["initialization"]["resource_initial_states"] = [
        {
            "resource_key": resource_key,
            "scope_node_key": (
                "west_logistics_district"
                if resource_key == "electrical_repair_parts"
                else "central_district"
            ),
            "value": 10 if resource_key == "electrical_repair_parts" else 0,
            "reserved_value": 0,
        }
        for resource_key in (
            "communication_equipment",
            "electrical_repair_parts",
            "general_engineering_parts",
            "municipal_repair_materials",
            "water_system_parts",
            "emergency_relief_supplies",
            "emergency_fuel",
        )
    ]
    pool_keys = {
        "north_service_depot_stock",
        "north_heavy_equipment_stock",
        "southeast_district_service_stock",
    }
    document["rules"] = [
        rule
        for rule in document["rules"]
        if not any(pool_key in str(rule) for pool_key in pool_keys)
    ]
    _remove_unknown_pool_effects(document)
    return ScenarioDefinitionV2.model_validate(document)


def _canonical_general_parts_definition(
    key: str,
    *,
    west_quantity: int = 10,
) -> ScenarioDefinitionV2:
    document: dict[str, Any] = deepcopy(
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.model_dump(mode="json")
    )
    document["metadata"]["key"] = key
    document["metadata"]["name"] = key
    document["world"]["key"] = key
    document["world"]["name"] = key
    document["initialization"]["resource_initial_states"] = []
    for pool in document["initialization"]["resource_pools"]:
        if pool["pool_key"] == "central_general_stock":
            pool["quantity"] = 0
        elif pool["pool_key"] == "west_general_stock":
            pool["quantity"] = west_quantity
    return ScenarioDefinitionV2.model_validate(document)


def _runtime(
    session: Session,
    definition: ScenarioDefinitionV2,
    key: str,
    *,
    use_platform_player: bool = False,
) -> tuple[object, object]:
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    if use_platform_player:
        player = GameLifecycleService(session).platform_player()
    else:
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


def test_legacy_balance_becomes_default_visible_available_pool_and_region_defaults(
    session: Session,
) -> None:
    runtime, _scope = _runtime(
        session,
        _legacy_balance_definition(),
        "resource-pool-legacy-defaults",
    )
    row = session.get(
        GameInstanceResourceState,
        (runtime.instance.id, "electrical_repair_parts@west_logistics_district"),
    )
    assert row is not None
    assert row.pool_key == "default"
    assert row.visibility == ResourcePoolVisibility.VISIBLE
    assert row.availability == ResourcePoolAvailability.AVAILABLE
    knowledge = session.scalars(
        select(GameInstanceRegionResourceKnowledge).where(
            GameInstanceRegionResourceKnowledge.game_instance_id == runtime.instance.id
        )
    ).all()
    assert knowledge
    assert all(
        row.resource_inventory_visibility == ResourceInventoryVisibility.VISIBLE
        and row.resource_survey_completed
        for row in knowledge
    )


def test_hidden_pool_is_absent_from_shared_planner_and_player_safe_projection(
    session: Session,
) -> None:
    definition = _pool_definition()
    runtime, scope = _runtime(
        session,
        definition,
        "resource-pool-hidden-before-survey",
        use_platform_player=True,
    )
    projection = SharedKnowledgeProjection(session, scope, definition)

    visible = projection.visible_resource_pools()
    assert {item.pool_key for item in visible} == {"default", "west_pool_a", "west_pool_b"}
    intelligence = projection.resource_intelligence()
    assert intelligence["regions"]["north_industrial_district"]["resources"] == {}
    player_intelligence = (
        PlayerProjectionService(session)
        .game_state(GameInstanceId(runtime.instance.id))
        .resource_intelligence
    )
    assert player_intelligence["regions"]["north_industrial_district"]["resources"] == {}
    planner = projection.planner_resources()
    planner_json = str(planner)
    assert "north_hidden_pool" not in planner_json
    assert all(
        "pool_key" not in pool
        for pool in planner["resources"]["electrical_repair_parts"]["regions"][
            "west_logistics_district"
        ]["pools"]
    )
    assert all(
        "pool_key" not in pool
        for pool in planner["regions"]["west_logistics_district"]["resources"][
            "electrical_repair_parts"
        ]["pools"]
    )
    task = GenericAgentService(session, scope).create_task(
        runtime.session,
        "restore central communications",
        resolved_goal=predefined_goal_resolution("restore_central_communication_capability"),
        initialize_plan=False,
    )
    objective = definition.objectives[0]
    context = PlanningContextBuilder(session, scope).build(
        definition,
        (objective,),
        task=task,
        replan_reason=None,
    )
    context_json = json.dumps(context.model_dump(mode="json"), ensure_ascii=False)
    assert "north_hidden_pool" not in context_json
    assert '"pool_key"' not in context_json
    assert runtime.instance.id == scope.game_instance_id


def test_authored_visible_pool_stays_private_before_region_survey(
    session: Session,
) -> None:
    definition = _pool_definition()
    runtime, scope = _runtime(
        session,
        definition,
        "resource-pool-visible-before-survey",
        use_platform_player=True,
    )
    pool = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            "electrical_repair_parts@north_industrial_district@north_hidden_pool",
        ),
    )
    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "north_industrial_district"),
    )
    assert pool is not None and knowledge is not None
    pool.visibility = ResourcePoolVisibility.VISIBLE
    pool.facility_key = None
    pool.availability = ResourcePoolAvailability.AVAILABLE
    pool.availability_requirement = None
    knowledge.resource_inventory_visibility = ResourceInventoryVisibility.HIDDEN
    knowledge.resource_survey_completed = False
    session.flush()

    projection = SharedKnowledgeProjection(session, scope, definition)
    visible = projection.visible_resource_pools()
    assert not any(item.pool_key == "north_hidden_pool" for item in visible)
    assert (
        projection.resource_intelligence()["regions"]["north_industrial_district"]["resources"]
        == {}
    )

    player = PlayerProjectionService(session).game_state(GameInstanceId(runtime.instance.id))
    player_region = player.resource_intelligence["regions"]["north_industrial_district"]
    assert player_region["resource_survey_completed"] is False
    assert player_region["resources"] == {}


def test_fresh_linjiang_hides_authored_unsurveyed_inventory_from_all_projections(
    session: Session,
) -> None:
    runtime, scope = _runtime(
        session,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        "resource-pool-fresh-linjiang-knowledge",
        use_platform_player=True,
    )
    projection = SharedKnowledgeProjection(session, scope, LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0)
    intelligence = projection.resource_intelligence()

    for region_key in (
        "central_district",
        "east_residential_district",
        "north_industrial_district",
        "south_waterfront_district",
        "southeast_heights_district",
        "west_logistics_district",
    ):
        assert intelligence["regions"][region_key]["resource_survey_completed"] is False
        assert intelligence["regions"][region_key]["resources"] == {}

    planner = projection.planner_resources()
    for region_key in (
        "central_district",
        "east_residential_district",
        "north_industrial_district",
        "south_waterfront_district",
        "southeast_heights_district",
        "west_logistics_district",
    ):
        assert planner["regions"][region_key]["resources"] == {}

    task = GenericAgentService(session, scope).create_task(
        runtime.session,
        "restore central communications",
        resolved_goal=predefined_goal_resolution("restore_central_communication_capability"),
        initialize_plan=False,
    )
    planner_input = PlanningContextBuilder(session, scope).build_v2(
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        (LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.objectives[0],),
        task=task,
        replan_reason=None,
    )
    for resource in planner_input.known_world.resources.values():
        assert isinstance(resource, dict)
        scopes = resource.get("scopes", {})
        assert isinstance(scopes, dict)
        assert all(
            region_key not in scopes
            for region_key in (
                "east_residential_district",
                "north_industrial_district",
                "south_waterfront_district",
                "southeast_heights_district",
                "west_logistics_district",
            )
        )

    player = PlayerProjectionService(session).game_state(GameInstanceId(runtime.instance.id))
    assert player.resource_intelligence["regions"]["west_logistics_district"]["resources"] == {}
    state = GenericGameService(session, scope)._locked_state(LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0)
    assert (
        GenericGameService._known_source_pools(
            state,
            "east_residential_district",
            "communication_equipment",
        )
        == ()
    )


def test_missing_pool_is_known_zero_only_after_completed_visible_survey(
    session: Session,
) -> None:
    definition = _pool_definition()
    runtime, scope = _runtime(session, definition, "resource-pool-known-zero-semantic")
    projection = SharedKnowledgeProjection(session, scope, definition)

    planner = projection.planner_resources()
    central = planner["regions"]["central_district"]["resources"]
    assert central["electrical_repair_parts"]["known_total"] == 0
    assert central["electrical_repair_parts"]["known_available"] == 0
    assert central["electrical_repair_parts"]["knowledge_status"] == "KNOWN_ZERO"
    assert central["electrical_repair_parts"]["pools"] == []

    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "central_district"),
    )
    assert knowledge is not None
    knowledge.resource_inventory_visibility = ResourceInventoryVisibility.HIDDEN
    knowledge.resource_survey_completed = False
    session.flush()

    incomplete_projection = SharedKnowledgeProjection(session, scope, definition)
    incomplete_planner = incomplete_projection.planner_resources()
    assert (
        "electrical_repair_parts"
        not in incomplete_planner["regions"]["central_district"]["resources"]
    )

    agent = GenericAgentService(session, scope)
    projected_pools, projected_knowledge = agent._projected_resource_state(definition)
    with pytest.raises(GenericAgentError) as error:
        agent._consume_projected_resource(
            "central_district",
            "electrical_repair_parts",
            10,
            projected_pools,
            projected_knowledge,
        )
    assert error.value.code == "RESOURCE_INVENTORY_UNKNOWN"


def test_hidden_truth_pool_presence_cannot_change_public_resource_knowledge(
    session: Session,
) -> None:
    definition = _pool_definition()
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version

    def public_projection(
        key: str, *, add_hidden_truth_row: bool
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        player = Player(name=key)
        session.add(player)
        session.flush()
        runtime = RuntimeInitializationService(session).create(
            player_id=player.id,
            scenario_version_id=version.id,
            creation_key=key,
        )
        if add_hidden_truth_row:
            session.add(
                GameInstanceResourceState(
                    game_instance_id=runtime.instance.id,
                    resource_identity=(
                        "electrical_repair_parts@central_district@central_hidden_truth_only"
                    ),
                    resource_key="electrical_repair_parts",
                    scope_node_key="central_district",
                    pool_key="central_hidden_truth_only",
                    facility_key="central_telecom_hub",
                    value=40,
                    reserved_value=0,
                    visibility=ResourcePoolVisibility.HIDDEN,
                    availability=ResourcePoolAvailability.AVAILABLE,
                    survey_discoverable=True,
                )
            )
            session.flush()
        scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
        projection = SharedKnowledgeProjection(session, scope, definition)
        task = GenericAgentService(session, scope).create_task(
            runtime.session,
            "restore central communications",
            resolved_goal=predefined_goal_resolution("restore_central_communication_capability"),
            initialize_plan=False,
        )
        closure = PlanningContextBuilder(session, scope).build_v2_closure(
            definition,
            GenericAgentService(session, scope)._objectives(task, definition),
            task=task,
            replan_reason=None,
        )
        agent = GenericAgentService(session, scope)
        pools, knowledge = agent._projected_resource_state(definition)
        with pytest.raises(GenericAgentError) as error:
            agent._consume_projected_resource(
                "central_district",
                "electrical_repair_parts",
                10,
                pools,
                knowledge,
            )
        return (
            projection.planner_resources(),
            closure.planner_input.model_dump(mode="json"),
            error.value.code,
        )

    no_hidden_resources, no_hidden_input, no_hidden_error = public_projection(
        "resource-pool-anti-leak-no-hidden",
        add_hidden_truth_row=False,
    )
    hidden_resources, hidden_input, hidden_error = public_projection(
        "resource-pool-anti-leak-hidden",
        add_hidden_truth_row=True,
    )

    assert no_hidden_resources == hidden_resources
    assert no_hidden_input == hidden_input
    assert no_hidden_error == hidden_error == "KNOWN_RESOURCE_INSUFFICIENT"


def test_reserved_inventory_is_not_available_to_closure_validator_or_runtime(
    session: Session,
) -> None:
    document: dict[str, Any] = deepcopy(_pool_definition().model_dump(mode="json"))
    for pool in document["initialization"]["resource_pools"]:
        if pool["pool_key"] == "west_pool_a":
            pool["quantity"] = 20
            pool["reserved_value"] = 10
        elif pool["pool_key"] == "west_pool_b":
            pool["quantity"] = 0
            pool["reserved_value"] = 0
    definition = ScenarioDefinitionV2.model_validate(document)
    _runtime_value, scope = _runtime(session, definition, "resource-pool-reserved-available")

    projection = SharedKnowledgeProjection(session, scope, definition)
    pool = next(
        item for item in projection.visible_resource_pools() if item.pool_key == "west_pool_a"
    )
    summary = projection.resource_intelligence()["regions"]["west_logistics_district"]["resources"][
        "electrical_repair_parts"
    ]
    assert pool.quantity == 20
    assert pool.available_quantity == 10
    assert summary["known_total"] == 20
    assert summary["known_available"] == 10

    raw_resource = {
        "scopes": {
            "west_logistics_district": {
                "value": 20,
                "known_total": 20,
                "known_available": 10,
            }
        }
    }
    assert not _has_known_available_resource_at(raw_resource, "west_logistics_district", 15)
    assert _has_known_available_resource_at(raw_resource, "west_logistics_district", 10)

    objective = definition.objectives[0]
    requirement = objective.completion_requirements[0]

    def closure_for(cost: int):
        planner_input = PlannerInput(
            action_contracts=(
                PlannerActionContract(
                    action_key="repair_reserved_test",
                    deterministic_effects=(
                        {
                            "type": "FACT_MUTATION",
                            "target": "target_key",
                            "fact_key": requirement.fact_key,
                            "value": requirement.accepted_values[0],
                        },
                        {
                            "type": "RESOURCE_DELTA",
                            "resource_key": "electrical_repair_parts",
                            "amount": -cost,
                        },
                    ),
                ),
            ),
            known_world=PlannerKnownWorldSlice(
                nodes=(
                    {
                        "key": requirement.node_key,
                        "type": "facility",
                        "interactions": [],
                    },
                ),
                resources={"electrical_repair_parts": raw_resource},
                resource_knowledge=(
                    {
                        "region_key": "west_logistics_district",
                        "resource_inventory_visibility": "VISIBLE",
                        "resource_survey_completed": True,
                    },
                ),
            ),
        )
        return build_dependency_closure(
            definition,
            (objective,),
            planner_input,
        )

    insufficient_closure = closure_for(15)
    assert all(
        item.get("status") == "UNKNOWN"
        for item in insufficient_closure.planner_input.known_world.unknown_dependencies
    )
    assert not any(
        item.get("dimension") == "RESOURCE_SOURCE"
        for item in insufficient_closure.planner_input.known_world.unknown_dependencies
    )
    sufficient_closure = closure_for(10)
    assert not any(
        item.get("dimension") == "RESOURCE_SOURCE"
        for item in sufficient_closure.planner_input.known_world.unknown_dependencies
    )

    agent = GenericAgentService(session, scope)
    projected_pools, region_knowledge = agent._projected_resource_state(definition)
    with pytest.raises(GenericAgentError) as validator_error:
        agent._consume_projected_resource(
            "west_logistics_district",
            "electrical_repair_parts",
            15,
            projected_pools,
            region_knowledge,
        )
    assert validator_error.value.code == "KNOWN_RESOURCE_INSUFFICIENT"
    agent._consume_projected_resource(
        "west_logistics_district",
        "electrical_repair_parts",
        10,
        projected_pools,
        region_knowledge,
    )

    game = GenericGameService(session, scope)
    with pytest.raises(GenericGameError) as runtime_error:
        game._expand_resource_mutations(
            (
                ResourceMutation(
                    "electrical_repair_parts",
                    -15,
                    "west_logistics_district",
                    "default",
                ),
            )
        )
    assert runtime_error.value.code == "KNOWN_RESOURCE_INSUFFICIENT"
    expanded = game._expand_resource_mutations(
        (
            ResourceMutation(
                "electrical_repair_parts",
                -10,
                "west_logistics_district",
                "default",
            ),
        )
    )
    assert expanded == (
        ResourceMutation(
            "electrical_repair_parts",
            -10,
            "west_logistics_district",
            "west_pool_a",
        ),
    )


def test_survey_reveals_discoverable_facility_pool_without_unlocking_it(
    session: Session,
) -> None:
    definition = _pool_definition()
    runtime, scope = _runtime(session, definition, "resource-pool-survey")
    actor = session.get(GameInstanceActor, (runtime.instance.id, "logistics_team_alpha"))
    assert actor is not None
    actor.current_node_key = "north_industrial_district"
    session.flush()

    result = GenericGameService(session, scope).execute(
        actor_key="logistics_team_alpha",
        action_key="survey_resources",
        target_node_key="north_industrial_district",
        parameters={},
    )
    assert result.outcome.failure is None
    assert result.outcome.outcome_code == "SURVEYED"
    assert any(item.kind == "RESOURCE_DISCOVERED" for item in result.knowledge_changes)
    assert any(
        item.kind == "RESOURCE_INVENTORY_REVEALED"
        and item.key == "north_industrial_district.resource_inventory_visibility"
        for item in result.knowledge_changes
    )
    assert any(
        item.kind == "RESOURCE_SURVEY_COMPLETED"
        and item.key == "north_industrial_district.resource_survey_completed"
        and item.value is True
        for item in result.knowledge_changes
    )

    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "north_industrial_district"),
    )
    assert knowledge is not None
    assert knowledge.resource_inventory_visibility == ResourceInventoryVisibility.VISIBLE
    assert knowledge.resource_survey_completed is True
    pool = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            "electrical_repair_parts@north_industrial_district@north_hidden_pool",
        ),
    )
    assert pool is not None
    assert pool.visibility == ResourcePoolVisibility.VISIBLE
    assert pool.availability == ResourcePoolAvailability.UNAVAILABLE

    intelligence = SharedKnowledgeProjection(session, scope, definition).resource_intelligence()
    summary = intelligence["regions"]["north_industrial_district"]["resources"][
        "electrical_repair_parts"
    ]
    assert summary["known_total"] == 20
    assert summary["known_available"] == 0
    assert summary["pools"][0]["availability_requirement"]["known_value"] is False
    assert summary["pools"][0]["availability_requirement_status"] == "KNOWN"

    repeated = GenericGameService(session, scope).execute(
        actor_key="logistics_team_alpha",
        action_key="survey_resources",
        target_node_key="north_industrial_district",
        parameters={},
    )
    assert repeated.outcome.failure is not None
    assert repeated.outcome.failure.code == "RESOURCE_SURVEY_ALREADY_COMPLETED"


def test_transport_aggregates_multiple_visible_available_pools_and_creates_destination(
    session: Session,
) -> None:
    definition = _pool_definition()
    runtime, scope = _runtime(session, definition, "resource-pool-aggregate-transport")
    assert (
        session.get(
            GameInstanceResourceState,
            (runtime.instance.id, "electrical_repair_parts@central_district"),
        )
        is None
    )
    actor = session.get(GameInstanceActor, (runtime.instance.id, "logistics_team_alpha"))
    assert actor is not None
    actor.current_node_key = "west_logistics_district"
    blocked = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "west_freight_corridor", "passable"),
    )
    assert blocked is not None
    blocked.truth_value = True
    session.flush()

    result = GenericGameService(session, scope).execute(
        actor_key="logistics_team_alpha",
        action_key="transport_resource",
        target_node_key="central_district",
        parameters={"resource_key": "electrical_repair_parts", "amount": 40},
    )
    assert result.outcome.failure is None
    rows = {
        (row.scope_node_key, row.pool_key): row.value
        for row in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == runtime.instance.id
            )
        )
    }
    assert rows[("west_logistics_district", "west_pool_a")] == 0
    assert rows[("west_logistics_district", "west_pool_b")] == 15
    assert rows[("central_district", RUNTIME_KNOWN_INFLOW_POOL_KEY)] == 40
    intelligence = SharedKnowledgeProjection(session, scope, definition).resource_intelligence()
    west_summary = intelligence["regions"]["west_logistics_district"]["resources"][
        "electrical_repair_parts"
    ]
    assert west_summary["known_total"] == 15
    assert west_summary["known_available"] == 15
    assert any(pool["quantity"] == 0 for pool in west_summary["pools"])


def _prepare_general_parts_transport(session: Session, runtime: object) -> None:
    actor = session.get(GameInstanceActor, (runtime.instance.id, "logistics_team_alpha"))
    passability = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "west_freight_corridor", "passable"),
    )
    assert actor is not None and passability is not None
    actor.current_node_key = "west_logistics_district"
    passability.truth_value = True
    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "west_logistics_district"),
    )
    assert knowledge is not None
    knowledge.resource_inventory_visibility = ResourceInventoryVisibility.VISIBLE
    knowledge.resource_survey_completed = True
    session.flush()


def _add_legacy_central_general_parts_row(session: Session, runtime: object) -> None:
    session.add(
        GameInstanceResourceState(
            game_instance_id=runtime.instance.id,
            resource_identity="general_engineering_parts@central_district",
            resource_key="general_engineering_parts",
            scope_node_key="central_district",
            pool_key="default",
            value=0,
            reserved_value=0,
            visibility=ResourcePoolVisibility.VISIBLE,
            availability=ResourcePoolAvailability.AVAILABLE,
            survey_discoverable=False,
        )
    )
    session.flush()


def _central_general_parts_player_rows(session: Session, runtime: object):
    state = PlayerProjectionService(session).game_state(GameInstanceId(runtime.instance.id))
    return [
        item
        for item in state.resources
        if item.key == "general_engineering_parts" and item.scope_region_key == "central_district"
    ]


def test_player_projection_keeps_facility_stock_out_of_usable_regional_total(
    session: Session,
) -> None:
    runtime, scope = _runtime(
        session,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        "resource-pool-player-facility-separation",
        use_platform_player=True,
    )
    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "north_industrial_district"),
    )
    assert knowledge is not None
    knowledge.resource_inventory_visibility = ResourceInventoryVisibility.VISIBLE
    knowledge.resource_survey_completed = True
    for pool_key in ("north_emergency_engineering_stock", "north_service_depot_stock"):
        pool = session.get(
            GameInstanceResourceState,
            (
                runtime.instance.id,
                f"general_engineering_parts@north_industrial_district@{pool_key}",
            ),
        )
        assert pool is not None
        pool.visibility = ResourcePoolVisibility.VISIBLE
        if pool_key != "north_emergency_engineering_stock":
            pool.availability_requirement = None
    heavy_equipment_state = session.get(
        GameInstanceNodeState,
        (runtime.instance.id, "heavy_equipment_yard"),
    )
    assert heavy_equipment_state is not None
    heavy_equipment_state.visibility = Visibility.KNOWN
    utility_service_state = session.get(
        GameInstanceNodeState,
        (runtime.instance.id, "utility_service_depot"),
    )
    assert utility_service_state is not None
    utility_service_state.visibility = Visibility.KNOWN
    session.flush()

    shared = SharedKnowledgeProjection(session, scope, LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0)
    shared_summary = shared.resource_intelligence()["regions"]["north_industrial_district"][
        "resources"
    ]["general_engineering_parts"]
    assert shared_summary["known_total"] == 105
    assert shared_summary["known_available"] == 5

    state = PlayerProjectionService(session).game_state(GameInstanceId(runtime.instance.id))
    north_rows = [
        item
        for item in state.resources
        if item.key == "general_engineering_parts"
        and item.scope_region_key == "north_industrial_district"
    ]
    assert len(north_rows) == 1
    assert north_rows[0].value == 5
    assert north_rows[0].facility_key is None
    assert north_rows[0].availability == ResourcePoolAvailability.AVAILABLE.value

    player_summary = state.resource_intelligence["regions"]["north_industrial_district"][
        "resources"
    ]["general_engineering_parts"]
    assert player_summary["known_total"] == 105
    assert player_summary["known_available"] == 5
    assert {pool["pool_key"] for pool in player_summary["pools"]} == {
        "north_emergency_engineering_stock",
        "north_service_depot_stock",
    }

    nodes_by_key = {node.key: node for node in state.visible_nodes}
    assert nodes_by_key["heavy_equipment_yard"].associated_known_resources == []
    associated = nodes_by_key["utility_service_depot"].associated_known_resources
    assert len(associated) == 1
    assert associated[0]["resource_key"] == "general_engineering_parts"
    assert associated[0]["resource_name"] == "通用工程部件"
    assert associated[0]["facility_name"] == "市政工程维修基地"
    assert associated[0]["quantity"] == 100
    assert associated[0]["availability"] == "UNAVAILABLE"
    assert associated[0]["availability_requirement"] == {
        "node_key": "utility_service_depot",
        "fact_key": "operational",
        "value": True,
    }
    assert associated[0]["availability_requirement_status"] == "KNOWN"
    assert "known_value" not in associated[0]["availability_requirement"]

    for pool_key in ("north_service_depot_stock",):
        pool = session.get(
            GameInstanceResourceState,
            (
                runtime.instance.id,
                f"general_engineering_parts@north_industrial_district@{pool_key}",
            ),
        )
        assert pool is not None
        pool.availability = ResourcePoolAvailability.AVAILABLE
    session.flush()
    refreshed = PlayerProjectionService(session).game_state(GameInstanceId(runtime.instance.id))
    refreshed_pools = refreshed.resource_intelligence["regions"]["north_industrial_district"][
        "resources"
    ]["general_engineering_parts"]["pools"]
    assert {
        pool["pool_key"]: pool["availability"]
        for pool in refreshed_pools
        if pool["pool_key"] in {"north_service_depot_stock"}
    } == {
        "north_service_depot_stock": "AVAILABLE",
    }
    refreshed_nodes = {node.key: node for node in refreshed.visible_nodes}
    assert refreshed_nodes["heavy_equipment_yard"].associated_known_resources == []
    assert refreshed_nodes["utility_service_depot"].associated_known_resources == []


def test_transport_reuses_canonical_destination_and_player_aggregates_legacy_duplicate(
    session: Session,
) -> None:
    definition = _canonical_general_parts_definition(
        "resource_pool_canonical_destination",
        west_quantity=10,
    )
    runtime, scope = _runtime(
        session,
        definition,
        "resource-pool-canonical-destination",
        use_platform_player=True,
    )
    _add_legacy_central_general_parts_row(session, runtime)
    _prepare_general_parts_transport(session, runtime)

    result = GenericGameService(session, scope).execute(
        actor_key="logistics_team_alpha",
        action_key="transport_resource",
        target_node_key="central_district",
        parameters={"resource_key": "general_engineering_parts", "amount": 5},
    )
    assert result.outcome.failure is None
    central_rows = [
        row
        for row in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == runtime.instance.id,
                GameInstanceResourceState.scope_node_key == "central_district",
                GameInstanceResourceState.resource_key == "general_engineering_parts",
            )
        )
    ]
    assert {(row.pool_key, row.value) for row in central_rows} == {
        ("default", 0),
        ("central_general_stock", 0),
        (RUNTIME_KNOWN_INFLOW_POOL_KEY, 5),
    }
    player_rows = _central_general_parts_player_rows(session, runtime)
    assert len(player_rows) == 1
    assert player_rows[0].value == 5
    assert player_rows[0].reserved_value == 0

    GenericGameService(session, scope)._apply(
        definition,
        "logistics_team_alpha",
        GenericRuleOutcome(
            selected_rule_key="test:consume_general_parts",
            resource_mutations=(
                ResourceMutation(
                    "general_engineering_parts",
                    -5,
                    "central_district",
                    "default",
                ),
            ),
        ),
    )
    session.flush()
    player_rows = _central_general_parts_player_rows(session, runtime)
    assert len(player_rows) == 1
    assert player_rows[0].value == 0


def test_repeated_transport_accumulates_in_one_canonical_destination_balance(
    session: Session,
) -> None:
    definition = _canonical_general_parts_definition(
        "resource_pool_repeated_canonical_transport",
        west_quantity=10,
    )
    runtime, scope = _runtime(
        session,
        definition,
        "resource-pool-repeated-canonical-transport",
        use_platform_player=True,
    )
    _prepare_general_parts_transport(session, runtime)
    game = GenericGameService(session, scope)

    first = game.execute(
        actor_key="logistics_team_alpha",
        action_key="transport_resource",
        target_node_key="central_district",
        parameters={"resource_key": "general_engineering_parts", "amount": 5},
    )
    assert first.outcome.failure is None
    returned = game.execute(
        actor_key="logistics_team_alpha",
        action_key="travel",
        target_node_key="west_logistics_district",
        parameters={},
    )
    assert returned.outcome.failure is None
    second = game.execute(
        actor_key="logistics_team_alpha",
        action_key="transport_resource",
        target_node_key="central_district",
        parameters={"resource_key": "general_engineering_parts", "amount": 3},
    )
    assert second.outcome.failure is None

    central_rows = [
        row
        for row in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == runtime.instance.id,
                GameInstanceResourceState.scope_node_key == "central_district",
                GameInstanceResourceState.resource_key == "general_engineering_parts",
            )
        )
    ]
    assert {(row.pool_key, row.value) for row in central_rows} == {
        ("central_general_stock", 0),
        (RUNTIME_KNOWN_INFLOW_POOL_KEY, 8),
    }
    player_rows = _central_general_parts_player_rows(session, runtime)
    assert len(player_rows) == 1
    assert player_rows[0].value == 8


def test_transport_excludes_reserved_source_inventory(
    session: Session,
) -> None:
    document: dict[str, Any] = deepcopy(_pool_definition().model_dump(mode="json"))
    for pool in document["initialization"]["resource_pools"]:
        if pool["pool_key"] == "west_pool_a":
            pool["quantity"] = 20
            pool["reserved_value"] = 10
        elif pool["pool_key"] == "west_pool_b":
            pool["quantity"] = 0
            pool["reserved_value"] = 0
    definition = ScenarioDefinitionV2.model_validate(document)
    runtime, scope = _runtime(session, definition, "resource-pool-reserved-transport")
    actor = session.get(GameInstanceActor, (runtime.instance.id, "logistics_team_alpha"))
    passability = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "west_freight_corridor", "passable"),
    )
    assert actor is not None and passability is not None
    actor.current_node_key = "west_logistics_district"
    passability.truth_value = True
    session.flush()

    insufficient = GenericGameService(session, scope).execute(
        actor_key="logistics_team_alpha",
        action_key="transport_resource",
        target_node_key="central_district",
        parameters={"resource_key": "electrical_repair_parts", "amount": 15},
    )
    assert insufficient.outcome.failure is not None
    assert insufficient.outcome.failure.code == "TRANSPORT_RESOURCE_INSUFFICIENT"

    successful = GenericGameService(session, scope).execute(
        actor_key="logistics_team_alpha",
        action_key="transport_resource",
        target_node_key="central_district",
        parameters={"resource_key": "electrical_repair_parts", "amount": 10},
    )
    assert successful.outcome.failure is None
    source = session.get(
        GameInstanceResourceState,
        (runtime.instance.id, "electrical_repair_parts@west_logistics_district@west_pool_a"),
    )
    destination = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            resource_state_key(
                "electrical_repair_parts",
                "central_district",
                RUNTIME_KNOWN_INFLOW_POOL_KEY,
            ),
        ),
    )
    assert source is not None and source.value == 10 and source.reserved_value == 10
    assert destination is not None and destination.value == 10


def test_repair_adjust_resource_aggregates_multiple_visible_available_pools(
    session: Session,
) -> None:
    document: dict[str, Any] = deepcopy(_pool_definition().model_dump(mode="json"))
    document["initialization"]["resource_pools"].extend(
        [
            {
                "pool_key": "east_pool_a",
                "resource_key": "electrical_repair_parts",
                "region_key": "east_residential_district",
                "quantity": 4,
                "visibility": "VISIBLE",
                "availability": "AVAILABLE",
            },
            {
                "pool_key": "east_pool_b",
                "resource_key": "electrical_repair_parts",
                "region_key": "east_residential_district",
                "quantity": 8,
                "visibility": "VISIBLE",
                "availability": "AVAILABLE",
            },
            {
                "pool_key": "east_general_pool",
                "resource_key": "general_engineering_parts",
                "region_key": "east_residential_district",
                "quantity": 5,
                "visibility": "VISIBLE",
                "availability": "AVAILABLE",
            },
        ]
    )
    definition = ScenarioDefinitionV2.model_validate(document)
    runtime, scope = _runtime(session, definition, "resource-pool-aggregate-repair")

    result = GenericGameService(session, scope).execute(
        actor_key="electrical_repair_team_alpha",
        action_key="repair_electrical",
        target_node_key="east_distribution_station",
        parameters={},
    )
    assert result.outcome.failure is None
    rows = {
        (row.scope_node_key, row.pool_key): row.value
        for row in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == runtime.instance.id
            )
        )
    }
    assert rows[("east_residential_district", "east_pool_a")] == 0
    assert rows[("east_residential_district", "east_pool_b")] == 2
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "east_distribution_station", "operational"),
    )
    assert fact is not None and fact.truth_value is True


def test_unknown_unlock_requirement_is_explicitly_safe_in_player_and_planner_projection(
    session: Session,
) -> None:
    definition = _pool_definition()
    runtime, scope = _runtime(
        session,
        definition,
        "resource-pool-unknown-requirement",
        use_platform_player=True,
    )
    pool = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            "electrical_repair_parts@north_industrial_district@north_hidden_pool",
        ),
    )
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "north_power_substation", "operational"),
    )
    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "north_industrial_district"),
    )
    assert pool is not None and fact is not None and knowledge is not None
    pool.visibility = ResourcePoolVisibility.VISIBLE
    fact.visibility = Visibility.HIDDEN
    knowledge.resource_inventory_visibility = ResourceInventoryVisibility.VISIBLE
    knowledge.resource_survey_completed = True
    session.flush()

    projection = SharedKnowledgeProjection(session, scope, definition)
    known_pool = next(
        item for item in projection.visible_resource_pools() if item.pool_key == "north_hidden_pool"
    )
    assert known_pool.availability_requirement == {
        "node_key": "north_power_substation",
        "fact_key": "operational",
        "value": True,
    }
    assert known_pool.availability_requirement_status == "KNOWN"
    assert "known_value" not in known_pool.availability_requirement
    summary = projection.resource_intelligence()["regions"]["north_industrial_district"][
        "resources"
    ]["electrical_repair_parts"]
    pool_summary = summary["pools"][0]
    assert pool_summary["availability_requirement"] == {
        "node_key": "north_power_substation",
        "fact_key": "operational",
        "value": True,
    }
    assert pool_summary["availability_requirement_status"] == "KNOWN"
    planner_pool = projection.planner_resources()["resources"]["electrical_repair_parts"][
        "regions"
    ]["north_industrial_district"]["pools"][0]
    assert planner_pool["availability_requirement"] == {
        "node_key": "north_power_substation",
        "fact_key": "operational",
        "value": True,
    }
    assert planner_pool["availability_requirement_status"] == "KNOWN"
    player_pool = (
        PlayerProjectionService(session)
        .game_state(GameInstanceId(runtime.instance.id))
        .resource_intelligence["regions"]["north_industrial_district"]["resources"][
            "electrical_repair_parts"
        ]["pools"][0]
    )
    assert player_pool["availability_requirement"] == {
        "node_key": "north_power_substation",
        "fact_key": "operational",
        "value": True,
    }
    assert player_pool["availability_requirement_status"] == "KNOWN"
    assert "known_value" not in player_pool["availability_requirement"]
    task = GenericAgentService(session, scope).create_task(
        runtime.session,
        "restore central communications",
        resolved_goal=predefined_goal_resolution("restore_central_communication_capability"),
        initialize_plan=False,
    )
    context = PlanningContextBuilder(session, scope).build(
        definition,
        (definition.objectives[0],),
        task=task,
        replan_reason=None,
    )
    context_json = json.dumps(context.model_dump(mode="json"), ensure_ascii=False)
    assert "north_power_substation" in context_json
    assert '"known_value":' not in context_json


def test_region_visibility_effect_does_not_complete_survey_or_reveal_hidden_pools(
    session: Session,
) -> None:
    document: dict[str, Any] = deepcopy(_pool_definition().model_dump(mode="json"))
    document["initialization"]["region_resource_knowledge"].append(
        {
            "region_key": "central_district",
            "resource_inventory_visibility": "HIDDEN",
            "resource_survey_completed": False,
        }
    )
    document["initialization"]["resource_pools"].append(
        {
            "pool_key": "central_hidden_facility_pool",
            "resource_key": "electrical_repair_parts",
            "region_key": "central_district",
            "facility_key": "central_telecom_hub",
            "quantity": 30,
            "visibility": "HIDDEN",
            "availability": "UNAVAILABLE",
            "survey_discoverable": True,
        }
    )
    logistics_actor = next(
        actor
        for actor in document["actors"]["actor_profiles"]
        if actor["key"] == "logistics_team_alpha"
    )
    logistics_actor["allowed_action_keys"].append("restore_inventory_visibility")
    document["actions"].append(
        {
            "key": "restore_inventory_visibility",
            "name": "Restore inventory visibility",
            "description": "Restore ordinary inventory visibility for the current Region.",
            "required_interaction_key": "transport_destination",
            "execution_mode": "IMMEDIATE",
            "allowed_actor_capabilities": ["EXECUTE_ACTION"],
            "expected_outcomes": [
                {"code": "VISIBLE", "name": "Inventory visible", "success": True}
            ],
            "behavior": "RULE",
            "locality": "REGION",
        }
    )
    document["rules"].append(
        {
            "key": "restore_inventory_visibility_resolution",
            "phase": "RESOLVE",
            "action_key": "restore_inventory_visibility",
            "priority": 0,
            "effects": [
                {
                    "kind": "SET_REGION_RESOURCE_VISIBILITY",
                    "region_key": "central_district",
                    "visibility": "VISIBLE",
                },
                {"kind": "EMIT_OUTCOME", "outcome_code": "VISIBLE"},
            ],
        }
    )
    definition = ScenarioDefinitionV2.model_validate(document)
    runtime, scope = _runtime(session, definition, "resource-pool-region-visibility-effect")
    result = GenericGameService(session, scope).execute(
        actor_key="logistics_team_alpha",
        action_key="restore_inventory_visibility",
        target_node_key="central_district",
        parameters={},
    )
    assert result.outcome.failure is None
    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "central_district"),
    )
    hidden_pool = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            "electrical_repair_parts@central_district@central_hidden_facility_pool",
        ),
    )
    assert knowledge is not None and hidden_pool is not None
    assert knowledge.resource_inventory_visibility == ResourceInventoryVisibility.VISIBLE
    assert knowledge.resource_survey_completed is False
    assert hidden_pool.visibility == ResourcePoolVisibility.HIDDEN


def test_planning_guidance_is_present_for_initial_replan_and_repair_contexts(
    session: Session,
) -> None:
    document: dict[str, Any] = deepcopy(LINJIANG_V2_TEST.model_dump(mode="json"))
    document["metadata"]["key"] = "resource_pool_planning_guidance_test"
    document["metadata"]["name"] = "Resource Pool Planning Guidance Test"
    document["world"]["key"] = "resource_pool_planning_guidance_test"
    document["world"]["name"] = "Resource Pool Planning Guidance Test"
    document["objectives"][0]["planning_guidance"] = (
        "Prefer known reachable Regions and preserve enough parts for repair."
    )
    definition = ScenarioDefinitionV2.model_validate(document)
    runtime, scope = _runtime(session, definition, "resource-pool-planning-guidance")
    service = GenericAgentService(session, scope)
    task = service.create_task(
        runtime.session,
        "restore central communications",
        resolved_goal=predefined_goal_resolution("restore_central_communication_capability"),
        initialize_plan=False,
    )
    objective = definition.objectives[0]
    known_refs = PlanningActionCatalogBuilder(session, scope).known_fact_refs()
    builder = PlanningContextBuilder(session, scope)

    for call_type, reason in (
        ("INITIAL_PLAN", None),
        ("REPLAN", "TRAVEL_BLOCKED"),
        ("REPAIR", "TRAVEL_BLOCKED"),
    ):
        context = builder.build(
            definition,
            (objective,),
            task=task,
            replan_reason=reason,
        )
        planner_input = builder.build_v2(
            definition,
            (objective,),
            task=task,
            replan_reason=reason,
        )
        request = PlanRequest(
            call_type=call_type,
            goal=task.goal_description,
            objective_scope=objective_context(
                (objective,),
                known_fact_refs=known_refs,
            ),
            planner_input=planner_input,
        )
        assert context.goal["objectives"][0]["planning_guidance"] == (
            "Prefer known reachable Regions and preserve enough parts for repair."
        )
        assert request.objective_scope[0]["planning_guidance"] == (
            "Prefer known reachable Regions and preserve enough parts for repair."
        )
        assert (
            request.provider_payload()["planner_input"]["objective"]["objectives"][0][
                "planning_guidance"
            ]
            == "Prefer known reachable Regions and preserve enough parts for repair."
        )

    assert service.evaluate(task).completed is False


def _projected_resource_validator() -> GenericAgentService:
    return GenericAgentService.__new__(GenericAgentService)


def _synthetic_region_definition() -> Any:
    region_keys = ("source_region", "middle_region", "destination_region")
    nodes = tuple(
        type("SyntheticNode", (), {"key": key, "node_type_key": "region"})() for key in region_keys
    )
    node_by_key = {node.key: node for node in nodes}
    locality = type(
        "SyntheticLocality",
        (),
        {
            "region_node_type_key": "region",
            "facility_node_type_key": "facility",
            "transport_node_type_key": "transport",
            "located_in_relation_type_key": "located_in",
            "transport_endpoint_relation_type_key": "endpoint",
            "passability_fact_key": "passable",
        },
    )()
    world = type(
        "SyntheticWorld",
        (),
        {
            "nodes": nodes,
            "resources": (type("SyntheticResource", (), {"key": "synthetic_resource"})(),),
            "node": lambda self, key: node_by_key.get(key),
        },
    )()
    return type(
        "SyntheticDefinition",
        (),
        {
            "metadata": type("SyntheticMetadata", (), {"locality": locality})(),
            "world": world,
        },
    )()


def _projected_pool(
    region_key: str,
    *,
    quantity: int | None,
    resource_key: str = "synthetic_resource",
    pool_key: str = "base",
    facility_key: str | None = None,
    visibility: ResourcePoolVisibility = ResourcePoolVisibility.VISIBLE,
    availability: ResourcePoolAvailability = ResourcePoolAvailability.AVAILABLE,
) -> _ProjectedResourcePool:
    return _ProjectedResourcePool(
        pool_key=pool_key,
        resource_key=resource_key,
        region_key=region_key,
        facility_key=facility_key,
        quantity=quantity,
        visibility=visibility,
        availability=availability,
        survey_discoverable=False,
    )


def _projected_knowledge(
    region_key: str,
    *,
    visibility: ResourceInventoryVisibility,
    survey_completed: bool,
) -> dict[str, _ProjectedRegionResourceKnowledge]:
    return {
        region_key: _ProjectedRegionResourceKnowledge(
            visibility=visibility,
            survey_completed=survey_completed,
        )
    }


def _add_inflow(
    validator: GenericAgentService,
    region_key: str,
    amount: int,
    balance: dict[tuple[str | None, str], int],
) -> None:
    validator._add_projected_resource(
        region_key,
        "synthetic_resource",
        amount,
        balance,
    )


def _consume(
    validator: GenericAgentService,
    region_key: str,
    amount: int,
    pools: dict[str, _ProjectedResourcePool],
    knowledge: dict[str, _ProjectedRegionResourceKnowledge],
    balance: dict[tuple[str | None, str], int],
) -> bool:
    return validator._consume_projected_resource(
        region_key,
        "synthetic_resource",
        amount,
        pools,
        knowledge,
        projected_known_resource_balance=balance,
    )


def test_projected_transport_inflow_can_be_consumed_in_unknown_region() -> None:
    validator = _projected_resource_validator()
    definition = _synthetic_region_definition()
    transport = type("SyntheticTransport", (), {"behavior": ActionBehavior.TRANSPORT_RESOURCE})()
    pools = {
        "source": _projected_pool("source_region", quantity=10),
    }
    knowledge = {
        **_projected_knowledge(
            "source_region",
            visibility=ResourceInventoryVisibility.VISIBLE,
            survey_completed=True,
        ),
        **_projected_knowledge(
            "middle_region",
            visibility=ResourceInventoryVisibility.HIDDEN,
            survey_completed=False,
        ),
        **_projected_knowledge(
            "destination_region",
            visibility=ResourceInventoryVisibility.HIDDEN,
            survey_completed=False,
        ),
    }
    balance: dict[tuple[str | None, str], int] = {}
    locations = {"carrier": "source_region"}

    validator._validate_and_advance_projected_resources(
        definition,
        transport,
        "carrier",
        "middle_region",
        {"resource_key": "synthetic_resource", "amount": 10},
        locations,
        pools,
        knowledge,
        balance,
        (),
    )
    assert balance[("middle_region", "synthetic_resource")] == 10

    locations["carrier"] = "middle_region"
    validator._validate_and_advance_projected_resources(
        definition,
        transport,
        "carrier",
        "destination_region",
        {"resource_key": "synthetic_resource", "amount": 10},
        locations,
        pools,
        knowledge,
        balance,
        (),
    )

    assert pools["source"].quantity == 0
    assert balance[("middle_region", "synthetic_resource")] == 0
    assert balance[("destination_region", "synthetic_resource")] == 10
    assert knowledge["middle_region"].visibility == ResourceInventoryVisibility.HIDDEN
    assert knowledge["middle_region"].survey_completed is False


def test_projected_inflow_shortfall_preserves_unknown_error() -> None:
    validator = _projected_resource_validator()
    knowledge = _projected_knowledge(
        "middle_region",
        visibility=ResourceInventoryVisibility.HIDDEN,
        survey_completed=False,
    )
    balance: dict[tuple[str | None, str], int] = {}
    _add_inflow(validator, "middle_region", 10, balance)

    with pytest.raises(GenericAgentError) as error:
        _consume(validator, "middle_region", 15, {}, knowledge, balance)

    assert error.value.code == "RESOURCE_INVENTORY_UNKNOWN"
    assert balance[("middle_region", "synthetic_resource")] == 10


def test_projected_inflow_supports_local_consumption() -> None:
    validator = _projected_resource_validator()
    knowledge = _projected_knowledge(
        "middle_region",
        visibility=ResourceInventoryVisibility.HIDDEN,
        survey_completed=False,
    )
    balance: dict[tuple[str | None, str], int] = {}
    _add_inflow(validator, "middle_region", 10, balance)

    assert _consume(validator, "middle_region", 10, {}, knowledge, balance) is True
    assert balance[("middle_region", "synthetic_resource")] == 0


def test_known_zero_region_accepts_projected_inflow_transit() -> None:
    validator = _projected_resource_validator()
    knowledge = _projected_knowledge(
        "middle_region",
        visibility=ResourceInventoryVisibility.VISIBLE,
        survey_completed=True,
    )
    balance: dict[tuple[str | None, str], int] = {}
    _add_inflow(validator, "middle_region", 10, balance)

    assert _consume(validator, "middle_region", 10, {}, knowledge, balance) is True
    assert balance[("middle_region", "synthetic_resource")] == 0


def test_unknown_region_without_projected_inflow_remains_unknown() -> None:
    validator = _projected_resource_validator()
    knowledge = _projected_knowledge(
        "middle_region",
        visibility=ResourceInventoryVisibility.HIDDEN,
        survey_completed=False,
    )

    with pytest.raises(GenericAgentError) as error:
        _consume(validator, "middle_region", 10, {}, knowledge, {})

    assert error.value.code == "RESOURCE_INVENTORY_UNKNOWN"


def test_projected_inflow_does_not_reveal_unknown_inventory() -> None:
    validator = _projected_resource_validator()
    hidden_pool = _projected_pool(
        "middle_region",
        quantity=90,
        pool_key="hidden_pool",
        facility_key="hidden_facility",
        visibility=ResourcePoolVisibility.HIDDEN,
    )
    pools = {"hidden": hidden_pool}
    knowledge = _projected_knowledge(
        "middle_region",
        visibility=ResourceInventoryVisibility.HIDDEN,
        survey_completed=False,
    )
    balance: dict[tuple[str | None, str], int] = {}
    _add_inflow(validator, "middle_region", 10, balance)

    assert _consume(validator, "middle_region", 10, pools, knowledge, balance) is True
    assert hidden_pool.quantity == 90
    assert hidden_pool.visibility == ResourcePoolVisibility.HIDDEN
    assert knowledge["middle_region"].visibility == ResourceInventoryVisibility.HIDDEN
    assert knowledge["middle_region"].survey_completed is False


def test_base_and_projected_known_balances_are_combined() -> None:
    validator = _projected_resource_validator()
    pools = {
        "base": _projected_pool("middle_region", quantity=5),
    }
    knowledge = _projected_knowledge(
        "middle_region",
        visibility=ResourceInventoryVisibility.VISIBLE,
        survey_completed=True,
    )
    balance: dict[tuple[str | None, str], int] = {}
    _add_inflow(validator, "middle_region", 10, balance)

    assert _consume(validator, "middle_region", 12, pools, knowledge, balance) is True
    assert pools["base"].quantity == 0
    assert balance[("middle_region", "synthetic_resource")] == 3


def test_unknown_base_inventory_with_projected_inflow_stays_unknown() -> None:
    validator = _projected_resource_validator()
    knowledge = _projected_knowledge(
        "middle_region",
        visibility=ResourceInventoryVisibility.HIDDEN,
        survey_completed=False,
    )
    balance: dict[tuple[str | None, str], int] = {}
    _add_inflow(validator, "middle_region", 10, balance)

    with pytest.raises(GenericAgentError) as error:
        _consume(validator, "middle_region", 12, {}, knowledge, balance)

    assert error.value.code == "RESOURCE_INVENTORY_UNKNOWN"
    assert balance[("middle_region", "synthetic_resource")] == 10


def _runtime_transport_definition(
    key: str,
    *,
    resource_key: str = "municipal_repair_materials",
    source_region: str = "east_residential_district",
    source_quantity: int = 10,
    south_base_quantity: int | None = None,
    hidden_south_quantity: int | None = None,
    extra_pools: tuple[dict[str, Any], ...] = (),
) -> ScenarioDefinitionV2:
    document: dict[str, Any] = deepcopy(_pool_definition().model_dump(mode="json"))
    normalized_key = key.replace("-", "_")
    document["metadata"]["key"] = normalized_key
    document["metadata"]["name"] = key
    document["world"]["key"] = normalized_key
    document["world"]["name"] = key
    for node in document["world"]["nodes"]:
        if (
            node["node_type_key"] == "region"
            and "transport_destination" not in node["interaction_keys"]
        ):
            node["interaction_keys"].append("transport_destination")
    document["initialization"]["region_resource_knowledge"].append(
        {
            "region_key": "south_waterfront_district",
            "resource_inventory_visibility": "HIDDEN",
            "resource_survey_completed": False,
        }
    )
    document["initialization"]["resource_pools"].append(
        {
            "pool_key": "runtime_transport_source",
            "resource_key": resource_key,
            "region_key": source_region,
            "quantity": source_quantity,
            "visibility": "VISIBLE",
            "availability": "AVAILABLE",
        }
    )
    if south_base_quantity is not None:
        document["initialization"]["resource_pools"].append(
            {
                "pool_key": "runtime_south_known_base",
                "resource_key": resource_key,
                "region_key": "south_waterfront_district",
                "quantity": south_base_quantity,
                "visibility": "VISIBLE",
                "availability": "AVAILABLE",
            }
        )
    if hidden_south_quantity is not None:
        document["initialization"]["resource_pools"].append(
            {
                "pool_key": "runtime_south_hidden_base",
                "resource_key": resource_key,
                "region_key": "south_waterfront_district",
                "facility_key": "south_substation",
                "quantity": hidden_south_quantity,
                "visibility": "HIDDEN",
                "availability": "AVAILABLE",
                "survey_discoverable": True,
            }
        )
    document["initialization"]["resource_pools"].extend(extra_pools)
    return ScenarioDefinitionV2.model_validate(document)


def _prepare_runtime_transport(
    session: Session,
    definition: ScenarioDefinitionV2,
    creation_key: str,
    *,
    actor_start: str = "east_residential_district",
    source_region: str = "east_residential_district",
    corridors: tuple[str, ...] = (
        "waterfront_access_corridor",
        "southeast_access_corridor",
    ),
) -> tuple[object, object]:
    runtime, scope = _runtime(session, definition, creation_key)
    actor = session.get(GameInstanceActor, (runtime.instance.id, "logistics_team_alpha"))
    assert actor is not None
    actor.current_node_key = actor_start
    source_knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, source_region),
    )
    south_knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "south_waterfront_district"),
    )
    assert source_knowledge is not None and south_knowledge is not None
    source_knowledge.resource_inventory_visibility = ResourceInventoryVisibility.VISIBLE
    source_knowledge.resource_survey_completed = True
    south_knowledge.resource_inventory_visibility = ResourceInventoryVisibility.HIDDEN
    south_knowledge.resource_survey_completed = False
    for corridor in corridors:
        passability = session.get(
            GameInstanceFactState,
            (runtime.instance.id, corridor, "passable"),
        )
        assert passability is not None
        passability.truth_value = True
    session.flush()
    return runtime, scope


def _runtime_transport(
    session: Session,
    scope: object,
    *,
    resource_key: str,
    target_region: str,
    amount: int,
) -> object:
    return GenericGameService(session, scope).execute(
        actor_key="logistics_team_alpha",
        action_key="transport_resource",
        target_node_key=target_region,
        parameters={"resource_key": resource_key, "amount": amount},
    )


@pytest.mark.parametrize(
    "resource_key",
    ("municipal_repair_materials", "general_engineering_parts", "electrical_repair_parts"),
)
def test_runtime_transport_inflow_can_cross_unknown_region(
    session: Session,
    resource_key: str,
) -> None:
    definition = _runtime_transport_definition(
        f"runtime-known-inflow-{resource_key}",
        resource_key=resource_key,
    )
    runtime, scope = _prepare_runtime_transport(
        session,
        definition,
        f"runtime-known-inflow-{resource_key}",
    )

    first = _runtime_transport(
        session,
        scope,
        resource_key=resource_key,
        target_region="south_waterfront_district",
        amount=10,
    )
    assert first.outcome.failure is None
    projection = SharedKnowledgeProjection(session, scope, definition)
    summary = projection.resource_intelligence()["regions"]["south_waterfront_district"][
        "resources"
    ][resource_key]
    assert summary["known_available"] == 10
    assert (
        projection.planner_resources()["regions"]["south_waterfront_district"]["resources"][
            resource_key
        ]["known_available"]
        == 10
    )
    recovered = RuntimeRecoveryService(session).recover(GameInstanceId(runtime.instance.id))
    scope = recovered.scope
    actor = session.get(GameInstanceActor, (runtime.instance.id, "logistics_team_alpha"))
    assert actor is not None
    assert actor.current_node_key == "south_waterfront_district"

    second = _runtime_transport(
        session,
        scope,
        resource_key=resource_key,
        target_region="southeast_heights_district",
        amount=10,
    )
    assert second.outcome.failure is None
    source = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            f"{resource_key}@east_residential_district@runtime_transport_source",
        ),
    )
    south = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            resource_state_key(
                resource_key,
                "south_waterfront_district",
                RUNTIME_KNOWN_INFLOW_POOL_KEY,
            ),
        ),
    )
    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "south_waterfront_district"),
    )
    assert source is not None and source.value == 0
    assert south is not None and south.value == 0
    assert knowledge is not None
    assert knowledge.resource_inventory_visibility == ResourceInventoryVisibility.HIDDEN
    assert knowledge.resource_survey_completed is False


def test_runtime_transport_inflow_shortfall_preserves_unknown_error(session: Session) -> None:
    resource_key = "municipal_repair_materials"
    definition = _runtime_transport_definition("runtime-inflow-shortfall")
    runtime, scope = _prepare_runtime_transport(
        session,
        definition,
        "runtime-inflow-shortfall",
    )
    first = _runtime_transport(
        session,
        scope,
        resource_key=resource_key,
        target_region="south_waterfront_district",
        amount=10,
    )
    assert first.outcome.failure is None

    result = _runtime_transport(
        session,
        scope,
        resource_key=resource_key,
        target_region="southeast_heights_district",
        amount=15,
    )
    assert result.outcome.failure is not None
    assert result.outcome.failure.code == "TRANSPORT_RESOURCE_KNOWLEDGE_UNKNOWN"
    south = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            resource_state_key(
                resource_key,
                "south_waterfront_district",
                RUNTIME_KNOWN_INFLOW_POOL_KEY,
            ),
        ),
    )
    target = session.get(
        GameInstanceResourceState,
        (runtime.instance.id, f"{resource_key}@southeast_heights_district"),
    )
    assert south is not None and south.value == 10
    assert target is None


def test_survey_after_inflow_reveals_base_without_double_counting(session: Session) -> None:
    definition = _runtime_transport_definition(
        "runtime-survey-after-inflow",
        south_base_quantity=5,
    )
    runtime, scope = _prepare_runtime_transport(
        session,
        definition,
        "runtime-survey-after-inflow",
    )
    first = _runtime_transport(
        session,
        scope,
        resource_key="municipal_repair_materials",
        target_region="south_waterfront_district",
        amount=10,
    )
    assert first.outcome.failure is None
    game = GenericGameService(session, scope)
    surveyed = game.execute(
        actor_key="logistics_team_alpha",
        action_key="survey_resources",
        target_node_key="south_waterfront_district",
        parameters={},
    )
    assert surveyed.outcome.failure is None

    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "south_waterfront_district"),
    )
    assert knowledge is not None and knowledge.resource_survey_completed is True
    summary = SharedKnowledgeProjection(session, scope, definition).resource_intelligence()[
        "regions"
    ]["south_waterfront_district"]["resources"]["municipal_repair_materials"]
    assert summary["known_total"] == 15
    assert summary["known_available"] == 15


def test_runtime_transport_inflow_supports_local_rule_resource_consumption(
    session: Session,
) -> None:
    resource_key = "water_system_parts"
    definition = _runtime_transport_definition(
        "runtime-inflow-local-repair",
        resource_key=resource_key,
        extra_pools=(
            {
                "pool_key": "runtime_general_support_source",
                "resource_key": "general_engineering_parts",
                "region_key": "east_residential_district",
                "quantity": 5,
                "visibility": "VISIBLE",
                "availability": "AVAILABLE",
            },
        ),
    )
    runtime, scope = _prepare_runtime_transport(
        session,
        definition,
        "runtime-inflow-local-repair",
    )
    game = GenericGameService(session, scope)
    combined = game.execute(
        actor_key="logistics_team_alpha",
        action_key="transport_resource",
        target_node_key="south_waterfront_district",
        parameters={
            "resources": [
                {"resource_key": resource_key, "amount": 10},
                {"resource_key": "general_engineering_parts", "amount": 5},
            ]
        },
    )
    assert combined.outcome.failure is None
    logistics = session.get(
        GameInstanceActor,
        (runtime.instance.id, "logistics_team_alpha"),
    )
    assert logistics is not None and logistics.current_node_key == "south_waterfront_district"
    water = session.get(
        GameInstanceActor,
        (runtime.instance.id, "water_repair_team_alpha"),
    )
    assert water is not None
    water.current_node_key = "south_waterfront_district"
    water.command_reachability = "ONLINE"
    pump_state = session.get(
        GameInstanceNodeState,
        (runtime.instance.id, "south_pump_station"),
    )
    assert pump_state is not None
    pump_state.visibility = Visibility.KNOWN
    session.flush()

    repaired = game.execute(
        actor_key="water_repair_team_alpha",
        action_key="repair_water_facility",
        target_node_key="south_pump_station",
        parameters={},
    )
    assert repaired.outcome.failure is None
    municipal = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            resource_state_key(
                resource_key,
                "south_waterfront_district",
                RUNTIME_KNOWN_INFLOW_POOL_KEY,
            ),
        ),
    )
    general = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            resource_state_key(
                "general_engineering_parts",
                "south_waterfront_district",
                RUNTIME_KNOWN_INFLOW_POOL_KEY,
            ),
        ),
    )
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "south_pump_station", "operational"),
    )
    assert municipal is not None and municipal.value == 0
    assert general is not None and general.value == 0
    assert fact is not None and fact.truth_value is True


def test_runtime_transport_known_base_and_inflow_are_combined(session: Session) -> None:
    resource_key = "municipal_repair_materials"
    definition = _runtime_transport_definition(
        "runtime-known-base-and-inflow",
        south_base_quantity=5,
    )
    runtime, scope = _prepare_runtime_transport(
        session,
        definition,
        "runtime-known-base-and-inflow",
    )
    south_knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "south_waterfront_district"),
    )
    assert south_knowledge is not None
    south_knowledge.resource_inventory_visibility = ResourceInventoryVisibility.VISIBLE
    south_knowledge.resource_survey_completed = True
    session.flush()
    first = _runtime_transport(
        session,
        scope,
        resource_key=resource_key,
        target_region="south_waterfront_district",
        amount=10,
    )
    assert first.outcome.failure is None
    second = _runtime_transport(
        session,
        scope,
        resource_key=resource_key,
        target_region="southeast_heights_district",
        amount=12,
    )
    assert second.outcome.failure is None
    south = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            resource_state_key(
                "municipal_repair_materials",
                "south_waterfront_district",
                "runtime_south_known_base",
            ),
        ),
    )
    assert south is not None and south.value == 3


def test_runtime_transport_known_zero_base_accepts_inflow(session: Session) -> None:
    resource_key = "municipal_repair_materials"
    definition = _runtime_transport_definition(
        "runtime-known-zero-base-and-inflow",
        south_base_quantity=0,
    )
    runtime, scope = _prepare_runtime_transport(
        session,
        definition,
        "runtime-known-zero-base-and-inflow",
    )
    south_knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "south_waterfront_district"),
    )
    assert south_knowledge is not None
    south_knowledge.resource_inventory_visibility = ResourceInventoryVisibility.VISIBLE
    south_knowledge.resource_survey_completed = True
    session.flush()
    first = _runtime_transport(
        session,
        scope,
        resource_key=resource_key,
        target_region="south_waterfront_district",
        amount=10,
    )
    assert first.outcome.failure is None
    second = _runtime_transport(
        session,
        scope,
        resource_key=resource_key,
        target_region="southeast_heights_district",
        amount=10,
    )
    assert second.outcome.failure is None


def test_runtime_transport_without_inflow_keeps_unknown_region_unknown(session: Session) -> None:
    definition = _runtime_transport_definition("runtime-no-inflow")
    runtime, scope = _prepare_runtime_transport(
        session,
        definition,
        "runtime-no-inflow",
    )
    actor = session.get(GameInstanceActor, (runtime.instance.id, "logistics_team_alpha"))
    assert actor is not None
    actor.current_node_key = "south_waterfront_district"
    session.flush()

    result = GenericGameService(session, scope).execute(
        actor_key="logistics_team_alpha",
        action_key="transport_resource",
        target_node_key="southeast_heights_district",
        parameters={"resource_key": "municipal_repair_materials", "amount": 10},
    )
    assert result.outcome.failure is not None
    assert result.outcome.failure.code == "TRANSPORT_RESOURCE_KNOWLEDGE_UNKNOWN"


def test_runtime_transport_inflow_does_not_reveal_hidden_base_pool(session: Session) -> None:
    resource_key = "municipal_repair_materials"
    definition = _runtime_transport_definition(
        "runtime-hidden-base-isolated",
        hidden_south_quantity=90,
    )
    runtime, scope = _prepare_runtime_transport(
        session,
        definition,
        "runtime-hidden-base-isolated",
    )
    first = _runtime_transport(
        session,
        scope,
        resource_key=resource_key,
        target_region="south_waterfront_district",
        amount=10,
    )
    assert first.outcome.failure is None
    second = _runtime_transport(
        session,
        scope,
        resource_key=resource_key,
        target_region="southeast_heights_district",
        amount=10,
    )
    assert second.outcome.failure is None
    hidden = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            "municipal_repair_materials@south_waterfront_district@runtime_south_hidden_base",
        ),
    )
    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "south_waterfront_district"),
    )
    assert hidden is not None and hidden.value == 90
    assert hidden.visibility == ResourcePoolVisibility.HIDDEN
    assert knowledge is not None
    assert knowledge.resource_inventory_visibility == ResourceInventoryVisibility.HIDDEN
    assert knowledge.resource_survey_completed is False


def test_runtime_transport_inflow_supports_three_hop_transit(session: Session) -> None:
    resource_key = "municipal_repair_materials"
    definition = _runtime_transport_definition(
        "runtime-three-hop-transit",
        source_region="west_logistics_district",
    )
    runtime, scope = _prepare_runtime_transport(
        session,
        definition,
        "runtime-three-hop-transit",
        actor_start="west_logistics_district",
        source_region="west_logistics_district",
        corridors=(
            "west_freight_corridor",
            "central_river_tunnel",
            "waterfront_access_corridor",
        ),
    )
    for target_region in (
        "central_district",
        "east_residential_district",
        "south_waterfront_district",
    ):
        transported = _runtime_transport(
            session,
            scope,
            resource_key=resource_key,
            target_region=target_region,
            amount=10,
        )
        assert transported.outcome.failure is None

    south = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            resource_state_key(
                resource_key,
                "south_waterfront_district",
                RUNTIME_KNOWN_INFLOW_POOL_KEY,
            ),
        ),
    )
    assert south is not None and south.value == 10
