"""Frozen semantic inventory for the Linjiang Repair-family convergence."""

from __future__ import annotations

import hashlib
import json

from app.domain.scenario_v2 import ScenarioDefinitionV2

LEGACY_REPAIR_ACTION_KEYS = frozenset(
    {
        "repair_communications",
        "repair_electrical",
        "repair_industrial_facility",
        "repair_water_facility",
    }
)

EXPECTED_REPAIR_TARGET_ROLES = {
    "central_telecom_hub": "communications_repair_team",
    "north_communication_relay": "communications_repair_team",
    "south_communication_core": "communications_repair_team",
    "east_distribution_station": "electrical_response_team",
    "southeast_emergency_power_station": "electrical_response_team",
    "south_substation": "electrical_response_team",
    "southeast_fuel_emergency_power_plant": "electrical_response_team",
    "utility_service_depot": "industrial_repair_team",
    "heavy_equipment_yard": "industrial_repair_team",
    "district_service_center": "industrial_repair_team",
    "east_community_hospital": "industrial_repair_team",
    "riverside_shelter": "industrial_repair_team",
    "city_distribution_center": "industrial_repair_team",
    "emergency_supply_warehouse": "industrial_repair_team",
    "north_fuel_depot": "industrial_repair_team",
    "rail_freight_yard": "industrial_repair_team",
    "river_port": "industrial_repair_team",
    "south_fuel_terminal": "industrial_repair_team",
    "vehicle_depot": "industrial_repair_team",
    "water_treatment_plant": "water_repair_team",
    "south_pump_station": "water_repair_team",
    "east_water_pump_station": "water_repair_team",
}

LEGACY_REPAIR_RULE_COUNT = 71
LEGACY_REPAIR_RULE_SEMANTIC_HASH = (
    "1681ca966d39025464fd2e79ab8d790c74ddef014dc4b029e1267008bdff2d96"
)


def repair_rule_semantic_hash(definition: ScenarioDefinitionV2) -> tuple[int, str]:
    """Hash every non-family-gate Repair Rule independent of key/action renames."""

    family_gates = {f"{key}_target_profile_required" for key in LEGACY_REPAIR_ACTION_KEYS}
    rows: list[dict[str, object]] = []
    for rule in definition.rules:
        if rule.action_key not in {*LEGACY_REPAIR_ACTION_KEYS, "repair_facility"}:
            continue
        if rule.key in family_gates or rule.key == "repair_facility_target_contract_required":
            continue
        payload = rule.model_dump(mode="json")
        payload.pop("key", None)
        payload["action_key"] = "repair_facility"
        rows.append(payload)
    rows.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return len(rows), hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "EXPECTED_REPAIR_TARGET_ROLES",
    "LEGACY_REPAIR_ACTION_KEYS",
    "LEGACY_REPAIR_RULE_COUNT",
    "LEGACY_REPAIR_RULE_SEMANTIC_HASH",
    "repair_rule_semantic_hash",
]
