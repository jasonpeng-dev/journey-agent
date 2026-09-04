from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.provider import (
    ContinuityPlan,
    ContinuityStep,
    PlannerInput,
    PlanningContinuity,
    PlanProposal,
    PlanRequest,
    PlanStepProposal,
)


def _step() -> PlanStepProposal:
    return PlanStepProposal(
        action_key="support_action",
        actor_key="support_actor",
        target_key="support_target",
    )


def test_plan_segment_requires_bounded_auditable_intent_metadata() -> None:
    with pytest.raises(ValidationError):
        PlanProposal(steps=(_step(),))

    proposal = PlanProposal(
        segment_goal="repair the southeast plant",
        goal_link="advances the active emergency-power dependency",
        continuation_intent="continue transport to southeast and repair",
        steps=(_step(),),
    )
    assert proposal.segment_goal == "repair the southeast plant"
    assert proposal.goal_link.startswith("advances")
    assert proposal.continuation_intent.endswith("repair")

    with pytest.raises(ValidationError):
        PlanProposal(
            segment_goal="x" * 241,
            goal_link="supports the objective",
            continuation_intent="continue the mainline",
            steps=(_step(),),
        )


def test_planning_continuity_exposes_intent_and_new_knowledge_without_authority() -> None:
    continuity = PlanningContinuity(
        prior_plans=(
            ContinuityPlan(
                plan_summary="Reach the southeast plant",
                stop_reason="SEGMENT_COMPLETE",
                segment_goal="repair the southeast plant",
                goal_link="advances the active emergency-power dependency",
                continuation_intent="continue transport to southeast and repair",
                steps=(
                    ContinuityStep(
                        action_key="travel",
                        actor_key="logistics_team_alpha",
                        target_key="southeast_heights_district",
                        execution_status="SUCCEEDED",
                    ),
                ),
            ),
        ),
        latest_replan_trigger="RUNTIME_ACTION_FAILED",
        latest_new_knowledge=(
            {
                "dimension": "FACT",
                "subject_key": "south_bridge",
                "fact_key": "passable",
                "value": False,
            },
        ),
    )
    request = PlanRequest(
        call_type="REPLAN",
        goal="restore emergency power",
        planner_input=PlannerInput(
            objective={"objective_keys": ["restore_emergency_power"]},
            execution_context={
                "latest_new_knowledge": list(continuity.latest_new_knowledge),
            },
        ),
        planning_continuity=continuity,
        replan_reason="RUNTIME_ACTION_FAILED",
    )

    payload = request.provider_payload()
    serialized_continuity = payload["planning_continuity"]
    assert isinstance(serialized_continuity, dict)
    prior_plan = serialized_continuity["prior_plans"][0]
    assert isinstance(prior_plan, dict)
    assert prior_plan["continuation_intent"] == "continue transport to southeast and repair"
    assert serialized_continuity["latest_new_knowledge"] == [
        {
            "dimension": "FACT",
            "subject_key": "south_bridge",
            "fact_key": "passable",
            "value": False,
        }
    ]
    planner_payload = payload["planner_input"]
    assert isinstance(planner_payload, dict)
    assert planner_payload["objective"] == {"objective_keys": ["restore_emergency_power"]}
    assert planner_payload["execution_context"]["latest_new_knowledge"] == list(
        continuity.latest_new_knowledge
    )
