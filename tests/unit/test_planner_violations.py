from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.generic import GenericAgentService, _remember_prior_contradictions
from app.agent.provider import (
    AntiRegressionMemoryItem,
    PlannerActionContract,
    PlannerInput,
    PlannerTargetBinding,
    PlanningContext,
    PlanRequest,
    PlanViolation,
)


def test_objective_effect_refs_keep_explicit_cross_target_fact_effect() -> None:
    planner_input = PlannerInput(
        action_contracts=(
            PlannerActionContract(
                action_key="repair_network",
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "central_telecom_hub",
                        "fact_key": "operational",
                    },
                ),
            ),
        ),
        target_bindings=(
            PlannerTargetBinding(
                action_key="repair_network",
                target_key="field_relay",
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "field_relay",
                        "fact_key": "operational",
                    },
                ),
            ),
            PlannerTargetBinding(
                action_key="repair_network",
                target_key="other_relay",
                deterministic_effects=(
                    {
                        "type": "FACT_MUTATION",
                        "target": "other_relay",
                        "fact_key": "operational",
                    },
                ),
            ),
        ),
    )

    refs = GenericAgentService._objective_effect_refs(
        PlanningContext(),
        "repair_network",
        "field_relay",
        planner_input=planner_input,
    )

    assert refs == {("central_telecom_hub", "operational"), ("field_relay", "operational")}


def test_plan_violation_serializes_canonical_wire_shape() -> None:
    violation = PlanViolation(
        code="LOCALITY_INVALID",
        failure_code="LOCALITY_ROUTE_NOT_FOUND",
        dimension="LOCALITY",
        step_id="step-4",
        action_key="travel",
        actor_key="team-alpha",
        target_key="region-b",
        required="ONE_HOP_TRANSPORT",
        actual={"actor_region": "region-a", "target_region": "region-b"},
        source_region="region-a",
        target_region="region-b",
    )

    assert violation.model_dump(mode="json", exclude_none=True, exclude_defaults=True) == {
        "code": "LOCALITY_INVALID",
        "failure_code": "LOCALITY_ROUTE_NOT_FOUND",
        "dimension": "LOCALITY",
        "step_id": "step-4",
        "action_key": "travel",
        "actor_key": "team-alpha",
        "target_key": "region-b",
        "required": "ONE_HOP_TRANSPORT",
        "actual": {"actor_region": "region-a", "target_region": "region-b"},
        "source_region": "region-a",
        "target_region": "region-b",
    }


def test_repair_payload_uses_validator_violations_wire_key() -> None:
    payload = PlanRequest(
        call_type="REPAIR",
        planner_input=PlannerInput(),
        repair_attempt=1,
        rejected_segment={"stop_reason": "OBJECTIVE_COMPLETION", "steps": []},
        repair_diagnostics=(
            PlanViolation(
                code="TARGET_INTERACTION_INVALID",
                dimension="TARGET_INTERACTION",
                step_id="step-1",
                action_key="repair",
                actor_key="team-alpha",
                target_key="target-a",
                required_interaction_key="repairable",
                actual_interactions=("inspectable",),
            ),
        ),
    ).provider_payload()

    assert payload["validator_violations"] == [
        {
            "code": "TARGET_INTERACTION_INVALID",
            "dimension": "TARGET_INTERACTION",
            "step_id": "step-1",
            "action_key": "repair",
            "actor_key": "team-alpha",
            "target_key": "target-a",
            "required_interaction_key": "repairable",
            "actual_interactions": ["inspectable"],
        }
    ]
    assert "repair_diagnostics" not in payload


def test_repair_payload_separates_current_violations_from_historical_memory() -> None:
    payload = PlanRequest(
        call_type="REPAIR",
        planner_input=PlannerInput(),
        repair_attempt=2,
        rejected_segment={"stop_reason": "OBJECTIVE_COMPLETION", "steps": []},
        repair_diagnostics=(
            PlanViolation(
                code="LOCALITY_INVALID",
                step_id="current-step",
                dimension="LOCALITY",
                required="SAME_REGION",
                actual={"actor_region": "central", "target_region": "east"},
            ),
        ),
        anti_regression_memory=(
            AntiRegressionMemoryItem(
                code="RESOURCE_SURVEY_ALREADY_COMPLETED",
                step_id="old-step",
                message="old rejected proposal",
                dimension="RESOURCE_SURVEY_STATE",
                target_key="central",
                required="NOT_COMPLETED",
                actual="COMPLETED",
                first_seen_attempt=0,
                last_seen_attempt=0,
            ),
        ),
    ).provider_payload()

    assert payload["validator_violations"] == [
        {
            "code": "LOCALITY_INVALID",
            "dimension": "LOCALITY",
            "step_id": "current-step",
            "required": "SAME_REGION",
            "actual": {"actor_region": "central", "target_region": "east"},
        }
    ]
    assert payload["anti_regression_memory"] == [
        {
            "code": "RESOURCE_SURVEY_ALREADY_COMPLETED",
            "dimension": "RESOURCE_SURVEY_STATE",
            "target_key": "central",
            "required": "NOT_COMPLETED",
            "actual": "COMPLETED",
            "first_seen_attempt": 0,
            "last_seen_attempt": 0,
        }
    ]
    historical = payload["anti_regression_memory"][0]
    for forbidden_key in (
        "step_id",
        "sequence",
        "message",
        "recommended_action",
        "suggested_actor",
        "suggested_target",
        "suggested_source",
        "suggested_route",
        "next_step",
        "recovery_sequence",
    ):
        assert forbidden_key not in historical


def test_anti_regression_memory_deduplicates_without_step_identity() -> None:
    first = PlanViolation(
        code="KNOWN_RESOURCE_INSUFFICIENT",
        step_id="repair-v1",
        dimension="RESOURCE_QUANTITY",
        action_key="repair",
        target_key="facility",
        resource_key="parts",
        scope_region="central",
        required_amount=15,
        projected_known_available_amount=10,
        deficit=5,
    )
    same_contradiction = first.model_copy(update={"step_id": "repair-v2"})
    different_contradiction = first.model_copy(update={"deficit": 6})

    memory = _remember_prior_contradictions((), (first,), seen_attempt=0)
    memory = _remember_prior_contradictions(
        memory, (same_contradiction, different_contradiction), seen_attempt=1
    )

    assert len(memory) == 2
    assert memory[0].step_id is None
    assert memory[0].first_seen_attempt == 0
    assert memory[0].last_seen_attempt == 1
    assert memory[0].seen_count == 2
    assert memory[1].deficit == 6
    assert memory[1].first_seen_attempt == 1


def test_plan_violation_forbids_unmodeled_hidden_truth_fields() -> None:
    with pytest.raises(ValidationError, match="hidden_truth"):
        PlanViolation.model_validate(
            {
                "code": "PROPOSAL_INVALID",
                "hidden_truth": {"secret_fact": False},
            }
        )


@pytest.mark.parametrize(
    ("violation", "expected"),
    [
        (
            PlanViolation(
                code="PROPOSAL_INVALID",
                failure_code="RESOURCE_SURVEY_ALREADY_COMPLETED",
                dimension="RESOURCE_SURVEY_STATE",
                step_id="survey-1",
                target_key="central_district",
                required="NOT_COMPLETED",
                actual="COMPLETED",
            ),
            {
                "code": "PROPOSAL_INVALID",
                "failure_code": "RESOURCE_SURVEY_ALREADY_COMPLETED",
                "dimension": "RESOURCE_SURVEY_STATE",
                "step_id": "survey-1",
                "target_key": "central_district",
                "required": "NOT_COMPLETED",
                "actual": "COMPLETED",
            },
        ),
        (
            PlanViolation(
                code="PROPOSAL_INVALID",
                failure_code="KNOWN_RESOURCE_INSUFFICIENT",
                dimension="RESOURCE_QUANTITY",
                step_id="repair-1",
                resource_key="general_engineering_parts",
                scope_region="central_district",
                required_amount=15,
                projected_known_available_amount=10,
                deficit=5,
            ),
            {
                "code": "PROPOSAL_INVALID",
                "failure_code": "KNOWN_RESOURCE_INSUFFICIENT",
                "dimension": "RESOURCE_QUANTITY",
                "step_id": "repair-1",
                "resource_key": "general_engineering_parts",
                "scope_region": "central_district",
                "required_amount": 15,
                "projected_known_available_amount": 10,
                "deficit": 5,
            },
        ),
    ],
)
def test_existing_typed_resource_violation_payloads_do_not_regress(
    violation: PlanViolation, expected: dict[str, object]
) -> None:
    payload = PlanRequest(
        call_type="REPAIR",
        planner_input=PlannerInput(),
        repair_diagnostics=(violation,),
    ).provider_payload()

    assert payload["validator_violations"] == [expected]
