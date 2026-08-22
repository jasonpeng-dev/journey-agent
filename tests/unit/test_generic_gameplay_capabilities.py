from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentError, GenericAgentService
from app.agent.planning_context import PlanningContextBuilder
from app.agent.provider import PlanProposal, PlanRequest, PlanStepProposal
from app.domain.enums import AgentTaskStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import GameInstanceActor, GameInstanceFactState, Player
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V1
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import GameLifecycleService
from app.services.generic_actions import GenericActionError, GenericActionService
from app.services.knowledge_projection import SharedKnowledgeProjection
from app.services.player_projection import PlayerProjectionService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService


def _fact(
    key: str,
    name: str,
    value_type: str,
    value: str | bool,
    *,
    allowed_values: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "key": key,
        "name": name,
        "description": name,
        "value_type": value_type,
        "initial_value": value,
        "initial_visibility": "KNOWN",
    }
    if allowed_values is not None:
        result["allowed_values"] = allowed_values
    return result


def _add_fact(nodes: list[dict[str, Any]], node_key: str, fact: dict[str, Any]) -> None:
    node = next(item for item in nodes if item["key"] == node_key)
    node.setdefault("facts", []).append(fact)


def _condition(
    kind: str,
    *,
    node: dict[str, Any],
    fact_key: str,
    value: str | bool,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "node": node,
        "fact_key": fact_key,
        "value": value,
    }


def _definition() -> ScenarioDefinitionV2:
    document: dict[str, Any] = deepcopy(LINJIANG_INFRASTRUCTURE_RECOVERY_V1.model_dump(mode="json"))
    document["metadata"]["key"] = "generic_gameplay_capabilities"
    document["metadata"]["name"] = "Generic Gameplay Capabilities"
    document["world"]["key"] = "generic_gameplay_capabilities"
    document["world"]["name"] = "Generic Gameplay Capabilities"

    nodes = document["world"]["nodes"]
    for node_key in (
        "central_telecom_hub",
        "central_fire_rescue_station",
    ):
        next(item for item in nodes if item["key"] == node_key)["interaction_keys"].extend(
            ["power_targetable", "heavy_support_target", "repairable"]
        )
    next(item for item in nodes if item["key"] == "west_freight_corridor")[
        "interaction_keys"
    ].append("heavy_support_target")
    _add_fact(nodes, "central_hospital", _fact("operational", "Operational", "BOOLEAN", True))
    _add_fact(
        nodes,
        "central_hospital",
        _fact("power_generation_capable", "Power generation capable", "BOOLEAN", False),
    )
    _add_fact(
        nodes,
        "central_hospital",
        _fact(
            "power_supply",
            "Power supply",
            "ENUM",
            "AVAILABLE",
            allowed_values=["AVAILABLE", "UNAVAILABLE"],
        ),
    )
    _add_fact(nodes, "central_telecom_hub", _fact("operational", "Operational", "BOOLEAN", True))
    _add_fact(
        nodes,
        "central_telecom_hub",
        _fact("power_generation_capable", "Power generation capable", "BOOLEAN", False),
    )
    _add_fact(
        nodes,
        "central_telecom_hub",
        _fact(
            "power_supply",
            "Power supply",
            "ENUM",
            "UNAVAILABLE",
            allowed_values=["AVAILABLE", "UNAVAILABLE"],
        ),
    )
    _add_fact(
        nodes,
        "central_fire_rescue_station",
        _fact(
            "power_supply",
            "Power supply",
            "ENUM",
            "UNAVAILABLE",
            allowed_values=["AVAILABLE", "UNAVAILABLE"],
        ),
    )
    _add_fact(
        nodes,
        "central_fire_rescue_station",
        _fact("power_generation_capable", "Power generation capable", "BOOLEAN", False),
    )
    _add_fact(
        nodes,
        "heavy_equipment_yard",
        _fact("operational", "Operational", "BOOLEAN", True),
    )
    _add_fact(
        nodes,
        "heavy_equipment_yard",
        _fact("power_generation_capable", "Power generation capable", "BOOLEAN", True),
    )
    _add_fact(
        nodes,
        "west_freight_corridor",
        _fact(
            "heavy_engineering_support_ready",
            "Heavy engineering support ready",
            "BOOLEAN",
            False,
        ),
    )
    _add_fact(
        nodes,
        "heavy_equipment_yard",
        _fact(
            "heavy_engineering_support",
            "Heavy engineering support",
            "ENUM",
            "AVAILABLE",
            allowed_values=["AVAILABLE", "UNAVAILABLE"],
        ),
    )
    _add_fact(
        nodes,
        "central_fire_rescue_station",
        _fact(
            "heavy_engineering_support_ready",
            "Heavy engineering support ready",
            "BOOLEAN",
            False,
        ),
    )
    document["world"]["relations"].extend(
        [
            {
                "source_node_key": "central_hospital",
                "relation_type_key": "supplies_power_to",
                "target_node_key": "central_telecom_hub",
            },
            {
                "source_node_key": "central_telecom_hub",
                "relation_type_key": "supplies_power_to",
                "target_node_key": "central_fire_rescue_station",
            },
            {
                "source_node_key": "heavy_equipment_yard",
                "relation_type_key": "supplies_power_to",
                "target_node_key": "central_fire_rescue_station",
            },
        ]
    )
    document["interactions"].extend(
        [
            {
                "key": "power_targetable",
                "name": "Power target",
                "description": "Can receive power.",
            },
            {
                "key": "heavy_support_target",
                "name": "Heavy support target",
                "description": "Can receive heavy engineering support.",
            },
        ]
    )
    document["actors"]["roles"].extend(
        [
            {
                "key": "industrial_support_team",
                "name": "Industrial support team",
                "description": "Deploys heavy engineering support.",
                "capabilities": ["EXECUTE_ACTION"],
            },
            {
                "key": "water_repair_team",
                "name": "Water repair team",
                "description": "Performs specialist facility repair.",
                "capabilities": ["EXECUTE_ACTION"],
            },
        ]
    )
    document["actors"]["actor_profiles"].extend(
        [
            {
                "key": "industrial_team",
                "name": "Industrial team",
                "role_key": "industrial_support_team",
                "persona": "Deploys heavy support.",
                "initial_node_key": "central_district",
                "allowed_action_keys": [
                    "travel",
                    "deploy_heavy_engineering_support",
                    "repair_supported_facility",
                    "repair_electrical",
                ],
            },
            {
                "key": "water_team",
                "name": "Water team",
                "role_key": "water_repair_team",
                "persona": "Repairs supported facilities.",
                "initial_node_key": "central_district",
                "allowed_action_keys": ["repair_supported_facility"],
            },
        ]
    )
    document["actors"]["actor_profiles"][0]["allowed_action_keys"].append("supply_power")
    next(action for action in document["actions"] if action["key"] == "repair_electrical")[
        "required_actor_role_key"
    ] = "electrical_response_team"
    document["actions"].extend(
        [
            {
                "key": "supply_power",
                "name": "Supply power",
                "description": "Establish direct power at a target.",
                "required_interaction_key": "power_targetable",
                "execution_mode": "IMMEDIATE",
                "parameters": [
                    {
                        "key": "source_key",
                        "name": "Power source",
                        "value_type": "STRING",
                    }
                ],
                "allowed_actor_capabilities": ["EXECUTE_ACTION"],
                "required_actor_role_key": "electrical_response_team",
                "expected_outcomes": [
                    {"code": "POWER_SUPPLIED", "name": "Power supplied", "success": True}
                ],
                "planning": {
                    "terminal_effects": [
                        {
                            "node_key": "central_fire_rescue_station",
                            "fact_key": "power_supply",
                        }
                    ],
                    "success_outcome_codes": ["POWER_SUPPLIED"],
                },
                "behavior": "SUPPLY_POWER",
                "locality": "FACILITY_REGION",
                "source_relation_type_key": "supplies_power_to",
            },
            {
                "key": "deploy_heavy_engineering_support",
                "name": "Deploy heavy engineering support",
                "description": "Deploy heavy support at a target.",
                "required_interaction_key": "heavy_support_target",
                "execution_mode": "IMMEDIATE",
                "allowed_actor_capabilities": ["EXECUTE_ACTION"],
                "required_actor_role_key": "industrial_support_team",
                "expected_outcomes": [
                    {"code": "SUPPORT_DEPLOYED", "name": "Support deployed", "success": True}
                ],
                "planning": {"success_outcome_codes": ["SUPPORT_DEPLOYED"]},
                "behavior": "DEPLOY_HEAVY_ENGINEERING_SUPPORT",
                "locality": "LOCAL_TARGET",
            },
            {
                "key": "repair_supported_facility",
                "name": "Repair supported facility",
                "description": "Repair a facility after heavy support is ready.",
                "required_interaction_key": "repairable",
                "execution_mode": "IMMEDIATE",
                "allowed_actor_capabilities": ["EXECUTE_ACTION"],
                "required_actor_role_key": "water_repair_team",
                "expected_outcomes": [
                    {"code": "SPECIALIST_REPAIRED", "name": "Repaired", "success": True}
                ],
                "planning": {
                    "terminal_effects": [
                        {"node_key": "central_fire_rescue_station", "fact_key": "power_supply"}
                    ],
                    "success_outcome_codes": ["SPECIALIST_REPAIRED"],
                },
                "locality": "FACILITY_REGION",
            },
        ]
    )
    document["rules"].extend(
        [
            {
                "key": "supply_source_not_operational",
                "phase": "PREFLIGHT",
                "action_key": "supply_power",
                "priority": 100,
                "condition": _condition(
                    "FACT_NOT_EQUALS",
                    node={"kind": "ACTION_SOURCE"},
                    fact_key="operational",
                    value=True,
                ),
                "effects": [
                    {
                        "kind": "EMIT_FAILURE",
                        "failure_code": "POWER_SOURCE_NOT_OPERATIONAL",
                        "message": "Power source is not operational.",
                        "retryable": True,
                    }
                ],
            },
            {
                "key": "supply_source_unavailable",
                "phase": "PREFLIGHT",
                "action_key": "supply_power",
                "priority": 90,
                "condition": {
                    "kind": "ALL",
                    "conditions": [
                        _condition(
                            "FACT_EQUALS",
                            node={"kind": "ACTION_SOURCE"},
                            fact_key="power_generation_capable",
                            value=False,
                        ),
                        _condition(
                            "FACT_NOT_EQUALS",
                            node={"kind": "ACTION_SOURCE"},
                            fact_key="power_supply",
                            value="AVAILABLE",
                        ),
                    ],
                },
                "effects": [
                    {
                        "kind": "EMIT_FAILURE",
                        "failure_code": "POWER_SOURCE_UNAVAILABLE",
                        "message": "Power source is unavailable.",
                        "retryable": True,
                    }
                ],
            },
            {
                "key": "supply_power_resolution",
                "phase": "RESOLVE",
                "action_key": "supply_power",
                "priority": 0,
                "effects": [
                    {
                        "kind": "SET_FACT",
                        "node": {"kind": "CURRENT_TARGET"},
                        "fact_key": "power_supply",
                        "value": {"source": "LITERAL", "literal": "AVAILABLE"},
                    },
                    {"kind": "EMIT_OUTCOME", "outcome_code": "POWER_SUPPLIED"},
                ],
            },
            {
                "key": "heavy_support_unavailable",
                "phase": "PREFLIGHT",
                "action_key": "deploy_heavy_engineering_support",
                "priority": 100,
                "condition": _condition(
                    "FACT_NOT_EQUALS",
                    node={"kind": "EXPLICIT", "node_key": "heavy_equipment_yard"},
                    fact_key="heavy_engineering_support",
                    value="AVAILABLE",
                ),
                "effects": [
                    {
                        "kind": "EMIT_FAILURE",
                        "failure_code": "HEAVY_SUPPORT_UNAVAILABLE",
                        "message": "Heavy engineering support is unavailable.",
                        "retryable": True,
                    }
                ],
            },
            {
                "key": "heavy_support_resolution",
                "phase": "RESOLVE",
                "action_key": "deploy_heavy_engineering_support",
                "priority": 0,
                "effects": [
                    {
                        "kind": "SET_FACT",
                        "node": {"kind": "CURRENT_TARGET"},
                        "fact_key": "heavy_engineering_support_ready",
                        "value": {"source": "LITERAL", "literal": True},
                    },
                    {"kind": "EMIT_OUTCOME", "outcome_code": "SUPPORT_DEPLOYED"},
                ],
            },
            {
                "key": "specialist_support_required",
                "phase": "PREFLIGHT",
                "action_key": "repair_supported_facility",
                "priority": 100,
                "condition": _condition(
                    "FACT_NOT_EQUALS",
                    node={"kind": "CURRENT_TARGET"},
                    fact_key="heavy_engineering_support_ready",
                    value=True,
                ),
                "effects": [
                    {
                        "kind": "EMIT_FAILURE",
                        "failure_code": "HEAVY_SUPPORT_REQUIRED",
                        "message": "Deploy heavy engineering support first.",
                        "retryable": True,
                    }
                ],
            },
            {
                "key": "specialist_repair_resolution",
                "phase": "RESOLVE",
                "action_key": "repair_supported_facility",
                "priority": 0,
                "effects": [{"kind": "EMIT_OUTCOME", "outcome_code": "SPECIALIST_REPAIRED"}],
            },
        ]
    )
    objective = document["objectives"][0]
    objective["completion_requirements"][0]["node_key"] = "central_fire_rescue_station"
    objective["completion_requirements"][0]["fact_key"] = "power_supply"
    objective["completion_requirements"][0]["accepted_values"] = ["AVAILABLE"]
    objective["completion_requirements"][0]["description"] = "The facility has power."
    return ScenarioDefinitionV2.model_validate(document)


class _CapabilityProvider:
    model_name = "capability-test-provider"

    def __init__(self, steps: tuple[PlanStepProposal, ...]) -> None:
        self.steps = steps
        self.requests: list[PlanRequest] = []

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.requests.append(request)
        return PlanProposal(plan_summary="capability test", steps=self.steps)


def _runtime(
    session: Session,
    creation_key: str,
    *,
    steps: tuple[PlanStepProposal, ...] = (),
    use_platform_player: bool = False,
) -> tuple[GenericAgentService, object, ScenarioDefinitionV2]:
    definition = _definition()
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    if use_platform_player:
        player = GameLifecycleService(session).platform_player()
    else:
        player = Player(name=creation_key)
        session.add(player)
        session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=creation_key,
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    provider = _CapabilityProvider(steps) if steps else None
    return GenericAgentService(session, scope, provider=provider), runtime, definition


def _session_for_agent(session: Session, runtime: object):
    return runtime.session


def test_supply_power_uses_declared_role_relation_and_fact_effects(session: Session) -> None:
    steps = (
        PlanStepProposal(
            action_key="supply_power",
            actor_key="electrical_team_beta",
            target_key="central_telecom_hub",
            parameters={"source_key": "central_hospital"},
        ),
        PlanStepProposal(
            action_key="supply_power",
            actor_key="electrical_team_beta",
            target_key="central_fire_rescue_station",
            parameters={"source_key": "central_telecom_hub"},
        ),
    )
    agent, runtime, definition = _runtime(
        session,
        "generic-power",
        steps=steps,
        use_platform_player=True,
    )
    task = agent.create_task(
        _session_for_agent(session, runtime),
        "restore central hospital emergency power",
    )

    assert task.status == AgentTaskStatus.ACTIVE
    relation_projection = SharedKnowledgeProjection(
        session,
        agent.scope,
        definition,
    ).known_relations()
    assert {
        (item["source_node_key"], item["target_node_key"])
        for item in relation_projection
        if item["relation_type_key"] == "supplies_power_to"
    } == {
        ("central_hospital", "central_telecom_hub"),
        ("central_telecom_hub", "central_fire_rescue_station"),
        ("heavy_equipment_yard", "central_fire_rescue_station"),
    }
    action_requirements = {
        item["action_key"]: item
        for item in SharedKnowledgeProjection(
            session,
            agent.scope,
            definition,
        ).known_action_requirements()
    }
    assert action_requirements["repair_electrical"]["required_actor_role_key"] == (
        "electrical_response_team"
    )
    assert action_requirements["supply_power"]["source_relation_type_key"] == ("supplies_power_to")
    assert any(
        item["node_key"] == "central_hospital" and item["fact_key"] == "power_supply"
        for item in action_requirements["supply_power"]["known_preconditions"]
    )
    assert any(
        item["node_key"] == "heavy_equipment_yard"
        and item["fact_key"] == "heavy_engineering_support"
        for item in action_requirements["deploy_heavy_engineering_support"]["known_preconditions"]
    )
    player_state = PlayerProjectionService(session).game_state(GameInstanceId(runtime.instance.id))
    assert {item.action_key for item in player_state.known_action_requirements} >= {
        "repair_electrical",
        "supply_power",
        "deploy_heavy_engineering_support",
    }
    context = PlanningContextBuilder(session, agent.scope).build(
        definition,
        (definition.objectives[0],),
        task=task,
        replan_reason=None,
    )
    supply_context = next(
        item for item in context.relevant_actions if item["action_key"] == "supply_power"
    )
    assert supply_context["target_requirements"]["source_relation_type_key"] == "supplies_power_to"
    assert context.current_knowledge["known_action_requirements"] == list(
        SharedKnowledgeProjection(session, agent.scope, definition).planner_action_requirements()
    )

    first = agent.execute_next(task)
    second = agent.execute_next(task)
    assert first is not None and second is not None
    target = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "central_telecom_hub", "power_supply"),
    )
    downstream = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "central_fire_rescue_station", "power_supply"),
    )
    assert target is not None and target.truth_value == "AVAILABLE"
    assert downstream is not None and downstream.truth_value == "AVAILABLE"


def test_supply_power_generation_qualification_is_declarative(session: Session) -> None:
    steps = (
        PlanStepProposal(
            action_key="supply_power",
            actor_key="electrical_team_beta",
            target_key="central_fire_rescue_station",
            parameters={"source_key": "heavy_equipment_yard"},
        ),
    )
    agent, runtime, _definition_value = _runtime(session, "generic-power-generator", steps=steps)
    task = agent.create_task(
        _session_for_agent(session, runtime), "restore central hospital emergency power"
    )

    assert task.status == AgentTaskStatus.ACTIVE
    result = agent.execute_next(task)
    assert result is not None
    target = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "central_fire_rescue_station", "power_supply"),
    )
    assert target is not None and target.truth_value == "AVAILABLE"


def test_supply_power_unknown_requirement_is_not_rejected_but_runtime_enforces_truth(
    session: Session,
) -> None:
    steps = (
        PlanStepProposal(
            action_key="supply_power",
            actor_key="electrical_team_beta",
            target_key="central_telecom_hub",
            parameters={"source_key": "central_hospital"},
        ),
    )
    agent, runtime, _definition_value = _runtime(session, "generic-power-unknown", steps=steps)
    source = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "central_hospital", "power_supply"),
    )
    assert source is not None
    source.visibility = "HIDDEN"
    source.truth_value = "UNAVAILABLE"
    session.flush()

    hidden_requirements = {
        item["action_key"]: item
        for item in SharedKnowledgeProjection(
            session,
            agent.scope,
            _definition_value,
        ).known_action_requirements()
    }
    assert not any(
        item["node_key"] == "central_hospital" and item["fact_key"] == "power_supply"
        for item in hidden_requirements["supply_power"]["known_preconditions"]
    )

    task = agent.create_task(
        _session_for_agent(session, runtime), "restore central hospital emergency power"
    )
    assert task.status == AgentTaskStatus.ACTIVE
    with pytest.raises(GenericActionError) as error:
        GenericActionService(session, agent.scope).execute_action(
            actor_key="electrical_team_beta",
            action_key="supply_power",
            target_key="central_telecom_hub",
            parameters={"source_key": "central_hospital"},
            idempotency_key="hidden-power-source",
        )
    assert error.value.code == "POWER_SOURCE_UNAVAILABLE"


def test_heavy_support_and_specialist_repair_are_sequential_and_role_restricted(
    session: Session,
) -> None:
    steps = (
        PlanStepProposal(
            action_key="deploy_heavy_engineering_support",
            actor_key="industrial_team",
            target_key="central_fire_rescue_station",
        ),
        PlanStepProposal(
            action_key="repair_supported_facility",
            actor_key="water_team",
            target_key="central_fire_rescue_station",
        ),
    )
    agent, runtime, _definition_value = _runtime(session, "generic-heavy", steps=steps)
    task = agent.create_task(
        _session_for_agent(session, runtime), "restore central hospital emergency power"
    )

    first = agent.execute_next(task)
    second = agent.execute_next(task)
    assert first is not None and second is not None
    support = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "central_fire_rescue_station", "heavy_engineering_support_ready"),
    )
    assert support is not None and support.truth_value is True

    with pytest.raises(GenericActionError) as error:
        GenericActionService(session, agent.scope).execute_action(
            actor_key="industrial_team",
            action_key="repair_supported_facility",
            target_key="central_fire_rescue_station",
            parameters={},
            idempotency_key="wrong-specialist-role",
        )
    assert error.value.code == "ACTOR_ROLE_MISSING"


def test_heavy_support_accepts_transport_corridor_target_with_generic_locality(
    session: Session,
) -> None:
    agent, runtime, _definition_value = _runtime(session, "generic-heavy-corridor")
    result = GenericActionService(session, agent.scope).execute_action(
        actor_key="industrial_team",
        action_key="deploy_heavy_engineering_support",
        target_key="west_freight_corridor",
        parameters={},
        idempotency_key="heavy-support-corridor",
    )

    assert result.applied is not None
    assert result.applied.outcome.failure is None
    support = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "west_freight_corridor", "heavy_engineering_support_ready"),
    )
    assert support is not None and support.truth_value is True


def test_repair_actor_requirement_is_generic_not_resource_derived(session: Session) -> None:
    agent, runtime, _definition_value = _runtime(session, "generic-repair-role")
    with pytest.raises(GenericActionError) as error:
        GenericActionService(session, agent.scope).execute_action(
            actor_key="industrial_team",
            action_key="repair_supported_facility",
            target_key="central_fire_rescue_station",
            parameters={},
            idempotency_key="wrong-repair-role",
        )
    assert error.value.code == "ACTOR_ROLE_MISSING"

    actor = session.get(GameInstanceActor, (runtime.instance.id, "water_team"))
    assert actor is not None and actor.role_key == "water_repair_team"


def test_validator_rejects_known_repair_role_mismatch(session: Session) -> None:
    steps = (
        PlanStepProposal(
            action_key="repair_electrical",
            actor_key="industrial_team",
            target_key="central_hospital",
        ),
    )
    agent, runtime, _definition_value = _runtime(session, "generic-repair-validator", steps=steps)

    with pytest.raises(GenericAgentError) as error:
        agent.create_task(
            _session_for_agent(session, runtime),
            "restore central hospital emergency power",
        )

    assert error.value.code == "MODEL_PLAN_REJECTED"
    assert isinstance(agent.provider, _CapabilityProvider)
    diagnostic = agent.provider.requests[1].repair_diagnostics[0]
    assert diagnostic.code == "ACTOR_ROLE_MISSING"
    assert diagnostic.failure_code == "ACTOR_ROLE_MISSING"
    assert diagnostic.dimension == "ACTOR_ROLE"
    assert diagnostic.action_key == "repair_electrical"
    assert diagnostic.actor_key == "industrial_team"
    assert diagnostic.target_key == "central_hospital"
    assert diagnostic.required == "electrical_response_team"
    assert diagnostic.actual == "industrial_support_team"


def test_validator_rejects_unknown_power_relation_without_pathfinding(session: Session) -> None:
    steps = (
        PlanStepProposal(
            action_key="supply_power",
            actor_key="electrical_team_beta",
            target_key="central_fire_rescue_station",
            parameters={"source_key": "central_hospital"},
        ),
    )
    agent, runtime, _definition_value = _runtime(session, "generic-power-relation", steps=steps)

    with pytest.raises(GenericAgentError) as error:
        agent.create_task(
            _session_for_agent(session, runtime),
            "restore central hospital emergency power",
        )

    assert error.value.code == "MODEL_PLAN_REJECTED"
    assert isinstance(agent.provider, _CapabilityProvider)
    diagnostic = agent.provider.requests[1].repair_diagnostics[0]
    assert diagnostic.code == "SUPPLY_POWER_REQUIREMENT_UNKNOWN"
    assert diagnostic.failure_code == "SUPPLY_POWER_RELATION_UNKNOWN"
    assert diagnostic.dimension == "POWER_SOURCE_REQUIREMENT"
    assert diagnostic.action_key == "supply_power"
    assert diagnostic.actor_key == "electrical_team_beta"
    assert diagnostic.target_key == "central_fire_rescue_station"
    assert diagnostic.required == "KNOWN_DIRECT_RELATION"
    assert diagnostic.actual == "UNKNOWN"
    assert diagnostic.known_predicate == {
        "node_key": "central_hospital",
        "relation_type": "supplies_power_to",
        "target_key": "central_fire_rescue_station",
        "operator": "EXISTS",
        "expected": True,
        "actual": False,
    }
