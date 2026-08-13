"""Persisted Scenario document decoder for the v2+ generic engine."""

from __future__ import annotations

from typing import Any

from app.domain.scenario_v2 import ScenarioDefinitionV2

SCENARIO_DOCUMENT_SCHEMA_VERSION = 2
SUPPORTED_SCENARIO_DOCUMENT_SCHEMA_VERSIONS = frozenset({2})
type ScenarioDefinitionDocument = ScenarioDefinitionV2


def parse_scenario_document(document: dict[str, Any]) -> ScenarioDefinitionV2:
    schema_version = document.get("schema_version")
    if type(schema_version) is not int:
        raise ValueError("Scenario definition requires an integer schema_version")
    if schema_version != SCENARIO_DOCUMENT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported Scenario definition schema_version {schema_version}; v2 is required"
        )
    return ScenarioDefinitionV2.model_validate(document)


__all__ = [
    "SCENARIO_DOCUMENT_SCHEMA_VERSION",
    "SUPPORTED_SCENARIO_DOCUMENT_SCHEMA_VERSIONS",
    "ScenarioDefinitionDocument",
    "parse_scenario_document",
]
