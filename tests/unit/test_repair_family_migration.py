from typing import Any, cast

from app.agent.planner_contract import (
    action_planner_constraints,
    action_planner_effects,
    planner_target_contracts,
)
from app.domain.action_invocation import canonical_action_invocation_contract
from app.domain.scenario_v2 import ActionDefinitionV2
from tests.repair_parity import (
    EXPECTED_REPAIR_TARGET_ROLES,
    LEGACY_FACILITY_FACT_SEMANTIC_HASH,
    LEGACY_INITIAL_FACT_SEMANTIC_HASH,
    LEGACY_POWER_TOPOLOGY_SEMANTIC_HASH,
    LEGACY_REPAIR_ACTION_KEYS,
    LEGACY_REPAIR_RULE_COUNT,
    LEGACY_REPAIR_RULE_SEMANTIC_HASH,
    facility_fact_semantic_hash,
    initial_fact_semantic_hash,
    power_topology_semantic_hash,
    repair_rule_semantic_hash,
)
from tests.scenario_fixtures import LINJIANG_V2_TEST


def _repair_action() -> ActionDefinitionV2:
    return next(action for action in LINJIANG_V2_TEST.actions if action.key == "repair_facility")


def test_linjiang_has_one_node_typed_facility_repair_action() -> None:
    action = _repair_action()
    repair_keys = {action.key for action in LINJIANG_V2_TEST.actions if "repair" in action.key}

    assert repair_keys == {"repair_facility"}
    assert action.required_interaction_key == "repairable"
    assert action.execution_mode.value == "IMMEDIATE"
    assert action.locality.value == "FACILITY_REGION"
    assert [item.value for item in action.allowed_actor_capabilities] == ["EXECUTE_ACTION"]
    assert action.target_semantic_reference_type is not None
    assert action.target_semantic_reference_type.value == "NODE"
    assert action.target_node_type_keys == ("facility",)
    assert action.required_actor_role_key is None
    assert action.planning.terminal_effects == ()
    assert [
        (effect.fact_key, effect.value) for effect in action.planning.target_terminal_effects
    ] == [("operational", True)]

    contract = canonical_action_invocation_contract(action)
    target_contract = cast(dict[str, Any], contract["target"])
    assert target_contract["semantic_reference_type"] == "NODE"
    assert target_contract["node_type_keys"] == ["facility"]


def test_linjiang_facility_authoring_invariant_is_complete() -> None:
    facilities = [
        node for node in LINJIANG_V2_TEST.world.nodes if node.node_type_key == "facility"
    ]

    assert len(facilities) == 29
    for facility in facilities:
        fact_keys = {fact.key for fact in facility.facts}
        assert {"operational", "power_supply"}.issubset(fact_keys), facility.key
        assert {"repairable", "power_targetable"}.issubset(
            facility.interaction_keys
        ), facility.key


def test_linjiang_repair_migration_preserves_initial_state_and_power_topology() -> None:
    assert facility_fact_semantic_hash(LINJIANG_V2_TEST) == LEGACY_FACILITY_FACT_SEMANTIC_HASH
    assert initial_fact_semantic_hash(LINJIANG_V2_TEST) == LEGACY_INITIAL_FACT_SEMANTIC_HASH
    assert power_topology_semantic_hash(LINJIANG_V2_TEST) == LEGACY_POWER_TOPOLOGY_SEMANTIC_HASH
    assert sum(
        relation.relation_type_key == "supplies_power_to"
        for relation in LINJIANG_V2_TEST.world.relations
    ) == 7


def test_linjiang_repair_specialization_and_rule_semantics_are_preserved() -> None:
    action = _repair_action()
    assert {
        item.target_key: item.required_actor_role_key for item in action.target_actor_roles
    } == EXPECTED_REPAIR_TARGET_ROLES
    assert repair_rule_semantic_hash(LINJIANG_V2_TEST) == (
        LEGACY_REPAIR_RULE_COUNT,
        LEGACY_REPAIR_RULE_SEMANTIC_HASH,
    )


def test_linjiang_newly_admitted_facilities_have_no_invented_role_or_cost() -> None:
    action = _repair_action()
    role_targets = {item.target_key for item in action.target_actor_roles}
    unconstrained = {
        "central_hospital",
        "east_telecom_station",
        "emergency_command_center",
        "north_power_substation",
        "southeast_shelter",
        "southeast_telecom_relay",
        "west_communication_relay",
    }

    assert unconstrained.isdisjoint(role_targets)
    for target_key in unconstrained:
        assert action.required_actor_role_for_target(target_key) is None

    base = next(
        rule
        for rule in LINJIANG_V2_TEST.rules
        if rule.key == "repair_facility_base_resolution"
    )
    assert base.priority == -100
    assert not any(effect.kind.value == "ADJUST_RESOURCE" for effect in base.effects)


def test_linjiang_catalog_and_rules_have_no_legacy_repair_action_identity() -> None:
    assert LEGACY_REPAIR_ACTION_KEYS.isdisjoint(action.key for action in LINJIANG_V2_TEST.actions)
    assert LEGACY_REPAIR_ACTION_KEYS.isdisjoint(
        actor_action
        for actor in LINJIANG_V2_TEST.actors.actor_profiles
        for actor_action in actor.allowed_action_keys
    )
    assert {rule.action_key for rule in LINJIANG_V2_TEST.rules}.isdisjoint(
        LEGACY_REPAIR_ACTION_KEYS
    )
    assert not any(
        rule.key.endswith("target_profile_required")
        and rule.action_key == "repair_facility"
        for rule in LINJIANG_V2_TEST.rules
    )


def test_repair_planner_contract_is_target_relative_and_reads_target_roles() -> None:
    action = _repair_action()
    constraints = action_planner_constraints(action)
    effects = action_planner_effects(action)
    known_nodes = {node.key for node in LINJIANG_V2_TEST.world.nodes}
    known_facts = {
        (node.key, fact.key): fact.initial_value
        for node in LINJIANG_V2_TEST.world.nodes
        for fact in node.facts
    }
    target_contracts = planner_target_contracts(
        LINJIANG_V2_TEST,
        action,
        known_node_keys=known_nodes,
        known_facts=known_facts,
    )

    target_constraints = cast(dict[str, object], constraints["target"])
    executor_constraints = cast(dict[str, object], constraints["executor"])
    assert target_constraints["node_type_keys"] == ["facility"]
    assert len(cast(list[object], executor_constraints["target_role_requirements"])) == 22
    assert effects == [
        {
            "type": "FACT_MUTATION",
            "target": "target_key",
            "fact_key": "operational",
            "value": True,
        },
        {"type": "NO_IMPLIED_FACT_MUTATION", "fact_key": "power_supply"},
    ]
    assert set(target_contracts) == {
        node.key
        for node in LINJIANG_V2_TEST.world.nodes
        if node.node_type_key == "facility"
    }
    assert target_contracts["east_telecom_station"] == {
        "effects": [
            {
                "type": "FACT_MUTATION",
                "target": "target_key",
                "fact_key": "operational",
                "value": True,
            }
        ]
    }
    central_effects = cast(
        list[dict[str, object]],
        target_contracts["central_telecom_hub"]["effects"],
    )
    assert any(effect.get("type") == "ACTOR_COMMAND_REACHABILITY" for effect in central_effects)
    assert any(
        effect.get("target") == "TARGET_REGION_FACILITIES" for effect in central_effects
    )
