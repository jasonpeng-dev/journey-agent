from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.dependency_closure import (
    DependencyClosureError,
    _has_known_available_resource_at,
    build_dependency_closure,
)
from app.agent.generic import (
    GenericAgentError,
    GenericAgentService,
    GenericGoalResolution,
    _ProjectedFact,
)
from app.agent.planner_contract import (
    action_planner_constraints,
    action_planner_effects,
    declarative_action_effects,
)
from app.agent.planning_context import PlanningContextBuilder, _canonical_planner_input
from app.agent.provider import (
    PlannerActionContract,
    PlannerActorState,
    PlannerInput,
    PlannerKnownWorldSlice,
    PlannerTargetBinding,
    PlanProposal,
    PlanRequest,
    PlanStepProposal,
)
from app.domain.enums import (
    CommandReachability,
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.resources import resource_state_key
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ObjectiveDefinitionV2, ObjectiveRequirementV2
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
from app.scenarios.builtin import (
    LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    require_builtin_v2_version,
)
from app.scenarios.linjiang_v1_draft import build_linjiang_v1_definition
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.validation import ScenarioDefinitionValidator
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionService
from app.services.knowledge_projection import SharedKnowledgeProjection
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService
from tests.scenario_fixtures import LINJIANG_V1_TEST


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


def test_dependency_closure_uses_known_available_not_total_at_scope() -> None:
    resource = {
        "scopes": {
            "central_district": {
                "value": 100,
                "known_total": 100,
                "known_available": 5,
            }
        }
    }

    assert _has_known_available_resource_at(resource, "central_district", 5)
    assert not _has_known_available_resource_at(resource, "central_district", 6)


def _v2_0_runtime(session: Session, key: str):  # type: ignore[no-untyped-def]
    definition = build_linjiang_v1_definition(LINJIANG_V1_TEST)
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


def _linjiang_v4_runtime(session: Session, key: str):  # type: ignore[no-untyped-def]
    version = require_builtin_v2_version(session, LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0)
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


class _RepeatingPlanProvider:
    model_name = "typed-diagnostic-test-provider"

    def __init__(self, steps: tuple[PlanStepProposal, ...]) -> None:
        self.steps = steps
        self.requests: list[PlanRequest] = []

    def select_objectives(self, request: object) -> object:
        raise AssertionError(f"exact objective should not call goal selection: {request}")

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.requests.append(request)
        return PlanProposal(plan_summary=request.call_type, steps=self.steps)


class _InspectBoundaryProvider:
    model_name = "inspect-boundary-test-provider"

    def select_objectives(self, request: object) -> object:
        raise AssertionError(f"exact objective should not call goal selection: {request}")

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        planner_input = request.planner_input
        dependency = next(
            item
            for item in planner_input.known_world.unknown_dependencies
            if item.get("dimension") == "OBJECTIVE_FACT_KNOWLEDGE"
        )
        assert any(item.action_key == "inspect" for item in planner_input.action_contracts)
        assert any(item.actor_key == "logistics_team_alpha" for item in planner_input.actors)
        assert any(
            item.action_key == "inspect" and item.target_key == "central_telecom_hub"
            for item in planner_input.target_bindings
        )
        return PlanProposal(
            plan_summary="Inspect the unknown communication hub state.",
            stop_reason="INFORMATION_BOUNDARY",
            boundary_dependency_id=str(dependency["dependency_id"]),
            steps=(
                PlanStepProposal(
                    purpose="Inspect the central communication hub.",
                    action_key="inspect",
                    actor_key="logistics_team_alpha",
                    target_key="central_telecom_hub",
                ),
            ),
        )


def test_linjiang_v1_draft_is_complete_and_does_not_mutate_v9() -> None:
    definition = build_linjiang_v1_definition(LINJIANG_V1_TEST)

    assert LINJIANG_V1_TEST.metadata.key == "linjiang_infrastructure_recovery"
    assert definition.metadata.key == "linjiang_infrastructure_recovery_v2_0"
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
    definition = build_linjiang_v1_definition(LINJIANG_V1_TEST)

    result = ScenarioDefinitionValidator().validate(definition.model_dump(mode="json"))

    assert result.passed
    assert result.issues == ()
    assert all(objective.planning_guidance for objective in definition.objectives)


def test_linjiang_v2_0_planning_context_uses_sparse_target_requirements(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-sparse-planning-context")
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


def test_linjiang_v2_0_planner_action_contract_is_generic_and_knowledge_safe(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-planner-action-contract")
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
    communication = context.current_knowledge["resources"]["communication_equipment"]
    assert communication["known_total"] == 0
    assert communication["known_available"] == 0
    assert communication["scopes"]["central_district"]["known_total"] == 0
    assert communication["scopes"]["central_district"]["known_available"] == 0

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
    _runtime_value, scope = _v2_0_runtime(session, "linjiang-v2_0-hidden-target-requirement")
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


def test_linjiang_v2_0_provider_input_is_canonical_v2_and_knowledge_safe(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-planner-input-v2")
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
    assert payload["objective"]["objective_scope"] == ["restore_central_communication_capability"]
    assert payload["objective"]["completion_requirements"][0]["node_key"] == ("central_telecom_hub")
    actors = {item["actor_key"]: item for item in payload["actors"]}
    assert all(
        set(actor)
        == {
            "actor_key",
            "role_key",
            "capabilities",
            "allowed_action_keys",
            "availability",
            "current_region",
            "command_reachability",
            "execution_state",
        }
        for actor in actors.values()
    )
    assert actors["logistics_team_alpha"]["current_region"] == "central_district"
    assert actors["logistics_team_alpha"]["command_reachability"] == "ONLINE"
    communications = actors["communications_repair_team_alpha"]
    assert communications["current_region"] == "east_residential_district"
    assert communications["command_reachability"] == "DISCONNECTED"
    assert communications["execution_state"]["status"] == "KNOWN_BLOCKED"
    repair_contract = next(
        item
        for item in payload["action_contracts"]
        if item["action_key"] == "repair_communications"
    )
    assert set(repair_contract["executor_requirements"]["required_capabilities"]).issubset(
        communications["capabilities"]
    )
    assert "repair_communications" in communications["allowed_action_keys"]
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
    assert all(
        set(actor["allowed_action_keys"]).issubset(action_keys) for actor in payload["actors"]
    )
    assert actors["logistics_team_alpha"]["allowed_action_keys"] == [
        "relay_message",
        "survey_resources",
        "travel",
    ]
    runtime_logistics = session.get(
        GameInstanceActor,
        (scope.game_instance_id, "logistics_team_alpha"),
    )
    assert runtime_logistics is not None
    assert "inspect" in runtime_logistics.allowed_action_keys
    assert "transport_resource" in runtime_logistics.allowed_action_keys
    canonical_actors = payload["actors"]
    for call_type in ("INITIAL_PLAN", "REPLAN", "REPAIR"):
        request_payload = PlanRequest(
            call_type=call_type,
            planner_input=planner_input,
            repair_attempt=1 if call_type == "REPAIR" else 0,
        ).provider_payload()
        assert request_payload["planner_input"]["actors"] == canonical_actors
    assert any(
        item.get("dimension") == "TRANSPORT_PASSABILITY"
        and item.get("status") == "UNKNOWN"
        and item.get("attempt_policy") == "MAY_ATTEMPT"
        for item in payload["known_world"]["unknown_dependencies"]
    )
    dependencies = payload["known_world"]["unknown_dependencies"]
    assert all(item.get("dependency_id") for item in dependencies)
    assert len({item["dependency_id"] for item in dependencies}) == len(dependencies)
    resource_dependencies = {
        item["resource_key"]: item
        for item in dependencies
        if item.get("dimension") == "RESOURCE_SOURCE"
    }
    communication_dependency = resource_dependencies["communication_equipment"]
    assert (
        communication_dependency["required_amount"],
        communication_dependency["known_available_amount"],
        communication_dependency["deficit"],
    ) == (10, 0, 10)
    assert communication_dependency["source_knowledge_status"] == "UNKNOWN"
    assert resource_dependencies["general_engineering_parts"]["required_amount"] == 15
    assert (
        resource_dependencies["general_engineering_parts"]["source_knowledge_status"] == "UNKNOWN"
    )
    assert all(item.get("status") == "UNKNOWN" for item in dependencies)
    resource_knowledge = {
        item["region_key"]: item for item in payload["known_world"]["resource_knowledge"]
    }
    assert resource_knowledge["central_district"] == {
        "region_key": "central_district",
        "resource_inventory_visibility": "VISIBLE",
        "resource_survey_completed": True,
    }
    assert resource_knowledge["southeast_heights_district"]["resource_survey_completed"] is False
    assert len(resource_knowledge) == 6
    repeated = (
        PlanningContextBuilder(session, scope)
        .build_v2_closure(
            definition,
            agent._objectives(task, definition),
            task=task,
            replan_reason=None,
        )
        .planner_input.model_dump(mode="json")
    )
    assert [item["dependency_id"] for item in dependencies] == [
        item["dependency_id"] for item in repeated["known_world"]["unknown_dependencies"]
    ]
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
    replanned_action_keys = {item.action_key for item in replanned.planner_input.action_contracts}
    assert all(
        set(actor.allowed_action_keys).issubset(replanned_action_keys)
        for actor in replanned.planner_input.actors
    )


def test_linjiang_v4_unknown_objective_fact_keeps_inspect_dependency_closure(
    session: Session,
) -> None:
    runtime, scope = _linjiang_v4_runtime(session, "linjiang-v4-unknown-fact-closure")
    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "restore central communication capability",
        resolved_goal=GenericGoalResolution(
            "RESOLVED",
            "restore_central_communication_capability",
            ("restore_central_communication_capability",),
        ),
        initialize_plan=False,
    )
    definition = agent._definition()
    planner_input = (
        PlanningContextBuilder(session, scope)
        .build_v2_closure(
            definition,
            agent._objectives(task, definition),
            task=task,
            replan_reason=None,
        )
        .planner_input
    )

    action_keys = {item.action_key for item in planner_input.action_contracts}
    actor_keys = {item.actor_key for item in planner_input.actors}
    bindings = {(item.action_key, item.target_key) for item in planner_input.target_bindings}
    dependency = next(
        item
        for item in planner_input.known_world.unknown_dependencies
        if item.get("dimension") == "OBJECTIVE_FACT_KNOWLEDGE"
    )

    assert "central_telecom_hub.operational" not in planner_input.known_world.facts
    assert "inspect" in action_keys
    assert "logistics_team_alpha" in actor_keys
    assert ("inspect", "central_telecom_hub") in bindings
    assert dependency["subject_key"] == "central_telecom_hub"
    assert dependency["fact_key"] == "operational"
    assert dependency["status"] == "UNKNOWN"
    assert dependency["blocks"] == "OBJECTIVE_PROGRESSION"
    assert dependency["resolvable_by_effect_types"] == ["KNOWLEDGE_REVEAL"]
    logistics = next(
        item for item in planner_input.actors if item.actor_key == "logistics_team_alpha"
    )
    assert "inspect" in logistics.allowed_action_keys
    payload = PlanRequest(call_type="INITIAL_PLAN", planner_input=planner_input).provider_payload()
    assert payload["planner_input"]["action_contracts"]
    assert payload["planner_input"]["actors"]
    assert payload["planner_input"]["target_bindings"]


def test_dependency_closure_expands_each_new_binding_after_action_selection() -> None:
    definition = LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0
    objective = ObjectiveDefinitionV2(
        key="binding_expansion",
        name="Binding expansion",
        description="Exercise independent target binding expansion.",
        completion_requirements=(
            ObjectiveRequirementV2(
                key="hospital",
                node_key="central_hospital",
                fact_key="operational",
                accepted_values=(True,),
                description="Hospital is operational.",
            ),
            ObjectiveRequirementV2(
                key="telecom",
                node_key="central_telecom_hub",
                fact_key="operational",
                accepted_values=(True,),
                description="Telecom hub is operational.",
            ),
        ),
    )
    actor = PlannerActorState(
        actor_key="test_executor",
        role_key="test_role",
        capabilities=("EXECUTE_ACTION",),
        allowed_action_keys=("repair_test",),
        availability="ACTIVE",
        current_region="central_district",
        command_reachability="ONLINE",
    )
    contract = PlannerActionContract(
        action_key="repair_test",
        executor_requirements={
            "command_reachability": "ONLINE",
            "required_capabilities": ["EXECUTE_ACTION"],
        },
        deterministic_effects=(
            {
                "type": "FACT_MUTATION",
                "target": "target_key",
                "fact_key": "operational",
                "value": True,
            },
        ),
    )
    result = build_dependency_closure(
        definition,
        (objective,),
        PlannerInput(
            actors=(actor,),
            action_contracts=(contract,),
            target_bindings=(
                PlannerTargetBinding(
                    action_key="repair_test",
                    target_key="central_hospital",
                    requirements=({"cost": {"hospital_parts": 2}},),
                    deterministic_effects=(
                        {
                            "type": "FACT_MUTATION",
                            "target": "target_key",
                            "fact_key": "operational",
                            "value": True,
                        },
                    ),
                ),
                PlannerTargetBinding(
                    action_key="repair_test",
                    target_key="central_telecom_hub",
                    requirements=({"cost": {"telecom_parts": 3}},),
                    deterministic_effects=(
                        {
                            "type": "FACT_MUTATION",
                            "target": "target_key",
                            "fact_key": "operational",
                            "value": True,
                        },
                    ),
                ),
            ),
            known_world=PlannerKnownWorldSlice(
                nodes=(
                    {
                        "key": "central_hospital",
                        "access": "AVAILABLE",
                        "interactions": [],
                    },
                    {
                        "key": "central_telecom_hub",
                        "access": "AVAILABLE",
                        "interactions": [],
                    },
                ),
                resources={
                    "hospital_parts": {"known_available": 2, "known_total": 2},
                    "telecom_parts": {"known_available": 3, "known_total": 3},
                },
            ),
        ),
    )

    assert {
        (item.action_key, item.target_key) for item in result.planner_input.target_bindings
    } == {
        ("repair_test", "central_hospital"),
        ("repair_test", "central_telecom_hub"),
    }
    assert {"hospital_parts", "telecom_parts"}.issubset(result.planner_input.known_world.resources)


def _v4_blocked_route_planner_base(
    session: Session,
    key: str,
) -> tuple[object, object, object, object, PlannerInput]:
    runtime, scope = _linjiang_v4_runtime(session, key)
    route = session.get(
        GameInstanceFactState,
        (scope.game_instance_id, "central_river_tunnel", "passable"),
    )
    west_knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (scope.game_instance_id, "west_logistics_district"),
    )
    assert route is not None and west_knowledge is not None
    route.visibility = Visibility.KNOWN
    route.truth_value = False
    west_knowledge.resource_inventory_visibility = ResourceInventoryVisibility.VISIBLE
    west_knowledge.resource_survey_completed = True
    session.flush()

    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "restore central communication capability",
        resolved_goal=GenericGoalResolution(
            "RESOLVED",
            "restore_central_communication_capability",
            ("restore_central_communication_capability",),
        ),
        initialize_plan=False,
    )
    definition = agent._definition()
    context = PlanningContextBuilder(session, scope).build(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason="TRAVEL_BLOCKED",
    )
    return runtime, scope, task, definition, _canonical_planner_input(context)


def test_dependency_closure_keeps_generic_resource_transport_for_late_action(
    session: Session,
) -> None:
    _runtime, scope, task, definition, base = _v4_blocked_route_planner_base(
        session,
        "linjiang-v4-resource-support",
    )
    closure = build_dependency_closure(
        definition,
        GenericAgentService(session, scope)._objectives(task, definition),
        base,
    )
    action_keys = {item.action_key for item in closure.planner_input.action_contracts}
    assert {"clear_transport", "transport_resource"}.issubset(action_keys)
    transport_contract = next(
        item
        for item in closure.planner_input.action_contracts
        if item.action_key == "transport_resource"
    )
    assert {item["key"] for item in transport_contract.parameters} == {
        "amount",
        "resource_key",
    }
    region_keys = {
        item["key"]
        for item in closure.planner_input.known_world.nodes
        if item.get("type") == "region"
    }
    assert {"central_district", "west_logistics_district"}.issubset(region_keys)
    logistics = next(
        item for item in closure.planner_input.actors if item.actor_key == "logistics_team_alpha"
    )
    assert "transport_resource" in logistics.allowed_action_keys
    assert any(
        "resource:municipal_repair_materials" in item["dependency_path"]
        for item in closure.relevance_reason["transport_resource"]
    )
    assert len(closure.planner_input.action_contracts) <= len(base.action_contracts)
    assert len(closure.planner_input.actors) <= len(base.actors)
    assert len(closure.planner_input.target_bindings) <= len(base.target_bindings) + 8
    assert len(closure.planner_input.known_world.nodes) <= len(base.known_world.nodes)
    assert len(closure.planner_input.known_world.resources) <= len(base.known_world.resources)


def test_dependency_closure_keeps_recoverable_disconnected_executor_and_relay(
    session: Session,
) -> None:
    _runtime, scope, task, definition, base = _v4_blocked_route_planner_base(
        session,
        "linjiang-v4-recoverable-executor",
    )
    closure = build_dependency_closure(
        definition,
        GenericAgentService(session, scope)._objectives(task, definition),
        base,
    )

    action_keys = {item.action_key for item in closure.planner_input.action_contracts}
    actor_keys = {item.actor_key for item in closure.planner_input.actors}
    assert {"clear_transport", "relay_message"}.issubset(action_keys)
    assert {"municipal_transport_team", "logistics_team_alpha"}.issubset(actor_keys)


def test_dependency_closure_drops_disconnected_executor_without_reachability_producer(
    session: Session,
) -> None:
    _runtime, scope, task, definition, base = _v4_blocked_route_planner_base(
        session,
        "linjiang-v4-no-reachability-producer",
    )
    action_contracts = tuple(
        item for item in base.action_contracts if item.action_key != "relay_message"
    )
    actors = tuple(
        actor.model_copy(
            update={
                "allowed_action_keys": tuple(
                    key for key in actor.allowed_action_keys if key != "relay_message"
                )
            }
        )
        for actor in base.actors
    )
    closure = build_dependency_closure(
        definition,
        GenericAgentService(session, scope)._objectives(task, definition),
        base.model_copy(update={"action_contracts": action_contracts, "actors": actors}),
    )

    assert "clear_transport" not in {
        item.action_key for item in closure.planner_input.action_contracts
    }


def test_dependency_closure_rejects_recoverable_executor_with_role_mismatch(
    session: Session,
) -> None:
    _runtime, scope, task, definition, base = _v4_blocked_route_planner_base(
        session,
        "linjiang-v4-recoverable-role-mismatch",
    )
    actors = tuple(
        actor.model_copy(update={"role_key": "wrong_role"})
        if actor.actor_key == "municipal_transport_team"
        else actor
        for actor in base.actors
    )
    closure = build_dependency_closure(
        definition,
        GenericAgentService(session, scope)._objectives(task, definition),
        base.model_copy(update={"actors": actors}),
    )

    assert "clear_transport" not in {
        item.action_key for item in closure.planner_input.action_contracts
    }


def test_dependency_closure_does_not_add_transport_when_target_has_resource(
    session: Session,
) -> None:
    runtime, scope = _linjiang_v4_runtime(session, "linjiang-v4-resource-already-at-target")
    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "restore central communication capability",
        resolved_goal=GenericGoalResolution(
            "RESOLVED",
            "restore_central_communication_capability",
            ("restore_central_communication_capability",),
        ),
        initialize_plan=False,
    )
    inspected = GenericActionService(session, scope).execute_action(
        actor_key="logistics_team_alpha",
        action_key="inspect",
        target_key="central_telecom_hub",
        parameters={},
        idempotency_key="linjiang-v4-resource-already-at-target-inspect",
    )
    assert inspected.applied is not None and inspected.applied.outcome.failure is None
    session.expire_all()
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    agent = GenericAgentService(session, scope)
    definition = agent._definition()
    context = PlanningContextBuilder(session, scope).build(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason="INSPECTION_KNOWLEDGE_CHANGED",
    )
    base = _canonical_planner_input(context)
    resources = json.loads(json.dumps(base.known_world.resources))
    for resource_key, amount in (
        ("communication_equipment", 10),
        ("general_engineering_parts", 15),
    ):
        resource = resources[resource_key]
        resource["known_total"] = amount
        resource["known_available"] = amount
        central = resource["scopes"]["central_district"]
        central["value"] = amount
        central["known_total"] = amount
        central["known_available"] = amount
        central["knowledge_status"] = "KNOWN"
    base = base.model_copy(
        update={
            "known_world": base.known_world.model_copy(update={"resources": resources}),
        }
    )
    closure = build_dependency_closure(
        definition,
        agent._objectives(task, definition),
        base,
    )
    action_keys = {item.action_key for item in closure.planner_input.action_contracts}
    assert "repair_communications" in action_keys
    assert "transport_resource" not in action_keys


def test_unknown_resource_source_does_not_select_transport_without_known_source(
    session: Session,
) -> None:
    runtime, scope = _linjiang_v4_runtime(session, "linjiang-v4-unknown-resource-source")
    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "restore central communication capability",
        resolved_goal=GenericGoalResolution(
            "RESOLVED",
            "restore_central_communication_capability",
            ("restore_central_communication_capability",),
        ),
        initialize_plan=False,
    )
    inspected = GenericActionService(session, scope).execute_action(
        actor_key="logistics_team_alpha",
        action_key="inspect",
        target_key="central_telecom_hub",
        parameters={},
        idempotency_key="linjiang-v4-unknown-resource-source-inspect",
    )
    assert inspected.applied is not None and inspected.applied.outcome.failure is None
    session.expire_all()
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    agent = GenericAgentService(session, scope)
    definition = agent._definition()
    planner_input = (
        PlanningContextBuilder(session, scope)
        .build_v2_closure(
            definition,
            agent._objectives(task, definition),
            task=task,
            replan_reason="INSPECTION_KNOWLEDGE_CHANGED",
        )
        .planner_input
    )
    action_keys = {item.action_key for item in planner_input.action_contracts}
    assert "survey_resources" in action_keys
    assert "transport_resource" not in action_keys
    assert any(
        item.get("dimension") == "RESOURCE_SOURCE"
        and item.get("resource_key") == "communication_equipment"
        and item.get("source_knowledge_status") == "UNKNOWN"
        for item in planner_input.known_world.unknown_dependencies
    )


def test_resource_transport_requires_a_known_legal_transport_executor(
    session: Session,
) -> None:
    _runtime, scope, task, definition, base = _v4_blocked_route_planner_base(
        session,
        "linjiang-v4-no-transport-executor",
    )
    actors = tuple(
        actor.model_copy(
            update={
                "allowed_action_keys": tuple(
                    key for key in actor.allowed_action_keys if key != "transport_resource"
                )
            }
        )
        for actor in base.actors
    )
    no_transport_actor_base = base.model_copy(update={"actors": actors})
    closure = build_dependency_closure(
        definition,
        GenericAgentService(session, scope)._objectives(task, definition),
        no_transport_actor_base,
    )
    action_keys = {item.action_key for item in closure.planner_input.action_contracts}
    assert "clear_transport" in action_keys
    assert "transport_resource" not in action_keys


def test_linjiang_v4_mock_provider_can_submit_inspect_information_boundary(
    session: Session,
) -> None:
    runtime, scope = _linjiang_v4_runtime(session, "linjiang-v4-mock-inspect-plan")
    provider = _InspectBoundaryProvider()
    task = GenericAgentService(session, scope, provider=provider).create_task(
        runtime.session,
        "restore central communication capability",
        resolved_goal=GenericGoalResolution(
            "RESOLVED",
            "restore_central_communication_capability",
            ("restore_central_communication_capability",),
        ),
    )

    assert task.current_plan_version == 1
    assert task.status.value != "BLOCKED"


def test_linjiang_v4_inspect_knowledge_replan_exposes_state_producer(
    session: Session,
) -> None:
    runtime, scope = _linjiang_v4_runtime(session, "linjiang-v4-inspect-replan")
    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "restore central communication capability",
        resolved_goal=GenericGoalResolution(
            "RESOLVED",
            "restore_central_communication_capability",
            ("restore_central_communication_capability",),
        ),
        initialize_plan=False,
    )
    inspected = GenericActionService(session, scope).execute_action(
        actor_key="logistics_team_alpha",
        action_key="inspect",
        target_key="central_telecom_hub",
        parameters={},
        idempotency_key="linjiang-v4-inspect-replan",
    )
    assert inspected.applied is not None
    assert inspected.applied.outcome.failure is None
    session.expire_all()
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    agent = GenericAgentService(session, scope)
    task = session.get(type(task), task.id)
    assert task is not None
    definition = agent._definition()
    replanned = (
        PlanningContextBuilder(session, scope)
        .build_v2_closure(
            definition,
            agent._objectives(task, definition),
            task=task,
            replan_reason="INSPECTION_KNOWLEDGE_CHANGED",
        )
        .planner_input
    )

    assert replanned.known_world.facts["central_telecom_hub.operational"] is False
    assert "repair_communications" in {item.action_key for item in replanned.action_contracts}
    assert ("repair_communications", "central_telecom_hub") in {
        (item.action_key, item.target_key) for item in replanned.target_bindings
    }
    assert not any(
        item.get("dimension") == "OBJECTIVE_FACT_KNOWLEDGE"
        and item.get("subject_key") == "central_telecom_hub"
        and item.get("fact_key") == "operational"
        for item in replanned.known_world.unknown_dependencies
    )


def test_unknown_fact_without_legal_knowledge_producer_does_not_invent_inspect(
    session: Session,
) -> None:
    runtime, scope = _linjiang_v4_runtime(session, "linjiang-v4-no-knowledge-producer")
    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "restore central communication capability",
        resolved_goal=GenericGoalResolution(
            "RESOLVED",
            "restore_central_communication_capability",
            ("restore_central_communication_capability",),
        ),
        initialize_plan=False,
    )
    definition = agent._definition()
    base = PlanningContextBuilder(session, scope).build_v2(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason=None,
    )
    action_contracts = tuple(item for item in base.action_contracts if item.action_key != "inspect")
    actors = tuple(
        item.model_copy(
            update={
                "allowed_action_keys": tuple(
                    key for key in item.allowed_action_keys if key != "inspect"
                )
            }
        )
        for item in base.actors
    )
    result = build_dependency_closure(
        definition,
        agent._objectives(task, definition),
        base.model_copy(update={"action_contracts": action_contracts, "actors": actors}),
    )
    action_keys = {item.action_key for item in result.planner_input.action_contracts}
    assert "inspect" not in action_keys
    assert any(
        item.get("dimension") == "OBJECTIVE_FACT_KNOWLEDGE" and item.get("status") == "UNKNOWN"
        for item in result.planner_input.known_world.unknown_dependencies
    )


def test_locality_dependency_closure_keeps_relocation_capability_for_local_actions(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-locality-closure-relocation")
    route = session.get(
        GameInstanceFactState,
        (scope.game_instance_id, "central_river_tunnel", "passable"),
    )
    assert route is not None
    route.visibility = Visibility.KNOWN
    route.truth_value = False
    session.flush()

    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "restore central communication capability",
        resolved_goal=GenericGoalResolution(
            "RESOLVED",
            "restore_central_communication_capability",
            ("restore_central_communication_capability",),
        ),
        initialize_plan=False,
    )
    definition = agent._definition()
    closure = PlanningContextBuilder(session, scope).build_v2_closure(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason="TRAVEL_BLOCKED",
    )
    action_keys = {item.action_key for item in closure.planner_input.action_contracts}
    assert {"clear_transport", "repair_communications", "travel"}.issubset(action_keys)


def test_historical_travel_success_requires_current_location_redundancy_proof(
    session: Session,
) -> None:
    definition = build_linjiang_v1_definition(LINJIANG_V1_TEST)
    travel = next(item for item in definition.actions if item.key == "travel")
    actor = GameInstanceActor(
        game_instance_id=uuid4(),
        actor_key="actor",
        name="Actor",
        role_key="worker",
        persona="worker",
        current_node_key="west_logistics_district",
        allowed_action_keys=["travel"],
        capabilities=["EXECUTE_ACTION"],
        status="ACTIVE",
        command_reachability="ONLINE",
    )
    assert GenericAgentService._historical_success_is_redundant(
        definition=definition,
        action=travel,
        actor=actor,
        target_key="west_logistics_district",
        planner_input=None,
    )
    assert GenericAgentService._historical_success_is_redundant(
        definition=definition,
        action=travel,
        actor=actor,
        target_key="west_logistics_district",
        planner_input=None,
        projected_actor_locations={"actor": "west_logistics_district"},
    )
    actor.current_node_key = "central_district"
    assert not GenericAgentService._historical_success_is_redundant(
        definition=definition,
        action=travel,
        actor=actor,
        target_key="west_logistics_district",
        planner_input=None,
    )
    assert not GenericAgentService._historical_success_is_redundant(
        definition=definition,
        action=travel,
        actor=actor,
        target_key="west_logistics_district",
        planner_input=None,
        projected_actor_locations={"actor": "central_district"},
    )


def test_linjiang_v2_0_completed_survey_repair_diagnostic_is_typed(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-survey-diagnostic")
    provider = _RepeatingPlanProvider(
        (
            PlanStepProposal(
                step_id="survey-central",
                purpose="Survey Central resources.",
                action_key="survey_resources",
                actor_key="logistics_team_alpha",
                target_key="central_district",
            ),
        )
    )

    with pytest.raises(GenericAgentError, match="backend-valid current Plan"):
        GenericAgentService(session, scope, provider=provider).create_task(
            runtime.session,
            "restore_central_communication_capability",
        )

    diagnostic = provider.requests[1].repair_diagnostics[0]
    assert diagnostic.model_dump(mode="json", exclude_none=True, exclude_defaults=True) == {
        "code": "RESOURCE_SURVEY_ALREADY_COMPLETED",
        "step_id": "survey-central",
        "failure_code": "RESOURCE_SURVEY_ALREADY_COMPLETED",
        "dimension": "RESOURCE_SURVEY_STATE",
        "action_key": "survey_resources",
        "actor_key": "logistics_team_alpha",
        "target_key": "central_district",
        "required": "NOT_COMPLETED",
        "actual": "COMPLETED",
    }


def test_linjiang_v2_0_known_resource_deficit_repair_diagnostic_is_typed(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-resource-diagnostic")
    communications = session.get(
        GameInstanceActor,
        (runtime.instance.id, "communications_repair_team_alpha"),
    )
    assert communications is not None
    communications.current_node_key = "central_district"
    communications.command_reachability = "ONLINE"
    session.flush()
    provider = _RepeatingPlanProvider(
        (
            PlanStepProposal(
                step_id="repair-central",
                purpose="Repair Central communications.",
                action_key="repair_communications",
                actor_key="communications_repair_team_alpha",
                target_key="central_telecom_hub",
            ),
        )
    )

    with pytest.raises(GenericAgentError, match="backend-valid current Plan"):
        GenericAgentService(session, scope, provider=provider).create_task(
            runtime.session,
            "restore_central_communication_capability",
        )

    diagnostic = provider.requests[1].repair_diagnostics[0]
    assert diagnostic.model_dump(mode="json", exclude_none=True, exclude_defaults=True) == {
        "code": "KNOWN_RESOURCE_INSUFFICIENT",
        "step_id": "repair-central",
        "failure_code": "KNOWN_RESOURCE_INSUFFICIENT",
        "dimension": "RESOURCE_QUANTITY",
        "action_key": "repair_communications",
        "actor_key": "communications_repair_team_alpha",
        "target_key": "central_telecom_hub",
        "resource_key": "communication_equipment",
        "scope_region": "central_district",
        "required_amount": 10,
        "projected_known_available_amount": 0,
        "deficit": 10,
    }


def test_linjiang_v2_0_unknown_resource_quantity_is_not_known_insufficient(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-unknown-resource")
    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "central_district"),
    )
    assert knowledge is not None
    knowledge.resource_inventory_visibility = ResourceInventoryVisibility.HIDDEN
    knowledge.resource_survey_completed = False
    session.flush()
    agent = GenericAgentService(session, scope)
    definition = agent._definition()
    pools, region_knowledge = agent._projected_resource_state(definition)

    with pytest.raises(GenericAgentError) as error:
        agent._consume_projected_resource(
            "central_district",
            "communication_equipment",
            10,
            pools,
            region_knowledge,
            require_known=False,
        )
    assert error.value.code == "RESOURCE_INVENTORY_UNKNOWN"
    assert error.value.details == {
        "dimension": "RESOURCE_KNOWLEDGE",
        "resource_key": "communication_equipment",
        "scope_region": "central_district",
        "required_amount": 10,
        "required": "KNOWN_VISIBLE_AVAILABLE",
        "actual": "UNKNOWN",
    }


def test_linjiang_v2_0_known_preflight_repair_diagnostic_has_public_witness(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-known-preflight-diagnostic")
    actor = session.get(
        GameInstanceActor,
        (runtime.instance.id, "water_repair_team_alpha"),
    )
    assert actor is not None
    actor.command_reachability = "ONLINE"
    session.flush()
    provider = _RepeatingPlanProvider(
        (
            PlanStepProposal(
                step_id="repair-water-without-support",
                action_key="repair_water_facility",
                actor_key="water_repair_team_alpha",
                target_key="water_treatment_plant",
            ),
        )
    )

    with pytest.raises(GenericAgentError, match="backend-valid current Plan"):
        GenericAgentService(session, scope, provider=provider).create_task(
            runtime.session,
            "restore_east_emergency_water_supply",
        )

    diagnostic = provider.requests[1].repair_diagnostics[0]
    assert diagnostic.code == "ACTION_OUTSIDE_PLANNER_CONTEXT"
    assert diagnostic.failure_code == "ACTION_OUTSIDE_PLANNER_CONTEXT"
    assert diagnostic.dimension == "ACTION_BINDING"
    assert diagnostic.step_id == "repair-water-without-support"
    assert diagnostic.action_key == "repair_water_facility"
    assert diagnostic.actor_key == "water_repair_team_alpha"
    assert diagnostic.target_key == "water_treatment_plant"
    assert diagnostic.required == "ACTION_IN_CANONICAL_ACTION_CONTRACTS"
    assert diagnostic.actual == "repair_water_facility"


def test_validator_stops_projected_diagnostics_after_static_root_failure(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-static-root-no-cascade")
    provider = _RepeatingPlanProvider(
        (
            PlanStepProposal(
                step_id="invalid-interaction",
                action_key="repair_communications",
                actor_key="communications_repair_team_alpha",
                target_key="central_district",
            ),
            PlanStepProposal(
                step_id="downstream-locality",
                action_key="travel",
                actor_key="logistics_team_alpha",
                target_key="central_district",
            ),
            PlanStepProposal(
                step_id="downstream-command-resource",
                action_key="repair_communications",
                actor_key="communications_repair_team_alpha",
                target_key="central_telecom_hub",
            ),
        )
    )

    with pytest.raises(GenericAgentError, match="backend-valid current Plan"):
        GenericAgentService(session, scope, provider=provider).create_task(
            runtime.session,
            "restore_central_communication_capability",
        )

    diagnostics = provider.requests[1].repair_diagnostics
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "TARGET_INTERACTION_INVALID"
    assert diagnostics[0].step_id == "invalid-interaction"


def test_validator_reports_multiple_independent_static_root_failures(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-multiple-static-roots")
    provider = _RepeatingPlanProvider(
        (
            PlanStepProposal(
                step_id="invalid-interaction",
                action_key="repair_communications",
                actor_key="communications_repair_team_alpha",
                target_key="central_district",
            ),
            PlanStepProposal(
                step_id="invalid-parameters",
                action_key="transport_resource",
                actor_key="logistics_team_alpha",
                target_key="central_district",
                parameters={"resource_key": "communication_equipment", "amount": "many"},
            ),
            PlanStepProposal(
                step_id="actor-not-allowed",
                action_key="repair_communications",
                actor_key="logistics_team_alpha",
                target_key="central_telecom_hub",
            ),
        )
    )

    with pytest.raises(GenericAgentError, match="backend-valid current Plan"):
        GenericAgentService(session, scope, provider=provider).create_task(
            runtime.session,
            "restore_central_communication_capability",
        )

    diagnostics = provider.requests[1].repair_diagnostics
    assert [(item.step_id, item.code) for item in diagnostics] == [
        ("invalid-interaction", "TARGET_INTERACTION_INVALID"),
        ("invalid-parameters", "ACTION_OUTSIDE_PLANNER_CONTEXT"),
        ("actor-not-allowed", "ACTOR_ACTION_OUTSIDE_PLANNER_CONTEXT"),
    ]


def test_validator_reports_actor_capability_mismatch_from_public_actor_state(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-actor-capability-diagnostic")
    actor = session.get(
        GameInstanceActor,
        (runtime.instance.id, "communications_repair_team_alpha"),
    )
    assert actor is not None
    actor.capabilities = [
        capability for capability in actor.capabilities if capability != "EXECUTE_ACTION"
    ]
    session.flush()
    provider = _RepeatingPlanProvider(
        (
            PlanStepProposal(
                step_id="missing-capability",
                action_key="repair_communications",
                actor_key="communications_repair_team_alpha",
                target_key="central_telecom_hub",
            ),
        )
    )

    with pytest.raises(GenericAgentError, match="backend-valid current Plan"):
        GenericAgentService(session, scope, provider=provider).create_task(
            runtime.session,
            "restore_central_communication_capability",
        )

    planner_actor = next(
        item
        for item in provider.requests[0].planner_input.actors
        if item.actor_key == "communications_repair_team_alpha"
    )
    assert "EXECUTE_ACTION" not in planner_actor.capabilities
    diagnostic = provider.requests[1].repair_diagnostics[0]
    assert diagnostic.code == "ACTOR_CAPABILITY_MISSING"
    assert diagnostic.failure_code == "ACTOR_CAPABILITY_MISSING"
    assert diagnostic.dimension == "ACTOR_CAPABILITY"
    assert diagnostic.step_id == "missing-capability"
    assert diagnostic.action_key == "repair_communications"
    assert diagnostic.actor_key == "communications_repair_team_alpha"
    assert diagnostic.target_key == "central_telecom_hub"
    assert diagnostic.required == ["EXECUTE_ACTION"]
    assert diagnostic.actual == ["INSPECT_STATE", "PLAN"]


def test_validator_stops_after_first_projected_state_root_failure(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-state-root-no-cascade")
    provider = _RepeatingPlanProvider(
        (
            PlanStepProposal(
                step_id="same-region-travel",
                action_key="travel",
                actor_key="logistics_team_alpha",
                target_key="central_district",
            ),
            PlanStepProposal(
                step_id="downstream-command",
                action_key="repair_communications",
                actor_key="communications_repair_team_alpha",
                target_key="central_telecom_hub",
            ),
        )
    )

    with pytest.raises(GenericAgentError, match="backend-valid current Plan"):
        GenericAgentService(session, scope, provider=provider).create_task(
            runtime.session,
            "restore_central_communication_capability",
        )

    diagnostics = provider.requests[1].repair_diagnostics
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "LOCALITY_INVALID"
    assert diagnostics[0].step_id == "same-region-travel"


def test_linjiang_v2_0_projected_repair_applies_selected_target_cost_once(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-selected-target-cost")
    communications = session.get(
        GameInstanceActor,
        (runtime.instance.id, "communications_repair_team_alpha"),
    )
    known_parts = session.get(
        GameInstanceResourceState,
        (
            runtime.instance.id,
            resource_state_key(
                "general_engineering_parts",
                "central_district",
                "central_general_stock",
            ),
        ),
    )
    assert communications is not None
    assert known_parts is not None
    communications.current_node_key = "central_district"
    communications.command_reachability = "ONLINE"
    known_parts.value = 15
    session.add(
        GameInstanceResourceState(
            game_instance_id=runtime.instance.id,
            resource_identity=resource_state_key(
                "communication_equipment",
                "central_district",
                "test_central_communication_stock",
            ),
            resource_key="communication_equipment",
            scope_node_key="central_district",
            pool_key="test_central_communication_stock",
            value=10,
            reserved_value=0,
            visibility=ResourcePoolVisibility.VISIBLE,
            availability=ResourcePoolAvailability.AVAILABLE,
            survey_discoverable=False,
        )
    )
    session.flush()
    provider = _RepeatingPlanProvider(
        (
            PlanStepProposal(
                step_id="repair-central",
                purpose="Repair Central communications.",
                action_key="repair_communications",
                actor_key="communications_repair_team_alpha",
                target_key="central_telecom_hub",
            ),
        )
    )

    task = GenericAgentService(session, scope, provider=provider).create_task(
        runtime.session,
        "restore_central_communication_capability",
    )

    assert task.current_plan_version == 1
    assert len(provider.requests) == 1


def test_linjiang_v2_0_projected_repair_does_not_leak_cross_target_reachability(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-target-reachability")
    agent = GenericAgentService(session, scope)
    definition = agent._definition()
    action = next(item for item in definition.actions if item.key == "repair_communications")
    facts = agent._known_fact_projection()
    nodes = agent._known_node_keys()
    relations = agent._known_relation_keys(definition)
    effects = agent._projected_resolution_effects(
        definition,
        action,
        "central_telecom_hub",
        {},
        facts,
        nodes,
        relations,
    )
    reachability = {
        actor.actor_key: CommandReachability(actor.command_reachability)
        for actor in session.scalars(
            select(GameInstanceActor).where(
                GameInstanceActor.game_instance_id == runtime.instance.id
            )
        )
    }
    industrial_before = reachability["industrial_repair_team_alpha"]

    agent._apply_projected_actor_reachability_effect(
        action,
        "communications_repair_team_alpha",
        "central_telecom_hub",
        effects,
        reachability,
    )

    assert reachability["communications_repair_team_alpha"] == CommandReachability.ONLINE
    assert reachability["industrial_repair_team_alpha"] == industrial_before


def test_projected_ambiguous_rules_apply_only_common_effects_once(
    session: Session,
) -> None:
    _runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-ambiguous-effects")
    agent = GenericAgentService(session, scope)
    definition = agent._definition()
    action = next(item for item in definition.actions if item.key == "repair_communications")
    facts = agent._known_fact_projection()
    nodes = agent._known_node_keys()
    relations = agent._known_relation_keys(definition)
    profile = facts[("central_telecom_hub", "repair_profile")]
    profile.visibility = Visibility.HIDDEN

    effects = agent._projected_resolution_effects(
        definition,
        action,
        "central_telecom_hub",
        {},
        facts,
        nodes,
        relations,
    )
    effect_payloads = [item.model_dump(mode="json") for item in effects]
    resource_effects = [item for item in effect_payloads if item["kind"] == "ADJUST_RESOURCE"]
    reachability_effects = [
        item for item in effect_payloads if item["kind"] == "SET_ACTOR_COMMAND_REACHABILITY"
    ]

    assert [(item["resource_key"], item["amount"]["literal"]) for item in resource_effects] == [
        ("communication_equipment", -10),
        ("general_engineering_parts", -15),
    ]
    assert reachability_effects == []
    assert facts[("central_telecom_hub", "operational")].value is False

    agent._apply_projected_fact_effects(
        definition,
        action,
        "communications_repair_team_alpha",
        "central_telecom_hub",
        {},
        effects,
        facts,
        nodes,
        relations,
    )

    assert facts[("central_telecom_hub", "operational")].value is True


def test_linjiang_v2_0_all_regions_have_the_generic_travel_target_contract() -> None:
    definition = build_linjiang_v1_definition(LINJIANG_V1_TEST)
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


def test_linjiang_v2_0_central_east_hidden_route_is_accepted_by_validator(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-central-east-validator")
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


def test_linjiang_v2_0_clear_transport_uses_endpoint_locality_not_passability(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-clear-transport-locality")
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
    action = next(item for item in definition.actions if item.key == "clear_transport")
    projected_locations = {key: item.current_node_key for key, item in actors.items()}
    projected_reachability = {key: CommandReachability.ONLINE for key in actors}
    projected_passability = {"central_river_tunnel": False}
    projected_facts = agent._known_fact_projection()
    projected_nodes = agent._known_node_keys()
    projected_relations = agent._known_relation_keys(definition)

    with pytest.raises(GenericAgentError) as wrong_endpoint:
        agent._validate_projected_action_state(
            definition,
            action,
            "municipal_transport_team",
            "central_river_tunnel",
            {},
            {**projected_locations, "municipal_transport_team": "west_logistics_district"},
            projected_passability,
            projected_facts,
            projected_nodes,
            projected_relations,
            actors=actors,
            projected_command_reachability=projected_reachability,
        )
    assert wrong_endpoint.value.code == "LOCALITY_TRANSPORT_ENDPOINT_INVALID"

    projected_locations["municipal_transport_team"] = "central_district"
    assert (
        agent._validate_projected_action_state(
            definition,
            action,
            "municipal_transport_team",
            "central_river_tunnel",
            {},
            projected_locations,
            projected_passability,
            projected_facts,
            projected_nodes,
            projected_relations,
            actors=actors,
            projected_command_reachability=projected_reachability,
        )
        is None
    )

    travel = next(item for item in definition.actions if item.key == "travel")
    projected_locations["municipal_transport_team"] = "west_logistics_district"
    travel_connector = agent._validate_projected_action_state(
        definition,
        travel,
        "municipal_transport_team",
        "central_district",
        {},
        projected_locations,
        projected_passability,
        projected_facts,
        projected_nodes,
        projected_relations,
        actors=actors,
        projected_command_reachability=projected_reachability,
    )
    assert travel_connector == "west_freight_corridor"
    travel_effects = agent._projected_resolution_effects(
        definition,
        travel,
        "central_district",
        {},
        projected_facts,
        projected_nodes,
        projected_relations,
    )
    agent._advance_projected_action_state(
        definition,
        travel,
        "municipal_transport_team",
        "central_district",
        {},
        projected_locations,
        projected_passability,
        projected_facts,
        projected_nodes,
        projected_relations,
        travel_effects,
        projected_command_reachability=projected_reachability,
    )
    assert projected_locations["municipal_transport_team"] == "central_district"
    assert (
        agent._validate_projected_action_state(
            definition,
            action,
            "municipal_transport_team",
            "central_river_tunnel",
            {},
            projected_locations,
            projected_passability,
            projected_facts,
            projected_nodes,
            projected_relations,
            actors=actors,
            projected_command_reachability=projected_reachability,
        )
        is None
    )


def test_linjiang_v2_0_hidden_block_is_discovered_only_during_runtime(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-central-east-runtime")
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
        idempotency_key="linjiang-v2_0-central-east-blocked",
    )

    assert result.applied is not None
    assert result.applied.outcome.failure is not None
    assert result.applied.outcome.failure.code == "TRAVEL_BLOCKED"
    assert fact.visibility == Visibility.KNOWN
    assert fact.truth_value is False
    assert any(
        item.key == "central_river_tunnel.passable" for item in result.applied.knowledge_changes
    )


def test_linjiang_v2_0_relay_recovery_path_is_sequentially_validated(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-v2_0-relay-path")
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
        projected_effects = agent._projected_resolution_effects(
            definition,
            action,
            target_key,
            {},
            projected_facts,
            projected_nodes,
            projected_relations,
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
            projected_effects,
            projected_command_reachability=projected_reachability,
        )

    assert projected_locations["logistics_team_alpha"] == "east_residential_district"
    assert projected_locations["communications_repair_team_alpha"] == "central_district"
    assert projected_reachability["communications_repair_team_alpha"] == CommandReachability.ONLINE


def test_projected_canonical_actor_location_effect_updates_following_step(
    session: Session,
) -> None:
    runtime, scope = _v2_0_runtime(session, "linjiang-projected-actor-location-effect")
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
    projected_locations = {key: item.current_node_key for key, item in actors.items()}
    projected_passability = agent._known_passability(definition)
    projected_facts = agent._known_fact_projection()
    projected_nodes = agent._known_node_keys()
    projected_relations = agent._known_relation_keys(definition)
    projected_reachability = {
        key: CommandReachability(actor.command_reachability) for key, actor in actors.items()
    }
    action = next(item for item in definition.actions if item.key == "repair_communications")
    planner_input = PlannerInput(
        action_contracts=(
            PlannerActionContract(
                action_key=action.key,
                deterministic_effects=(
                    {"type": "ACTOR_LOCATION", "actor": "executor", "value": "target_key"},
                ),
            ),
        ),
    )

    agent._advance_projected_action_state(
        definition,
        action,
        "logistics_team_alpha",
        "west_logistics_district",
        {},
        projected_locations,
        projected_passability,
        projected_facts,
        projected_nodes,
        projected_relations,
        (),
        planner_input=planner_input,
        projected_command_reachability=projected_reachability,
    )

    assert projected_locations["logistics_team_alpha"] == "west_logistics_district"
    travel = next(item for item in definition.actions if item.key == "travel")
    assert (
        agent._validate_projected_plan_locality(
            definition,
            travel,
            "logistics_team_alpha",
            "central_district",
            {},
            projected_locations,
        )
        == "west_freight_corridor"
    )


def test_linjiang_v2_0_known_route_remains_one_hop_after_hidden_route_failure() -> None:
    definition = build_linjiang_v1_definition(LINJIANG_V1_TEST)
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
    definition = build_linjiang_v1_definition(LINJIANG_V1_TEST)
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
    assert known_failure is not None
    assert known_failure.failure_code == "HEAVY_ENGINEERING_SUPPORT_REQUIRED"
    assert known_failure.known_predicate == {
        "operator": "ALL",
        "predicates": [
            {
                "kind": "FACT_EQUALS",
                "node_key": "water_treatment_plant",
                "fact_key": "repair_profile",
                "operator": "EQ",
                "expected": "water_treatment_plant",
                "actual": "water_treatment_plant",
            },
            {
                "kind": "FACT_NOT_EQUALS",
                "node_key": "water_treatment_plant",
                "fact_key": "heavy_engineering_support_ready",
                "operator": "NE",
                "expected": True,
                "actual": False,
            },
        ],
    }

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
    definition = build_linjiang_v1_definition(LINJIANG_V1_TEST)
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
