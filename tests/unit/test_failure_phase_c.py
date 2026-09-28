"""Phase C recovery ownership and canonical Provider contract tests."""

from __future__ import annotations

from app.agent.planning_context import GENERIC_PLANNER_POLICY
from app.agent.provider import GenericProviderError, PlannerInput, PlanningContext, PlanRequest
from app.agent.recovery import GenericRecoveryPolicy, RecoveryDecision
from app.api.schemas.phase_d import PublicPlanDisplayStatus
from app.domain.enums import AgentStepStatus, StepExecutionType
from app.domain.failures import FailureDomain, FailureEvent, FailureKind, FailurePhase
from app.infrastructure.db.models import AgentPlan, AgentStep
from app.services.player_projection import _plan_display_reason
from tests.scenario_fixtures import LINJIANG_V2_TEST


def _event(
    code: str,
    kind: FailureKind,
    *,
    retryable: bool | None = True,
    domain: FailureDomain = FailureDomain.ACTION_RUNTIME,
) -> FailureEvent:
    return FailureEvent(
        domain=domain,
        kind=kind,
        code=code,
        phase=(
            FailurePhase.PROVIDER_CALL
            if domain == FailureDomain.PROVIDER
            else FailurePhase.EXECUTION
        ),
        legacy_retryable=retryable,
        provider_retryable=(retryable if domain == FailureDomain.PROVIDER else None),
        producer="phase-c-test",
    )


def test_v2_mapped_rule_failures_keep_retryable_parity() -> None:
    policy = GenericRecoveryPolicy()
    mapped = (
        ("INSUFFICIENT_COMMUNICATION_EQUIPMENT", FailureKind.RESOURCE_INSUFFICIENT),
        ("INSUFFICIENT_ELECTRICAL_REPAIR_PARTS", FailureKind.RESOURCE_INSUFFICIENT),
        ("INSUFFICIENT_GENERAL_ENGINEERING_PARTS", FailureKind.RESOURCE_INSUFFICIENT),
        ("INSUFFICIENT_MUNICIPAL_REPAIR_MATERIALS", FailureKind.RESOURCE_INSUFFICIENT),
        ("INSUFFICIENT_WATER_SYSTEM_PARTS", FailureKind.RESOURCE_INSUFFICIENT),
        ("RESOURCE_INVENTORY_UNKNOWN", FailureKind.RESOURCE_INSUFFICIENT),
        ("ACTION_PRECONDITION", FailureKind.PRECONDITION_UNMET),
        ("HEAVY_ENGINEERING_SUPPORT_REQUIRED", FailureKind.PRECONDITION_UNMET),
        ("HEAVY_SUPPORT_UNAVAILABLE", FailureKind.PRECONDITION_UNMET),
        ("POWER_SOURCE_NOT_OPERATIONAL", FailureKind.PRECONDITION_UNMET),
        ("POWER_SOURCE_UNAVAILABLE", FailureKind.PRECONDITION_UNMET),
    )
    for code, kind in mapped:
        context = policy.evaluate(_event(code, kind), legacy_mode=True)
        assert context.decision is RecoveryDecision.REPLAN
        assert context.proposed_decision is RecoveryDecision.REPLAN
        assert context.legacy_decision is RecoveryDecision.REPLAN
        assert context.legacy_parity is True


def test_recovery_decision_is_domain_specific() -> None:
    policy = GenericRecoveryPolicy()
    assert (
        policy.evaluate(
            _event(
                "PROPOSAL_INVALID",
                FailureKind.VALIDATOR_REJECTED,
                domain=FailureDomain.PLAN_VALIDATION,
            ),
            legacy_mode=False,
        ).decision
        is RecoveryDecision.REPAIR
    )
    assert (
        policy.evaluate(
            _event(
                "MODEL_PROVIDER_TIMEOUT",
                FailureKind.PROVIDER_ERROR,
                domain=FailureDomain.PROVIDER,
            ),
            legacy_mode=False,
        ).decision
        is RecoveryDecision.RETRY_PROVIDER
    )
    assert (
        policy.evaluate(
            _event(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                FailureKind.PROVIDER_ERROR,
                domain=FailureDomain.PROVIDER,
                retryable=False,
            ),
            legacy_mode=False,
        ).decision
        is RecoveryDecision.BLOCK
    )
    assert (
        policy.evaluate(
            _event("ACTION_APPROVAL_REQUIRED", FailureKind.AUTHORITY_BLOCKED, retryable=False),
            legacy_mode=False,
        ).decision
        is RecoveryDecision.PLAYER_DECISION
    )
    assert (
        policy.evaluate(
            _event("SURVEY_ALREADY_COMPLETED", FailureKind.ALREADY_COMPLETED, retryable=False),
            legacy_mode=False,
        ).decision
        is RecoveryDecision.NOOP
    )


def test_replan_guard_is_backend_owned_and_fail_closed() -> None:
    policy = GenericRecoveryPolicy(max_replans=2)
    context = policy.evaluate(
        _event("TRAVEL_BLOCKED", FailureKind.TRAVEL_BLOCKED),
        replan_count=2,
        state_markers={"recovery_hints": ["TRAVEL_BLOCKED"]},
    )
    assert context.decision is RecoveryDecision.BLOCK
    assert "recovery_hints" not in context.state_markers
    assert "preserve_completed_steps" in context.directives


def test_author_instructions_are_a_provider_sibling_for_every_plan_call() -> None:
    instructions = ("优先完成当前区域的公开前置动作", "不要重复已经完成的调查")
    for call_type in ("INITIAL_PLAN", "REPLAN", "REPAIR"):
        payload = PlanRequest(
            call_type=call_type,
            planner_input=PlannerInput(),
            author_planning_instructions=instructions,
        ).provider_payload()
        assert payload["author_planning_instructions"] == list(instructions)
        assert "recovery_hints" not in payload
    empty_payload = PlanRequest(
        call_type="INITIAL_PLAN",
        planner_input=PlannerInput(),
    ).provider_payload()
    assert "author_planning_instructions" not in empty_payload


def test_current_planning_context_has_no_legacy_scenario_hint_projection() -> None:
    context = PlanningContext(author_planning_instructions=("Keep the public boundary",))

    payload = context.compact_dump()

    assert payload["author_planning_instructions"] == ["Keep the public boundary"]
    assert "scenario_planning_hints" not in payload
    assert "recovery_hints" not in payload


def test_provider_error_exposes_one_typed_failure_source() -> None:
    error = GenericProviderError("MODEL_PROVIDER_TIMEOUT", "provider timed out")
    event = error.failure_event
    assert event.domain is FailureDomain.PROVIDER
    assert event.phase is FailurePhase.PROVIDER_CALL
    assert event.code == error.code
    assert event.provider_retryable is True
    assert event.to_legacy().retryable is None


def test_generic_planner_policy_is_backend_owned() -> None:
    assert any("Hidden Truth" in item for item in GENERIC_PLANNER_POLICY)
    assert any("UNKNOWN" in item for item in GENERIC_PLANNER_POLICY)
    assert any("allowed_action_keys" in item for item in GENERIC_PLANNER_POLICY)


def test_player_projection_uses_typed_transport_kind_instead_of_blocked_substring() -> None:
    travel = next(item for item in LINJIANG_V2_TEST.actions if item.behavior.value == "TRAVEL")
    step = AgentStep(
        sequence=1,
        description="travel",
        execution_type=StepExecutionType.TOOL,
        status=AgentStepStatus.FAILED,
        assigned_actor_key="actor",
        action_intent=travel.key,
        tool_arguments={"action_key": travel.key, "target_key": "target"},
        failure_code="CUSTOM_BLOCKED_LABEL",
        actual_result={
            "failure_event": _event(
                "ACTION_PRECONDITION_FAILED",
                FailureKind.PRECONDITION_UNMET,
                retryable=False,
            ).model_dump(mode="json")
        },
    )
    plan = AgentPlan(stop_reason="BLOCKED")
    reason = _plan_display_reason(
        PublicPlanDisplayStatus.BLOCKED,
        plan,
        (step,),
        (step,),
        LINJIANG_V2_TEST,
    )
    assert reason == "前往区域失败"
