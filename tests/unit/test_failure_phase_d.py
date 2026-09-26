"""Phase D schema boundary, v2 compatibility and read-only migration tests."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.agent.recovery import GenericRecoveryPolicy, RecoveryDecision
from app.domain.failures import FailureKind
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.domain.scenario_v3 import ScenarioDefinitionV3
from app.engine.rules import DeclarativeRuleEngine
from app.scenarios.authoring import reference_index
from app.scenarios.completeness import evaluate_completeness
from app.scenarios.documents import (
    parse_scenario_document,
    parse_scenario_document_versioned,
)
from app.scenarios.migration import (
    migrate_current_v3_document,
    preview_current_v3_migration,
    preview_v2_to_v3,
)
from app.scenarios.serialization import scenario_content_hash
from tests.scenario_fixtures import GENERIC_TEST
from tests.unit.test_failure_phase_b import _rule_context, _rule_state


def test_v3_authoring_schema_removes_recovery_and_failure_metadata() -> None:
    preview = preview_v2_to_v3(GENERIC_TEST.model_dump(mode="json"))
    document = preview.target_document
    model = ScenarioDefinitionV3.model_validate(document)

    assert model.schema_version == 3
    assert "recovery_hints" not in document["planning"]
    serialized = model.model_dump(mode="json")
    assert "recovery_hints" not in serialized["planning"]
    assert "failure_code" not in str(serialized)
    assert "retryable" not in str(serialized)
    schema_text = str(ScenarioDefinitionV3.model_json_schema(mode="validation"))
    assert "recovery_hints" not in schema_text
    assert "failure_code" not in schema_text
    assert "retryable" not in schema_text
    assert "BLOCK_ACTION" in schema_text
    assert "EMIT_FAILURE" not in schema_text


def test_v3_rejects_legacy_authoring_fields() -> None:
    preview = preview_v2_to_v3(GENERIC_TEST.model_dump(mode="json"))
    with_hints = deepcopy(preview.target_document)
    with_hints["planning"]["recovery_hints"] = [{"failure_code": "OLD", "hint": "old"}]
    with pytest.raises(ValidationError):
        ScenarioDefinitionV3.model_validate(with_hints)

    with_failure_fields = deepcopy(preview.target_document)
    effect = with_failure_fields["rules"][0]["effects"][0]
    effect.update({"failure_code": "OLD", "message": "old", "retryable": True})
    with pytest.raises(ValidationError):
        ScenarioDefinitionV3.model_validate(with_failure_fields)

    with_goal_policy = deepcopy(preview.target_document)
    with_goal_policy["goal_resolution"].update(
        allow_llm_fallback=False,
        clarification_prompt="旧版提示",
        world_goal_state_catalog=False,
    )
    with pytest.raises(ValidationError):
        ScenarioDefinitionV3.model_validate(with_goal_policy)


def test_v3_goal_resolution_is_ordered_quick_inputs_and_runtime_policy_is_injected() -> None:
    source = deepcopy(GENERIC_TEST.model_dump(mode="json"))
    source["goal_resolution"] = {
        "allow_llm_fallback": False,
        "clarification_prompt": "旧版提示",
        "quick_inputs": ["  先诊断  ", "稳定病情"],
        "world_goal_state_catalog": False,
    }
    v3_document = preview_v2_to_v3(source).target_document
    v3 = ScenarioDefinitionV3.model_validate(v3_document)
    assert v3.goal_resolution.quick_inputs == ("先诊断", "稳定病情")
    normalized = v3.to_v2()
    assert normalized.goal_resolution.allow_llm_fallback is True
    assert normalized.goal_resolution.world_goal_state_catalog is True
    assert normalized.goal_resolution.clarification_prompt
    assert list(normalized.goal_resolution.quick_inputs) == ["先诊断", "稳定病情"]
    assert list(normalized.objectives) == list(GENERIC_TEST.objectives)


def test_current_v3_goal_cleanup_is_deterministic_idempotent_and_preserves_non_goal_content(
) -> None:
    source = preview_v2_to_v3(GENERIC_TEST.model_dump(mode="json")).target_document
    source["goal_resolution"].update(
        allow_llm_fallback=False,
        clarification_prompt="legacy prompt",
        world_goal_state_catalog=False,
        quick_inputs=["first", "second"],
    )
    before = deepcopy(source)
    preview = preview_current_v3_migration(source)
    assert source == before
    assert preview.dropped_paths == (
        "goal_resolution.allow_llm_fallback",
        "goal_resolution.clarification_prompt",
        "goal_resolution.world_goal_state_catalog",
    )
    assert preview.changed_paths == preview.dropped_paths
    migrated = migrate_current_v3_document(source)
    assert migrated["goal_resolution"]["quick_inputs"] == source["goal_resolution"]["quick_inputs"]
    preserved_source = deepcopy(source)
    for key in ("allow_llm_fallback", "clarification_prompt", "world_goal_state_catalog"):
        preserved_source["goal_resolution"].pop(key)
    assert migrated == preserved_source
    for key in ("allow_llm_fallback", "clarification_prompt", "world_goal_state_catalog"):
        assert key not in migrated["goal_resolution"]
    repeated = preview_current_v3_migration(migrated)
    assert repeated.changed_paths == ()
    assert repeated.dropped_paths == ()
    assert repeated.target_document == migrated


def test_v3_block_action_is_derived_into_failure_event() -> None:
    preview = preview_v2_to_v3(GENERIC_TEST.model_dump(mode="json"))
    v3 = ScenarioDefinitionV3.model_validate(preview.target_document)
    normalized = v3.to_v2()
    rule = next(item for item in normalized.rules if item.key == "treatment_needs_diagnosis")
    outcome = DeclarativeRuleEngine(normalized)._outcome(rule, _rule_state(), _rule_context())

    assert outcome.failure is not None
    assert outcome.failure.code == FailureKind.PRECONDITION_UNMET.value
    assert outcome.failure.retryable is True
    assert outcome.failure_event is not None
    assert outcome.failure_event.kind is FailureKind.PRECONDITION_UNMET
    assert outcome.failure_event.message
    assert outcome.failure_event.metadata["authoring_effect"] == "BLOCK_ACTION"


def test_v3_block_action_recovery_ignores_legacy_retryable_projection() -> None:
    preview = preview_v2_to_v3(GENERIC_TEST.model_dump(mode="json"))
    v3 = ScenarioDefinitionV3.model_validate(preview.target_document)
    normalized = v3.to_v2()
    rule = next(item for item in normalized.rules if item.key == "treatment_needs_diagnosis")
    outcome = DeclarativeRuleEngine(normalized)._outcome(rule, _rule_state(), _rule_context())

    assert outcome.failure_event is not None
    event = outcome.failure_event.model_copy(update={"legacy_retryable": False})
    context = GenericRecoveryPolicy().evaluate(event, legacy_mode=False)

    assert context.decision is RecoveryDecision.REPLAN


def test_v3_completeness_and_references_have_no_recovery_hint_authority() -> None:
    document = preview_v2_to_v3(GENERIC_TEST.model_dump(mode="json")).target_document

    result = evaluate_completeness(document)

    assert not any(item.path == "planning.recovery_hints" for item in result.items)
    assert not any(edge.source.object_kind == "planning" for edge in reference_index(document))


def test_versioned_loader_preserves_v2_and_selects_v3_without_rewriting() -> None:
    v2_document = GENERIC_TEST.model_dump(mode="json")
    v3_document = preview_v2_to_v3(v2_document).target_document
    v2 = parse_scenario_document_versioned(v2_document)
    v3 = parse_scenario_document_versioned(v3_document)

    assert isinstance(v2, ScenarioDefinitionV2)
    assert v2.schema_version == 2
    assert isinstance(v3, ScenarioDefinitionV3)
    assert v3.schema_version == 3
    assert parse_scenario_document(v3_document).schema_version == 2
    assert v2_document["schema_version"] == 2
    assert "recovery_hints" in v2_document["planning"]


def test_v2_hash_and_legacy_failure_projection_remain_unchanged() -> None:
    source = GENERIC_TEST.model_dump(mode="json")
    parsed = parse_scenario_document_versioned(source)
    assert isinstance(parsed, ScenarioDefinitionV2)
    assert parsed.planning.recovery_hints
    legacy_hash = scenario_content_hash(source)
    assert scenario_content_hash(parsed.model_dump(mode="json")) == legacy_hash
    assert any(
        effect.kind.value == "EMIT_FAILURE"
        for rule in parsed.rules
        for effect in rule.effects
    )


def test_v2_to_v3_preview_is_deterministic_and_read_only() -> None:
    source = GENERIC_TEST.model_dump(mode="json")
    before = deepcopy(source)
    first = preview_v2_to_v3(source)
    second = preview_v2_to_v3(source)

    assert source == before
    assert first.target_content_hash == second.target_content_hash
    assert first.changed_paths == second.changed_paths
    assert first.dropped_paths == second.dropped_paths
    assert first.converted_blocker_count >= 1
    assert first.dropped_recovery_hint_count == len(source["planning"]["recovery_hints"])
