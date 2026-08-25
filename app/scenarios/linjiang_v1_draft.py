# ruff: noqa: E501

"""Authoring data for the next Linjiang infrastructure-recovery Draft.

This module only builds a declarative Scenario document.  It is intentionally
not imported by the runtime engine and contains no Scenario-specific runtime
branch.  The canonical v9 Published snapshot is passed in by the Draft
creation command and is never mutated in place.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from app.domain.scenario_v2 import ScenarioDefinitionV2

LINJIANG_V1_DRAFT_KEY = "linjiang_infrastructure_recovery_v2_0"
LINJIANG_V1_DRAFT_NAME = "\u4e34\u6c5f\u5e02\u707e\u540e\u57fa\u7840\u8bbe\u65bd\u6062\u590d v2.0"

_KNOWN = "KNOWN"
_HIDDEN = "HIDDEN"
_ONLINE = "ONLINE"
_DISCONNECTED = "DISCONNECTED"
_VISIBLE = "VISIBLE"
_AVAILABLE = "AVAILABLE"
_UNAVAILABLE = "UNAVAILABLE"


def build_linjiang_v1_draft_document(base_document: Mapping[str, Any]) -> dict[str, Any]:
    """Return the complete Linjiang Draft document derived from a v9 snapshot."""

    document = deepcopy(dict(base_document))
    document["metadata"]["key"] = LINJIANG_V1_DRAFT_KEY
    document["metadata"]["name"] = LINJIANG_V1_DRAFT_NAME
    document["metadata"]["description"] = (
        "\u4e34\u6c5f\u707e\u540e\u57fa\u7840\u8bbe\u65bd\u6062\u590d\uff1a\u901a\u4fe1\u3001\u4ea4\u901a\u3001\u7535\u529b\u3001\u6c34\u52a1\u4e0e\u8d44\u6e90\u60c5\u62a5\u3002"
    )
    document["world"]["key"] = LINJIANG_V1_DRAFT_KEY
    document["world"]["name"] = LINJIANG_V1_DRAFT_NAME
    document["initialization"] = {
        "start_node_key": "central_district",
        "primary_actor_key": "logistics_team_alpha",
        "resource_initial_states": [],
    }

    _build_nodes(document)
    _build_relations(document)
    _build_resources(document)
    _build_actors(document)
    _build_actions(document)
    _build_rules(document)
    _build_objectives(document)
    document["goal_resolution"] = {
        "allow_llm_fallback": True,
        "clarification_prompt": "\u8bf7\u8bf4\u660e\u5e0c\u671b\u6062\u590d\u7684\u57fa\u7840\u8bbe\u65bd\u6216\u516c\u5171\u670d\u52a1\u3002",
    }
    document["planning"] = {
        "instructions": [
            "\u53ea\u4f7f\u7528\u5f53\u524d Knowledge \u4e2d\u5df2\u77e5\u7684\u5730\u70b9\u3001\u5173\u7cfb\u3001\u8d44\u6e90\u4e0e\u8bbe\u65bd\u72b6\u6001\u3002",
            "\u8d44\u6e90\u4f7f\u7528\u524d\u786e\u8ba4 source Region \u7684\u5df2\u77e5\u53ef\u7528\u6570\u91cf\uff1b\u4e0d\u8981\u9009\u62e9 pool_key\u3002",
            "\u4f9d\u6b21\u68c0\u67e5 Actor \u7684\u5728\u7ebf\u72b6\u6001\u3001\u533a\u57df\u5f52\u5c5e\u548c\u5df2\u77e5\u5de5\u7a0b\u6761\u4ef6\u3002",
        ],
        "recovery_hints": [
            {
                "failure_code": "TRAVEL_BLOCKED",
                "hint": "\u4fdd\u7559\u5df2\u5b8c\u6210\u6b65\u9aa4\uff0c\u8bfb\u53d6\u65b0\u63ed\u793a\u7684\u901a\u884c\u4e8b\u5b9e\u540e\u91cd\u65b0\u8bc4\u4f30\u5269\u4f59\u8ba1\u5212\u3002",
            },
            {
                "failure_code": "TRANSPORT_RESOURCE_INSUFFICIENT",
                "hint": "\u8d44\u6e90\u6570\u91cf\u4e0d\u8db3\u65f6\u91cd\u65b0\u68c0\u67e5\u5df2\u77e5\u5e93\u5b58\u4e0e\u89e3\u9501\u6761\u4ef6\u3002",
            },
            {
                "failure_code": "RESOURCE_SURVEY_ALREADY_COMPLETED",
                "hint": "\u5df2\u5b8c\u6210\u7684\u8d44\u6e90 Survey \u4e0d\u8981\u91cd\u590d\u6267\u884c\u3002",
            },
        ],
    }
    return document


def build_linjiang_v1_definition(base_definition: ScenarioDefinitionV2) -> ScenarioDefinitionV2:
    """Build and validate the Draft from an in-memory v9 definition."""

    return ScenarioDefinitionV2.model_validate(
        build_linjiang_v1_draft_document(base_definition.model_dump(mode="json"))
    )


def _build_nodes(document: dict[str, Any]) -> None:
    nodes = {node["key"]: node for node in document["world"]["nodes"]}
    nodes["central_hospital"]["facts"] = [
        fact
        for fact in nodes["central_hospital"].get("facts", [])
        if fact["key"] != "emergency_power_operational"
    ]
    facilities = {
        "north_communication_relay": (
            "\u5317\u90e8\u901a\u4fe1\u4e2d\u7ee7\u7ad9",
            "\u5317\u90e8\u5de5\u4e1a\u533a\u7684\u901a\u4fe1\u4e2d\u7ee7\u8bbe\u65bd\u3002",
        ),
        "west_communication_relay": (
            "\u897f\u90e8\u901a\u4fe1\u4e2d\u7ee7\u7ad9",
            "\u897f\u90e8\u7269\u6d41\u533a\u7684\u901a\u4fe1\u4e2d\u7ee7\u8bbe\u65bd\u3002",
        ),
        "east_distribution_station": (
            "\u4e1c\u90e8\u914d\u7535\u7ad9",
            "\u4e1c\u90e8\u5c45\u4f4f\u533a\u7684\u914d\u7535\u8bbe\u65bd\u3002",
        ),
        "south_communication_core": (
            "\u5357\u90e8\u901a\u4fe1\u6838\u5fc3",
            "\u5357\u90e8\u6c34\u6ee8\u533a\u7684\u533a\u57df\u901a\u4fe1\u6838\u5fc3\u3002",
        ),
        "south_substation": (
            "\u5357\u90e8\u53d8\u7535\u7ad9",
            "\u4e3a\u5357\u90e8\u6c34\u52a1\u7cfb\u7edf\u4f9b\u7535\u7684\u53d8\u7535\u8bbe\u65bd\u3002",
        ),
        "southeast_emergency_power_station": (
            "\u4e1c\u5357\u5e94\u6025\u7535\u6e90\u7ad9",
            "\u4e1c\u5357\u9ad8\u5730\u7684\u5e94\u6025\u53d1\u7535\u8bbe\u65bd\u3002",
        ),
    }
    for key, (name, description) in facilities.items():
        nodes[key] = {
            "key": key,
            "name": name,
            "description": description,
            "node_type_key": "facility",
            "initial_access": "AVAILABLE",
            "initial_visibility": _KNOWN,
            "interaction_keys": [],
            "facts": [],
        }

    regions = {
        "central_district",
        "north_industrial_district",
        "west_logistics_district",
        "east_residential_district",
        "south_waterfront_district",
        "southeast_heights_district",
    }
    transport_nodes = {
        node["key"] for node in nodes.values() if node["node_type_key"] == "transport"
    }
    for _key, node in nodes.items():
        if node["node_type_key"] == "region":
            _add_interaction(node, "surveyable")
            _add_interaction(node, "travel_destination")
        if node["node_type_key"] == "transport":
            _add_interaction(node, "inspectable")
        if node["node_type_key"] == "facility":
            _add_interaction(node, "inspectable")

    repairable = {
        "central_hospital",
        "central_telecom_hub",
        "north_power_substation",
        "north_communication_relay",
        "utility_service_depot",
        "heavy_equipment_yard",
        "east_community_hospital",
        "riverside_shelter",
        "east_water_pump_station",
        "east_telecom_station",
        "east_distribution_station",
        "water_treatment_plant",
        "south_pump_station",
        "south_communication_core",
        "south_substation",
        "southeast_emergency_power_station",
        "district_service_center",
    }
    power_targets = {
        "central_hospital",
        "east_community_hospital",
        "riverside_shelter",
        "east_water_pump_station",
        "east_distribution_station",
        "water_treatment_plant",
        "south_pump_station",
        "south_substation",
    }
    deploy_targets = {"water_treatment_plant", "south_bridge"}
    for key in repairable:
        _add_interaction(nodes[key], "repairable")
    for key in power_targets:
        _add_interaction(nodes[key], "power_targetable")
    for key in deploy_targets:
        _add_interaction(nodes[key], "heavy_support_target")

    for key in ("central_river_tunnel", "north_service_corridor"):
        _add_interaction(nodes[key], "clearable")
        _upsert_fact(
            nodes[key],
            _fact(
                "passable",
                "\u901a\u884c\u72b6\u6001",
                "BOOLEAN",
                False,
                _HIDDEN,
                description="\u8be5\u901a\u9053\u662f\u5426\u53ef\u901a\u884c\u3002",
            ),
        )
    _upsert_fact(
        nodes["west_freight_corridor"],
        _fact("passable", "\u901a\u884c\u72b6\u6001", "BOOLEAN", True, _KNOWN),
    )
    for key in transport_nodes - {
        "central_river_tunnel",
        "north_service_corridor",
        "west_freight_corridor",
    }:
        _upsert_fact(
            nodes[key],
            _fact("passable", "\u901a\u884c\u72b6\u6001", "BOOLEAN", True, _KNOWN),
        )
    for key in regions:
        nodes[key]["initial_visibility"] = _KNOWN

    profiles = {
        "central_telecom_hub": ("central_communication_core", False, "UNAVAILABLE", False),
        "north_communication_relay": ("north_communication_relay", False, "UNAVAILABLE", False),
        "south_communication_core": ("south_communication_core", False, "UNAVAILABLE", False),
        "east_telecom_station": ("east_telecom_station", True, "AVAILABLE", False),
        "west_communication_relay": ("west_communication_relay", True, "AVAILABLE", False),
        "southeast_telecom_relay": ("southeast_telecom_relay", True, "AVAILABLE", False),
        "central_hospital": ("central_hospital", False, "UNAVAILABLE", False),
        "east_community_hospital": ("east_community_hospital", False, "UNAVAILABLE", False),
        "riverside_shelter": ("riverside_shelter", False, "UNAVAILABLE", False),
        "east_distribution_station": ("east_distribution_station", False, "UNAVAILABLE", False),
        "north_power_substation": ("north_power_substation", False, "UNAVAILABLE", False),
        "south_substation": ("south_substation", False, "UNAVAILABLE", False),
        "southeast_emergency_power_station": (
            "southeast_emergency_power_generation",
            False,
            "UNAVAILABLE",
            True,
        ),
        "water_treatment_plant": ("water_treatment_plant", False, "UNAVAILABLE", False),
        "south_pump_station": ("south_pump_station", False, "UNAVAILABLE", False),
        "east_water_pump_station": ("east_water_pump_station", False, "UNAVAILABLE", False),
        "utility_service_depot": ("utility_service_depot", False, "UNAVAILABLE", False),
        "heavy_equipment_yard": ("heavy_equipment_yard", False, "UNAVAILABLE", False),
        "district_service_center": ("district_service_center", False, "UNAVAILABLE", False),
    }
    for key, (profile, operational, power_supply, generation) in profiles.items():
        _upsert_fact(
            nodes[key],
            _fact(
                "repair_profile", "\u8bbe\u65bd\u4fee\u590d\u7c7b\u578b", "STRING", profile, _KNOWN
            ),
        )
        _upsert_fact(
            nodes[key],
            _fact("operational", "\u8fd0\u884c\u72b6\u6001", "BOOLEAN", operational, _KNOWN),
        )
        _upsert_fact(
            nodes[key],
            _fact(
                "power_supply",
                "\u4f9b\u7535\u72b6\u6001",
                "ENUM",
                power_supply,
                _KNOWN,
                allowed_values=[_AVAILABLE, _UNAVAILABLE],
            ),
        )
        _upsert_fact(
            nodes[key],
            _fact(
                "power_generation_capable",
                "\u4f9b\u7535\u80fd\u529b",
                "BOOLEAN",
                generation,
                _KNOWN,
            ),
        )
    for key in deploy_targets:
        _upsert_fact(
            nodes[key],
            _fact(
                "heavy_engineering_support_ready",
                "\u91cd\u578b\u5de5\u7a0b\u652f\u63f4\u5c31\u7eea",
                "BOOLEAN",
                False,
                _KNOWN,
            ),
        )
    _upsert_fact(
        nodes["heavy_equipment_yard"],
        _fact(
            "heavy_engineering_support",
            "\u91cd\u578b\u5de5\u7a0b\u652f\u63f4",
            "ENUM",
            _UNAVAILABLE,
            _KNOWN,
            allowed_values=[_AVAILABLE, _UNAVAILABLE],
        ),
    )
    _upsert_fact(
        nodes["east_water_pump_station"],
        _fact(
            "east_emergency_water_supply",
            "\u4e1c\u90e8\u5e94\u6025\u4f9b\u6c34",
            "ENUM",
            _UNAVAILABLE,
            _KNOWN,
            allowed_values=[_AVAILABLE, _UNAVAILABLE],
        ),
    )
    document["world"]["nodes"] = list(nodes.values())


def _build_relations(document: dict[str, Any]) -> None:
    relations = list(document["world"].get("relations", []))
    existing = {
        (item["source_node_key"], item["relation_type_key"], item["target_node_key"]): item
        for item in relations
    }
    for item in relations:
        item["key"] = item.get("key") or _relation_key(
            item["source_node_key"], item["relation_type_key"], item["target_node_key"]
        )
        item["initial_visibility"] = _VISIBLE
    new_relations = [
        ("north_communication_relay", "located_in", "north_industrial_district"),
        ("west_communication_relay", "located_in", "west_logistics_district"),
        ("east_distribution_station", "located_in", "east_residential_district"),
        ("south_communication_core", "located_in", "south_waterfront_district"),
        ("south_substation", "located_in", "south_waterfront_district"),
        ("southeast_emergency_power_station", "located_in", "southeast_heights_district"),
        ("southeast_emergency_power_station", "supplies_power_to", "east_distribution_station"),
        ("east_distribution_station", "supplies_power_to", "east_community_hospital"),
        ("east_distribution_station", "supplies_power_to", "riverside_shelter"),
        ("southeast_emergency_power_station", "supplies_power_to", "south_substation"),
        ("south_substation", "supplies_power_to", "water_treatment_plant"),
        ("south_substation", "supplies_power_to", "south_pump_station"),
        ("east_distribution_station", "supplies_power_to", "east_water_pump_station"),
    ]
    for source, relation_type, target in new_relations:
        identity = (source, relation_type, target)
        if identity not in existing:
            relations.append(
                {
                    "key": _relation_key(source, relation_type, target),
                    "source_node_key": source,
                    "relation_type_key": relation_type,
                    "target_node_key": target,
                    "initial_visibility": _VISIBLE,
                }
            )
    document["world"]["relations"] = relations


def _build_resources(document: dict[str, Any]) -> None:
    document["world"]["resources"] = [
        {
            "key": "communication_equipment",
            "name": "\u901a\u4fe1\u8bbe\u5907",
            "description": "\u7528\u4e8e\u6062\u590d\u901a\u4fe1\u8282\u70b9\u7684\u8bbe\u5907\u3002",
            "initial_value": 0,
            "minimum": 0,
            "reservation_supported": False,
        },
        {
            "key": "general_engineering_parts",
            "name": "\u901a\u7528\u5de5\u7a0b\u90e8\u4ef6",
            "description": "\u7528\u4e8e\u666e\u901a\u8bbe\u65bd\u7ef4\u4fee\u7684\u901a\u7528\u5de5\u7a0b\u90e8\u4ef6\u3002",
            "initial_value": 0,
            "minimum": 0,
            "reservation_supported": False,
        },
        {
            "key": "municipal_repair_materials",
            "name": "\u5e02\u653f\u7ef4\u4fee\u6750\u6599",
            "description": "\u7528\u4e8e\u6e05\u7406\u548c\u4fee\u590d\u666e\u901a\u4ea4\u901a\u901a\u9053\u3002",
            "initial_value": 0,
            "minimum": 0,
            "reservation_supported": False,
        },
        {
            "key": "electrical_repair_parts",
            "name": "\u7535\u529b\u7ef4\u4fee\u90e8\u4ef6",
            "description": "\u7528\u4e8e\u7535\u529b\u8bbe\u65bd\u6062\u590d\u3002",
            "initial_value": 0,
            "minimum": 0,
            "reservation_supported": False,
        },
        {
            "key": "water_system_parts",
            "name": "\u6c34\u52a1\u7cfb\u7edf\u90e8\u4ef6",
            "description": "\u7528\u4e8e\u6c34\u5904\u7406\u4e0e\u4f9b\u6c34\u8bbe\u65bd\u6062\u590d\u3002",
            "initial_value": 0,
            "minimum": 0,
            "reservation_supported": False,
        },
    ]

    def req(node: str, fact: str, value: Any) -> dict[str, Any]:
        return {"node_key": node, "fact_key": fact, "value": value}

    document["initialization"]["resource_pools"] = [
        _pool("central_general_stock", "general_engineering_parts", "central_district", 10),
        _pool("west_general_stock", "general_engineering_parts", "west_logistics_district", 10),
        _pool("west_municipal_stock", "municipal_repair_materials", "west_logistics_district", 100),
        _pool(
            "east_communication_stock", "communication_equipment", "east_residential_district", 30
        ),
        _pool(
            "southeast_electrical_stock",
            "electrical_repair_parts",
            "southeast_heights_district",
            20,
        ),
        _pool("south_water_stock", "water_system_parts", "south_waterfront_district", 50),
        _pool(
            "north_emergency_engineering_stock",
            "general_engineering_parts",
            "north_industrial_district",
            5,
        ),
        _pool(
            "north_service_depot_stock",
            "general_engineering_parts",
            "north_industrial_district",
            50,
            facility_key="utility_service_depot",
            visibility=_HIDDEN,
            availability=_UNAVAILABLE,
            survey_discoverable=True,
            availability_requirement=req("utility_service_depot", "operational", True),
        ),
        _pool(
            "north_heavy_equipment_stock",
            "general_engineering_parts",
            "north_industrial_district",
            50,
            facility_key="heavy_equipment_yard",
            visibility=_HIDDEN,
            availability=_UNAVAILABLE,
            survey_discoverable=True,
            availability_requirement=req("heavy_equipment_yard", "operational", True),
        ),
        _pool(
            "southeast_district_service_stock",
            "electrical_repair_parts",
            "southeast_heights_district",
            50,
            facility_key="district_service_center",
            visibility=_HIDDEN,
            availability=_UNAVAILABLE,
            survey_discoverable=True,
            availability_requirement=req("district_service_center", "operational", True),
        ),
    ]
    document["initialization"]["region_resource_knowledge"] = [
        {
            "region_key": "central_district",
            "resource_inventory_visibility": "VISIBLE",
            "resource_survey_completed": True,
        },
        *(
            {
                "region_key": region,
                "resource_inventory_visibility": "HIDDEN",
                "resource_survey_completed": False,
            }
            for region in (
                "north_industrial_district",
                "west_logistics_district",
                "east_residential_district",
                "south_waterfront_district",
                "southeast_heights_district",
            )
        ),
    ]


def _build_actors(document: dict[str, Any]) -> None:
    document["actors"] = {
        "roles": [
            {
                "key": "emergency_logistics_team",
                "name": "\u5e94\u6025\u7269\u6d41\u961f",
                "description": "\u8d1f\u8d23\u8de8\u533a\u57df\u8d44\u6e90\u8c03\u5ea6\u3002",
                "capabilities": ["EXECUTE_ACTION", "INSPECT_STATE", "LOGISTICS", "PLAN"],
            },
            {
                "key": "municipal_repair_team",
                "name": "\u5e02\u653f\u62a2\u4fee\u961f",
                "description": "\u8d1f\u8d23\u4ea4\u901a\u901a\u9053\u6e05\u7406\u4e0e\u5e02\u653f\u8bbe\u65bd\u4fee\u590d\u3002",
                "capabilities": ["EXECUTE_ACTION", "INSPECT_STATE", "PLAN"],
            },
            {
                "key": "communications_repair_team",
                "name": "\u901a\u4fe1\u62a2\u4fee\u961f",
                "description": "\u8d1f\u8d23\u901a\u4fe1\u4e2d\u7ee7\u4e0e\u533a\u57df\u901a\u4fe1\u6838\u5fc3\u4fee\u590d\u3002",
                "capabilities": ["EXECUTE_ACTION", "INSPECT_STATE", "PLAN"],
            },
            {
                "key": "industrial_repair_team",
                "name": "\u5de5\u4e1a\u62a2\u4fee\u961f",
                "description": "\u8d1f\u8d23\u5de5\u4e1a\u8bbe\u65bd\u4e0e\u91cd\u578b\u5de5\u7a0b\u652f\u63f4\u3002",
                "capabilities": ["EXECUTE_ACTION", "INSPECT_STATE", "PLAN"],
            },
            {
                "key": "water_repair_team",
                "name": "\u6c34\u52a1\u62a2\u4fee\u961f",
                "description": "\u8d1f\u8d23\u6c34\u5904\u7406\u4e0e\u4f9b\u6c34\u8bbe\u65bd\u4fee\u590d\u3002",
                "capabilities": ["EXECUTE_ACTION", "INSPECT_STATE", "PLAN"],
            },
            {
                "key": "electrical_response_team",
                "name": "\u7535\u529b\u62a2\u4fee\u961f",
                "description": "\u8d1f\u8d23\u53d1\u7535\u3001\u53d8\u7535\u4e0e\u4f9b\u7535\u8bbe\u65bd\u3002",
                "capabilities": ["EXECUTE_ACTION", "INSPECT_STATE", "PLAN"],
            },
        ],
        "actor_profiles": [
            _actor(
                "logistics_team_alpha",
                "\u5e94\u6025\u7269\u6d41\u4e00\u961f",
                "emergency_logistics_team",
                "central_district",
                _ONLINE,
                ["travel", "inspect", "transport_resource", "relay_message", "survey_resources"],
            ),
            _actor(
                "municipal_transport_team",
                "\u5e02\u653f\u8fd0\u8f93\u4e00\u961f",
                "municipal_repair_team",
                "west_logistics_district",
                _DISCONNECTED,
                ["travel", "inspect", "clear_transport", "relay_message"],
            ),
            _actor(
                "communications_repair_team_alpha",
                "\u901a\u4fe1\u62a2\u4fee\u4e00\u961f",
                "communications_repair_team",
                "east_residential_district",
                _DISCONNECTED,
                ["travel", "inspect", "repair_communications", "relay_message", "survey_resources"],
            ),
            _actor(
                "industrial_repair_team_alpha",
                "\u5de5\u4e1a\u62a2\u4fee\u4e00\u961f",
                "industrial_repair_team",
                "north_industrial_district",
                _DISCONNECTED,
                [
                    "travel",
                    "inspect",
                    "repair_industrial_facility",
                    "deploy_heavy_engineering_support",
                    "relay_message",
                    "survey_resources",
                ],
            ),
            _actor(
                "water_repair_team_alpha",
                "\u6c34\u52a1\u62a2\u4fee\u4e00\u961f",
                "water_repair_team",
                "south_waterfront_district",
                _DISCONNECTED,
                [
                    "travel",
                    "inspect",
                    "repair_water_facility",
                    "activate_emergency_water_transfer",
                    "relay_message",
                    "survey_resources",
                ],
            ),
            _actor(
                "electrical_repair_team_alpha",
                "\u7535\u529b\u62a2\u4fee\u4e00\u961f",
                "electrical_response_team",
                "southeast_heights_district",
                _DISCONNECTED,
                [
                    "travel",
                    "inspect",
                    "repair_electrical",
                    "supply_power",
                    "relay_message",
                    "survey_resources",
                ],
            ),
        ],
    }


def _build_actions(document: dict[str, Any]) -> None:
    actions = [
        _action(
            "travel",
            "\u524d\u5f80\u533a\u57df",
            "\u901a\u8fc7\u4e00\u4e2a Transport \u8fde\u63a5\u79fb\u52a8\u5230\u76ee\u6807\u533a\u57df\u3002",
            "travel_destination",
            "TRAVEL",
            "NONE",
            ["EXECUTE_ACTION"],
            outcomes=[("TRAVELLED", "\u5df2\u5230\u8fbe\u76ee\u6807\u533a\u57df", True)],
        ),
        _action(
            "inspect",
            "\u68c0\u67e5\u72b6\u6001",
            "\u68c0\u67e5\u8bbe\u65bd\u6216\u901a\u9053\u7684\u5df2\u77e5\u72b6\u6001\u3002",
            "inspectable",
            "INSPECT",
            "LOCAL_TARGET",
            ["EXECUTE_ACTION", "INSPECT_STATE"],
            outcomes=[("INSPECTED", "\u72b6\u6001\u5df2\u68c0\u67e5", True)],
        ),
        _action(
            "relay_message",
            "\u4f20\u9012\u4fe1\u606f",
            "ONLINE Actor \u5728\u540c\u4e00 Region \u5411\u5931\u8054 Actor \u4f20\u9012\u547d\u4ee4\u3002",
            "relayable",
            "RELAY_MESSAGE",
            "ACTOR_REGION",
            ["EXECUTE_ACTION"],
            outcomes=[("RELAYED", "\u6d88\u606f\u5df2\u4f20\u9012", True)],
            target_kind="ACTOR",
        ),
        _action(
            "survey_resources",
            "\u67e5\u63a2\u8d44\u6e90",
            "\u5b8c\u6210\u4e00\u6b21\u533a\u57df\u8d44\u6e90\u76d8\u70b9\uff0c\u5e76\u53ef\u80fd\u53d1\u73b0\u8bbe\u65bd\u5e93\u5b58\u3002",
            "surveyable",
            "SURVEY_RESOURCES",
            "REGION",
            ["EXECUTE_ACTION", "INSPECT_STATE"],
            outcomes=[("SURVEYED", "\u8d44\u6e90\u5df2\u67e5\u63a2", True)],
        ),
        _action(
            "clear_transport",
            "\u6e05\u7406\u4ea4\u901a\u901a\u9053",
            "\u6e05\u7406\u5df2\u77e5\u53d7\u963b\u901a\u9053\u3002",
            "clearable",
            "RULE",
            "TRANSPORT_ENDPOINT",
            ["EXECUTE_ACTION"],
            role="municipal_repair_team",
            outcomes=[("CLEARED", "\u901a\u9053\u5df2\u6e05\u7406", True)],
        ),
        _action(
            "transport_resource",
            "\u8fd0\u8f93\u8d44\u6e90",
            "\u5c06\u5f53\u524d Region \u7684\u5df2\u77e5\u8d44\u6e90\u8fd0\u5f80\u76ee\u6807 Region\u3002",
            "transport_destination",
            "TRANSPORT_RESOURCE",
            "NONE",
            ["EXECUTE_ACTION", "LOGISTICS"],
            role="emergency_logistics_team",
            parameters=[
                {"key": "resource_key", "name": "\u8d44\u6e90\u952e", "value_type": "STRING"},
                {"key": "amount", "name": "\u6570\u91cf", "value_type": "INTEGER", "minimum": 1},
            ],
            outcomes=[("TRANSPORTED", "\u8d44\u6e90\u5df2\u8fd0\u8f93", True)],
        ),
        _action(
            "repair_communications",
            "\u4fee\u590d\u901a\u4fe1\u8bbe\u65bd",
            "\u6062\u590d\u901a\u4fe1\u4e2d\u7ee7\u4e0e\u533a\u57df\u901a\u4fe1\u6838\u5fc3\u3002",
            "repairable",
            "RULE",
            "FACILITY_REGION",
            ["EXECUTE_ACTION"],
            role="communications_repair_team",
            outcomes=[("COMMUNICATIONS_REPAIRED", "\u901a\u4fe1\u5df2\u6062\u590d", True)],
            planning=[
                ("central_telecom_hub", "operational"),
                ("north_communication_relay", "operational"),
                ("south_communication_core", "operational"),
            ],
        ),
        _action(
            "repair_industrial_facility",
            "\u4fee\u590d\u5de5\u4e1a\u8bbe\u65bd",
            "\u4fee\u590d\u5de5\u4e1a\u57fa\u5730\u6216\u670d\u52a1\u4e2d\u5fc3\u3002",
            "repairable",
            "RULE",
            "FACILITY_REGION",
            ["EXECUTE_ACTION"],
            role="industrial_repair_team",
            outcomes=[("INDUSTRIAL_REPAIRED", "\u5de5\u4e1a\u8bbe\u65bd\u5df2\u4fee\u590d", True)],
            planning=[
                ("utility_service_depot", "operational"),
                ("heavy_equipment_yard", "operational"),
                ("district_service_center", "operational"),
                ("east_community_hospital", "operational"),
                ("riverside_shelter", "operational"),
            ],
        ),
        _action(
            "repair_electrical",
            "\u4fee\u590d\u7535\u529b\u8bbe\u65bd",
            "\u4fee\u590d\u53d1\u7535\u3001\u53d8\u7535\u6216\u914d\u7535\u8bbe\u65bd\u3002",
            "repairable",
            "RULE",
            "FACILITY_REGION",
            ["EXECUTE_ACTION"],
            role="electrical_response_team",
            outcomes=[("ELECTRICAL_REPAIRED", "\u7535\u529b\u8bbe\u65bd\u5df2\u4fee\u590d", True)],
            planning=[
                ("east_distribution_station", "operational"),
                ("southeast_emergency_power_station", "operational"),
                ("south_substation", "operational"),
            ],
        ),
        _action(
            "repair_water_facility",
            "\u4fee\u590d\u6c34\u52a1\u8bbe\u65bd",
            "\u4fee\u590d\u6c34\u5904\u7406\u6216\u4f9b\u6c34\u8bbe\u65bd\u3002",
            "repairable",
            "RULE",
            "FACILITY_REGION",
            ["EXECUTE_ACTION"],
            role="water_repair_team",
            outcomes=[
                ("WATER_FACILITY_REPAIRED", "\u6c34\u52a1\u8bbe\u65bd\u5df2\u4fee\u590d", True)
            ],
            planning=[
                ("water_treatment_plant", "operational"),
                ("south_pump_station", "operational"),
                ("east_water_pump_station", "operational"),
            ],
        ),
        _action(
            "supply_power",
            "\u9001\u7535",
            "\u6839\u636e\u5df2\u77e5\u7684\u76f4\u63a5\u4f9b\u7535\u5173\u7cfb\u4e3a\u76ee\u6807\u8bbe\u65bd\u6062\u590d\u4f9b\u7535\u3002",
            "power_targetable",
            "SUPPLY_POWER",
            "FACILITY_REGION",
            ["EXECUTE_ACTION"],
            role="electrical_response_team",
            parameters=[
                {"key": "source_key", "name": "\u4f9b\u7535\u6765\u6e90", "value_type": "STRING"}
            ],
            outcomes=[("POWER_SUPPLIED", "\u5df2\u6062\u590d\u4f9b\u7535", True)],
            source_relation="supplies_power_to",
            planning=[
                ("east_community_hospital", "power_supply"),
                ("riverside_shelter", "power_supply"),
                ("water_treatment_plant", "power_supply"),
                ("south_pump_station", "power_supply"),
                ("east_water_pump_station", "power_supply"),
            ],
        ),
        _action(
            "deploy_heavy_engineering_support",
            "\u90e8\u7f72\u91cd\u578b\u5de5\u7a0b\u652f\u63f4",
            "\u5728\u8bbe\u65bd\u6216\u901a\u9053\u76ee\u6807\u4e0a\u90e8\u7f72\u5df2\u89e3\u9501\u7684\u91cd\u578b\u5de5\u7a0b\u652f\u63f4\u3002",
            "heavy_support_target",
            "DEPLOY_HEAVY_ENGINEERING_SUPPORT",
            "LOCAL_TARGET",
            ["EXECUTE_ACTION"],
            role="industrial_repair_team",
            outcomes=[
                ("SUPPORT_DEPLOYED", "\u91cd\u578b\u5de5\u7a0b\u652f\u63f4\u5df2\u5c31\u4f4d", True)
            ],
            planning=[
                ("water_treatment_plant", "heavy_engineering_support_ready"),
                ("south_bridge", "heavy_engineering_support_ready"),
            ],
        ),
        _action(
            "activate_emergency_water_transfer",
            "\u542f\u52a8\u5e94\u6025\u4f9b\u6c34",
            "\u5728\u6c34\u52a1\u3001\u4f9b\u7535\u3001\u91cd\u578b\u5de5\u7a0b\u4e0e\u901a\u4fe1\u6761\u4ef6\u5747\u5df2\u6ee1\u8db3\u65f6\u542f\u52a8\u4e1c\u90e8\u5e94\u6025\u4f9b\u6c34\u3002",
            "water_transfer_target",
            "RULE",
            "FACILITY_REGION",
            ["EXECUTE_ACTION"],
            role="water_repair_team",
            outcomes=[
                ("WATER_TRANSFER_ACTIVE", "\u5e94\u6025\u4f9b\u6c34\u5df2\u542f\u52a8", True)
            ],
            planning=[("east_water_pump_station", "east_emergency_water_supply")],
        ),
    ]
    document["interactions"] = _merge_interactions(
        document.get("interactions", []),
        [
            (
                "relayable",
                "\u53ef\u4f20\u9012\u4fe1\u606f",
                "\u53ef\u4f5c\u4e3a\u4f20\u9012\u4fe1\u606f\u7684 Actor \u76ee\u6807\u3002",
            ),
            (
                "surveyable",
                "\u53ef\u67e5\u63a2\u8d44\u6e90",
                "\u53ef\u6267\u884c\u533a\u57df\u8d44\u6e90\u67e5\u63a2\u3002",
            ),
            (
                "power_targetable",
                "\u53ef\u63a5\u6536\u4f9b\u7535",
                "\u53ef\u63a5\u53d7\u4f9b\u7535\u7684\u8bbe\u65bd\u3002",
            ),
            (
                "heavy_support_target",
                "\u91cd\u578b\u652f\u63f4\u76ee\u6807",
                "\u53ef\u90e8\u7f72\u91cd\u578b\u5de5\u7a0b\u652f\u63f4\u3002",
            ),
            (
                "water_transfer_target",
                "\u5e94\u6025\u4f9b\u6c34\u76ee\u6807",
                "\u53ef\u542f\u52a8\u5e94\u6025\u4f9b\u6c34\u3002",
            ),
        ],
    )
    document["actions"] = actions


def _build_rules(document: dict[str, Any]) -> None:
    rules: list[dict[str, Any]] = [
        _resolve("travel", "TRAVELLED"),
        _resolve("inspect", "INSPECTED"),
        _resolve("survey_resources", "SURVEYED"),
        _resolve("relay_message", "RELAYED"),
        _resolve("transport_resource", "TRANSPORTED"),
        _cost_rule(
            "clear_transport",
            "municipal_repair_materials",
            10,
            "INSUFFICIENT_MUNICIPAL_REPAIR_MATERIALS",
            "\u6e05\u7406\u901a\u9053\u9700\u8981\u8db3\u591f\u7684\u5e02\u653f\u7ef4\u4fee\u6750\u6599\u3002",
        ),
        {
            "key": "clear_transport_resolution",
            "phase": "RESOLVE",
            "action_key": "clear_transport",
            "priority": 0,
            "effects": [
                _set_fact("passable", True),
                _adjust("municipal_repair_materials", -10),
                _emit("CLEARED"),
            ],
        },
    ]
    rules.extend(
        _repair_rules(
            "repair_communications",
            {
                "central_communication_core": [
                    _set_fact("operational", True),
                    _set_region_visibility("central_district"),
                    _set_region_visibility("west_logistics_district"),
                    _set_region_visibility("east_residential_district"),
                    _set_actor_reachability("municipal_transport_team"),
                    _set_actor_reachability("communications_repair_team_alpha"),
                    _emit("COMMUNICATIONS_REPAIRED"),
                ],
                "north_communication_relay": [
                    _set_fact("operational", True),
                    _set_region_visibility("north_industrial_district"),
                    _set_pool_visibility("north_service_depot_stock"),
                    _set_actor_reachability("industrial_repair_team_alpha"),
                    _emit("COMMUNICATIONS_REPAIRED"),
                ],
                "south_communication_core": [
                    _set_fact("operational", True),
                    _set_region_visibility("south_waterfront_district"),
                    _set_region_visibility("southeast_heights_district"),
                    _set_pool_visibility("southeast_district_service_stock"),
                    _set_actor_reachability("water_repair_team_alpha"),
                    _set_actor_reachability("electrical_repair_team_alpha"),
                    _emit("COMMUNICATIONS_REPAIRED"),
                ],
            },
            {
                "central_communication_core": [
                    ("communication_equipment", 10),
                    ("general_engineering_parts", 15),
                ],
                "north_communication_relay": [
                    ("communication_equipment", 10),
                    ("general_engineering_parts", 15),
                ],
                "south_communication_core": [
                    ("communication_equipment", 10),
                    ("general_engineering_parts", 15),
                ],
            },
        )
    )
    rules.extend(
        _repair_rules(
            "repair_industrial_facility",
            {
                "utility_service_depot": [
                    _set_fact("operational", True),
                    _set_pool_availability("north_service_depot_stock"),
                    _emit("INDUSTRIAL_REPAIRED"),
                ],
                "heavy_equipment_yard": [
                    _set_fact("operational", True),
                    _set_fact("heavy_engineering_support", _AVAILABLE),
                    _set_pool_availability("north_heavy_equipment_stock"),
                    _emit("INDUSTRIAL_REPAIRED"),
                ],
                "district_service_center": [
                    _set_fact("operational", True),
                    _set_pool_availability("southeast_district_service_stock"),
                    _emit("INDUSTRIAL_REPAIRED"),
                ],
                "east_community_hospital": [
                    _set_fact("operational", True),
                    _emit("INDUSTRIAL_REPAIRED"),
                ],
                "riverside_shelter": [
                    _set_fact("operational", True),
                    _emit("INDUSTRIAL_REPAIRED"),
                ],
            },
            {
                "utility_service_depot": [
                    ("general_engineering_parts", 5),
                    ("municipal_repair_materials", 20),
                ],
                "heavy_equipment_yard": [
                    ("general_engineering_parts", 5),
                    ("municipal_repair_materials", 10),
                ],
                "district_service_center": [
                    ("general_engineering_parts", 5),
                    ("municipal_repair_materials", 10),
                ],
                "east_community_hospital": [
                    ("general_engineering_parts", 5),
                    ("municipal_repair_materials", 10),
                ],
                "riverside_shelter": [
                    ("general_engineering_parts", 5),
                    ("municipal_repair_materials", 10),
                ],
            },
        )
    )
    rules.extend(
        _repair_rules(
            "repair_electrical",
            {
                "central_hospital": [
                    _set_fact("operational", True),
                    _emit("ELECTRICAL_REPAIRED"),
                ],
                "north_power_substation": [
                    _set_fact("operational", True),
                    _set_fact("power_supply", _AVAILABLE),
                    _emit("ELECTRICAL_REPAIRED"),
                ],
                "east_distribution_station": [
                    _set_fact("operational", True),
                    _set_fact("power_supply", _UNAVAILABLE),
                    _emit("ELECTRICAL_REPAIRED"),
                ],
                "southeast_emergency_power_station": [
                    _set_fact("operational", True),
                    _set_fact("power_supply", _AVAILABLE),
                    _emit("ELECTRICAL_REPAIRED"),
                ],
                "south_substation": [
                    _set_fact("operational", True),
                    _set_fact("power_supply", _UNAVAILABLE),
                    _emit("ELECTRICAL_REPAIRED"),
                ],
            },
            {
                "central_hospital": [("electrical_repair_parts", 10)],
                "north_power_substation": [("electrical_repair_parts", 10)],
                "east_distribution_station": [
                    ("general_engineering_parts", 5),
                    ("electrical_repair_parts", 10),
                ],
                "southeast_emergency_power_station": [
                    ("general_engineering_parts", 5),
                    ("electrical_repair_parts", 20),
                ],
                "south_substation": [
                    ("general_engineering_parts", 5),
                    ("electrical_repair_parts", 20),
                ],
            },
        )
    )
    rules.extend(
        _repair_rules(
            "repair_water_facility",
            {
                "water_treatment_plant": [
                    _set_fact("operational", True),
                    _emit("WATER_FACILITY_REPAIRED"),
                ],
                "south_pump_station": [
                    _set_fact("operational", True),
                    _emit("WATER_FACILITY_REPAIRED"),
                ],
                "east_water_pump_station": [
                    _set_fact("operational", True),
                    _emit("WATER_FACILITY_REPAIRED"),
                ],
            },
            {
                "water_treatment_plant": [
                    ("water_system_parts", 15),
                    ("general_engineering_parts", 5),
                    ("municipal_repair_materials", 10),
                ],
                "south_pump_station": [
                    ("water_system_parts", 10),
                    ("general_engineering_parts", 5),
                ],
                "east_water_pump_station": [
                    ("water_system_parts", 5),
                    ("general_engineering_parts", 5),
                ],
            },
        )
    )
    rules.append(
        {
            "key": "repair_water_facility_heavy_support_required",
            "phase": "PREFLIGHT",
            "action_key": "repair_water_facility",
            "priority": 950,
            "condition": _all(
                _condition(
                    "FACT_EQUALS",
                    {"kind": "CURRENT_TARGET"},
                    "repair_profile",
                    "water_treatment_plant",
                ),
                _condition(
                    "FACT_NOT_EQUALS",
                    {"kind": "EXPLICIT", "node_key": "water_treatment_plant"},
                    "heavy_engineering_support_ready",
                    True,
                ),
            ),
            "effects": [
                _failure(
                    "HEAVY_ENGINEERING_SUPPORT_REQUIRED",
                    "维修南部水处理厂前必须先部署重型工程支援。",
                )
            ],
        }
    )
    rules.extend(
        [
            {
                "key": "supply_power_source_not_operational",
                "phase": "PREFLIGHT",
                "action_key": "supply_power",
                "priority": 100,
                "condition": _condition(
                    "FACT_NOT_EQUALS", {"kind": "ACTION_SOURCE"}, "operational", True
                ),
                "effects": [
                    _failure(
                        "POWER_SOURCE_NOT_OPERATIONAL", "\u7535\u6e90\u5c1a\u672a\u8fd0\u884c\u3002"
                    )
                ],
            },
            {
                "key": "supply_power_source_unavailable",
                "phase": "PREFLIGHT",
                "action_key": "supply_power",
                "priority": 90,
                "condition": _all(
                    _condition(
                        "FACT_EQUALS", {"kind": "ACTION_SOURCE"}, "power_generation_capable", False
                    ),
                    _condition(
                        "FACT_NOT_EQUALS", {"kind": "ACTION_SOURCE"}, "power_supply", _AVAILABLE
                    ),
                ),
                "effects": [
                    _failure("POWER_SOURCE_UNAVAILABLE", "\u7535\u6e90\u4e0d\u53ef\u7528\u3002")
                ],
            },
            {
                "key": "supply_power_resolution",
                "phase": "RESOLVE",
                "action_key": "supply_power",
                "priority": 0,
                "effects": [_set_fact("power_supply", _AVAILABLE), _emit("POWER_SUPPLIED")],
            },
            {
                "key": "heavy_support_unavailable",
                "phase": "PREFLIGHT",
                "action_key": "deploy_heavy_engineering_support",
                "priority": 100,
                "condition": _condition(
                    "FACT_NOT_EQUALS",
                    {"kind": "EXPLICIT", "node_key": "heavy_equipment_yard"},
                    "heavy_engineering_support",
                    _AVAILABLE,
                ),
                "effects": [
                    _failure(
                        "HEAVY_SUPPORT_UNAVAILABLE",
                        "\u91cd\u578b\u5de5\u7a0b\u652f\u63f4\u5c1a\u672a\u89e3\u9501\u3002",
                    )
                ],
            },
            {
                "key": "heavy_support_resolution",
                "phase": "RESOLVE",
                "action_key": "deploy_heavy_engineering_support",
                "priority": 0,
                "effects": [
                    _set_fact("heavy_engineering_support_ready", True),
                    _emit("SUPPORT_DEPLOYED"),
                ],
            },
            {
                "key": "activate_water_requirements",
                "phase": "PREFLIGHT",
                "action_key": "activate_emergency_water_transfer",
                "priority": 100,
                "condition": {
                    "kind": "NOT",
                    "condition": _all(
                        _condition(
                            "FACT_EQUALS",
                            {"kind": "EXPLICIT", "node_key": "water_treatment_plant"},
                            "operational",
                            True,
                        ),
                        _condition(
                            "FACT_EQUALS",
                            {"kind": "EXPLICIT", "node_key": "water_treatment_plant"},
                            "power_supply",
                            _AVAILABLE,
                        ),
                        _condition(
                            "FACT_EQUALS",
                            {"kind": "EXPLICIT", "node_key": "south_pump_station"},
                            "operational",
                            True,
                        ),
                        _condition(
                            "FACT_EQUALS",
                            {"kind": "EXPLICIT", "node_key": "south_pump_station"},
                            "power_supply",
                            _AVAILABLE,
                        ),
                        _condition(
                            "FACT_EQUALS",
                            {"kind": "EXPLICIT", "node_key": "east_water_pump_station"},
                            "operational",
                            True,
                        ),
                        _condition(
                            "FACT_EQUALS",
                            {"kind": "EXPLICIT", "node_key": "east_water_pump_station"},
                            "power_supply",
                            _AVAILABLE,
                        ),
                        _condition(
                            "FACT_EQUALS",
                            {"kind": "EXPLICIT", "node_key": "south_communication_core"},
                            "operational",
                            True,
                        ),
                    ),
                },
                "effects": [
                    _failure(
                        "WATER_TRANSFER_REQUIREMENTS_UNMET",
                        "\u5e94\u6025\u4f9b\u6c34\u7684\u5de5\u7a0b\u4e0e\u901a\u4fe1\u6761\u4ef6\u5c1a\u672a\u6ee1\u8db3\u3002",
                    )
                ],
            },
            {
                "key": "activate_water_resolution",
                "phase": "RESOLVE",
                "action_key": "activate_emergency_water_transfer",
                "priority": 0,
                "effects": [
                    _set_fact("east_emergency_water_supply", _AVAILABLE),
                    _emit("WATER_TRANSFER_ACTIVE"),
                ],
            },
        ]
    )
    document["rules"] = rules


def _build_objectives(document: dict[str, Any]) -> None:
    document["objectives"] = [
        _objective(
            "restore_central_communication_capability",
            "\u6062\u590d\u4e2d\u592e\u901a\u4fe1\u80fd\u529b",
            "\u6062\u590d\u4e2d\u592e\u901a\u4fe1\u6838\u5fc3\u5e76\u91cd\u65b0\u5efa\u7acb\u57ce\u5e02\u8c03\u5ea6\u8054\u7edc\u3002",
            [
                (
                    "central_telecom_operational",
                    "central_telecom_hub",
                    "operational",
                    True,
                    "\u4e2d\u592e\u901a\u4fe1\u6838\u5fc3\u5df2\u6062\u590d\u8fd0\u884c\u3002",
                )
            ],
            ["\u6062\u590d\u4e2d\u592e\u901a\u4fe1\u6838\u5fc3", "Restore central communications"],
            "\u4f18\u5148\u6062\u590d\u4e2d\u592e\u901a\u4fe1\u6838\u5fc3\uff1b\u9075\u5faa\u5df2\u77e5\u7684\u5730\u70b9\u3001\u901a\u9053\u548c\u8d44\u6e90\u6761\u4ef6\u3002",
        ),
        _objective(
            "restore_north_basic_engineering_support",
            "\u6062\u590d\u5317\u90e8\u57fa\u7840\u5de5\u7a0b\u652f\u63f4",
            "\u6062\u590d\u5317\u90e8\u901a\u4fe1\u4e2d\u7ee7\u4e0e\u5e02\u653f\u5de5\u7a0b\u670d\u52a1\u80fd\u529b\u3002",
            [
                (
                    "north_relay_operational",
                    "north_communication_relay",
                    "operational",
                    True,
                    "\u5317\u90e8\u901a\u4fe1\u4e2d\u7ee7\u5df2\u6062\u590d\u3002",
                ),
                (
                    "north_depot_operational",
                    "utility_service_depot",
                    "operational",
                    True,
                    "\u5e02\u653f\u5de5\u7a0b\u670d\u52a1\u57fa\u5730\u5df2\u6062\u590d\u3002",
                ),
            ],
            [
                "\u6062\u590d\u5317\u90e8\u57fa\u7840\u5de5\u7a0b\u652f\u63f4",
                "Restore north engineering support",
            ],
            "\u5148\u786e\u8ba4\u5317\u90e8\u901a\u9053\u4e0e\u901a\u4fe1\u6761\u4ef6\uff0c\u518d\u6309\u4e13\u4e1a\u961f\u4e0e\u5df2\u77e5\u8d44\u6e90\u7f16\u6392\u4fee\u590d\u3002",
        ),
        _objective(
            "restore_east_emergency_power_network",
            "\u6062\u590d\u4e1c\u90e8\u5e94\u6025\u4f9b\u7535\u7f51\u7edc",
            "\u6062\u590d\u4e1c\u90e8\u914d\u7535\u4e0e\u4e1c\u90e8\u5e94\u6025\u8bbe\u65bd\u7684\u4f9b\u7535\u80fd\u529b\u3002",
            [
                (
                    "east_hospital_operational",
                    "east_community_hospital",
                    "operational",
                    True,
                    "\u4e1c\u533a\u793e\u533a\u533b\u9662\u5df2\u6062\u590d\u8fd0\u884c\u3002",
                ),
                (
                    "east_hospital_power",
                    "east_community_hospital",
                    "power_supply",
                    _AVAILABLE,
                    "\u4e1c\u533a\u793e\u533a\u533b\u9662\u5df2\u83b7\u5f97\u4f9b\u7535\u3002",
                ),
                (
                    "riverside_shelter_operational",
                    "riverside_shelter",
                    "operational",
                    True,
                    "\u6cb3\u6ee8\u907f\u96be\u6240\u5df2\u6062\u590d\u8fd0\u884c\u3002",
                ),
                (
                    "riverside_shelter_power",
                    "riverside_shelter",
                    "power_supply",
                    _AVAILABLE,
                    "\u6cb3\u6ee8\u907f\u96be\u6240\u5df2\u83b7\u5f97\u4f9b\u7535\u3002",
                ),
            ],
            ["\u6062\u590d\u4e1c\u90e8\u5e94\u6025\u4f9b\u7535", "Restore east emergency power"],
            "\u4f9d\u636e\u5df2\u77e5\u7684\u7535\u529b\u62d3\u6251\u8fde\u7eed\u4fee\u590d\u3001\u4f9b\u7535\u5e76\u6821\u9a8c\u4e24\u4e2a\u4e1c\u90e8\u8bbe\u65bd\u3002",
        ),
        _objective(
            "restore_east_emergency_water_supply",
            "\u6062\u590d\u4e1c\u90e8\u5e94\u6025\u4f9b\u6c34",
            "\u6062\u590d\u4e1c\u90e8\u5e94\u6025\u4f9b\u6c34\u8c03\u5ea6\u80fd\u529b\u3002",
            [
                (
                    "east_emergency_water_supply",
                    "east_water_pump_station",
                    "east_emergency_water_supply",
                    _AVAILABLE,
                    "\u4e1c\u90e8\u5e94\u6025\u4f9b\u6c34\u5df2\u53ef\u7528\u3002",
                )
            ],
            [
                "\u6062\u590d\u4e1c\u90e8\u5e94\u6025\u4f9b\u6c34",
                "Restore east emergency water supply",
            ],
            "\u6ee1\u8db3\u6c34\u52a1\u8bbe\u65bd\u3001\u4f9b\u7535\u3001\u91cd\u578b\u5de5\u7a0b\u4e0e\u5357\u90e8\u901a\u4fe1\u534f\u8c03\u6761\u4ef6\u540e\u542f\u52a8\u8c03\u5ea6\u3002",
        ),
    ]


def _actor(
    key: str, name: str, role: str, region: str, reachability: str, actions: list[str]
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "role_key": role,
        "persona": f"{name}\u7684\u5e94\u6025\u6062\u590d\u5de5\u4f5c\u961f\u3002",
        "initial_node_key": region,
        "allowed_action_keys": actions,
        "command_reachability": reachability,
    }


def _action(
    key: str,
    name: str,
    description: str,
    interaction: str,
    behavior: str,
    locality: str,
    capabilities: list[str],
    *,
    role: str | None = None,
    parameters: list[dict[str, Any]] | None = None,
    outcomes: list[tuple[str, str, bool]],
    planning: list[tuple[str, str]] | None = None,
    source_relation: str | None = None,
    target_kind: str | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "key": key,
        "name": name,
        "description": description,
        "required_interaction_key": interaction,
        "execution_mode": "IMMEDIATE",
        "parameters": parameters or [],
        "allowed_actor_capabilities": capabilities,
        "expected_outcomes": [
            {"code": code, "name": outcome_name, "success": success}
            for code, outcome_name, success in outcomes
        ],
        "planning": {
            "terminal_effects": [
                {"node_key": node_key, "fact_key": fact_key}
                for node_key, fact_key in (planning or [])
            ],
            "success_outcome_codes": [code for code, _name, success in outcomes if success],
        },
        "behavior": behavior,
        "locality": locality,
    }
    if role is not None:
        action["required_actor_role_key"] = role
    if source_relation is not None:
        action["source_relation_type_key"] = source_relation
    if target_kind is not None:
        action["target_kind"] = target_kind
    return action


def _objective(
    key: str,
    name: str,
    description: str,
    requirements: list[tuple[str, str, str, Any, str]],
    aliases: list[str],
    guidance: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "description": description,
        "completion_requirements": [
            {
                "key": requirement_key,
                "node_key": node_key,
                "fact_key": fact_key,
                "accepted_values": [value],
                "description": requirement_description,
            }
            for requirement_key, node_key, fact_key, value, requirement_description in requirements
        ],
        "goal_aliases": aliases,
        "planning_guidance": guidance,
    }


def _repair_rules(
    action_key: str,
    outcomes: dict[str, list[dict[str, Any]]],
    costs: dict[str, list[tuple[str, int]]],
) -> list[dict[str, Any]]:
    profiles = list(outcomes)
    rules: list[dict[str, Any]] = [
        {
            "key": f"{action_key}_target_profile_required",
            "phase": "PREFLIGHT",
            "action_key": action_key,
            "priority": 1000,
            "condition": {
                "kind": "NOT",
                "condition": {
                    "kind": "FACT_IN",
                    "node": {"kind": "CURRENT_TARGET"},
                    "fact_key": "repair_profile",
                    "values": profiles,
                },
            },
            "effects": [
                _failure(
                    "REPAIR_TARGET_UNSUPPORTED",
                    "\u8be5\u4e13\u4e1a\u961f\u4e0d\u80fd\u4fee\u590d\u8be5\u76ee\u6807\u3002",
                )
            ],
        }
    ]
    for profile_index, (profile, profile_costs) in enumerate(costs.items()):
        for index, (resource_key, amount) in enumerate(profile_costs):
            cost_rule = _cost_rule(
                action_key,
                resource_key,
                amount,
                f"INSUFFICIENT_{resource_key.upper()}",
                "\u5f53\u524d Region \u7684\u5df2\u77e5\u53ef\u7528\u8d44\u6e90\u4e0d\u8db3\u3002",
                priority=900 - index,
                profile=profile,
            )
            cost_rule["key"] = f"cost_{action_key}_{profile_index}_{resource_key}"
            rules.append(cost_rule)
        effects = [effect for effect in outcomes[profile] if effect.get("kind") != "EMIT_OUTCOME"]
        effects.extend(_adjust(resource_key, -amount) for resource_key, amount in costs[profile])
        effects.append(
            _emit(
                next(
                    effect["outcome_code"]
                    for effect in outcomes[profile]
                    if effect.get("kind") == "EMIT_OUTCOME"
                )
            )
        )
        rules.append(
            {
                "key": f"{action_key}_{profile}_resolution",
                "phase": "RESOLVE",
                "action_key": action_key,
                "priority": 10,
                "condition": _condition(
                    "FACT_EQUALS", {"kind": "CURRENT_TARGET"}, "repair_profile", profile
                ),
                "effects": effects,
            }
        )
    return rules


def _cost_rule(
    action_key: str,
    resource_key: str,
    amount: int,
    failure_code: str,
    message: str,
    *,
    priority: int = 900,
    profile: str | None = None,
) -> dict[str, Any]:
    if amount <= 0:
        return {
            "key": f"{action_key}_noop_cost",
            "phase": "PREFLIGHT",
            "action_key": action_key,
            "priority": -1000,
            "condition": {
                "kind": "PARAMETER_COMPARE",
                "parameter_key": "amount",
                "operator": "LT",
                "value": 0,
            },
            "effects": [_failure("UNUSED", "unused")],
        }
    resource_condition = {
        "kind": "RESOURCE_COMPARE",
        "resource_key": resource_key,
        "resource_scope": {"kind": "ACTOR_CURRENT_REGION"},
        "operator": "LT",
        "value": amount,
    }
    condition = resource_condition
    if profile is not None:
        condition = _all(
            _condition("FACT_EQUALS", {"kind": "CURRENT_TARGET"}, "repair_profile", profile),
            resource_condition,
        )
    return {
        "key": f"{action_key}_{resource_key}_{amount}_required",
        "phase": "PREFLIGHT",
        "action_key": action_key,
        "priority": priority,
        "condition": condition,
        "effects": [_failure(failure_code, message)],
    }


def _resolve(action_key: str, outcome_code: str) -> dict[str, Any]:
    return {
        "key": f"{action_key}_resolution",
        "phase": "RESOLVE",
        "action_key": action_key,
        "priority": 0,
        "effects": [_emit(outcome_code)],
    }


def _condition(kind: str, node: dict[str, Any], fact_key: str, value: Any) -> dict[str, Any]:
    return {"kind": kind, "node": node, "fact_key": fact_key, "value": value}


def _all(*conditions: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "ALL", "conditions": list(conditions)}


def _set_fact(fact_key: str, value: Any) -> dict[str, Any]:
    return {
        "kind": "SET_FACT",
        "node": {"kind": "CURRENT_TARGET"},
        "fact_key": fact_key,
        "value": {"source": "LITERAL", "literal": value},
    }


def _adjust(resource_key: str, amount: int) -> dict[str, Any]:
    return {
        "kind": "ADJUST_RESOURCE",
        "resource_key": resource_key,
        "resource_scope": {"kind": "ACTOR_CURRENT_REGION"},
        "amount": {"source": "LITERAL", "literal": amount},
    }


def _emit(outcome_code: str) -> dict[str, Any]:
    return {"kind": "EMIT_OUTCOME", "outcome_code": outcome_code}


def _failure(code: str, message: str) -> dict[str, Any]:
    return {"kind": "EMIT_FAILURE", "failure_code": code, "message": message, "retryable": True}


def _set_actor_reachability(actor_key: str) -> dict[str, Any]:
    return {
        "kind": "SET_ACTOR_COMMAND_REACHABILITY",
        "actor_key": actor_key,
        "command_reachability": "ONLINE",
    }


def _set_region_visibility(region_key: str) -> dict[str, Any]:
    return {
        "kind": "SET_REGION_RESOURCE_VISIBILITY",
        "region_key": region_key,
        "visibility": "VISIBLE",
    }


def _set_pool_visibility(pool_key: str) -> dict[str, Any]:
    return {"kind": "SET_RESOURCE_POOL_VISIBILITY", "pool_key": pool_key, "visibility": "VISIBLE"}


def _set_pool_availability(pool_key: str) -> dict[str, Any]:
    return {
        "kind": "SET_RESOURCE_POOL_AVAILABILITY",
        "pool_key": pool_key,
        "availability": "AVAILABLE",
    }


def _pool(
    pool_key: str,
    resource_key: str,
    region_key: str,
    quantity: int,
    *,
    facility_key: str | None = None,
    visibility: str = _AVAILABLE,
    availability: str = _AVAILABLE,
    survey_discoverable: bool = False,
    availability_requirement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "pool_key": pool_key,
        "resource_key": resource_key,
        "region_key": region_key,
        "facility_key": facility_key,
        "quantity": quantity,
        "reserved_value": 0,
        "visibility": "VISIBLE" if visibility == _AVAILABLE else visibility,
        "availability": availability,
        "survey_discoverable": survey_discoverable,
        **(
            {"availability_requirement": availability_requirement}
            if availability_requirement is not None
            else {}
        ),
    }


def _fact(
    key: str,
    name: str,
    value_type: str,
    value: Any,
    visibility: str,
    *,
    description: str = "",
    allowed_values: list[Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "key": key,
        "name": name,
        "description": description or name,
        "value_type": value_type,
        "initial_value": value,
        "initial_visibility": visibility,
    }
    if allowed_values is not None:
        result["allowed_values"] = allowed_values
    return result


def _upsert_fact(node: dict[str, Any], fact: dict[str, Any]) -> None:
    facts = node.setdefault("facts", [])
    for index, existing in enumerate(facts):
        if existing["key"] == fact["key"]:
            facts[index] = fact
            return
    facts.append(fact)


def _add_interaction(node: dict[str, Any], interaction_key: str) -> None:
    interactions = node.setdefault("interaction_keys", [])
    if interaction_key not in interactions:
        interactions.append(interaction_key)


def _merge_interactions(
    existing: list[dict[str, Any]], additions: list[tuple[str, str, str]]
) -> list[dict[str, Any]]:
    result = list(existing)
    keys = {item["key"] for item in result}
    for key, name, description in additions:
        if key not in keys:
            result.append({"key": key, "name": name, "description": description})
    return result


def _relation_key(source: str, relation_type: str, target: str) -> str:
    return f"{source}__{relation_type}__{target}"


__all__ = [
    "LINJIANG_V1_DRAFT_KEY",
    "LINJIANG_V1_DRAFT_NAME",
    "build_linjiang_v1_definition",
    "build_linjiang_v1_draft_document",
]
