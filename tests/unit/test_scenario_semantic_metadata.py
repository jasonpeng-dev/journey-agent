from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.domain.scenario_v2 import (
    DerivedStateDefinitionV2,
    FactDefinitionV2,
    NodeFamilyV2,
    ResourceDefinitionV2,
    ScenarioDefinitionV2,
)
from app.scenarios.serialization import canonical_document_payload, scenario_content_hash
from tests.scenario_fixtures import GENERIC_TEST, LINJIANG_V2_TEST


def _enum_fact_payload() -> dict[str, object]:
    return {
        "key": "status",
        "name": "Status",
        "value_type": "ENUM",
        "initial_value": "READY",
        "initial_visibility": "KNOWN",
        "allowed_values": [
            {"value": "READY", "label": "Ready"},
            {"value": "BLOCKED", "label": "Blocked"},
        ],
    }


def test_typed_fact_value_labels_round_trip_and_preserve_scalar_runtime_domain() -> None:
    fact = FactDefinitionV2.model_validate(_enum_fact_payload())

    assert fact.allowed_values == ("READY", "BLOCKED")
    assert [(item.value, item.label) for item in fact.value_labels] == [
        ("READY", "Ready"),
        ("BLOCKED", "Blocked"),
    ]
    round_tripped = FactDefinitionV2.model_validate(fact.model_dump(mode="json"))
    assert round_tripped == fact


def test_fact_presentation_metadata_is_typed_optional_and_round_trips() -> None:
    payload = _enum_fact_payload()
    payload["presentation_role"] = "HEADER_PRIMARY"
    payload["value_labels"] = [
        {
            "value": "READY",
            "label": "Ready",
            "summary_label": "Ready now",
            "detail_label": "System ready",
        },
        {"value": "BLOCKED", "label": "Blocked"},
    ]
    fact = FactDefinitionV2.model_validate(payload)
    assert fact.presentation_role is not None
    assert fact.presentation_role.value == "HEADER_PRIMARY"
    assert fact.value_labels[0].summary_label == "Ready now"
    assert fact.value_labels[0].detail_label == "System ready"
    assert FactDefinitionV2.model_validate(fact.model_dump(mode="json")) == fact


def test_typed_value_labels_support_non_string_identity_and_reject_bad_vocabularies() -> None:
    boolean_fact = FactDefinitionV2.model_validate(
        {
            "key": "passable",
            "name": "Passable",
            "value_type": "BOOLEAN",
            "initial_value": True,
            "initial_visibility": "KNOWN",
            "value_labels": [
                {"value": True, "label": "Passable"},
                {"value": False, "label": "Blocked"},
            ],
        }
    )
    assert [item.value for item in boolean_fact.value_labels] == [True, False]

    with pytest.raises(ValidationError):
        FactDefinitionV2.model_validate(
            {
                **_enum_fact_payload(),
                "allowed_values": [
                    {"value": "READY", "label": "Ready"},
                    {"value": "READY", "label": "Again"},
                ],
            }
        )
    with pytest.raises(ValidationError):
        FactDefinitionV2.model_validate(
            {
                **_enum_fact_payload(),
                "value_labels": {"READY": "Ready"},
            }
        )
    with pytest.raises(ValidationError):
        FactDefinitionV2.model_validate(
            {
                "key": "status",
                "name": "Status",
                "value_type": "STRING",
                "initial_value": "READY",
                "initial_visibility": "KNOWN",
                "value_labels": [{"value": 1, "label": "One"}],
            }
        )


def test_derived_value_labels_round_trip() -> None:
    state = DerivedStateDefinitionV2.model_validate(
        {
            "key": "readiness",
            "name": "Readiness",
            "value_type": "ENUM",
            "available_value": "READY",
            "unavailable_value": "BLOCKED",
            "allowed_values": ["READY", "BLOCKED"],
            "value_labels": [
                {"value": "READY", "label": "Ready"},
                {"value": "BLOCKED", "label": "Blocked"},
            ],
            "dependencies": [
                {
                    "kind": "FACT",
                    "node_key": "clinic",
                    "fact_key": "status",
                    "accepted_values": ["READY"],
                }
            ],
        }
    )

    assert DerivedStateDefinitionV2.model_validate(state.model_dump(mode="json")) == state


def test_resource_units_are_optional_and_round_trip() -> None:
    resource = ResourceDefinitionV2.model_validate(
        {
            "key": "fuel",
            "name": "Fuel",
            "initial_value": 10,
            "minimum": 0,
            "maximum": 20,
            "unit": "L",
            "display_unit": "liters",
        }
    )
    assert ResourceDefinitionV2.model_validate(resource.model_dump(mode="json")) == resource
    assert "unit" not in ResourceDefinitionV2(
        key="fuel",
        name="Fuel",
        initial_value=10,
        minimum=0,
    ).model_dump(mode="json")


def test_relation_catalog_validates_edges_and_keeps_legacy_uncatalogued_fixture_readable() -> None:
    legacy = deepcopy(GENERIC_TEST.model_dump(mode="json"))
    assert "relation_types" not in legacy["world"]
    ScenarioDefinitionV2.model_validate(legacy)

    catalogued = deepcopy(legacy)
    catalogued["world"]["relation_types"] = [
        {
            "key": "contains",
            "name": "Contains",
            "description": "Contains the target entity.",
        }
    ]
    parsed = ScenarioDefinitionV2.model_validate(catalogued)
    assert parsed.world.relation_type("contains") is not None

    broken = deepcopy(catalogued)
    broken["world"]["relations"][0]["relation_type_key"] = "missing"
    with pytest.raises(ValidationError):
        ScenarioDefinitionV2.model_validate(broken)


def test_locality_is_the_single_node_family_authority_for_builtin_and_generic_scenarios() -> None:
    assert {item.key for item in LINJIANG_V2_TEST.world.relation_types} == {
        "contains",
        "endpoint",
        "located_in",
        "supplies_power_to",
    }
    assert LINJIANG_V2_TEST.node_family_for_type("region") == NodeFamilyV2.REGION
    assert LINJIANG_V2_TEST.node_family_for_type("facility") == NodeFamilyV2.FACILITY
    assert LINJIANG_V2_TEST.node_family_for_type("transport") == NodeFamilyV2.TRANSPORT
    assert LINJIANG_V2_TEST.node_family_for_node("central_district") == NodeFamilyV2.REGION

    assert GENERIC_TEST.node_family_for_type("room") == NodeFamilyV2.GENERIC
    assert all(
        family == NodeFamilyV2.GENERIC for family in GENERIC_TEST.node_family_metadata().values()
    )


def test_legacy_semantic_hash_is_stable_when_optional_metadata_is_omitted() -> None:
    old_document = deepcopy(GENERIC_TEST.model_dump(mode="json"))
    old_hash = scenario_content_hash(old_document)
    canonical = canonical_document_payload(old_document)

    assert scenario_content_hash(canonical) == old_hash
    assert "relation_types" not in canonical["world"]
    assert all(
        "value_labels" not in fact for node in canonical["world"]["nodes"] for fact in node["facts"]
    )
    assert all("unit" not in resource for resource in canonical["world"]["resources"])
