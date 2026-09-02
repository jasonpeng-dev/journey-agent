from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.scenarios.documents import parse_scenario_document
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.serialization import canonical_document, scenario_content_hash
from app.scenarios.validation import ScenarioDefinitionValidator
from app.scenarios.versions import ScenarioVersionRepository
from app.services.scenarios import ScenarioService
from tests.unit.test_scenario_definition_v2 import _contract_scenario_document


def _derived_document() -> dict[str, Any]:
    document = deepcopy(_contract_scenario_document())
    document["derived_states"] = [
        {
            "key": "patient_ready",
            "name": "Patient ready",
            "description": "Patient One is ready for discharge.",
            "value_type": "BOOLEAN",
            "available_value": True,
            "unavailable_value": False,
            "goal_addressable": True,
            "dependencies": [
                {
                    "kind": "FACT",
                    "node_key": "patient_one",
                    "fact_key": "stable",
                    "accepted_values": [True],
                }
            ],
        },
        {
            "key": "clinic_ready",
            "name": "Clinic ready",
            "description": "The clinic can discharge the patient.",
            "value_type": "ENUM",
            "available_value": "READY",
            "unavailable_value": "BLOCKED",
            "allowed_values": ["READY", "BLOCKED"],
            "goal_addressable": True,
            "dependencies": [
                {
                    "kind": "DERIVED_STATE",
                    "derived_key": "patient_ready",
                    "accepted_values": [True],
                }
            ],
        },
    ]
    document["objectives"][0]["completion_requirements"] = [
        {
            "key": "patient_is_ready",
            "kind": "DERIVED_STATE",
            "derived_key": "patient_ready",
            "accepted_values": [True],
            "description": "Patient One is ready.",
        }
    ]
    return document


def _resource_derived_document() -> dict[str, Any]:
    document = _derived_document()
    document["metadata"]["locality"] = {
        "enabled": True,
        "scoped_resources": True,
        "region_node_type_key": "room",
        "facility_node_type_key": "room",
        "transport_node_type_key": "room",
        "located_in_relation_type_key": "contains",
        "transport_endpoint_relation_type_key": "contains",
    }
    document["derived_states"][0]["dependencies"].append(
        {
            "kind": "RESOURCE_AT_LEAST",
            "region_key": "triage_room",
            "resource_key": "medicine",
            "minimum": 1,
        }
    )
    return document


def _total_ordering_document() -> dict[str, Any]:
    document = _resource_derived_document()
    patient = next(node for node in document["world"]["nodes"] if node["key"] == "patient_one")
    patient["facts"].append(
        {
            "key": "diagnosis",
            "name": "Diagnosis",
            "value_type": "BOOLEAN",
            "initial_value": False,
            "initial_visibility": "KNOWN",
        }
    )
    patient_ready = document["derived_states"][0]
    patient_ready["dependencies"][0]["knowledge_gate"] = {
        "node_key": "patient_one",
        "fact_key": "stable",
        "accepted_values": [True, False],
    }
    patient_ready["dependencies"].append(
        {
            "kind": "FACT",
            "node_key": "patient_one",
            "fact_key": "diagnosis",
            "accepted_values": [False],
        }
    )
    patient_ready["dependencies"].append(
        {
            "kind": "RESOURCE_AT_LEAST",
            "region_key": "triage_room",
            "resource_key": "medicine",
            "minimum": 2,
        }
    )
    document["derived_states"].append(
        {
            "key": "ward_ready",
            "name": "Ward ready",
            "description": "The ward is ready.",
            "value_type": "BOOLEAN",
            "available_value": True,
            "unavailable_value": False,
            "dependencies": [
                {
                    "kind": "FACT",
                    "node_key": "patient_one",
                    "fact_key": "diagnosis",
                    "accepted_values": [False],
                }
            ],
        }
    )
    document["derived_states"][1]["dependencies"].append(
        {
            "kind": "DERIVED_STATE",
            "derived_key": "ward_ready",
            "accepted_values": [True],
        }
    )
    return document


def test_derived_domain_is_typed_and_canonical() -> None:
    source = _derived_document()
    parsed = parse_scenario_document(source)

    assert isinstance(parsed, ScenarioDefinitionV2)
    assert tuple(parsed.derived_state_definitions) == ("patient_ready", "clinic_ready")
    assert parsed.derived_state_definitions["clinic_ready"].target_value == "READY"
    assert parsed.objectives[0].completion_requirements[0].derived_key == "patient_ready"

    reordered = deepcopy(source)
    reordered["derived_states"].reverse()
    reordered["derived_states"][1]["dependencies"].reverse()
    assert scenario_content_hash(reordered) == scenario_content_hash(source)
    assert canonical_document(reordered) == canonical_document(source)


def test_derived_dependency_canonicalization_has_a_total_order() -> None:
    source = _total_ordering_document()
    expected = scenario_content_hash(source)
    variants: list[dict[str, Any]] = []

    state_order = deepcopy(source)
    state_order["derived_states"].reverse()
    variants.append(state_order)

    dependency_order = deepcopy(source)
    dependency_order["derived_states"][0]["dependencies"].reverse()
    variants.append(dependency_order)

    accepted_value_order = deepcopy(source)
    accepted_value_order["derived_states"][0]["dependencies"][0]["knowledge_gate"][
        "accepted_values"
    ].reverse()
    variants.append(accepted_value_order)

    same_node_fact_order = deepcopy(source)
    dependencies = same_node_fact_order["derived_states"][0]["dependencies"]
    fact_dependencies = [item for item in dependencies if item["kind"] == "FACT"]
    remaining = [item for item in dependencies if item["kind"] != "FACT"]
    same_node_fact_order["derived_states"][0]["dependencies"] = [
        *reversed(fact_dependencies),
        *remaining,
    ]
    variants.append(same_node_fact_order)

    resource_dependency_order = deepcopy(source)
    dependencies = resource_dependency_order["derived_states"][0]["dependencies"]
    resource_dependencies = [item for item in dependencies if item["kind"] == "RESOURCE_AT_LEAST"]
    remaining = [item for item in dependencies if item["kind"] != "RESOURCE_AT_LEAST"]
    resource_dependency_order["derived_states"][0]["dependencies"] = [
        *remaining,
        *reversed(resource_dependencies),
    ]
    variants.append(resource_dependency_order)

    nested_dependency_order = deepcopy(source)
    nested_dependency_order["derived_states"][1]["dependencies"].reverse()
    variants.append(nested_dependency_order)

    assert len(variants) == 6
    assert all(scenario_content_hash(variant) == expected for variant in variants)


def test_derived_state_publishes_and_loads_as_part_of_exact_snapshot(session: Session) -> None:
    definition = ScenarioDefinitionV2.model_validate(_derived_document())
    repository = ScenarioDefinitionRepository(session)
    scenario = repository.persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    loaded = ScenarioVersionRepository(session).load(version.id)

    assert isinstance(loaded.definition, ScenarioDefinitionV2)
    assert tuple(loaded.definition.derived_state_definitions) == (
        "clinic_ready",
        "patient_ready",
    )
    assert version.content_hash == scenario_content_hash(loaded.definition.model_dump(mode="json"))


def test_derived_dependency_unknown_fact_fails_closed() -> None:
    document = _derived_document()
    document["derived_states"][0]["dependencies"][0]["fact_key"] = "missing_fact"

    with pytest.raises(ValueError, match="unknown Fact"):
        ScenarioDefinitionV2.model_validate(document)


def test_derived_dependency_unknown_resource_fails_closed() -> None:
    document = _resource_derived_document()
    document["derived_states"][0]["dependencies"][1]["resource_key"] = "missing_resource"

    with pytest.raises(ValueError, match=r"Derived State .* Resource"):
        ScenarioDefinitionV2.model_validate(document)


def test_derived_dependency_unknown_state_fails_closed() -> None:
    document = _derived_document()
    document["derived_states"][1]["dependencies"][0]["derived_key"] = "missing_derived"

    with pytest.raises(ValueError, match="unknown"):
        ScenarioDefinitionV2.model_validate(document)


def test_derived_duplicate_type_mismatch_and_cycles_fail_closed() -> None:
    duplicate = _derived_document()
    duplicate["derived_states"].append(deepcopy(duplicate["derived_states"][0]))
    with pytest.raises(ValueError, match="Derived State keys"):
        ScenarioDefinitionV2.model_validate(duplicate)

    type_mismatch = _derived_document()
    type_mismatch["derived_states"][0]["available_value"] = "true"
    with pytest.raises(ValueError, match="value_type"):
        ScenarioDefinitionV2.model_validate(type_mismatch)

    self_cycle = _derived_document()
    self_cycle["derived_states"][0]["dependencies"] = [
        {
            "kind": "DERIVED_STATE",
            "derived_key": "patient_ready",
            "accepted_values": [True],
        }
    ]
    with pytest.raises(ValueError, match="contains a cycle"):
        ScenarioDefinitionV2.model_validate(self_cycle)

    multi_cycle = _derived_document()
    multi_cycle["derived_states"][0]["dependencies"] = [
        {
            "kind": "DERIVED_STATE",
            "derived_key": "clinic_ready",
            "accepted_values": ["READY"],
        }
    ]
    with pytest.raises(ValueError, match="contains a cycle"):
        ScenarioDefinitionV2.model_validate(multi_cycle)


def test_rules_cannot_mutate_a_derived_state_directly() -> None:
    document = _derived_document()
    document["rules"][0]["effects"].insert(
        0,
        {
            "kind": "SET_FACT",
            "node": {"kind": "EXPLICIT", "node_key": "patient_one"},
            "fact_key": "patient_ready",
            "value": {"source": "LITERAL", "literal": True},
        },
    )

    with pytest.raises(ValueError, match="Fact"):
        ScenarioDefinitionV2.model_validate(document)


def test_legacy_v2_without_derived_states_remains_compatible() -> None:
    legacy = _contract_scenario_document()
    parsed = parse_scenario_document(legacy)

    assert parsed.derived_states == ()
    assert ScenarioDefinitionValidator().validate(legacy).passed


def test_derived_schema_rejects_unknown_fields() -> None:
    document = _derived_document()
    document["derived_states"][0]["hidden_truth"] = False

    with pytest.raises(ValidationError):
        ScenarioDefinitionV2.model_validate(document)
