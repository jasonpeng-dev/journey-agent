from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V1


def test_linjiang_v1_preserves_topology_and_adds_gameplay() -> None:
    definition = LINJIANG_INFRASTRUCTURE_RECOVERY_V1
    nodes_by_type = {
        node_type.key: {
            node.key for node in definition.world.nodes if node.node_type_key == node_type.key
        }
        for node_type in definition.world.node_types
    }
    assert definition.metadata.key == "linjiang_infrastructure_recovery"
    assert definition.metadata.name == "临江市灾后基础设施恢复"
    assert len(nodes_by_type["region"]) == 6
    assert len(nodes_by_type["facility"]) == 24
    assert len(nodes_by_type["transport"]) == 6
    assert len({node.key for node in definition.world.nodes}) == 36
    assert definition.metadata.locality.enabled
    assert len(definition.world.resources) == 1
    assert definition.world.resources[0].key == "electrical_repair_parts"
    assert len(definition.initialization.resource_initial_states) == 1
    assert definition.initialization.resource_initial_states[0].scope_node_key == (
        "west_logistics_district"
    )
    transport_action = next(
        action for action in definition.actions if action.key == "transport_resource"
    )
    assert {parameter.key for parameter in transport_action.parameters} == {
        "resource_key",
        "amount",
    }
    assert {item.key for item in definition.actors.actor_profiles} == {
        "electrical_team_beta",
        "logistics_team_alpha",
        "municipal_repair_team_alpha",
    }

    located_in = {
        relation.source_node_key: relation.target_node_key
        for relation in definition.world.relations
        if relation.relation_type_key == "located_in"
    }
    assert len(located_in) == 24
    assert located_in["central_hospital"] == "central_district"
    assert located_in["district_service_center"] == "southeast_heights_district"

    endpoints: dict[str, set[str]] = {}
    for relation in definition.world.relations:
        if relation.relation_type_key == "endpoint":
            endpoints.setdefault(relation.source_node_key, set()).add(relation.target_node_key)
    assert endpoints == {
        "north_service_corridor": {"north_industrial_district", "central_district"},
        "west_freight_corridor": {"central_district", "west_logistics_district"},
        "central_river_tunnel": {"central_district", "east_residential_district"},
        "south_bridge": {"west_logistics_district", "south_waterfront_district"},
        "waterfront_access_corridor": {"east_residential_district", "south_waterfront_district"},
        "southeast_access_corridor": {"south_waterfront_district", "southeast_heights_district"},
    }
