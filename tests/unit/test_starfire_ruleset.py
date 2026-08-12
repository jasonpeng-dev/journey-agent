from dataclasses import replace
from types import MappingProxyType

import pytest

from app.domain.world import RelationType
from app.scenarios.starfire.definition import STARFIRE_WORLD
from app.scenarios.starfire.ruleset import (
    StarfireFactState,
    StarfireResources,
    StarfireRuleset,
    StarfireRuleState,
    StarfireRuleViolation,
)


def test_reconnaissance_outcome_is_pure_and_canonical() -> None:
    ruleset = StarfireRuleset()

    outcome = ruleset.resolve_reconnaissance("northern_valley")

    assert dict(outcome.payload) == {
        "result": "PARTIAL_SUCCESS",
        "facts_discovered": ["valley_intelligence"],
        "casualties": 0,
    }
    assert outcome.fact_updates[0].node_key == "northern_valley"
    assert outcome.fact_updates[0].fact_key == "valley_intelligence"
    assert dict(outcome.fact_updates[0].value) == {"status": "PARTIAL"}
    assert outcome.unlock_node_keys == ("northern_valley",)


def test_village_support_decision_preserves_cost_and_minimum_offer_behavior() -> None:
    ruleset = StarfireRuleset()

    low_offer = ruleset.negotiate_village_support(_state(food=100), 10, "GUIDE")
    full_offer = ruleset.negotiate_village_support(_state(food=100), 20, "GUIDE")

    assert low_offer.food_delta == -10
    assert low_offer.payload["village_support"] == "INTELLIGENCE"
    assert full_offer.food_delta == -20
    assert full_offer.payload["village_support"] == "GUIDE"


def test_active_supply_support_relation_drives_first_clear_defeat() -> None:
    ruleset = StarfireRuleset()

    outcome = ruleset.resolve_clear_threat(
        "northern_valley",
        "CLEAR_VALLEY",
        _state(supply_status="ACTIVE"),
    )

    assert outcome.payload["result"] == "DEFEAT"
    assert outcome.payload["failure_code"] == "ENCOUNTER_DEFEAT"
    assert outcome.casualties == 18
    assert outcome.morale_delta == -10
    assert outcome.unlock_node_keys == ("enemy_north_supply_route",)
    assert [(update.node_key, update.fact_key) for update in outcome.fact_updates] == [
        ("enemy_north_supply_route", "supply_status"),
        ("northern_valley", "valley_intelligence"),
    ]


def test_missing_reveals_relation_fails_closed() -> None:
    world = replace(
        STARFIRE_WORLD,
        relations=tuple(
            relation
            for relation in STARFIRE_WORLD.relations
            if relation.relation_type != RelationType.REVEALS
        ),
    )
    ruleset = StarfireRuleset(world)

    with pytest.raises(StarfireRuleViolation) as exc_info:
        ruleset.resolve_clear_threat(
            "northern_valley",
            "CLEAR_VALLEY",
            _state(supply_status="ACTIVE"),
        )

    assert exc_info.value.code == "STARFIRE_RELATION_INVALID"


def test_disrupted_supply_and_village_support_drive_second_clear_success() -> None:
    ruleset = StarfireRuleset()

    guided = ruleset.resolve_clear_threat(
        "northern_valley",
        "CLEAR_VALLEY",
        _state(supply_status="DISRUPTED", village_support="GUIDE"),
    )
    unguided = ruleset.resolve_clear_threat(
        "northern_valley",
        "CLEAR_VALLEY",
        _state(supply_status="DISRUPTED", village_support="NONE"),
    )

    assert guided.payload["result"] == "VICTORY"
    assert guided.casualties == 3
    assert unguided.casualties == 6
    assert guided.morale_delta == 5
    assert guided.unlock_node_keys == ("starfire_outpost",)
    assert dict(guided.fact_updates[0].value) == {"status": "SAFE"}


def test_disrupt_supply_uses_shared_valley_support_relations() -> None:
    ruleset = StarfireRuleset()

    guided = ruleset.resolve_disrupt_supply(
        "enemy_north_supply_route",
        _state(village_support="GUIDE"),
    )
    unguided = ruleset.resolve_disrupt_supply(
        "enemy_north_supply_route",
        _state(village_support="NONE"),
    )

    assert guided.casualties == 2
    assert unguided.casualties == 4
    assert guided.morale_delta == 3
    assert dict(guided.fact_updates[0].value) == {"status": "DISRUPTED"}


def test_repair_uses_unlock_relation_for_valley_prerequisite() -> None:
    ruleset = StarfireRuleset()

    with pytest.raises(StarfireRuleViolation) as unsafe_error:
        ruleset.validate_repair(
            "starfire_outpost",
            "TEMPORARY",
            20,
            15,
            _state(valley_security="UNSAFE"),
        )
    assert unsafe_error.value.code == "VALLEY_UNSAFE"

    prepared = ruleset.prepare_repair(
        "starfire_outpost",
        "TEMPORARY",
        20,
        15,
        _state(valley_security="SAFE"),
    )
    resolved = ruleset.resolve_repair(
        "starfire_outpost",
        "TEMPORARY",
        _state(valley_security="SAFE"),
    )

    assert (prepared.food_delta, prepared.gold_delta) == (-20, -15)
    assert resolved.payload["outpost_status"] == "OPERATIONAL"
    assert resolved.unlock_node_keys == (
        "starfire_outpost",
        "northern_trade_route",
    )


def test_trade_uses_enables_and_supports_relations() -> None:
    ruleset = StarfireRuleset()
    invalid = ruleset.resolve_trade_route_test(
        "northern_trade_route",
        _state(
            valley_security="UNSAFE",
            outpost_status="DAMAGED",
            village_support="NONE",
        ),
    )
    valid = ruleset.resolve_trade_route_test(
        "northern_trade_route",
        _state(
            valley_security="SAFE",
            outpost_status="OPERATIONAL",
            village_support="GUIDE",
        ),
    )

    assert invalid.payload["invalidated_prerequisites"] == [
        "valley_security",
        "starfire_outpost_status",
        "village_support",
    ]
    assert valid.payload["result"] == "COMPLETED"
    assert valid.unlock_node_keys == ("northern_trade_route",)
    assert dict(valid.fact_updates[0].value) == {"status": "OPEN"}


def test_missing_trade_enable_relation_fails_closed() -> None:
    world = replace(
        STARFIRE_WORLD,
        relations=tuple(
            relation
            for relation in STARFIRE_WORLD.relations
            if not (
                relation.relation_type == RelationType.ENABLES
                and relation.source_node_key == "starfire_outpost"
            )
        ),
    )
    ruleset = StarfireRuleset(world)

    with pytest.raises(StarfireRuleViolation) as exc_info:
        ruleset.resolve_trade_route_test(
            "northern_trade_route",
            _state(
                valley_security="SAFE",
                outpost_status="OPERATIONAL",
                village_support="GUIDE",
            ),
        )

    assert exc_info.value.code == "STARFIRE_RELATION_INVALID"


def _state(
    *,
    supply_status: str = "ACTIVE",
    village_support: str = "NONE",
    valley_security: str = "UNSAFE",
    outpost_status: str = "DAMAGED",
    food: int = 100,
    gold: int = 80,
) -> StarfireRuleState:
    return StarfireRuleState(
        facts=MappingProxyType(
            {
                ("north_village", "village_support"): StarfireFactState(village_support),
                ("northern_valley", "valley_intelligence"): StarfireFactState("INCOMPLETE"),
                ("northern_valley", "valley_security"): StarfireFactState(valley_security),
                ("northern_valley", "ambush_status"): StarfireFactState("ACTIVE"),
                ("enemy_north_supply_route", "supply_status"): StarfireFactState(supply_status),
                ("starfire_outpost", "outpost_status"): StarfireFactState(outpost_status),
                ("northern_trade_route", "trade_route_status"): StarfireFactState("CLOSED"),
            }
        ),
        resources=StarfireResources(
            soldiers_available=300,
            food=food,
            gold=gold,
            morale=60,
        ),
    )
