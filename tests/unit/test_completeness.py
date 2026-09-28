from copy import deepcopy

from app.scenarios.completeness import evaluate_completeness
from app.scenarios.serialization import legacy_resource_source_hint_payload
from tests.scenario_fixtures import GENERIC_TEST, LINJIANG_V2_TEST


def test_complete_generic_document_has_no_required_guidance_blockers() -> None:
    result = evaluate_completeness(GENERIC_TEST.model_dump(mode="json"))

    assert result.required_missing == 0
    assert result.validation_issue_count == 0
    assert any(item.level == "OPTIONAL_ENHANCEMENT" for item in result.items)


def test_resource_source_guidance_uses_canonical_resource_owner_locator() -> None:
    document = LINJIANG_V2_TEST.model_dump(mode="json")
    hinted = next(item for item in document["world"]["resources"] if "source_hint" in item)
    unhinted = next(item for item in document["world"]["resources"] if item["key"] != hinted["key"])
    unhinted.pop("source_hint", None)

    current_result = evaluate_completeness(document)
    assert not any(
        item.key == f"resource:{hinted['key']}:public-source" for item in current_result.items
    )
    current_recommendation = next(
        item
        for item in current_result.items
        if item.key == f"resource:{unhinted['key']}:public-source"
    )
    assert current_recommendation.path == f"world.resources.{unhinted['key']}.source_hint"
    assert current_recommendation.locator is not None
    assert current_recommendation.locator["object_kind"] == "resource"
    assert current_recommendation.locator["object_key"] == unhinted["key"]
    assert current_recommendation.locator["field_path"] == "source_hint"

    legacy_document = legacy_resource_source_hint_payload(document)
    assert legacy_document is not None
    legacy_result = evaluate_completeness(legacy_document)
    assert not any(
        item.key == f"resource:{hinted['key']}:public-source" for item in legacy_result.items
    )


def test_completeness_uses_raw_working_shape_when_unrelated_field_is_invalid() -> None:
    document = deepcopy(GENERIC_TEST.model_dump(mode="json"))
    document["unrelated_editor_note"] = {"temporary": object()}

    result = evaluate_completeness(document)

    assert result.validation_issue_count >= 1
    assert result.required_missing >= 1
    assert any(item.key == "scenario-definition.validation" for item in result.items)
    assert len(result.validation_issues) == result.validation_issue_count


def test_completeness_exposes_typed_nested_action_outcome_schema_issues() -> None:
    document = deepcopy(GENERIC_TEST.model_dump(mode="json"))
    action = document["actions"][0]
    outcome = action["expected_outcomes"][0]
    outcome.pop("name", None)

    result = evaluate_completeness(document)

    issue = next(
        issue
        for issue in result.validation_issues
        if issue.path.endswith("expected_outcomes.0.name")
    )
    assert issue.code == "SCENARIO_DOCUMENT_SCHEMA_INVALID"
    assert issue.type == "missing"


def test_completeness_reports_action_rule_and_initialization_handoffs() -> None:
    document = GENERIC_TEST.model_dump(mode="json")
    document["rules"] = []
    document["initialization"]["start_node_key"] = "missing_node"
    document["initialization"]["primary_actor_key"] = "missing_actor"

    result = evaluate_completeness(document)
    keys = {item.key for item in result.items}

    assert "action:diagnose_patient:resolve-rule" in keys
    assert "initialization.start-node" in keys
    assert "initialization.primary-actor" in keys


def test_preflight_failure_does_not_mechanically_require_recovery_hint() -> None:
    document = deepcopy(GENERIC_TEST.model_dump(mode="json"))
    document["planning"]["recovery_hints"] = []
    document["rules"].append(
        {
            "key": "diagnose_known_preflight_blocker",
            "phase": "PREFLIGHT",
            "action_key": "diagnose_patient",
            "priority": 100,
            "condition": {
                "kind": "FACT_EQUALS",
                "node": {"kind": "EXPLICIT", "node_key": "clinic"},
                "fact_key": "open",
                "value": False,
            },
            "effects": [
                {
                    "kind": "EMIT_FAILURE",
                    "failure_code": "CLINIC_CLOSED",
                    "message": "The clinic is closed.",
                    "retryable": True,
                }
            ],
        }
    )

    result = evaluate_completeness(document)

    assert not any(item.key == "recovery-hint:CLINIC_CLOSED" for item in result.items)


def test_reachable_retryable_resolve_failure_without_hint_is_recommended() -> None:
    document = deepcopy(GENERIC_TEST.model_dump(mode="json"))
    document["planning"]["recovery_hints"] = []
    resolve_rule = next(
        rule
        for rule in document["rules"]
        if rule.get("action_key") == "diagnose_patient" and rule.get("phase") == "RESOLVE"
    )
    resolve_rule["effects"] = [
        {
            "kind": "EMIT_FAILURE",
            "failure_code": "DIAGNOSIS_INCONCLUSIVE",
            "message": "The diagnosis was inconclusive.",
            "retryable": True,
        }
    ]

    result = evaluate_completeness(document)
    item = next(item for item in result.items if item.key == "recovery-hint:DIAGNOSIS_INCONCLUSIVE")

    assert item.level == "OPTIONAL_ENHANCEMENT"
    assert item.dependency_kind == "RECOMMENDED"

    document["planning"]["recovery_hints"] = [
        {
            "failure_code": "DIAGNOSIS_INCONCLUSIVE",
            "hint": "Gather additional evidence before retrying.",
        }
    ]
    covered = evaluate_completeness(document)
    assert not any(item.key == "recovery-hint:DIAGNOSIS_INCONCLUSIVE" for item in covered.items)
