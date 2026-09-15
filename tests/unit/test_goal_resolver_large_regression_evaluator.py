from __future__ import annotations

from tools.goal_resolver_large_regression_evaluator import evaluate_run


def _case(
    *,
    case_id: str,
    disposition: str,
    input_text: str,
    action: str | None = None,
    actor: str | None = None,
    target: str | None = None,
    bindings: list[dict[str, str]] | None = None,
    parameters: dict | None = None,
    refs: list[dict[str, str]] | None = None,
    coverage: list[dict[str, str]] | None = None,
    route_match: str = "MATCHED",
    route_candidates: list[str] | None = None,
) -> dict:
    return {
        "case_id": case_id,
        "category": "OFFLINE_TEST",
        "input": input_text,
        "expected_disposition": disposition,
        "expected_family": "OPERATION",
        "expected_action_key": action,
        "expected_actor_key": actor,
        "expected_target_key": target,
        "expected_binding_constraints": bindings or [],
        "expected_parameter_constraints": parameters,
        "expected_canonical_refs": refs or [],
        "forbidden_families": [],
        "forbidden_canonical_refs": [],
        "coverage": coverage or [],
        "harness": {
            "provider_grounding": "DETERMINISTIC",
            "family": "OPERATION",
            "route_match": route_match,
            "route_action_key": action,
            "route_candidates": route_candidates or [],
            "operation": None,
            "state_requirement": None,
        },
    }


def _run(
    *,
    status: str,
    prompt: str | None = None,
    actual: dict | None = None,
    formal_goal: dict | None = None,
    classifications: list[str] | None = None,
    errors: list[dict] | None = None,
    provider_calls: list[dict] | None = None,
    recovery_attempt_count: int = 0,
    provider_observation: dict | None = None,
) -> dict:
    return {
        "run_index": 1,
        "input": "",
        "resolution": {
            "status": status,
            "clarification_prompt": prompt,
            "provider_observation": provider_observation,
        },
        "formal_goal": formal_goal,
        "actual_semantics": actual
        or {
            "resolution_status": status,
            "family": None,
            "action_key": None,
            "actor_key": None,
            "target_key": None,
            "bindings": [],
            "parameters": None,
            "canonical_refs": [],
            "state_requirement": None,
        },
        "errors": errors or [],
        "classifications": classifications or [],
        "provider_calls": provider_calls or [],
        "recovery_attempt_count": recovery_attempt_count,
    }


def _resolved_operation(
    *,
    action: str,
    target: str | None,
    actor: str | None = None,
    target_ref_type: str = "NODE",
    refs: list[dict[str, str]] | None = None,
    parameters: dict | None = None,
) -> dict:
    return {
        "resolution_status": "RESOLVED",
        "family": "OPERATION",
        "action_key": action,
        "actor_key": actor,
        "target_key": target,
        "target_ref_type": target_ref_type,
        "bindings": [],
        "parameters": parameters,
        "canonical_refs": refs or [],
        "state_requirement": None,
    }


def test_bare_repair_clarification_is_product_pass() -> None:
    case = _case(
        case_id="OFFLINE-BARE-REPAIR",
        disposition="MUST_CLARIFY",
        input_text="修一下",
        route_match="AMBIGUOUS",
        route_candidates=["repair_electrical", "repair_water_facility"],
    )
    run = _run(
        status="NEEDS_CLARIFICATION",
        prompt="请明确需要修复的具体设施或行动。",
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["product_semantic_verdict"] == "PASS"
    assert evaluation["provider_execution_verdict"] == "CLEAN"
    assert "CLARIFICATION_DRIFT" not in evaluation["corrected_classifications"]


def test_explicit_amount_cannot_be_reasked_in_clarification() -> None:
    case = _case(
        case_id="OFFLINE-BARE-PARTS",
        disposition="MUST_CLARIFY",
        input_text="运30个部件到南部",
        action="transport_resource",
        target="south_waterfront_district",
        refs=[
            {"ref_type": "ACTION", "key": "transport_resource"},
            {"ref_type": "REGION", "key": "south_waterfront_district"},
            {"ref_type": "RESOURCE", "key": "electrical_repair_parts"},
            {"ref_type": "RESOURCE", "key": "general_engineering_parts"},
        ],
    )
    run = _run(
        status="NEEDS_CLARIFICATION",
        prompt="请提供部件类型和数量。",
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["product_semantic_verdict"] == "FAIL"
    assert "CLARIFICATION_DRIFT" in evaluation["corrected_classifications"]
    assert "REDUNDANT_CLARIFICATION_FIELD" in evaluation["corrected_classifications"]
    assert evaluation["clarification_analysis"]["valid_clarification_fields"] == ["resource"]
    assert evaluation["clarification_analysis"]["redundant_clarification_fields"] == ["amount"]
    assert evaluation["explicit_constraints"]["fields"]["amount"] == 30


def test_omitted_actor_is_accepted_as_not_specified() -> None:
    case = _case(
        case_id="OFFLINE-OMITTED-ACTOR",
        disposition="MUST_RESOLVE",
        input_text="检查中央医院",
        action="inspect",
        target="central_hospital",
        refs=[
            {"ref_type": "ACTION", "key": "inspect"},
            {"ref_type": "NODE", "key": "central_hospital"},
        ],
    )
    run = _run(
        status="RESOLVED",
        actual=_resolved_operation(
            action="inspect",
            target="central_hospital",
            refs=[
                {"ref_type": "ACTION", "key": "inspect"},
                {"ref_type": "NODE", "key": "central_hospital"},
            ],
        ),
        formal_goal={},
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["product_semantic_verdict"] == "PASS"
    assert "NOT_SPECIFIED_VIOLATION" not in evaluation["corrected_classifications"]


def test_explicit_target_changed_to_not_specified_is_dropped() -> None:
    case = _case(
        case_id="OFFLINE-DROPPED-TARGET",
        disposition="MUST_RESOLVE",
        input_text="检查中央医院",
        action="inspect",
        target="central_hospital",
        refs=[
            {"ref_type": "ACTION", "key": "inspect"},
            {"ref_type": "NODE", "key": "central_hospital"},
        ],
    )
    run = _run(
        status="RESOLVED",
        actual=_resolved_operation(
            action="inspect",
            target=None,
            refs=[{"ref_type": "ACTION", "key": "inspect"}],
        ),
        formal_goal={},
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["product_semantic_verdict"] == "FAIL"
    assert "EXPLICIT_CONSTRAINT_DROPPED" in evaluation["corrected_classifications"]


def test_must_not_error_is_not_semantic_success() -> None:
    case = _case(
        case_id="OFFLINE-MUST-NOT-ERROR",
        disposition="MUST_NOT_RESOLVE",
        input_text="未知目标",
    )
    run = _run(
        status="ERROR",
        classifications=["FINAL_PROVIDER_INVALID"],
        errors=[{"code": "MODEL_PROVIDER_RESPONSE_INVALID"}],
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["product_semantic_verdict"] == "FAIL"
    assert evaluation["provider_execution_verdict"] == "PROVIDER_INVALID"
    assert "PRODUCT_SEMANTIC_SUCCESS" not in evaluation["corrected_classifications"]


def test_wire_reject_bounded_recovery_with_correct_formal_goal_passes_product() -> None:
    case = _case(
        case_id="OFFLINE-RECOVERED",
        disposition="MUST_RESOLVE",
        input_text="清理中央河底隧道",
        action="clear_transport",
        target="central_river_tunnel",
        refs=[
            {"ref_type": "ACTION", "key": "clear_transport"},
            {"ref_type": "NODE", "key": "central_river_tunnel"},
        ],
    )
    run = _run(
        status="RESOLVED",
        actual=_resolved_operation(
            action="clear_transport",
            target="central_river_tunnel",
            refs=[
                {"ref_type": "ACTION", "key": "clear_transport"},
                {"ref_type": "NODE", "key": "central_river_tunnel"},
            ],
        ),
        formal_goal={"schema_version": 2},
        classifications=["WIRE_REJECTED", "BOUNDED_RECOVERY"],
        provider_calls=[
            {"response_validation": "REJECTED", "recovery_attempt": 0},
            {"response_validation": "ACCEPTED", "recovery_attempt": 1},
        ],
        recovery_attempt_count=1,
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["product_semantic_verdict"] == "PASS"
    assert evaluation["provider_execution_verdict"] == "RECOVERED"
    assert "WIRE_REJECTED" in evaluation["corrected_classifications"]
    assert "BOUNDED_RECOVERY" in evaluation["corrected_classifications"]


def _accepted_operation_trace(
    *,
    action: str = "travel",
    family: str = "OPERATION",
    operation_result: str = "RESOLVED",
    contract_result: str = "ACCEPTED",
) -> dict:
    return {
        "stages": [
            {"stage": "SEMANTIC_GROUNDING", "status": "RESOLVED"},
            {"stage": "FAMILY_ROUTING", "frozen_family": family},
            {"stage": "ACTION_ROUTING", "action_key": action, "result": "MATCHED"},
            {"stage": "OPERATION_GROUNDING", "result": operation_result},
            {"stage": "CONTRACT_VALIDATION", "result": contract_result},
            {"stage": "FORMAL_GOAL", "result": "ACCEPTED"},
        ]
    }


def test_region_node_identity_is_equivalent_only_for_proven_region_contract() -> None:
    case = _case(
        case_id="OFFLINE-REGION-NODE",
        disposition="MUST_RESOLVE",
        input_text="前往东部居住区",
        action="travel",
        target="east_residential_district",
        refs=[
            {"ref_type": "ACTION", "key": "travel"},
            {"ref_type": "REGION", "key": "east_residential_district"},
        ],
        coverage=[{"type": "REGION", "key": "east_residential_district"}],
    )
    run = _run(
        status="RESOLVED",
        actual=_resolved_operation(
            action="travel",
            target="east_residential_district",
            refs=[
                {"ref_type": "ACTION", "key": "travel"},
                {"ref_type": "NODE", "key": "east_residential_district"},
            ],
        ),
        formal_goal={"schema_version": 2},
        provider_observation=_accepted_operation_trace(),
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["product_semantic_verdict"] == "PASS"
    assert "EXPLICIT_CONSTRAINT_DROPPED" not in evaluation["corrected_classifications"]


def test_region_node_compatibility_rejects_different_key() -> None:
    case = _case(
        case_id="OFFLINE-REGION-NODE-DIFFERENT",
        disposition="MUST_RESOLVE",
        input_text="前往东部居住区",
        action="travel",
        target="east_residential_district",
        refs=[{"ref_type": "REGION", "key": "east_residential_district"}],
        coverage=[{"type": "REGION", "key": "east_residential_district"}],
    )
    run = _run(
        status="RESOLVED",
        actual=_resolved_operation(
            action="travel",
            target="west_logistics_district",
            refs=[{"ref_type": "NODE", "key": "west_logistics_district"}],
        ),
        formal_goal={"schema_version": 2},
        provider_observation=_accepted_operation_trace(),
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["product_semantic_verdict"] == "FAIL"
    assert "EXPLICIT_CONSTRAINT_DROPPED" in evaluation["corrected_classifications"]


def test_region_node_compatibility_rejects_resource_node_mismatch() -> None:
    case = _case(
        case_id="OFFLINE-RESOURCE-NODE",
        disposition="MUST_RESOLVE",
        input_text="检查中央医院",
        action="inspect",
        target="central_hospital",
        refs=[{"ref_type": "RESOURCE", "key": "central_hospital"}],
    )
    run = _run(
        status="RESOLVED",
        actual=_resolved_operation(
            action="inspect",
            target="central_hospital",
            refs=[{"ref_type": "NODE", "key": "central_hospital"}],
        ),
        formal_goal={"schema_version": 2},
        provider_observation=_accepted_operation_trace(action="inspect"),
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["product_semantic_verdict"] == "FAIL"
    assert "EXPLICIT_CONSTRAINT_DROPPED" in evaluation["corrected_classifications"]


def test_region_node_compatibility_rejects_key_not_proven_as_region() -> None:
    case = _case(
        case_id="OFFLINE-NONREGION-NODE",
        disposition="MUST_RESOLVE",
        input_text="检查中央医院",
        action="inspect",
        target="central_hospital",
        refs=[{"ref_type": "REGION", "key": "central_hospital"}],
    )
    run = _run(
        status="RESOLVED",
        actual=_resolved_operation(
            action="inspect",
            target="central_hospital",
            refs=[{"ref_type": "NODE", "key": "central_hospital"}],
        ),
        formal_goal={"schema_version": 2},
        provider_observation=_accepted_operation_trace(action="inspect"),
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["product_semantic_verdict"] == "FAIL"
    assert "EXPLICIT_CONSTRAINT_DROPPED" in evaluation["corrected_classifications"]


def test_region_node_compatibility_rejects_type_outside_accepted_node_contract() -> None:
    case = _case(
        case_id="OFFLINE-REGION-FACILITY",
        disposition="MUST_RESOLVE",
        input_text="前往东部居住区",
        action="travel",
        target="east_residential_district",
        refs=[{"ref_type": "REGION", "key": "east_residential_district"}],
        coverage=[{"type": "REGION", "key": "east_residential_district"}],
    )
    run = _run(
        status="RESOLVED",
        actual=_resolved_operation(
            action="travel",
            target="east_residential_district",
            target_ref_type="FACILITY",
            refs=[{"ref_type": "FACILITY", "key": "east_residential_district"}],
        ),
        formal_goal={"schema_version": 2},
        provider_observation=_accepted_operation_trace(),
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["product_semantic_verdict"] == "FAIL"
    assert "EXPLICIT_CONSTRAINT_DROPPED" in evaluation["corrected_classifications"]


def test_first_divergence_uses_family_trace_before_later_state_rejection() -> None:
    case = _case(
        case_id="OFFLINE-FAMILY-FIRST",
        disposition="MUST_RESOLVE",
        input_text="探查中央城区的资源",
        action="survey_resources",
        target="central_district",
        refs=[{"ref_type": "REGION", "key": "central_district"}],
        coverage=[{"type": "REGION", "key": "central_district"}],
    )
    run = _run(
        status="UNSUPPORTED",
        provider_observation={
            "stages": [
                {"stage": "SEMANTIC_GROUNDING", "status": "RESOLVED"},
                {"stage": "FAMILY_ROUTING", "frozen_family": "STATE"},
                {"stage": "STATE_INTERPRETATION", "result": "UNSUPPORTED"},
            ]
        },
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["first_actual_semantic_divergence_stage"] == "FAMILY_ROUTING"
    assert evaluation["final_rejection_stage"] == "STATE_INTERPRETATION"


def test_first_divergence_uses_action_trace_after_correct_family() -> None:
    case = _case(
        case_id="OFFLINE-ACTION-FIRST",
        disposition="MUST_RESOLVE",
        input_text="检查中央医院",
        action="inspect",
        target="central_hospital",
        refs=[{"ref_type": "NODE", "key": "central_hospital"}],
    )
    run = _run(
        status="UNSUPPORTED",
        provider_observation={
            "stages": [
                {"stage": "SEMANTIC_GROUNDING", "status": "RESOLVED"},
                {"stage": "FAMILY_ROUTING", "frozen_family": "OPERATION"},
                {"stage": "ACTION_ROUTING", "action_key": "travel", "result": "MATCHED"},
                {"stage": "OPERATION_GROUNDING", "result": "UNSUPPORTED"},
            ]
        },
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["first_actual_semantic_divergence_stage"] == "ACTION_ROUTING"


def test_first_divergence_uses_operation_trace_for_dropped_constraint() -> None:
    case = _case(
        case_id="OFFLINE-OPERATION-FIRST",
        disposition="MUST_RESOLVE",
        input_text="前往东部居住区",
        action="travel",
        target="east_residential_district",
        refs=[{"ref_type": "REGION", "key": "east_residential_district"}],
        coverage=[{"type": "REGION", "key": "east_residential_district"}],
    )
    run = _run(
        status="RESOLVED",
        actual=_resolved_operation(
            action="travel",
            target=None,
            refs=[{"ref_type": "ACTION", "key": "travel"}],
        ),
        formal_goal={"schema_version": 2},
        provider_observation=_accepted_operation_trace(),
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["first_actual_semantic_divergence_stage"] == "OPERATION_GROUNDING"


def test_success_has_no_divergence_or_rejection_stage() -> None:
    case = _case(
        case_id="OFFLINE-SUCCESS",
        disposition="MUST_RESOLVE",
        input_text="前往东部居住区",
        action="travel",
        target="east_residential_district",
        refs=[
            {"ref_type": "ACTION", "key": "travel"},
            {"ref_type": "REGION", "key": "east_residential_district"},
        ],
        coverage=[{"type": "REGION", "key": "east_residential_district"}],
    )
    run = _run(
        status="RESOLVED",
        actual=_resolved_operation(
            action="travel",
            target="east_residential_district",
            refs=[
                {"ref_type": "ACTION", "key": "travel"},
                {"ref_type": "NODE", "key": "east_residential_district"},
            ],
        ),
        formal_goal={"schema_version": 2},
        provider_observation=_accepted_operation_trace(),
    )

    evaluation = evaluate_run(case, run)

    assert evaluation["product_semantic_verdict"] == "PASS"
    assert evaluation["first_actual_semantic_divergence_stage"] == "NONE"
    assert evaluation["final_rejection_stage"] == "NONE"
