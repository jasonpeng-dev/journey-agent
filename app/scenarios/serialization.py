"""Canonical serialization and hashing for Scenario definition documents."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.scenarios.documents import parse_scenario_document


def canonical_document(document: dict[str, Any]) -> ScenarioDefinitionV2:
    """Validate and normalize ordering without changing Scenario semantics."""

    return _canonical_v2(parse_scenario_document(document))


def _canonical_v2(parsed: ScenarioDefinitionV2) -> ScenarioDefinitionV2:
    normalized = parsed.model_dump(mode="json")
    world = normalized["world"]
    world["node_types"].sort(key=lambda item: item["key"])
    world["nodes"].sort(key=lambda item: item["key"])
    for node in world["nodes"]:
        node["interaction_keys"].sort()
        node["facts"].sort(key=lambda item: item["key"])
        for fact in node["facts"]:
            if fact["value_type"] == "ENUM":
                fact["allowed_values"].sort(key=_scalar_sort_key)
    world["relations"].sort(
        key=lambda item: (
            item["source_node_key"],
            item["relation_type_key"],
            item["target_node_key"],
            item.get("key") or "",
        )
    )
    world["resources"].sort(key=lambda item: item["key"])

    normalized["interactions"].sort(key=lambda item: item["key"])
    actors = normalized["actors"]
    actors["roles"].sort(key=lambda item: item["key"])
    for role in actors["roles"]:
        role["capabilities"].sort()
    actors["actor_profiles"].sort(key=lambda item: item["key"])
    for actor in actors["actor_profiles"]:
        actor["doctrine"].sort(key=lambda item: item["key"])
        actor["allowed_action_keys"].sort()
        _sort_authority(actor["authority_policy"])

    normalized["actions"].sort(key=lambda item: item["key"])
    for action in normalized["actions"]:
        action["parameters"].sort(key=lambda item: item["key"])
        for parameter in action["parameters"]:
            if parameter["value_type"] == "ENUM":
                parameter["allowed_values"].sort(key=_scalar_sort_key)
        action["allowed_actor_capabilities"].sort()
        action["expected_outcomes"].sort(key=lambda item: item["code"])
        _sort_authority(action["authority_policy"])
        planning = action["planning"]
        planning["terminal_effects"].sort(key=lambda item: (item["node_key"], item["fact_key"]))
        planning["supporting_effects"].sort(key=lambda item: (item["node_key"], item["fact_key"]))
        planning["success_outcome_codes"].sort()
        planning["wait_success_outcome_codes"].sort()

    normalized["rules"].sort(
        key=lambda item: (
            item["action_key"],
            item["phase"],
            -item["priority"],
            item["key"],
        )
    )
    normalized["objectives"].sort(key=lambda item: item["key"])
    for objective in normalized["objectives"]:
        objective["completion_requirements"].sort(key=lambda item: item["key"])
        for requirement in objective["completion_requirements"]:
            requirement["accepted_values"].sort(key=_scalar_sort_key)
        objective["prerequisites"].sort(key=lambda item: item["key"])
        for prerequisite in objective["prerequisites"]:
            prerequisite["requirements"].sort(key=lambda item: item["key"])
            for requirement in prerequisite["requirements"]:
                requirement["accepted_values"].sort(key=_scalar_sort_key)
        objective["subsumes"].sort()
        objective["goal_aliases"].sort(key=lambda value: value.casefold())
        objective["goal_examples"].sort(key=lambda value: value.casefold())
    normalized["planning"]["recovery_hints"].sort(key=lambda item: item["failure_code"])
    return ScenarioDefinitionV2.model_validate(normalized)


def _sort_authority(policy: dict[str, Any]) -> None:
    policy["autonomous_limits"].sort(key=lambda item: item["parameter_key"])
    policy["approval_required_values"].sort(key=lambda item: item["parameter_key"])
    for approval in policy["approval_required_values"]:
        approval["values"].sort(key=_scalar_sort_key)


def _scalar_sort_key(value: object) -> tuple[str, str]:
    return type(value).__name__, str(value)


def canonical_document_bytes(document: dict[str, Any]) -> bytes:
    normalized = canonical_document(document).model_dump(mode="json")
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def scenario_content_hash(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_document_bytes(document)).hexdigest()


__all__ = ["canonical_document", "canonical_document_bytes", "scenario_content_hash"]
