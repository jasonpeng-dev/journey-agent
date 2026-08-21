from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.dependency_closure import DependencyClosureError, build_dependency_closure
from app.agent.generic import GenericAgentService, _ProjectedFact
from app.agent.planner_contract import (
    action_planner_constraints,
    action_planner_effects,
    declarative_action_effects,
)
from app.agent.planning_context import PlanningContextBuilder
from app.domain.enums import CommandReachability
from app.domain.resources import resource_state_key
from app.domain.runtime_scope import GameInstanceId
from app.domain.world import AccessState, Visibility
from app.engine.locality import transport_between
from app.engine.rules import (
    ActionRuleContext,
    DeclarativeRuleEngine,
    DeclarativeRuleState,
    RuleFactState,
    RuleNodeState,
)
from app.infrastructure.db.models import (
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceRelationKnowledge,
    GameInstanceResourceState,
    Player,
)
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V1
from app.scenarios.linjiang_v1_draft import build_linjiang_v1_definition
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.validation import ScenarioDefinitionValidator
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionService
from app.services.knowledge_projection import SharedKnowledgeProjection
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService


def _rule_state(
    definition: Any,
    *,
    resources: dict[tuple[str, str], int],
    fact_overrides: dict[tuple[str, str], Any] | None = None,
) -> DeclarativeRuleState:
    overrides = fact_overrides or {}
    facts = {
        (node.key, fact.key): RuleFactState(fact.initial_value, Visibility.KNOWN)
        for node in definition.world.nodes
        for fact in node.facts
    }
    for identity, value in overrides.items():
        facts[identity] = RuleFactState(value, Visibility.KNOWN)
    return DeclarativeRuleState(
        nodes={
            node.key: RuleNodeState(Visibility.KNOWN, AccessState.AVAILABLE)
            for node in definition.world.nodes
        },
        facts=facts,
        resources={
            resource_state_key(resource, region): amount
            for (resource, region), amount in resources.items()
        },
        resource_reservations={},
    )


def _v10_runtime(session: Session, key: str):  # type: ignore[no-untyped-def]
    definition = build_linjiang_v1_definition(LINJIANG_INFRASTRUCTURE_RECOVERY_V1)
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


def test_linjiang_v1_draft_is_complete_and_does_not_mutate_v9() -> None:
    definition = build_linjiang_v1_definition(LINJIANG_INFRASTRUCTURE_RECOVERY_V1)

    assert LINJIANG_INFRASTRUCTURE_RECOVERY_V1.metadata.key == "linjiang_infrastructure_recovery"
    assert definition.metadata.key == "linjiang_infrastructure_recovery_v10"
    assert len([node for node in definition.world.nodes if node.node_type_key == "region"]) == 6
    assert len([node for node in definition.world.nodes if node.node_type_key == "facility"]) == 30
    assert len([node for node in definition.world.nodes if node.node_type_key == "transport"]) == 6
    assert len(definition.actors.actor_profiles) == 6
    assert len(definition.objectives) == 4
    assert {item.key for item in definition.world.resources} == {
        "communication_equipment",
        "general_engineering_parts",
        "municipal_repair_materials",
        "electrical_repair_parts",
        "water_system_parts",
    }

    relations = {
        (item.source_node_key, item.relation_type_key, item.target_node_key): item
        for item in definition.world.relations
    }
    for relation in (
        ("southeast_emergency_power_station", "supplies_power_to", "east_distribution_station"),
        ("east_distribution_station", "supplies_power_to", "east_community_hospital"),
        ("east_distribution_station", "supplies_power_to", "riverside_shelter"),
    ):
        assert relations[relation].initial_visibility.value == "VISIBLE"

    nodes = {node.key: node for node in definition.world.nodes}
    assert nodes["central_river_tunnel"].fact("passable").initial_visibility.value == "HIDDEN"
    assert nodes["north_service_corridor"].fact("passable").initial_visibility.value == "HIDDEN"
    assert nodes["west_freight_corridor"].fact("passable").initial_value is True
    assert (
        nodes["southeast_emergency_power_station"].fact("power_generation_capable").initial_value
        is True
    )
    assert nodes["south_communication_core"].fact("operational").initial_visibility.value == "KNOWN"

    pools = {pool.pool_key: pool for pool in definition.initialization.resource_pools}
    assert pools["southeast_electrical_stock"].visibility.value == "VISIBLE"
    assert pools["southeast_electrical_stock"].availability.value == "AVAILABLE"
    assert pools["southeast_district_service_stock"].visibility.value == "HIDDEN"
    assert pools["north_heavy_equipment_stock"].survey_discoverable is True
    assert pools["north_service_depot_stock"].availability_requirement is not None


def test_linjiang_v1_draft_passes_scenario_readiness_validation() -> None:
    definition = build_linjiang_v1_definition(LINJIANG_INFRASTRUCTURE_RECOVERY_V1)

    result = ScenarioDefinitionValidator().validate(definition.model_dump(mode="json"))

    assert result.passed
    assert result.issues == ()
    assert all(objective.planning_guidance for objective in definition.objectives)


def test_linjiang_v10_planning_context_uses_sparse_target_requirements(
    session: Session,
) -> None:
    runtime, scope = _v10_runtime(session, "linjiang-v10-sparse-planning-context")
    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "\u6062\u590d\u4e2d\u592e\u901a\u4fe1\u80fd\u529b",
        initialize_plan=False,
    )
    definition = agent._definition()
    context = PlanningContextBuilder(session, scope).build(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason=None,
    )
    requirements = {
        item["target_key"]: {entry["action_key"]: entry for entry in item["requirements"]}
        for item in context.current_knowledge["known_action_requirements"]
    }

    assert requirements["central_telecom_hub"]["repair_communications"] == {
        "action_key": "repair_communications",
        "required_actor_role_key": "communications_repair_team",
        "cost": {
            "communication_equipment": 10,
            "general_engineering_parts": 15,
        },
    }
    water_repair = requirements["water_treatment_plant"]["repair_water_facility"]
    assert water_repair["cost"] == {
        "water_system_parts": 15,
        "general_engineering_parts": 5,
        "municipal_repair_materials": 10,
    }
    assert water_repair["special_requirements"] == [
        {
            "node_key": "water_treatment_plant",
            "fact_key": "heavy_engineering_support_ready",
            "operator": "EQ",
            "value": True,
        }
    ]
    assert (
        "water_treatment_plant.heavy_engineering_support_ready"
        in context.current_knowledge["facts"]
    )
    assert all("known_requirements" not in action for action in context.relevant_actions)
    assert task.current_plan_version == 0


def test_linjiang_v10_planner_action_contract_is_generic_and_knowledge_safe(
    session: Session,
) -> None:
    runtime, scope = _v10_runtime(session, "linjiang-v10-planner-action-contract")
    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "\u6062\u590d\u4e2d\u592e\u901a\u4fe1\u80fd\u529b",
        initialize_plan=False,
    )
    definition = agent._definition()
    context = PlanningContextBuilder(session, scope).build(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason=None,
    )
    actions = {item["action_key"]: item for item in context.relevant_actions}
    definition_actions = {item.key: item for item in definition.actions}

    assert set(definition_actions) == {
        "activate_emergency_water_transfer",
        "clear_transport",
        "deploy_heavy_engineering_support",
        "inspect",
        "relay_message",
        "repair_communications",
        "repair_electrical",
        "repair_industrial_facility",
        "repair_water_facility",
        "supply_power",
        "survey_resources",
        "transport_resource",
        "travel",
    }

    actors = {item["actor_key"]: item for item in context.relevant_actors}
    assert actors["logistics_team_alpha"]["execution_state"] == {
        "status": "EXECUTABLE",
        "known_blockers": [],
    }
    communications = actors["communications_repair_team_alpha"]
    assert "repair_communications" in communications["allowed_action_keys"]
    assert communications["execution_state"]["status"] == "KNOWN_BLOCKED"
    assert communications["execution_state"]["known_blockers"][0]["type"] == (
        "COMMAND_REACHABILITY"
    )
    assert communications["execution_state"]["known_blockers"][0]["required_value"] == "ONLINE"

    relay = actions["relay_message"]
    assert relay["planner_constraints"]["executor"]["command_reachability"] == "ONLINE"
    assert relay["planner_constraints"]["target"]["command_reachability"] == "DISCONNECTED"
    assert any(
        effect["type"] == "ACTOR_COMMAND_REACHABILITY" and effect["value"] == "ONLINE"
        for effect in relay["planner_effects"]
    )

    travel = actions["travel"]
    assert travel["planner_constraints"]["locality"] == {
        "type": "ONE_HOP_TRANSPORT",
        "source": "PROJECTED_ACTOR_REGION",
        "destination": "TARGET_REGION",
    }
    assert any(
        effect["type"] == "ACTOR_LOCATION" and effect["value"] == "target_key"
        for effect in travel["planner_effects"]
    )
    assert any(
        item["type"] == "KNOWN_BLOCKED_ROUTE"
        and item["known_false"] == "BLOCKS_EXECUTION"
        and item["unknown"] == "MAY_ATTEMPT"
        for item in travel["planner_constraints"]["knowledge"]
    )

    transport = actions["transport_resource"]
    assert {item["key"] for item in transport["parameter_schema"]} == {
        "resource_key",
        "amount",
    }
    assert transport["planner_constraints"]["locality"]["source"] == ("PROJECTED_ACTOR_REGION")
    assert transport["planner_constraints"]["locality"]["destination"] == "TARGET_REGION"
    assert any(
        item["type"] == "SOURCE_INVENTORY"
        and item["required"] == "KNOWN_VISIBLE_AVAILABLE"
        and item["unknown"] == "CANNOT_INTENTIONALLY_TRANSPORT"
        for item in transport["planner_constraints"]["knowledge"]
    )
    assert any(
        effect["type"] == "RESOURCE_TRANSFER" and effect["destination"] == "target_key"
        for effect in transport["planner_effects"]
    )

    survey = actions["survey_resources"]
    assert {effect["type"] for effect in survey["planner_effects"]} >= {
        "REGION_RESOURCE_KNOWLEDGE",
        "RESOURCE_SURVEY_COMPLETED",
        "RESOURCE_POOL_KNOWLEDGE",
        "NO_TRUTH_CREATION",
    }
    clear = actions["clear_transport"]
    assert any(
        effect["type"] == "FACT_MUTATION"
        and effect["fact_key"] == "passable"
        and effect["value"] is True
        for effect in clear["planner_effects"]
    )

    water = actions["repair_water_facility"]
    water_target = water["target_contracts"]["water_treatment_plant"]
    known_requirements = {
        item["target_key"]: {entry["action_key"]: entry for entry in item["requirements"]}
        for item in context.current_knowledge["known_action_requirements"]
    }
    assert any(
        requirement.get("fact_key") == "heavy_engineering_support_ready"
        for requirement in known_requirements["water_treatment_plant"]["repair_water_facility"][
            "special_requirements"
        ]
    )
    assert any(
        effect["type"] == "FACT_MUTATION" and effect["fact_key"] == "operational"
        for effect in water_target["effects"]
    )
    assert "power_supply" not in json.dumps(water_target, ensure_ascii=False)

    supply = actions["supply_power"]
    assert any(
        item["type"] == "DIRECT_RELATION" and item["required"] == "KNOWN_VISIBLE"
        for item in supply["planner_constraints"]["knowledge"]
    )
    assert any(
        effect["type"] == "FACT_MUTATION" and effect["fact_key"] == "power_supply"
        for effect in supply["planner_effects"]
    )
    assert any(
        effect["type"] == "NO_IMPLIED_FACT_MUTATION" and effect["fact_key"] == "operational"
        for effect in supply["planner_effects"]
    )
    assert {item["fact_key"] for item in supply["planner_constraints"]["known_preconditions"]} >= {
        "operational",
        "power_generation_capable",
        "power_supply",
    }

    deploy = actions["deploy_heavy_engineering_support"]
    assert any(
        effect["type"] == "FACT_MUTATION"
        and effect["fact_key"] == "heavy_engineering_support_ready"
        for effect in deploy["planner_effects"]
    )
    assert any(
        item["type"] == "HEAVY_SUPPORT_AVAILABILITY" and item["required"] == "KNOWN_AVAILABLE"
        for item in deploy["planner_constraints"]["knowledge"]
    )

    serialized = json.dumps(
        context.compact_dump(), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert len(serialized) < 65_000, len(serialized)
    assert "north_heavy_equipment_stock" not in json.dumps(
        context.compact_dump(), ensure_ascii=False
    )
    assert "communication_equipment" not in context.current_knowledge["resources"]

    for action in definition_actions.values():
        constraints = action_planner_constraints(action)
        effects = action_planner_effects(action)
        assert "executor" in constraints
        assert isinstance(effects, list)
    activation_effects = declarative_action_effects(
        definition,
        definition_actions["activate_emergency_water_transfer"],
        known_node_keys={item.key for item in definition.world.nodes},
        known_facts={
            tuple(identity.split(".", 1)): value
            for identity, value in context.current_knowledge["facts"].items()
            if isinstance(identity, str) and "." in identity and isinstance(value, (str, int, bool))
        },
    )
    assert any(
        effect["type"] == "FACT_MUTATION" and effect["fact_key"] == "east_emergency_water_supply"
        for effect in activation_effects
    )


def test_planner_sparse_requirements_do_not_reveal_hidden_target_fact(
    session: Session,
) -> None:
    _runtime_value, scope = _v10_runtime(session, "linjiang-v10-hidden-target-requirement")
    definition = GenericAgentService(session, scope)._definition()
    fact = session.get(
        GameInstanceFactState,
        (scope.game_instance_id, "water_treatment_plant", "repair_profile"),
    )
    assert fact is not None
    fact.visibility = Visibility.HIDDEN
    session.flush()

    sparse = SharedKnowledgeProjection(session, scope, definition).planner_action_requirements()
    assert all(item["target_key"] != "water_treatment_plant" for item in sparse)


def test_linjiang_v10_provider_input_is_canonical_v2_and_knowledge_safe(
    session: Session,
) -> None:
    runtime, scope = _v10_runtime(session, "linjiang-v10-planner-input-v2")
    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "\u6062\u590d\u4e2d\u592e\u901a\u4fe1\u80fd\u529b",
        initialize_plan=False,
    )
    definition = agent._definition()
    closure = PlanningContextBuilder(session, scope).build_v2_closure(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason=None,
    )
    planner_input = closure.planner_input
    payload = planner_input.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["schema_version"] == 2
    assert payload["objective"]["objective_scope"] == [
        "restore_central_communication_capability"
    ]
    assert payload["objective"]["completion_requirements"][0]["node_key"] == (
        "central_telecom_hub"
    )
    actors = {item["actor_key"]: item for item in payload["actors"]}
    assert actors["logistics_team_alpha"]["current_region"] == "central_district"
    assert actors["logistics_team_alpha"]["command_reachability"] == "ONLINE"
    communications = actors["communications_repair_team_alpha"]
    assert communications["current_region"] == "east_residential_district"
    assert communications["command_reachability"] == "DISCONNECTED"
    assert communications["execution_state"]["status"] == "KNOWN_BLOCKED"
    assert "north_heavy_equipment_stock" not in serialized
    action_keys = {item["action_key"] for item in payload["action_contracts"]}
    assert "transport_resource" not in action_keys, closure.relevance_reason.get(
        "transport_resource"
    )
    assert action_keys == {
        "repair_communications",
        "relay_message",
        "survey_resources",
        "travel",
    }, closure.relevance_reason
    assert any(
        item.get("dimension") == "TRANSPORT_PASSABILITY"
        and item.get("status") == "UNKNOWN"
        and item.get("attempt_policy") == "MAY_ATTEMPT"
        for item in payload["known_world"]["unknown_dependencies"]
    )
    assert {item["actor_key"] for item in payload["actors"]} == {
        "communications_repair_team_alpha",
        "logistics_team_alpha",
    }
    assert "repair_communications" in closure.relevance_reason
    assert "relevance_reason" not in serialized
    assert len(serialized.encode("utf-8")) < 30_000
    for duplicate in (
        "planner_constraints",
        "planner_effects",
        "target_requirements",
        "known_action_requirements",
        "declared_world_effects",
        "declared_knowledge_effects",
        "target_contracts",
    ):
        assert duplicate not in serialized

    with pytest.raises(DependencyClosureError, match="dependency closure bound"):
        build_dependency_closure(
            definition,
            agent._objectives(task, definition),
            planner_input,
            dependency_limit=0,
        )

    route = session.get(
        GameInstanceFactState,
        (scope.game_instance_id, "central_river_tunnel", "passable"),
    )
    assert route is not None
    route.visibility = Visibility.KNOWN
    route.truth_value = False
    session.flush()
    replanned = PlanningContextBuilder(session, scope).build_v2_closure(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason="TRAVEL_BLOCKED",
    )
    assert "clear_transport" in {
        item.action_key for item in replanned.planner_input.action_contracts
    }


def test_linjiang_v10_all_regions_have_the_generic_travel_target_contract() -> None:
    definition = build_linjiang_v1_definition(LINJIANG_INFRASTRUCTURE_RECOVERY_V1)
    regions = {node.key: node for node in definition.world.nodes if node.node_type_key == "region"}

    assert set(regions) == {
        "central_district",
        "north_industrial_district",
        "west_logistics_district",
        "east_residential_district",
        "south_waterfront_district",
        "southeast_heights_district",
    }
    assert all("travel_destination" in node.interaction_keys for node in regions.values())
    assert all("surveyable" in node.interaction_keys for node in regions.values())


def test_linjiang_v10_central_east_hidden_route_is_accepted_by_validator(
    session: Session,
) -> None:
    runtime, scope = _v10_runtime(session, "linjiang-v10-central-east-validator")
    agent = GenericAgentService(session, scope)
    definition = agent._definition()
    actors = {
        actor.actor_key: actor
        for actor in session.scalars(
            select(GameInstanceActor).where(
                GameInstanceActor.game_instance_id == runtime.instance.id
            )
        )
    }
    action = next(item for item in definition.actions if item.key == "travel")
    projected_locations = {actor_key: actor.current_node_key for actor_key, actor in actors.items()}
    projected_reachability = {
        actor_key: CommandReachability(actor.command_reachability)
        for actor_key, actor in actors.items()
    }
    projected_passability = agent._known_passability(definition)
    projected_facts = agent._known_fact_projection()
    projected_nodes = agent._known_node_keys()
    projected_relations = agent._known_relation_keys(definition)

    assert "central_river_tunnel" not in projected_passability
    assert agent._validate_planning_action(
        definition,
        action,
        actors["logistics_team_alpha"],
        "east_residential_district",
    )
    agent._validate_projected_command_reachability(
        action,
        "logistics_team_alpha",
        "east_residential_district",
        actors,
        projected_reachability,
    )
    connector = agent._validate_projected_action_state(
        definition,
        action,
        "logistics_team_alpha",
        "east_residential_district",
        {},
        projected_locations,
        projected_passability,
        projected_facts,
        projected_nodes,
        projected_relations,
        actors=actors,
        projected_command_reachability=projected_reachability,
    )
    assert connector == "central_river_tunnel"


def test_linjiang_v10_hidden_block_is_discovered_only_during_runtime(
    session: Session,
) -> None:
    runtime, scope = _v10_runtime(session, "linjiang-v10-central-east-runtime")
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "central_river_tunnel", "passable"),
    )
    assert fact is not None and fact.visibility == Visibility.HIDDEN

    result = GenericActionService(session, scope).execute_action(
        actor_key="logistics_team_alpha",
        action_key="travel",
        target_key="east_residential_district",
        parameters={},
        idempotency_key="linjiang-v10-central-east-blocked",
    )

    assert result.applied is not None
    assert result.applied.outcome.failure is not None
    assert result.applied.outcome.failure.code == "TRAVEL_BLOCKED"
    assert fact.visibility == Visibility.KNOWN
    assert fact.truth_value is False
    assert any(
        item.key == "central_river_tunnel.passable" for item in result.applied.knowledge_changes
    )


def test_linjiang_v10_relay_recovery_path_is_sequentially_validated(
    session: Session,
) -> None:
    runtime, scope = _v10_runtime(session, "linjiang-v10-relay-path")
    agent = GenericAgentService(session, scope)
    definition = agent._definition()
    actors = {
        actor.actor_key: actor
        for actor in session.scalars(
            select(GameInstanceActor).where(
                GameInstanceActor.game_instance_id == runtime.instance.id
            )
        )
    }
    actions = {item.key: item for item in definition.actions}
    projected_locations = {actor_key: actor.current_node_key for actor_key, actor in actors.items()}
    projected_reachability = {
        actor_key: CommandReachability(actor.command_reachability)
        for actor_key, actor in actors.items()
    }
    projected_passability = agent._known_passability(definition)
    projected_facts = agent._known_fact_projection()
    projected_nodes = agent._known_node_keys()
    projected_relations = agent._known_relation_keys(definition)
    path = (
        ("travel", "logistics_team_alpha", "east_residential_district"),
        ("relay_message", "logistics_team_alpha", "communications_repair_team_alpha"),
        ("travel", "communications_repair_team_alpha", "central_district"),
        ("repair_communications", "communications_repair_team_alpha", "central_telecom_hub"),
    )

    for action_key, actor_key, target_key in path:
        action = actions[action_key]
        assert agent._validate_planning_action(definition, action, actors[actor_key], target_key)
        agent._validate_projected_command_reachability(
            action,
            actor_key,
            target_key,
            actors,
            projected_reachability,
        )
        agent._validate_projected_action_state(
            definition,
            action,
            actor_key,
            target_key,
            {},
            projected_locations,
            projected_passability,
            projected_facts,
            projected_nodes,
            projected_relations,
            actors=actors,
            projected_command_reachability=projected_reachability,
        )
        agent._advance_projected_action_state(
            definition,
            action,
            actor_key,
            target_key,
            {},
            projected_locations,
            projected_passability,
            projected_facts,
            projected_nodes,
            projected_relations,
            projected_command_reachability=projected_reachability,
        )

    assert projected_locations["logistics_team_alpha"] == "east_residential_district"
    assert projected_locations["communications_repair_team_alpha"] == "central_district"
    assert projected_reachability["communications_repair_team_alpha"] == CommandReachability.ONLINE


def test_linjiang_v10_known_route_remains_one_hop_after_hidden_route_failure() -> None:
    definition = build_linjiang_v1_definition(LINJIANG_INFRASTRUCTURE_RECOVERY_V1)
    travel = next(item for item in definition.actions if item.key == "travel")
    projected_locations = {"logistics_team_alpha": "central_district"}
    route = (
        ("west_logistics_district", "west_freight_corridor"),
        ("south_waterfront_district", "south_bridge"),
        ("east_residential_district", "waterfront_access_corridor"),
    )

    for target_region, expected_connector in route:
        connector = GenericAgentService._validate_projected_plan_locality(
            definition,
            travel,
            "logistics_team_alpha",
            target_region,
            {},
            projected_locations,
        )
        assert connector == expected_connector
        assert connector == transport_between(
            definition,
            projected_locations["logistics_team_alpha"],
            target_region,
        )
        projected_locations["logistics_team_alpha"] = target_region


def test_linjiang_v1_task_three_repairs_and_task_four_support_gate() -> None:
    definition = build_linjiang_v1_definition(LINJIANG_INFRASTRUCTURE_RECOVERY_V1)
    action = next(item for item in definition.actions if item.key == "repair_industrial_facility")
    assert action.required_actor_role_key == "industrial_repair_team"
    assert {item.node_key for item in action.planning.terminal_effects} >= {
        "east_community_hospital",
        "riverside_shelter",
    }

    engine = DeclarativeRuleEngine(definition)
    repair_resources = {
        ("general_engineering_parts", "east_residential_district"): 5,
        ("municipal_repair_materials", "east_residential_district"): 10,
    }
    for target_key in ("east_community_hospital", "riverside_shelter"):
        outcome = engine.evaluate(
            _rule_state(definition, resources=repair_resources),
            ActionRuleContext(
                action_key="repair_industrial_facility",
                target_node_key=target_key,
                parameters={},
                actor_key="industrial_repair_team_alpha",
                actor_current_node_key="east_residential_district",
            ),
        )
        assert outcome.failure is None
        assert outcome.outcome_code == "INDUSTRIAL_REPAIRED"
        assert (target_key, "operational", True) in {
            (item.node_key, item.fact_key, item.value) for item in outcome.fact_updates
        }
        assert not any(item.fact_key == "power_supply" for item in outcome.fact_updates)
        assert {(item.resource_key, item.amount) for item in outcome.resource_mutations} == {
            ("general_engineering_parts", -5),
            ("municipal_repair_materials", -10),
        }

    projected_facts = {
        (node.key, fact.key): _ProjectedFact(fact.initial_value, Visibility.KNOWN)
        for node in definition.world.nodes
        for fact in node.facts
    }
    water_action = next(item for item in definition.actions if item.key == "repair_water_facility")
    known_failure = GenericAgentService._known_preflight_failure(
        definition,
        water_action,
        "water_treatment_plant",
        {},
        projected_facts,
        {node.key for node in definition.world.nodes},
        set(),
    )
    assert known_failure == "HEAVY_ENGINEERING_SUPPORT_REQUIRED"

    water_resources = {
        ("water_system_parts", "south_waterfront_district"): 15,
        ("general_engineering_parts", "south_waterfront_district"): 5,
        ("municipal_repair_materials", "south_waterfront_district"): 10,
    }
    blocked = engine.evaluate_preflight(
        _rule_state(definition, resources=water_resources),
        ActionRuleContext(
            action_key="repair_water_facility",
            target_node_key="water_treatment_plant",
            parameters={},
            actor_key="water_repair_team_alpha",
            actor_current_node_key="south_waterfront_district",
        ),
    )
    assert blocked is not None and blocked.failure is not None
    assert blocked.failure.code == "HEAVY_ENGINEERING_SUPPORT_REQUIRED"

    supported = engine.evaluate(
        _rule_state(
            definition,
            resources=water_resources,
            fact_overrides={
                ("water_treatment_plant", "heavy_engineering_support_ready"): True,
            },
        ),
        ActionRuleContext(
            action_key="repair_water_facility",
            target_node_key="water_treatment_plant",
            parameters={},
            actor_key="water_repair_team_alpha",
            actor_current_node_key="south_waterfront_district",
        ),
    )
    assert supported.failure is None
    assert ("water_treatment_plant", "operational", True) in {
        (item.node_key, item.fact_key, item.value) for item in supported.fact_updates
    }

    deployed = engine.evaluate(
        _rule_state(
            definition,
            resources={},
            fact_overrides={
                ("heavy_equipment_yard", "heavy_engineering_support"): "AVAILABLE",
            },
        ),
        ActionRuleContext(
            action_key="deploy_heavy_engineering_support",
            target_node_key="water_treatment_plant",
            parameters={},
            actor_key="industrial_repair_team_alpha",
            actor_current_node_key="north_industrial_district",
        ),
    )
    assert deployed.failure is None
    assert ("water_treatment_plant", "heavy_engineering_support_ready", True) in {
        (item.node_key, item.fact_key, item.value) for item in deployed.fact_updates
    }

    activate_rule = next(
        item for item in definition.rules if item.key == "activate_water_requirements"
    )
    assert "heavy_engineering_support_ready" not in str(activate_rule.model_dump())

    activation_requirements = {
        ("water_treatment_plant", "operational"): True,
        ("water_treatment_plant", "power_supply"): "AVAILABLE",
        ("south_pump_station", "operational"): True,
        ("south_pump_station", "power_supply"): "AVAILABLE",
        ("east_water_pump_station", "operational"): True,
        ("east_water_pump_station", "power_supply"): "AVAILABLE",
        ("south_communication_core", "operational"): True,
    }
    activation = engine.evaluate(
        _rule_state(
            definition,
            resources={},
            fact_overrides=activation_requirements,
        ),
        ActionRuleContext(
            action_key="activate_emergency_water_transfer",
            target_node_key="east_water_pump_station",
            parameters={},
            actor_key="water_repair_team_alpha",
            actor_current_node_key="east_residential_district",
        ),
    )
    assert activation.failure is None
    assert activation.outcome_code == "WATER_TRANSFER_ACTIVE"


def test_linjiang_v1_runtime_initializes_knowledge_and_reachability(session: Session) -> None:
    definition = build_linjiang_v1_definition(LINJIANG_INFRASTRUCTURE_RECOVERY_V1)
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name="linjiang-v1-draft-runtime")
    session.add(player)
    session.flush()

    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="linjiang-v1-draft-runtime",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))

    actors = {
        actor.actor_key: actor
        for actor in session.scalars(
            select(GameInstanceActor).where(
                GameInstanceActor.game_instance_id == runtime.instance.id
            )
        )
    }
    assert actors["logistics_team_alpha"].command_reachability == CommandReachability.ONLINE.value
    assert all(
        actor.command_reachability == CommandReachability.DISCONNECTED.value
        for key, actor in actors.items()
        if key != "logistics_team_alpha"
    )
    assert scope.game_instance_id == runtime.instance.id

    region_knowledge = {
        row.region_key: row
        for row in session.scalars(
            select(GameInstanceRegionResourceKnowledge).where(
                GameInstanceRegionResourceKnowledge.game_instance_id == runtime.instance.id
            )
        )
    }
    assert region_knowledge["central_district"].resource_inventory_visibility == "VISIBLE"
    assert region_knowledge["southeast_heights_district"].resource_inventory_visibility == "HIDDEN"
    assert region_knowledge["southeast_heights_district"].resource_survey_completed is False

    relation_knowledge = {
        row.relation_key: row
        for row in session.scalars(
            select(GameInstanceRelationKnowledge).where(
                GameInstanceRelationKnowledge.game_instance_id == runtime.instance.id
            )
        )
    }
    power_relation_key = (
        "southeast_emergency_power_station__supplies_power_to__east_distribution_station"
    )
    assert relation_knowledge[power_relation_key].visibility == "VISIBLE"

    pools = {
        row.pool_key: row
        for row in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == runtime.instance.id
            )
        )
    }
    assert pools["southeast_electrical_stock"].value == 20
    assert pools["southeast_electrical_stock"].visibility == "VISIBLE"
    assert pools["southeast_district_service_stock"].visibility == "HIDDEN"
