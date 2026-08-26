from app.domain.scenario_v2 import ActionBehavior
from app.services.spatial_projection import SpatialDisplayProjector
from tests.scenario_fixtures import LINJIANG_V2_TEST


def test_linjiang_nodes_resources_and_transport_use_generic_spatial_projection() -> None:
    definition = LINJIANG_V2_TEST
    projector = SpatialDisplayProjector(definition)

    distribution = projector.node("east_distribution_station")
    assert distribution is not None
    east = projector.node("east_residential_district")
    assert east is not None
    assert distribution.region_key == "east_residential_district"
    assert distribution.region_name == east.name

    corridor = projector.node("west_freight_corridor")
    assert corridor is not None
    assert corridor.endpoint_region_keys == (
        "central_district",
        "west_logistics_district",
    )
    assert len(corridor.endpoint_region_names) == 2

    resource = projector.resource_scope("west_logistics_district")
    west = projector.node("west_logistics_district")
    assert west is not None
    assert resource.scope_region_key == "west_logistics_district"
    assert resource.scope_region_name == west.name


def test_action_location_formats_route_transport_facility_and_connector() -> None:
    definition = LINJIANG_V2_TEST
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
    north = projector.node("north_industrial_district")
    central = projector.node("central_district")
    assert north is not None and central is not None
    assert transport.summary == (f"{north.name} \u2192 {central.name}")
    assert "\u00d710" in (transport.detail or "")

    repair = projector.action_location(
        actions["repair_electrical"],
        target_node_key="east_distribution_station",
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
    corridor = projector.node("west_freight_corridor")
    assert corridor is not None
    assert corridor.name in clear.summary
    clear_compact = projector.action_location(
        actions["clear_transport"],
        target_node_key="west_freight_corridor",
        compact=True,
    )
    assert clear_compact is not None
    assert clear_compact.summary == corridor.name
    assert clear_compact.detail is None

    assert actions["travel"].behavior == ActionBehavior.TRAVEL
