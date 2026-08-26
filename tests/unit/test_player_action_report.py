from __future__ import annotations

from app.domain.resources import resource_pool_initial_states, resource_state_key
from app.scenarios.builtin import load_builtin_scenario
from app.services.player_action_report import format_player_knowledge_changes
from tests.scenario_fixtures import GENERIC_TEST


def _definition(name: str = "linjiang_infrastructure_recovery_v2_0.yaml"):
    return load_builtin_scenario(name)


def _render(change) -> str:  # type: ignore[no-untyped-def]
    return change.name if change.value is None else f"{change.name}\uff1a{change.value}"


def test_resource_discovery_qualifies_same_resource_by_facility_source() -> None:
    definition = _definition()
    pools = {
        item.pool_key: item
        for item in resource_pool_initial_states(definition)
        if item.pool_key in {"north_heavy_equipment_stock", "north_service_depot_stock"}
    }

    changes = format_player_knowledge_changes(
        [
            {
                "kind": "RESOURCE_DISCOVERED",
                "key": resource_state_key(
                    pools["north_heavy_equipment_stock"].resource_key,
                    pools["north_heavy_equipment_stock"].region_key,
                    pools["north_heavy_equipment_stock"].pool_key,
                ),
                "name": "general_engineering_parts",
                "value": 50,
            },
            {
                "kind": "RESOURCE_DISCOVERED",
                "key": resource_state_key(
                    pools["north_service_depot_stock"].resource_key,
                    pools["north_service_depot_stock"].region_key,
                    pools["north_service_depot_stock"].pool_key,
                ),
                "name": "general_engineering_parts",
                "value": 50,
            },
        ],
        definition,
    )

    assert len(changes) == 2
    assert changes[0].name == "重型工程设备场 · 通用工程部件"
    assert changes[1].name == "市政工程维修基地 · 通用工程部件"
    assert [change.value for change in changes] == [50, 50]
    assert all("general_engineering_parts" not in _render(change) for change in changes)
    assert all("@" not in _render(change) for change in changes)


def test_resource_discovery_qualifies_generic_pool_by_region() -> None:
    definition = _definition()
    pool = next(
        item
        for item in resource_pool_initial_states(definition)
        if item.pool_key == "north_emergency_engineering_stock"
    )
    region = definition.world.node(pool.region_key)
    resource = next(item for item in definition.world.resources if item.key == pool.resource_key)
    assert region is not None

    changes = format_player_knowledge_changes(
        [
            {
                "kind": "RESOURCE_DISCOVERED",
                "key": resource_state_key(pool.resource_key, pool.region_key, pool.pool_key),
                "name": pool.resource_key,
                "value": 5,
            }
        ],
        definition,
    )

    assert len(changes) == 1
    assert changes[0].name == f"{region.name} · {resource.name}"
    assert _render(changes[0]) == f"{region.name} · {resource.name}\uff1a5"


def test_action_report_localizes_internal_resource_survey_fields_and_values() -> None:
    definition = _definition()
    changes = format_player_knowledge_changes(
        [
            {
                "kind": "RESOURCE_INVENTORY_REVEALED",
                "key": "north_industrial_district.resource_inventory_visibility",
                "name": "Resource inventory visibility",
                "value": "VISIBLE",
            },
            {
                "kind": "RESOURCE_SURVEY_COMPLETED",
                "key": "north_industrial_district.resource_survey_completed",
                "name": "Resource survey completed",
                "value": True,
            },
            {
                "kind": "FACT_REVEALED",
                "key": "heavy_equipment_yard.operational",
                "name": "Operational",
                "value": False,
            },
            {
                "kind": "FACT_REVEALED",
                "key": "central_telecom_hub.power_generation_capable",
                "name": "Power generation capable",
                "value": False,
            },
            {
                "kind": "FACT_REVEALED",
                "key": "central_telecom_hub.repair_profile",
                "name": "Repair profile",
                "value": "central_telecom_hub",
            },
            {
                "kind": "FACT_REVEALED",
                "key": "central_telecom_hub.power_supply",
                "name": "Power supply",
                "value": "AVAILABLE",
            },
        ],
        definition,
    )

    rendered = [_render(change) for change in changes]
    assert rendered == [
        "资源库存信息\uff1a已可见",
        "资源调查已完成",
        "设备状态\uff1a待修复",
        "供电状态\uff1a已供电",
    ]
    assert all(token not in " ".join(rendered) for token in ("VISIBLE", "True", "False"))
    assert "heavy_equipment_yard" not in " ".join(rendered)


def test_action_report_keeps_generation_capability_for_genuine_generator() -> None:
    definition = _definition()
    changes = format_player_knowledge_changes(
        [
            {
                "kind": "FACT_REVEALED",
                "key": "southeast_emergency_power_station.power_generation_capable",
                "name": "Power generation capable",
                "value": True,
            },
        ],
        definition,
    )

    assert [_render(change) for change in changes] == ["发电能力\uff1a具备"]


def test_action_report_uses_normalized_operational_labels() -> None:
    definition = _definition()
    changes = format_player_knowledge_changes(
        [
            {
                "kind": "FACT_REVEALED",
                "key": "southeast_emergency_power_station.operational",
                "name": "Operational",
                "value": True,
            },
        ],
        definition,
    )

    assert [_render(change) for change in changes] == ["设备状态\uff1a正常"]


def test_action_report_does_not_leak_hidden_enum_and_works_for_other_scenario() -> None:
    definition = GENERIC_TEST
    resource = definition.world.resources[0]
    changes = format_player_knowledge_changes(
        [
            {
                "kind": "RESOURCE_DISCOVERED",
                "key": resource.key,
                "name": resource.key,
                "value": 7,
            },
            {
                "kind": "RESOURCE_INVENTORY_REVEALED",
                "key": "global.resource_inventory_visibility",
                "name": "resource_inventory_visibility",
                "value": "HIDDEN",
            },
        ],
        definition,
    )

    assert _render(changes[0]) == f"{resource.name}\uff1a7"
    assert _render(changes[1]) == "资源库存信息\uff1a未知"
    assert all(
        token not in " ".join(_render(change) for change in changes)
        for token in ("HIDDEN", resource.key)
    )
