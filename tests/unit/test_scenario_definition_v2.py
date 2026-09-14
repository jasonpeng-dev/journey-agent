from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.domain.formal_goal import compile_predefined_formal_goal
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import Scenario, ScenarioVersion
from app.scenarios.documents import parse_scenario_document
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.serialization import (
    canonical_document,
    canonical_document_payload,
    canonical_payload_hash,
    scenario_content_hash,
)
from app.scenarios.validation import ScenarioDefinitionValidator
from app.scenarios.versions import ScenarioVersionError, ScenarioVersionRepository
from app.services.scenarios import ScenarioService


def _contract_scenario_document() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "metadata": {
            "key": "generic_contract",
            "name": "Generic Contract",
            "description": "Exercise a small deterministic action contract.",
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
            "key": "generic_contract",
            "name": "Generic Contract",
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


def test_public_references_validate_canonical_identity_and_affect_hash_canonically() -> None:
    first = _contract_scenario_document()
    first["public_references"] = [
        {"term": "healing supplies", "ref_type": "RESOURCE", "ref_key": "medicine"},
        {"term": "patient", "ref_type": "NODE", "ref_key": "patient_one"},
    ]
    second = deepcopy(first)
    second["public_references"].reverse()

    parsed = ScenarioDefinitionV2.model_validate(first)

    assert parsed.public_references[0].ref_key == "medicine"
    assert scenario_content_hash(first) == scenario_content_hash(second)


def test_public_reference_rejects_missing_or_wrong_typed_identity() -> None:
    missing = _contract_scenario_document()
    missing["public_references"] = [
        {"term": "unknown", "ref_type": "RESOURCE", "ref_key": "unknown_resource"}
    ]
    wrong_type = _contract_scenario_document()
    wrong_type["public_references"] = [
        {"term": "medicine", "ref_type": "NODE", "ref_key": "medicine"}
    ]

    with pytest.raises(ValidationError):
        ScenarioDefinitionV2.model_validate(missing)
    with pytest.raises(ValidationError):
        ScenarioDefinitionV2.model_validate(wrong_type)


def test_v2_document_is_frozen_strict_and_canonical() -> None:
    source = _contract_scenario_document()
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
    document = _contract_scenario_document()
    mutate(document)

    result = ScenarioDefinitionValidator().validate(document)

    assert not result.passed
    assert code in {issue.code for issue in result.issues}


def _persist_scenario_version(
    session: Session,
    payload: dict[str, Any],
    *,
    key: str = "legacy_contract",
    content_hash: str | None = None,
) -> ScenarioVersion:
    scenario = Scenario(
        key=key,
        name="Legacy Contract",
        status="PUBLISHED",
    )
    session.add(scenario)
    session.flush()
    version = ScenarioVersion(
        scenario_id=scenario.id,
        version_number=1,
        schema_version=2,
        snapshot_document=payload,
        content_hash=content_hash or canonical_payload_hash(payload),
        engine_contract_key="declarative-rule-engine",
        engine_contract_version="1",
        published_at=datetime.now(UTC),
    )
    session.add(version)
    session.flush()
    return version


def test_legacy_v2_snapshot_loads_without_rewriting_optional_action_fields(
    session: Session,
) -> None:
    payload = canonical_document_payload(_contract_scenario_document())
    assert all(
        "target_node_type_keys" not in action
        and "target_actor_roles" not in action
        and "operation_bindings" not in action
        and "target_terminal_effects" not in action["planning"]
        and "goal_required_slots" not in action
        and "behavior" not in action
        and "locality" not in action
        for action in payload["actions"]
    )
    assert "resource_initial_states" not in payload["initialization"]
    assert "locality" not in payload["metadata"]
    assert all(
        "resource_scope" not in effect for rule in payload["rules"] for effect in rule["effects"]
    )

    version = _persist_scenario_version(session, payload)

    loaded = ScenarioVersionRepository(session).load(version.id)

    assert loaded.id == version.id
    assert loaded.definition.metadata.key == "generic_contract"
    assert version.content_hash in loaded.verified_content_hashes
    assert all(
        action.operation_bindings == ()
        and action.target_node_type_keys == ()
        and action.target_actor_roles == ()
        and action.planning.target_terminal_effects == ()
        and action.goal_required_slots == ()
        for action in loaded.definition.actions
    )
    contract = compile_predefined_formal_goal(loaded, (loaded.definition.objectives[0],))
    contract.assert_bound_to(loaded)
    assert canonical_document_payload(payload) == payload


def test_explicit_empty_action_fields_remain_a_valid_current_snapshot_shape() -> None:
    payload = canonical_document_payload(_contract_scenario_document())
    for action in payload["actions"]:
        action["target_node_type_keys"] = []
        action["target_actor_roles"] = []
        action["operation_bindings"] = []
        action["planning"]["target_terminal_effects"] = []

    canonical_payload = canonical_document_payload(payload)

    assert canonical_payload == payload


def test_known_legacy_default_omissions_preserve_explicit_defaults() -> None:
    source = _contract_scenario_document()
    action = source["actions"][0]
    action["behavior"] = "RULE"
    action["locality"] = "NONE"
    action["planning"]["target_terminal_effects"] = []
    source["initialization"]["resource_initial_states"] = []
    source["metadata"]["locality"] = {}
    source["rules"][0]["effects"][0]["resource_scope"] = None

    payload = canonical_document_payload(source)
    payload_action = payload["actions"][0]

    assert payload_action["behavior"] == "RULE"
    assert payload_action["locality"] == "NONE"
    assert payload_action["planning"]["target_terminal_effects"] == []
    assert payload["initialization"]["resource_initial_states"] == []
    assert payload["metadata"]["locality"] == {
        "enabled": False,
        "scoped_resources": False,
        "region_node_type_key": None,
        "facility_node_type_key": None,
        "transport_node_type_key": None,
        "located_in_relation_type_key": None,
        "transport_endpoint_relation_type_key": None,
        "passability_fact_key": None,
    }
    assert payload["rules"][0]["effects"][0]["resource_scope"] is None


def test_new_optional_action_omissions_preserve_explicit_empty_distinction() -> None:
    omitted = canonical_document_payload(_contract_scenario_document())
    explicit_source = _contract_scenario_document()
    for action in explicit_source["actions"]:
        action["target_actor_roles"] = []
        action["planning"]["target_terminal_effects"] = []
    explicit = canonical_document_payload(explicit_source)

    for omitted_action, explicit_action in zip(
        omitted["actions"], explicit["actions"], strict=True
    ):
        assert "target_actor_roles" not in omitted_action
        assert omitted_action["planning"].get("target_terminal_effects") is None
        assert explicit_action["target_actor_roles"] == []
        assert explicit_action["planning"]["target_terminal_effects"] == []
    assert omitted != explicit


def test_new_optional_action_fields_preserve_non_empty_content() -> None:
    source = _contract_scenario_document()
    source["actions"][0]["target_actor_roles"] = [
        {
            "target_key": "patient_one",
            "required_actor_role_key": "clinician",
        }
    ]
    source["actions"][0]["planning"]["target_terminal_effects"] = [
        {"fact_key": "stable", "value": True}
    ]

    payload = canonical_document_payload(source)
    action = payload["actions"][0]

    assert action["target_actor_roles"] == [
        {
            "target_key": "patient_one",
            "required_actor_role_key": "clinician",
        }
    ]
    assert action["planning"]["target_terminal_effects"] == [{"fact_key": "stable", "value": True}]


@pytest.mark.parametrize("present_field", ["target_actor_roles", "target_terminal_effects"])
def test_historical_v2_snapshot_missing_each_new_optional_field_loads(
    session: Session,
    present_field: str,
) -> None:
    source = _contract_scenario_document()
    if present_field == "target_actor_roles":
        source["actions"][0]["target_actor_roles"] = []
    else:
        source["actions"][0]["planning"]["target_terminal_effects"] = []
    payload = canonical_document_payload(source)
    action = payload["actions"][0]
    assert ("target_actor_roles" in action) is (present_field == "target_actor_roles")
    assert ("target_terminal_effects" in action["planning"]) is (
        present_field == "target_terminal_effects"
    )

    version = _persist_scenario_version(session, payload, key=f"legacy_{present_field}")
    loaded = ScenarioVersionRepository(session).load(version.id)

    assert loaded.id == version.id
    assert loaded.definition.actions[0].target_actor_roles == ()
    assert loaded.definition.actions[0].planning.target_terminal_effects == ()
    assert version.snapshot_document == payload


def test_historical_snapshot_still_requires_a_verified_hash(session: Session) -> None:
    payload = canonical_document_payload(_contract_scenario_document())
    version = _persist_scenario_version(
        session,
        payload,
        key="legacy_hash_required",
        content_hash="0" * 64,
    )

    with pytest.raises(ScenarioVersionError) as caught:
        ScenarioVersionRepository(session).load(version.id)

    assert caught.value.code == "SCENARIO_VERSION_HASH_MISMATCH"


def test_v2_draft_publishes_and_loads_exact_snapshot(session: Session) -> None:
    repository = ScenarioDefinitionRepository(session)
    contract_definition = ScenarioDefinitionV2.model_validate(_contract_scenario_document())
    contract = repository.persist_initial_draft(contract_definition)
    contract_version = (
        ScenarioService(session)
        .publish_draft(
            contract.id,
            expected_revision=1,
        )
        .version
    )
    loaded_v2 = ScenarioVersionRepository(session).load(contract_version.id)

    assert contract_version.schema_version == 2
    assert isinstance(loaded_v2.definition, ScenarioDefinitionV2)
    assert loaded_v2.definition.metadata.key == "generic_contract"
    assert contract_version.engine_contract_key == "declarative-rule-engine"
    assert contract_version.engine_contract_version == "1"


def test_v1_documents_fail_closed() -> None:
    with pytest.raises(ValueError, match="v2 is required"):
        parse_scenario_document({"schema_version": 1})
