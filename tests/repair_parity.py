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
    # Intentional Phase 2 root-repair change; old hash was
    # 184ec249f7577205dcb010195f6f694ac56d05da5635c2c077c1db9457d544e0.
    "ae4542484cef9539795c67c49257510d01b75152aaa95bb5fc30946d5e0d9ecc"
)
LEGACY_FACILITY_FACT_SEMANTIC_HASH = (
    # Intentional Phase 2 change: remove power_generation_capable and make the
    # root power station's authored initial power state available; old hash
    # was 9bc794c0f53e3fe35e63e54e8766fa906d5ac59a596eb8ced230d1ba42eaa209.
    "d509fc9aaedd07e0198ea34a7cb6d9a5a373ff2a0090de9ac411a5e9ba02cd4c"
)
LEGACY_INITIAL_FACT_SEMANTIC_HASH = (
    # Intentional Phase 2 initial-Fact semantic migration; old hash was
    # 62be14f9535df02fef8799dc612d20f29606858d324393295ac68f04dff91b26.
    "3a88bf989f79bb94799aa8ee841e4be491879b4eaa0656731cb8bfdf7f261988"
)
LEGACY_POWER_TOPOLOGY_SEMANTIC_HASH = (
    "a472c4331fb65d2d69d8096b753528eb84477a5b442ce57f6749a56469dcb29a"
)


def _semantic_hash(rows: object) -> str:
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def repair_rule_semantic_hash(definition: ScenarioDefinitionV2) -> tuple[int, str]:
    """Hash every non-family-gate Repair Rule independent of key/action renames."""

    family_gates = {f"{key}_target_profile_required" for key in LEGACY_REPAIR_ACTION_KEYS}
    rows: list[dict[str, object]] = []
    for rule in definition.rules:
        if rule.action_key not in {*LEGACY_REPAIR_ACTION_KEYS, "repair_facility"}:
            continue
        if rule.key in family_gates or rule.key in {
            "repair_facility_target_contract_required",
            "repair_facility_base_resolution",
        }:
            continue
        payload = rule.model_dump(mode="json")
        payload.pop("key", None)
        payload["action_key"] = "repair_facility"
        payload["effects"] = [
            effect
            for effect in payload["effects"]
            if effect["kind"] != "REVEAL_TARGET_REGION_FACILITY_FACTS"
        ]
        rows.append(payload)
    rows.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return len(rows), _semantic_hash(rows)


def facility_fact_semantic_hash(definition: ScenarioDefinitionV2) -> str:
    """Freeze operational/power Fact authoring independently of Interactions."""

    rows: list[dict[str, object]] = []
    for node in definition.world.nodes:
        if node.node_type_key != "facility":
            continue
        facts = []
        for fact in node.facts:
            if fact.key not in {"operational", "power_supply"}:
                continue
            facts.append(
                {
                    "key": fact.key,
                    "initial_value": fact.initial_value,
                    "initial_visibility": fact.initial_visibility.value,
                    "goal_addressable": fact.goal_addressable,
                    "allowed_values": list(fact.allowed_values),
                }
            )
        rows.append({"node_key": node.key, "facts": sorted(facts, key=lambda item: item["key"])})
    return _semantic_hash(sorted(rows, key=lambda item: item["node_key"]))


def initial_fact_semantic_hash(definition: ScenarioDefinitionV2) -> str:
    """Freeze every existing Fact initial value, visibility, and Goal contract."""

    rows = [
        {
            "node_key": node.key,
            "facts": sorted(
                (
                    {
                        "key": fact.key,
                        "initial_value": fact.initial_value,
                        "initial_visibility": fact.initial_visibility.value,
                        "goal_addressable": fact.goal_addressable,
                        "allowed_values": list(fact.allowed_values),
                    }
                    for fact in node.facts
                ),
                key=lambda item: item["key"],
            ),
        }
        for node in definition.world.nodes
    ]
    return _semantic_hash(sorted(rows, key=lambda item: item["node_key"]))


def power_topology_semantic_hash(definition: ScenarioDefinitionV2) -> str:
    """Freeze authored supplies_power_to Relations."""

    rows = [
        relation.model_dump(mode="json")
        for relation in definition.world.relations
        if relation.relation_type_key == "supplies_power_to"
    ]
    rows.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return _semantic_hash(rows)


__all__ = [
    "EXPECTED_REPAIR_TARGET_ROLES",
    "LEGACY_FACILITY_FACT_SEMANTIC_HASH",
    "LEGACY_INITIAL_FACT_SEMANTIC_HASH",
    "LEGACY_POWER_TOPOLOGY_SEMANTIC_HASH",
    "LEGACY_REPAIR_ACTION_KEYS",
    "LEGACY_REPAIR_RULE_COUNT",
    "LEGACY_REPAIR_RULE_SEMANTIC_HASH",
    "facility_fact_semantic_hash",
    "initial_fact_semantic_hash",
    "power_topology_semantic_hash",
    "repair_rule_semantic_hash",
]
