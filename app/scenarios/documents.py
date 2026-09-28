"""Versioned Scenario document decoders.

Version 2 is the immutable legacy wire contract.  Version 3 is the current
authoring contract.  ``parse_scenario_document`` returns the normalized v2
semantic model consumed by existing runtime code; callers that need to retain
the authored version should use ``parse_scenario_document_versioned``.
"""

from __future__ import annotations

from typing import Any

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.domain.scenario_v3 import ScenarioDefinitionV3

SCENARIO_DOCUMENT_SCHEMA_VERSION = 3
LEGACY_SCENARIO_DOCUMENT_SCHEMA_VERSION = 2
SUPPORTED_SCENARIO_DOCUMENT_SCHEMA_VERSIONS = frozenset({2, 3})
type ScenarioDefinitionDocument = ScenarioDefinitionV2 | ScenarioDefinitionV3


def parse_scenario_document(document: dict[str, Any]) -> ScenarioDefinitionV2:
    """Parse one document into the normalized internal semantic model."""

    authored = parse_scenario_document_versioned(document)
    return authored.to_v2() if isinstance(authored, ScenarioDefinitionV3) else authored


def parse_scenario_document_versioned(document: dict[str, Any]) -> ScenarioDefinitionDocument:
    """Parse according to ``schema_version`` without rewriting the document."""

    schema_version = document.get("schema_version")
    if type(schema_version) is not int:
        raise ValueError("Scenario definition requires an integer schema_version")
    if schema_version == LEGACY_SCENARIO_DOCUMENT_SCHEMA_VERSION:
        return ScenarioDefinitionV2.model_validate(document)
    if schema_version == SCENARIO_DOCUMENT_SCHEMA_VERSION:
        return ScenarioDefinitionV3.model_validate(document)
    if schema_version not in SUPPORTED_SCENARIO_DOCUMENT_SCHEMA_VERSIONS:
        raise ValueError(
            f"Unsupported Scenario definition schema_version {schema_version}; "
            "v2 is required for legacy documents (v3 is current)"
        )
    raise AssertionError("unreachable")


# The explicit alias makes the load-time compatibility boundary discoverable
# to services that must preserve the authored schema version.
parse_versioned_scenario_document = parse_scenario_document_versioned


__all__ = [
    "LEGACY_SCENARIO_DOCUMENT_SCHEMA_VERSION",
    "SCENARIO_DOCUMENT_SCHEMA_VERSION",
    "SUPPORTED_SCENARIO_DOCUMENT_SCHEMA_VERSIONS",
    "ScenarioDefinitionDocument",
    "parse_scenario_document",
    "parse_scenario_document_versioned",
    "parse_versioned_scenario_document",
]
