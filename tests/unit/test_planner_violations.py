from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.provider import PlannerInput, PlanRequest, PlanViolation


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
