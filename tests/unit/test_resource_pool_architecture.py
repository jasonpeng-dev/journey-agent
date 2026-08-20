from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService, PlanningActionCatalogBuilder
from app.agent.planning_context import PlanningContextBuilder, objective_context
from app.agent.provider import PlanRequest
from app.domain.enums import (
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceResourceState,
    Player,
)
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V1
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.generic_game import GenericGameService
from app.services.knowledge_projection import SharedKnowledgeProjection
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService


def _pool_definition() -> ScenarioDefinitionV2:
    document: dict[str, Any] = deepcopy(LINJIANG_INFRASTRUCTURE_RECOVERY_V1.model_dump(mode="json"))
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
        if actor["key"] == "logistics_team_alpha":
            actor["allowed_action_keys"].append("survey_resources")
    document["actions"].append(
        {
            "key": "survey_resources",
            "name": "Survey Resources",
            "description": "Survey ordinary inventory and discover eligible hidden stock.",
            "required_interaction_key": "transport_destination",
            "execution_mode": "IMMEDIATE",
            "allowed_actor_capabilities": ["EXECUTE_ACTION"],
            "expected_outcomes": [
                {"code": "SURVEYED", "name": "Resources surveyed", "success": True}
            ],
            "planning": {
                "success_outcome_codes": ["SURVEYED"],
                "hints": [
                    "Use when ordinary inventory is unknown or a full survey is incomplete.",
                    "A survey may discover hidden Facility-bound stock.",
                ],
            },
            "behavior": "SURVEY_RESOURCES",
            "locality": "REGION",
        }
    )
    return ScenarioDefinitionV2.model_validate(document)


def _runtime(
    session: Session,
    definition: ScenarioDefinitionV2,
    key: str,
) -> tuple[object, object]:
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
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
        LINJIANG_INFRASTRUCTURE_RECOVERY_V1,
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
    runtime, scope = _runtime(session, definition, "resource-pool-hidden-before-survey")
    projection = SharedKnowledgeProjection(session, scope, definition)

    visible = projection.visible_resource_pools()
    assert {item.pool_key for item in visible} == {"west_pool_a", "west_pool_b"}
    intelligence = projection.resource_intelligence()
    assert intelligence["regions"]["north_industrial_district"]["resources"] == {}
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
        "restore central hospital emergency power",
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
    assert rows[("central_district", "default")] == 40
    intelligence = SharedKnowledgeProjection(session, scope, definition).resource_intelligence()
    west_summary = intelligence["regions"]["west_logistics_district"]["resources"][
        "electrical_repair_parts"
    ]
    assert west_summary["known_total"] == 15
    assert west_summary["known_available"] == 15
    assert any(pool["quantity"] == 0 for pool in west_summary["pools"])


def test_repair_adjust_resource_aggregates_multiple_visible_available_pools(
    session: Session,
) -> None:
    document: dict[str, Any] = deepcopy(_pool_definition().model_dump(mode="json"))
    document["initialization"]["resource_pools"].extend(
        [
            {
                "pool_key": "central_pool_a",
                "resource_key": "electrical_repair_parts",
                "region_key": "central_district",
                "quantity": 4,
                "visibility": "VISIBLE",
                "availability": "AVAILABLE",
            },
            {
                "pool_key": "central_pool_b",
                "resource_key": "electrical_repair_parts",
                "region_key": "central_district",
                "quantity": 8,
                "visibility": "VISIBLE",
                "availability": "AVAILABLE",
            },
        ]
    )
    definition = ScenarioDefinitionV2.model_validate(document)
    runtime, scope = _runtime(session, definition, "resource-pool-aggregate-repair")

    result = GenericGameService(session, scope).execute(
        actor_key="electrical_team_beta",
        action_key="repair_electrical",
        target_node_key="central_hospital",
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
    assert rows[("central_district", "central_pool_a")] == 0
    assert rows[("central_district", "central_pool_b")] == 2
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "central_hospital", "emergency_power_operational"),
    )
    assert fact is not None and fact.truth_value is True


def test_unknown_unlock_requirement_is_explicitly_safe_in_player_and_planner_projection(
    session: Session,
) -> None:
    definition = _pool_definition()
    runtime, scope = _runtime(session, definition, "resource-pool-unknown-requirement")
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
    assert pool is not None and fact is not None
    pool.visibility = ResourcePoolVisibility.VISIBLE
    fact.visibility = Visibility.HIDDEN
    session.flush()

    projection = SharedKnowledgeProjection(session, scope, definition)
    known_pool = next(
        item for item in projection.visible_resource_pools() if item.pool_key == "north_hidden_pool"
    )
    assert known_pool.availability_requirement is None
    assert known_pool.availability_requirement_status == "UNKNOWN"
    summary = projection.resource_intelligence()["regions"]["north_industrial_district"][
        "resources"
    ]["electrical_repair_parts"]
    pool_summary = summary["pools"][0]
    assert pool_summary["availability_requirement_status"] == "UNKNOWN"
    assert "availability_requirement" not in pool_summary
    planner_pool = projection.planner_resources()["resources"]["electrical_repair_parts"][
        "regions"
    ]["north_industrial_district"]["pools"][0]
    assert planner_pool["availability_requirement_status"] == "UNKNOWN"
    assert "availability_requirement" not in planner_pool


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
    document["actors"]["actor_profiles"][1]["allowed_action_keys"].append(
        "restore_inventory_visibility"
    )
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
    document: dict[str, Any] = deepcopy(LINJIANG_INFRASTRUCTURE_RECOVERY_V1.model_dump(mode="json"))
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
        "restore central hospital emergency power",
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
        request = PlanRequest(
            call_type=call_type,
            goal=task.goal_description,
            objective_scope=objective_context(
                (objective,),
                known_fact_refs=known_refs,
            ),
            planning_context=context,
        )
        assert context.goal["objectives"][0]["planning_guidance"] == (
            "Prefer known reachable Regions and preserve enough parts for repair."
        )
        assert request.objective_scope[0]["planning_guidance"] == (
            "Prefer known reachable Regions and preserve enough parts for repair."
        )
        assert (
            request.provider_payload()["planning_context"]["goal"]["objectives"][0][
                "planning_guidance"
            ]
            == "Prefer known reachable Regions and preserve enough parts for repair."
        )

    assert service.evaluate(task).completed is False
