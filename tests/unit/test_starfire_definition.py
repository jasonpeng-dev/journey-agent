import pytest

from app.domain.world import AccessState, RelationType, Visibility, WorldNodeType
from app.scenarios.starfire.compatibility import (
    LEGACY_FACT_REFS,
    canonical_node_key,
    initial_legacy_world_facts,
    initial_resource_values,
    project_legacy_supply_status,
)
from app.scenarios.starfire.definition import STARFIRE_WORLD


def test_starfire_definition_contains_the_canonical_world() -> None:
    assert [node.key for node in STARFIRE_WORLD.nodes] == [
        "capital_council",
        "north_village",
        "northern_valley",
        "enemy_north_supply_route",
        "starfire_outpost",
        "northern_trade_route",
    ]
    assert {interaction.name for interaction in STARFIRE_WORLD.interactions} == {
        "NEGOTIATE_SUPPORT",
        "RECONNAISSANCE",
        "CLEAR_THREAT",
        "DISRUPT_SUPPLY",
        "REPAIR",
        "TEST_TRADE_ROUTE",
    }


def test_starfire_nodes_expose_type_access_visibility_and_interactions() -> None:
    expected = {
        "capital_council": (
            WorldNodeType.HEADQUARTERS,
            AccessState.AVAILABLE,
            Visibility.KNOWN,
            set(),
        ),
        "north_village": (
            WorldNodeType.SETTLEMENT,
            AccessState.AVAILABLE,
            Visibility.KNOWN,
            {"negotiate_support"},
        ),
        "northern_valley": (
            WorldNodeType.LOCATION,
            AccessState.AVAILABLE,
            Visibility.KNOWN,
            {"reconnaissance", "clear_threat"},
        ),
        "enemy_north_supply_route": (
            WorldNodeType.ROUTE,
            AccessState.LOCKED,
            Visibility.HIDDEN,
            {"disrupt_supply"},
        ),
        "starfire_outpost": (
            WorldNodeType.FACILITY,
            AccessState.LOCKED,
            Visibility.KNOWN,
            {"repair"},
        ),
        "northern_trade_route": (
            WorldNodeType.ROUTE,
            AccessState.LOCKED,
            Visibility.KNOWN,
            {"test_trade_route"},
        ),
    }

    for key, values in expected.items():
        node = STARFIRE_WORLD.node(key)
        assert node is not None
        node_type, access, visibility, interactions = values
        assert node.node_type == node_type
        assert node.initial_access == access
        assert node.initial_visibility == visibility
        assert {interaction.key for interaction in node.interactions} == interactions


def test_starfire_truth_and_visibility_are_not_conflated() -> None:
    valley = STARFIRE_WORLD.node("northern_valley")
    supply_route = STARFIRE_WORLD.node("enemy_north_supply_route")
    assert valley is not None and supply_route is not None
    ambush = valley.fact("ambush_status")
    supply = supply_route.fact("supply_status")
    assert ambush is not None and supply is not None

    assert ambush.initial_value == "ACTIVE"
    assert ambush.initial_visibility == Visibility.HIDDEN
    assert supply.initial_value == "ACTIVE"
    assert supply.initial_visibility == Visibility.HIDDEN
    assert "UNKNOWN" not in supply.allowed_values

    assert project_legacy_supply_status("UNKNOWN").truth_status == "ACTIVE"
    assert project_legacy_supply_status("UNKNOWN").known is False
    assert project_legacy_supply_status("ACTIVE").known is True
    assert project_legacy_supply_status("DISRUPTED").truth_status == "DISRUPTED"
    with pytest.raises(ValueError, match="Unsupported legacy"):
        project_legacy_supply_status("MISSING")


def test_starfire_relations_are_semantic_links_without_rule_payloads() -> None:
    relations = {
        (relation.source_node_key, relation.relation_type, relation.target_node_key)
        for relation in STARFIRE_WORLD.relations
    }

    assert relations == {
        ("north_village", RelationType.SUPPORTS, "northern_valley"),
        ("north_village", RelationType.SUPPORTS, "northern_trade_route"),
        ("enemy_north_supply_route", RelationType.SUPPORTS, "northern_valley"),
        ("northern_valley", RelationType.REVEALS, "enemy_north_supply_route"),
        ("northern_valley", RelationType.UNLOCKS, "starfire_outpost"),
        ("northern_valley", RelationType.ENABLES, "northern_trade_route"),
        ("starfire_outpost", RelationType.ENABLES, "northern_trade_route"),
    }


def test_starfire_resources_preserve_current_gameplay_values() -> None:
    assert initial_resource_values() == {
        "soldiers": 300,
        "food": 100,
        "gold": 80,
        "morale": 60,
    }


def test_legacy_compatibility_preserves_old_keys_at_the_boundary() -> None:
    assert canonical_node_key("valley_entrance") == "northern_valley"
    assert canonical_node_key("ambush_valley") == "northern_valley"
    assert canonical_node_key("starfire_outpost") == "starfire_outpost"
    assert LEGACY_FACT_REFS["enemy_supply_route"].node_key == "enemy_north_supply_route"
    assert LEGACY_FACT_REFS["enemy_supply_route"].fact_key == "supply_status"
    assert initial_legacy_world_facts()["enemy_supply_route"] == {"status": "UNKNOWN"}
