from app.domain.scenario_v2 import ActionBehavior
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V1
from app.services.spatial_projection import SpatialDisplayProjector


def test_linjiang_nodes_resources_and_transport_use_generic_spatial_projection() -> None:
    definition = LINJIANG_INFRASTRUCTURE_RECOVERY_V1
    projector = SpatialDisplayProjector(definition)

    hospital = projector.node("central_hospital")
    assert hospital is not None
    assert hospital.region_key == "central_district"
    assert hospital.region_name == definition.world.node("central_district").name

    corridor = projector.node("west_freight_corridor")
    assert corridor is not None
    assert corridor.endpoint_region_keys == (
        "central_district",
        "west_logistics_district",
    )
    assert len(corridor.endpoint_region_names) == 2

    resource = projector.resource_scope("west_logistics_district")
    assert resource.scope_region_key == "west_logistics_district"
    assert resource.scope_region_name == definition.world.node("west_logistics_district").name


def test_action_location_formats_route_transport_facility_and_connector() -> None:
    definition = LINJIANG_INFRASTRUCTURE_RECOVERY_V1
    projector = SpatialDisplayProjector(definition)
    actions = {action.key: action for action in definition.actions}

    travel = projector.action_location(
        actions["travel"],
        source_node_key="central_district",
        target_node_key="west_logistics_district",
    )
    assert travel is not None
    assert travel.kind == "ROUTE"
    assert "→" in travel.summary

    transport = projector.action_location(
        actions["transport_resource"],
        source_node_key="north_industrial_district",
        target_node_key="central_district",
        parameters={"resource_key": "electrical_repair_parts", "amount": 10},
    )
    assert transport is not None
    assert transport.kind == "ROUTE"
    assert "\u00d710" in (transport.detail or "")

    repair = projector.action_location(
        actions["repair_electrical"],
        target_node_key="central_hospital",
    )
    assert repair is not None
    assert repair.kind == "FACILITY"
    assert "·" in repair.summary

    clear = projector.action_location(
        actions["clear_transport"],
        target_node_key="west_freight_corridor",
    )
    assert clear is not None
    assert clear.kind == "TRANSPORT"
    assert "↔" in clear.summary

    assert actions["travel"].behavior == ActionBehavior.TRAVEL
