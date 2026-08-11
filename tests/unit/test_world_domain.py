from dataclasses import FrozenInstanceError

import pytest

from app.domain.world import (
    AccessState,
    FactDefinition,
    FactValueType,
    InteractionDefinition,
    NodeDefinition,
    RelationDefinition,
    RelationType,
    ResourceDefinition,
    Visibility,
    WorldNodeType,
)


def test_node_definition_composes_capabilities_facts_and_initial_boundaries() -> None:
    reconnaissance = InteractionDefinition(
        key="reconnaissance",
        name="Reconnaissance",
    )
    ambush = FactDefinition(
        key="ambush_status",
        name="Ambush status",
        value_type=FactValueType.ENUM,
        initial_value="ACTIVE",
        initial_visibility=Visibility.HIDDEN,
        allowed_values=("ACTIVE", "CLEARED"),
    )

    valley = NodeDefinition(
        key="northern_valley",
        name="Northern Valley",
        description="A strategic route to Starfire Outpost.",
        node_type=WorldNodeType.LOCATION,
        initial_access=AccessState.AVAILABLE,
        initial_visibility=Visibility.KNOWN,
        interactions=(reconnaissance,),
        facts=(ambush,),
    )

    assert valley.supports("reconnaissance")
    assert not valley.supports("repair")
    assert valley.fact("ambush_status") == ambush
    assert valley.fact("missing") is None
    ambush_fact = valley.fact("ambush_status")
    assert ambush_fact is not None
    assert ambush_fact.initial_visibility == Visibility.HIDDEN


def test_world_definitions_are_immutable_value_objects() -> None:
    relation = RelationDefinition(
        source_node_key="northern_valley",
        relation_type=RelationType.UNLOCKS,
        target_node_key="starfire_outpost",
    )
    same_relation = RelationDefinition(
        source_node_key="northern_valley",
        relation_type=RelationType.UNLOCKS,
        target_node_key="starfire_outpost",
    )

    assert relation == same_relation
    assert hash(relation) == hash(same_relation)
    with pytest.raises(FrozenInstanceError):
        relation.__setattr__("target_node_key", "other_outpost")


@pytest.mark.parametrize(
    "value_type, initial_value",
    [
        (FactValueType.STRING, "DAMAGED"),
        (FactValueType.INTEGER, 100),
        (FactValueType.BOOLEAN, True),
    ],
)
def test_scalar_fact_types_accept_matching_values(
    value_type: FactValueType,
    initial_value: str | int | bool,
) -> None:
    fact = FactDefinition(
        key="example_fact",
        name="Example fact",
        value_type=value_type,
        initial_value=initial_value,
    )

    assert fact.initial_value == initial_value


def test_fact_definition_rejects_invalid_enum_configuration() -> None:
    with pytest.raises(ValueError, match="declared value_type"):
        FactDefinition(
            key="supply_status",
            name="Supply status",
            value_type=FactValueType.ENUM,
            initial_value="UNKNOWN",
            allowed_values=("ACTIVE", "DISRUPTED"),
        )

    with pytest.raises(ValueError, match="one scalar type"):
        FactDefinition(
            key="supply_status",
            name="Supply status",
            value_type=FactValueType.ENUM,
            initial_value="ACTIVE",
            allowed_values=("ACTIVE", 1),
        )


def test_node_definition_rejects_duplicate_capability_and_fact_keys() -> None:
    interaction = InteractionDefinition(key="repair", name="Repair")
    fact = FactDefinition(
        key="outpost_status",
        name="Outpost status",
        value_type=FactValueType.ENUM,
        initial_value="DAMAGED",
        allowed_values=("DAMAGED", "OPERATIONAL"),
    )

    with pytest.raises(ValueError, match="interactions"):
        NodeDefinition(
            key="starfire_outpost",
            name="Starfire Outpost",
            description="A damaged northern facility.",
            node_type=WorldNodeType.FACILITY,
            initial_access=AccessState.LOCKED,
            initial_visibility=Visibility.KNOWN,
            interactions=(interaction, interaction),
        )
    with pytest.raises(ValueError, match="facts"):
        NodeDefinition(
            key="starfire_outpost",
            name="Starfire Outpost",
            description="A damaged northern facility.",
            node_type=WorldNodeType.FACILITY,
            initial_access=AccessState.LOCKED,
            initial_visibility=Visibility.KNOWN,
            facts=(fact, fact),
        )


def test_domain_keys_are_stable_machine_identifiers() -> None:
    with pytest.raises(ValueError, match="lowercase"):
        InteractionDefinition(key="Clear Threat", name="Clear Threat")
    with pytest.raises(ValueError, match="lowercase"):
        RelationDefinition(
            source_node_key="NorthernValley",
            relation_type=RelationType.SUPPORTS,
            target_node_key="starfire_outpost",
        )


def test_resource_definition_enforces_numeric_boundaries() -> None:
    morale = ResourceDefinition(
        key="morale",
        name="Morale",
        initial_value=60,
        minimum=0,
        maximum=100,
    )

    assert morale.initial_value == 60
    with pytest.raises(ValueError, match="less than or equal"):
        ResourceDefinition(
            key="morale",
            name="Morale",
            initial_value=120,
            minimum=0,
            maximum=100,
        )
