"""Canonical serialization and hashing for Scenario definition documents."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.scenarios.documents import ScenarioDefinitionDocument


def canonical_document(document: dict[str, Any]) -> ScenarioDefinitionDocument:
    """Validate and normalize semantic collection ordering through domain objects."""

    parsed = ScenarioDefinitionDocument.model_validate(document)
    normalized = ScenarioDefinitionDocument.from_domain(parsed.to_domain()).model_dump(mode="json")
    world = normalized["world"]
    world["interactions"].sort(key=lambda item: item["key"])
    world["nodes"].sort(key=lambda item: item["key"])
    for node in world["nodes"]:
        node["interaction_keys"].sort()
        node["facts"].sort(key=lambda item: item["key"])
        for fact in node["facts"]:
            if fact["value_type"] == "ENUM":
                fact["allowed_values"].sort(key=lambda value: (type(value).__name__, str(value)))
    world["relations"].sort(
        key=lambda item: (
            item["source_node_key"],
            item["relation_type"],
            item["target_node_key"],
        )
    )
    world["resources"].sort(key=lambda item: item["key"])
    objectives = normalized["objective_catalog"]["definitions"]
    objectives.sort(key=lambda item: item["key"])
    for objective in objectives:
        objective["completion_requirements"].sort(key=lambda item: item["key"])
        objective["prerequisites"].sort(key=lambda item: item["key"])
        objective["subsumes"].sort()
        for prerequisite in objective["prerequisites"]:
            prerequisite["requirements"].sort(key=lambda item: item["key"])
    return ScenarioDefinitionDocument.model_validate(normalized)


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
