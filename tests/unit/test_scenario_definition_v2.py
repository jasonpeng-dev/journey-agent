from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.domain.scenario import ScenarioDefinition
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.scenarios.documents import (
    ScenarioDefinitionDocument,
    parse_scenario_document,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.runtime_binding import (
    ScenarioRuntimeBindingError,
    require_v1_runtime_definition,
)
from app.scenarios.serialization import canonical_document, scenario_content_hash
from app.scenarios.starfire.scenario import STARFIRE_SCENARIO_DEFINITION
from app.scenarios.validation import ScenarioDefinitionValidator
from app.scenarios.versions import ScenarioVersionRepository
from app.services.scenarios import ScenarioService


def _medical_scenario_document() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "metadata": {
            "key": "medical_emergency",
            "name": "Medical Emergency",
            "description": "Stabilize a patient in a small clinic.",
        },
        "engine_contract": {
            "key": "declarative-rule-engine",
            "version": "1",
        },
        "initialization": {
            "start_node_key": "triage_room",
            "primary_actor_key": "doctor_lee",
        },
        "world": {
            "key": "medical_emergency",
            "name": "Medical Emergency",
            "node_types": [
                {"key": "patient", "name": "Patient"},
                {"key": "room", "name": "Room"},
            ],
            "nodes": [
                {
                    "key": "patient_one",
                    "name": "Patient One",
                    "node_type_key": "patient",
                    "initial_access": "AVAILABLE",
                    "initial_visibility": "KNOWN",
                    "interaction_keys": ["treatable"],
                    "facts": [
                        {
                            "key": "stable",
                            "name": "Stable",
                            "value_type": "BOOLEAN",
                            "initial_value": False,
                            "initial_visibility": "KNOWN",
                        }
                    ],
                },
                {
                    "key": "triage_room",
                    "name": "Triage Room",
                    "node_type_key": "room",
                    "initial_access": "AVAILABLE",
                    "initial_visibility": "KNOWN",
                },
            ],
            "relations": [
                {
                    "source_node_key": "triage_room",
                    "relation_type_key": "contains",
                    "target_node_key": "patient_one",
                }
            ],
            "resources": [
                {
                    "key": "medicine",
                    "name": "Medicine",
                    "initial_value": 10,
                    "minimum": 0,
                    "maximum": 20,
                    "reservation_supported": True,
                }
            ],
        },
        "actors": {
            "roles": [
                {
                    "key": "clinician",
                    "name": "Clinician",
                    "capabilities": ["PLAN", "EXECUTE_ACTION", "INSPECT_STATE"],
                }
            ],
            "actor_profiles": [
                {
                    "key": "doctor_lee",
                    "name": "Doctor Lee",
                    "role_key": "clinician",
                    "persona": "A careful emergency physician.",
                    "initial_node_key": "triage_room",
                    "allowed_action_keys": ["treat_patient"],
                }
            ],
        },
        "interactions": [
            {
                "key": "treatable",
                "name": "Treatable",
                "description": "Can receive medical treatment.",
            }
        ],
        "actions": [
            {
                "key": "treat_patient",
                "name": "Treat Patient",
                "required_interaction_key": "treatable",
                "execution_mode": "IMMEDIATE",
                "parameters": [
                    {
                        "key": "dosage",
                        "name": "Dosage",
                        "value_type": "INTEGER",
                        "minimum": 1,
                        "maximum": 5,
                    }
                ],
                "allowed_actor_capabilities": ["EXECUTE_ACTION"],
                "expected_outcomes": [{"code": "COMPLETED", "name": "Completed", "success": True}],
                "planning": {
                    "terminal_effects": [{"node_key": "patient_one", "fact_key": "stable"}],
                    "success_outcome_codes": ["COMPLETED"],
                },
            }
        ],
        "rules": [
            {
                "key": "treatment_succeeds",
                "phase": "RESOLVE",
                "action_key": "treat_patient",
                "priority": 0,
                "effects": [
                    {
                        "kind": "SET_FACT",
                        "node": {"kind": "EXPLICIT", "node_key": "patient_one"},
                        "fact_key": "stable",
                        "value": {"source": "LITERAL", "literal": True},
                    },
                    {"kind": "EMIT_OUTCOME", "outcome_code": "COMPLETED"},
                ],
            }
        ],
        "objectives": [
            {
                "key": "stabilize_patient",
                "name": "Stabilize Patient",
                "description": "Make Patient One stable.",
                "completion_requirements": [
                    {
                        "key": "patient_is_stable",
                        "node_key": "patient_one",
                        "fact_key": "stable",
                        "accepted_values": [True],
                        "description": "Patient One is stable.",
                    }
                ],
                "goal_aliases": ["stabilize the patient"],
                "goal_examples": ["Help Patient One"],
            }
        ],
        "goal_resolution": {
            "allow_llm_fallback": True,
            "clarification_prompt": "Which patient outcome do you want?",
        },
        "planning": {
            "instructions": ["Prefer the smallest safe treatment."],
            "recovery_hints": [{"failure_code": "INSUFFICIENT_MEDICINE", "hint": "Find medicine."}],
        },
    }


def test_v2_document_is_frozen_strict_and_canonical() -> None:
    source = _medical_scenario_document()
    parsed = parse_scenario_document(source)

    assert isinstance(parsed, ScenarioDefinitionV2)
    assert parsed.initialization.start_node_key == "triage_room"
    assert parsed.engine_contract.key == "declarative-rule-engine"
    with pytest.raises(ValidationError):
        parsed.metadata.name = "Changed"  # type: ignore[misc]

    reordered = deepcopy(source)
    reordered["world"]["nodes"].reverse()
    reordered["world"]["node_types"].reverse()
    reordered["actors"]["roles"][0]["capabilities"].reverse()
    assert scenario_content_hash(reordered) == scenario_content_hash(source)
    assert canonical_document(reordered) == canonical_document(source)

    changed = deepcopy(source)
    changed["rules"][0]["effects"][0]["value"]["literal"] = False
    assert scenario_content_hash(changed) != scenario_content_hash(source)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda document: document["actors"]["actor_profiles"][0]["allowed_action_keys"].append(
                "missing_action"
            ),
            "SCENARIO_DOCUMENT_SCHEMA_INVALID",
        ),
        (
            lambda document: document["engine_contract"].update({"version": "unavailable"}),
            "SCENARIO_ENGINE_CONTRACT_UNAVAILABLE",
        ),
    ],
)
def test_v2_validation_fails_closed_for_references_and_engine_contract(
    mutate: Any,
    code: str,
) -> None:
    document = _medical_scenario_document()
    mutate(document)

    result = ScenarioDefinitionValidator().validate(document)

    assert not result.passed
    assert code in {issue.code for issue in result.issues}


def test_v1_decoder_remains_available_without_rewriting_snapshot() -> None:
    document = ScenarioDefinitionDocument.from_domain(STARFIRE_SCENARIO_DEFINITION).model_dump(
        mode="json"
    )

    parsed = parse_scenario_document(document)

    assert isinstance(parsed, ScenarioDefinitionDocument)
    assert parsed.model_dump(mode="json") == document


def test_v2_draft_publishes_and_loads_beside_v1(session: Session) -> None:
    repository = ScenarioDefinitionRepository(session)
    starfire = repository.persist_initial_draft(STARFIRE_SCENARIO_DEFINITION)
    starfire_version = (
        ScenarioService(session)
        .publish_draft(
            starfire.id,
            expected_revision=1,
        )
        .version
    )
    medical_definition = ScenarioDefinitionV2.model_validate(_medical_scenario_document())
    medical = repository.persist_initial_draft(medical_definition)
    medical_version = (
        ScenarioService(session)
        .publish_draft(
            medical.id,
            expected_revision=1,
        )
        .version
    )
    original_v1_document = deepcopy(starfire_version.snapshot_document)

    loaded_v1 = ScenarioVersionRepository(session).load(starfire_version.id)
    loaded_v2 = ScenarioVersionRepository(session).load(medical_version.id)

    assert starfire_version.schema_version == 1
    assert medical_version.schema_version == 2
    assert isinstance(loaded_v1.definition, ScenarioDefinition)
    assert isinstance(loaded_v2.definition, ScenarioDefinitionV2)
    assert loaded_v2.definition.metadata.key == "medical_emergency"
    assert medical_version.behavior_bundle_key == "declarative-rule-engine"
    assert medical_version.behavior_bundle_version == "1"
    session.refresh(starfire_version)
    assert starfire_version.snapshot_document == original_v1_document
    with pytest.raises(ScenarioRuntimeBindingError) as unsupported_runtime:
        require_v1_runtime_definition(loaded_v2)
    assert unsupported_runtime.value.code == "SCENARIO_RUNTIME_SCHEMA_UNSUPPORTED"
