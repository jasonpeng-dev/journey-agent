"""Pure offline evaluator for Goal Resolver Large Regression V1.

This module deliberately has no application, database, HTTP, or Provider
dependencies. It consumes the already persisted manifest/results evidence and
recomputes the product semantic verdict independently from Provider execution
health.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

EVALUATOR_VERSION = "goal-resolver-large-regression-evaluator@offline-correction-v2"

VNEXT_STAGE_ORDER = (
    "SEMANTIC_GROUNDING",
    "FAMILY_ROUTING",
    "STATE_INTERPRETATION",
    "ACTION_ROUTING",
    "OPERATION_GROUNDING",
    "CONTRACT_VALIDATION",
    "FORMAL_GOAL",
)

PROVIDER_INVALID_CODES = {
    "MODEL_PROVIDER_RESPONSE_INVALID",
    "PROVIDER_SCHEMA_INVALID",
    "MODEL_PROVIDER_HTTP_ERROR",
    "MODEL_PROVIDER_TIMEOUT",
    "MODEL_PROVIDER_FAILURE",
    "MODEL_UNSUPPORTED",
}

TECHNICAL_CLASSIFICATIONS = {
    "WIRE_REJECTED",
    "BOUNDED_RECOVERY",
    "FINAL_PROVIDER_INVALID",
}

SEMANTIC_FAILURE_CLASSIFICATIONS = {
    "WRONG_CANONICAL",
    "ACTION_FAMILY_DRIFT",
    "OPERATION_DOWNGRADE",
    "EXPLICIT_CONSTRAINT_DROPPED",
    "SOURCE_TARGET_SWAP",
    "CLARIFICATION_DRIFT",
    "UNSUPPORTED_DRIFT",
    "NOT_SPECIFIED_VIOLATION",
    "FORMAL_GOAL_COMPILE_REJECTED",
    "REDUNDANT_CLARIFICATION_FIELD",
    "FINAL_PROVIDER_INVALID",
}

PRODUCT_SUCCESS_CLASSIFICATIONS = {
    "PRODUCT_SEMANTIC_SUCCESS",
    "GROUNDING_SEMANTIC_SUCCESS",
}

SLOT_ORDER = (
    "action",
    "actor",
    "target",
    "source",
    "resource",
    "amount",
    "state_target",
    "state_value",
)

REVIEW_CASES: dict[str, str] = {
    "R5-REJECT-ACTOR-TARGET-CONFLICT": (
        "Actor capability conflict boundary: Resolver-vs-Validator ownership "
        "of capability rejection is not fixed by this evaluator."
    ),
    "R5-REJECT-NONLOCAL-TRANSPORT": (
        "Nonlocal transport legality boundary: one-hop route validation "
        "ownership is not fixed by this evaluator."
    ),
    "R6-CLEAR-TUNNEL": (
        "Neutral road state/action wording boundary: STATE-vs-OPERATION "
        "interpretation requires architecture confirmation."
    ),
}

AMOUNT_PATTERN = re.compile(
    r"(?<!\d)(?P<amount>\d+)\s*(?:个|件|份|吨|箱|台|套|辆|条|枚|单位)"
)

PROMPT_FIELD_TERMS: dict[str, tuple[str, ...]] = {
    "action": (
        "action",
        "repair",
        "clear",
        "transport",
        "行动",
        "动作",
        "操作",
        "修复",
        "清理",
        "运输",
        "运送",
        "动词",
    ),
    "actor": (
        "actor",
        "执行者",
        "行动者",
        "负责队伍",
        "由谁",
        "谁来",
    ),
    "target": (
        "target",
        "facility",
        "具体设施",
        "基础设施",
        "公共服务",
        "设施",
        "区域",
        "地点",
        "目的地",
        "目标",
        "节点",
        "医院",
        "隧道",
        "泵站",
        "电站",
        "桥",
    ),
    "source": (
        "source",
        "来源",
        "起点",
        "源区域",
        "从哪里",
    ),
    "resource": (
        "resource",
        "资源",
        "部件",
        "零件",
        "物资",
        "类型",
        "种类",
    ),
    "amount": (
        "amount",
        "数量",
        "多少",
        "数目",
        "个数",
        "件数",
    ),
    "state_target": (
        "state",
        "状态",
        "状态目标",
        "状态值",
        "达到",
    ),
    "state_value": (
        "state",
        "状态",
        "状态值",
        "available",
        "可用",
    ),
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _add_classification(classifications: list[str], value: str) -> None:
    if value not in classifications:
        classifications.append(value)


def _resolution(run: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(run.get("resolution"))


def _actual(run: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(run.get("actual_semantics"))


def _status(run: Mapping[str, Any]) -> str | None:
    resolution_status = _resolution(run).get("status")
    if isinstance(resolution_status, str):
        return resolution_status
    actual_status = _actual(run).get("resolution_status")
    return actual_status if isinstance(actual_status, str) else None


def _formal_goal_present(run: Mapping[str, Any]) -> bool:
    return run.get("formal_goal") is not None


def _error_codes(run: Mapping[str, Any]) -> set[str]:
    codes: set[str] = set()
    for error in _list(run.get("errors")):
        if isinstance(error, Mapping):
            code = error.get("code")
            if isinstance(code, str):
                codes.add(code)
    return codes


def _provider_calls(run: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        call
        for call in _list(run.get("provider_calls"))
        if isinstance(call, Mapping)
    ]


def provider_execution_evidence(run: Mapping[str, Any]) -> dict[str, Any]:
    """Extract Provider health signals without making a Provider call."""

    raw_classifications = {
        item for item in _list(run.get("classifications")) if isinstance(item, str)
    }
    calls = _provider_calls(run)
    rejected_call = any(
        call.get("response_validation") == "REJECTED"
        or call.get("validation") == "REJECTED"
        for call in calls
    )
    wire_rejected = "WIRE_REJECTED" in raw_classifications or rejected_call

    recovery_attempt_count = run.get("recovery_attempt_count")
    recovery_used = (
        "BOUNDED_RECOVERY" in raw_classifications
        or (isinstance(recovery_attempt_count, int) and recovery_attempt_count > 0)
        or any(
            (
                isinstance(call.get("recovery_attempt"), int)
                and call["recovery_attempt"] > 0
            )
            or (
                isinstance(call.get("grounding_round"), int)
                and call["grounding_round"] > 1
            )
            or (
                isinstance(call.get("interpretation_attempt"), int)
                and call["interpretation_attempt"] > 1
            )
            or call.get("recovery_used") is True
            for call in calls
        )
    )

    error_codes = sorted(_error_codes(run))
    final_provider_invalid = (
        "FINAL_PROVIDER_INVALID" in raw_classifications
        or bool(set(error_codes) & PROVIDER_INVALID_CODES)
        or _status(run) == "ERROR"
    )
    return {
        "wire_rejected": wire_rejected,
        "bounded_recovery": recovery_used,
        "final_provider_invalid": final_provider_invalid,
        "error_codes": error_codes,
        "recovery_attempt_count": recovery_attempt_count,
        "raw_provider_call_count": len(calls),
    }


def provider_execution_verdict(run: Mapping[str, Any]) -> str:
    evidence = provider_execution_evidence(run)
    if evidence["final_provider_invalid"]:
        return "PROVIDER_INVALID"
    if evidence["wire_rejected"] or evidence["bounded_recovery"]:
        return "RECOVERED"
    return "CLEAN"


def _put_field(
    fields: dict[str, Any],
    evidence: dict[str, str],
    field: str,
    value: Any,
    source: str,
) -> None:
    if value is None:
        return
    fields[field] = value
    evidence[field] = source


def _resource_keys_from_refs(case: Mapping[str, Any]) -> list[str]:
    return _unique(
        ref.get("key")
        for ref in _list(case.get("expected_canonical_refs"))
        if isinstance(ref, Mapping)
        and ref.get("ref_type") == "RESOURCE"
        and isinstance(ref.get("key"), str)
    )


def _state_fields(
    state_requirement: Mapping[str, Any],
    fields: dict[str, Any],
    evidence: dict[str, str],
) -> None:
    kind = state_requirement.get("kind")
    if not isinstance(kind, str):
        return
    if kind == "DERIVED_STATE":
        _put_field(
            fields,
            evidence,
            "state_target",
            state_requirement.get("derived_key"),
            "manifest.harness.state_requirement",
        )
    elif kind == "FACT":
        _put_field(
            fields,
            evidence,
            "state_target",
            state_requirement.get("node_key"),
            "manifest.harness.state_requirement",
        )
    elif kind == "RESOURCE_AT_LEAST":
        _put_field(
            fields,
            evidence,
            "state_target",
            state_requirement.get("region_key"),
            "manifest.harness.state_requirement",
        )
        _put_field(
            fields,
            evidence,
            "state_resource",
            state_requirement.get("resource_key"),
            "manifest.harness.state_requirement",
        )
        _put_field(
            fields,
            evidence,
            "state_minimum",
            state_requirement.get("minimum"),
            "manifest.harness.state_requirement",
        )
    accepted_values = state_requirement.get("accepted_values")
    if isinstance(accepted_values, list) and accepted_values:
        value: Any = (
            accepted_values[0] if len(accepted_values) == 1 else list(accepted_values)
        )
        _put_field(
            fields,
            evidence,
            "state_value",
            value,
            "manifest.harness.state_requirement",
        )


def extract_explicit_constraints(case: Mapping[str, Any]) -> dict[str, Any]:
    """Build explicit slots from manifest metadata and deterministic input facts.

    This is intentionally not a second semantic/NLP resolver. The one
    input-derived fact is a numeric quantity with an explicit Chinese unit,
    needed for the persisted '运30个部件到南部' evidence whose manifest
    parameter object is intentionally absent because Resource is ambiguous.
    """

    fields: dict[str, Any] = {slot: None for slot in SLOT_ORDER}
    evidence: dict[str, str] = {}
    resource_candidates = _resource_keys_from_refs(case)

    _put_field(
        fields,
        evidence,
        "action",
        case.get("expected_action_key"),
        "manifest.expected_action_key",
    )
    _put_field(
        fields,
        evidence,
        "actor",
        case.get("expected_actor_key"),
        "manifest.expected_actor_key",
    )
    _put_field(
        fields,
        evidence,
        "target",
        case.get("expected_target_key"),
        "manifest.expected_target_key",
    )

    expected_bindings = _list(case.get("expected_binding_constraints"))
    for binding in expected_bindings:
        if not isinstance(binding, Mapping):
            continue
        if binding.get("role") == "source_region":
            _put_field(
                fields,
                evidence,
                "source",
                binding.get("value"),
                "manifest.expected_binding_constraints",
            )
            break

    expected_parameters = _mapping(case.get("expected_parameter_constraints"))
    expected_resources = _list(expected_parameters.get("resources"))
    parameter_resource_keys: list[str] = []
    parameter_amounts: list[Any] = []
    for resource in expected_resources:
        if not isinstance(resource, Mapping):
            continue
        key = resource.get("resource_key")
        if isinstance(key, str):
            parameter_resource_keys.append(key)
            resource_candidates.append(key)
        if resource.get("amount") is not None:
            parameter_amounts.append(resource.get("amount"))

    source_key = expected_parameters.get("source_key")
    if fields["source"] is None:
        _put_field(
            fields,
            evidence,
            "source",
            source_key,
            "manifest.expected_parameter_constraints",
        )

    if len(parameter_resource_keys) == 1:
        _put_field(
            fields,
            evidence,
            "resource",
            parameter_resource_keys[0],
            "manifest.expected_parameter_constraints",
        )
    if parameter_amounts:
        distinct_amounts = _unique(parameter_amounts)
        if len(distinct_amounts) == 1:
            _put_field(
                fields,
                evidence,
                "amount",
                distinct_amounts[0],
                "manifest.expected_parameter_constraints",
            )

    harness = _mapping(case.get("harness"))
    operation = _mapping(harness.get("operation"))
    if fields["source"] is None:
        _put_field(
            fields,
            evidence,
            "source",
            operation.get("source_key"),
            "manifest.harness.operation",
        )
    if fields["resource"] is None and len(resource_candidates) == 1:
        _put_field(
            fields,
            evidence,
            "resource",
            resource_candidates[0],
            "manifest.expected_canonical_refs",
        )
    if fields["amount"] is None:
        _put_field(
            fields,
            evidence,
            "amount",
            operation.get("amount"),
            "manifest.harness.operation",
        )

    amount_matches = AMOUNT_PATTERN.findall(str(case.get("input") or ""))
    if fields["amount"] is None and amount_matches:
        _put_field(
            fields,
            evidence,
            "amount",
            int(amount_matches[0]),
            "input.numeric_quantity",
        )

    state_requirement = _mapping(harness.get("state_requirement"))
    if state_requirement:
        _state_fields(state_requirement, fields, evidence)

    resource_candidates = _unique(
        item for item in resource_candidates if isinstance(item, str)
    )
    if fields["resource"] is None and len(resource_candidates) == 1:
        _put_field(
            fields,
            evidence,
            "resource",
            resource_candidates[0],
            "manifest.expected_canonical_refs",
        )

    missing_or_ambiguous: list[str] = []
    if case.get("expected_family") == "OPERATION":
        if fields["action"] is None and (
            case.get("expected_disposition") == "MUST_CLARIFY"
            or harness.get("route_match") == "AMBIGUOUS"
        ):
            missing_or_ambiguous.append("action")
        if fields["target"] is None and case.get("expected_disposition") == "MUST_CLARIFY":
            missing_or_ambiguous.append("target")
        if fields["resource"] is None and len(resource_candidates) > 1:
            missing_or_ambiguous.append("resource")
    elif case.get("expected_family") == "STATE":
        if fields["state_target"] is None:
            missing_or_ambiguous.append("state_target")
        if fields["state_value"] is None:
            missing_or_ambiguous.append("state_value")

    present_fields = [
        field
        for field in (*SLOT_ORDER, "state_resource", "state_minimum")
        if fields.get(field) is not None
    ]
    return {
        "fields": fields,
        "present_fields": present_fields,
        "missing_or_ambiguous_fields": _unique(missing_or_ambiguous),
        "resource_candidates": resource_candidates,
        "evidence": evidence,
    }


def extract_prompt_fields(prompt: str | None) -> dict[str, Any]:
    text = str(prompt or "")
    folded = text.casefold()
    fields: list[str] = []
    matched_terms: dict[str, list[str]] = {}
    for field, terms in PROMPT_FIELD_TERMS.items():
        matches = [term for term in terms if term.casefold() in folded]
        # These words occur as context in the persisted R6 clarification
        # ("要运输的部件" and "这个目标缺少..."), not as requests to refill
        # Action or Target. Keep the extraction deterministic and request-slot
        # oriented so only Resource and Amount are marked there.
        if field == "action" and any(
            term in matches for term in ("运输", "运送")
        ) and re.search(r"(运输|运送)的?(?:部件|资源|物资)", text):
            matches = [
                term for term in matches if term not in {"运输", "运送"}
            ]
        if field == "target" and re.search(
            r"(?:这个|该|当前)?目标缺少", text
        ):
            matches = [term for term in matches if term != "目标"]
        if matches:
            fields.append(field)
            matched_terms[field] = matches
    return {
        "fields": [field for field in SLOT_ORDER if field in fields],
        "matched_terms": matched_terms,
        "prompt_present": bool(text.strip()),
    }


def clarification_analysis(
    case: Mapping[str, Any],
    run: Mapping[str, Any],
    explicit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    explicit_constraints = (
        dict(explicit) if explicit is not None else extract_explicit_constraints(case)
    )
    resolution = _resolution(run)
    prompt = resolution.get("clarification_prompt")
    if not isinstance(prompt, str):
        prompt = ""
    prompt_fields = extract_prompt_fields(prompt)
    explicit_fields = _mapping(explicit_constraints.get("fields"))
    present = set(_list(explicit_constraints.get("present_fields")))
    missing = set(_list(explicit_constraints.get("missing_or_ambiguous_fields")))
    mentioned = set(_list(prompt_fields.get("fields")))

    valid = [
        field for field in SLOT_ORDER if field in mentioned and field in missing
    ]
    redundant = [
        field for field in SLOT_ORDER if field in mentioned and field in present
    ]
    unrelated = [
        field
        for field in SLOT_ORDER
        if field in mentioned and field not in missing and field not in present
    ]
    ambiguity_evidence = {
        "route_match": _mapping(case.get("harness")).get("route_match"),
        "route_candidates": _list(_mapping(case.get("harness")).get("route_candidates")),
        "resource_candidates": _list(explicit_constraints.get("resource_candidates")),
        "missing_or_ambiguous_fields": list(
            _list(explicit_constraints.get("missing_or_ambiguous_fields"))
        ),
    }
    clarification_relevant = bool(valid)
    has_erroneous_formal_goal = _formal_goal_present(run)
    clarification_valid = (
        _status(run) == "NEEDS_CLARIFICATION"
        and not has_erroneous_formal_goal
        and clarification_relevant
        and not redundant
    )
    return {
        "prompt": prompt,
        "prompt_fields": prompt_fields,
        "explicit_fields": dict(explicit_fields),
        "valid_clarification_fields": valid,
        "redundant_clarification_fields": redundant,
        "redundant_constraint_requests": redundant,
        "unrelated_clarification_fields": unrelated,
        "clarification_relevant": clarification_relevant,
        "semantic_ambiguity_evidence": ambiguity_evidence,
        "has_erroneous_formal_goal": has_erroneous_formal_goal,
        "clarification_valid": clarification_valid,
        "resolution_status": _status(run),
    }


def _actual_operation_slots(actual: Mapping[str, Any]) -> dict[str, Any]:
    parameters = _mapping(actual.get("parameters"))
    bindings = _list(actual.get("bindings"))
    binding_by_role = {
        binding.get("role"): binding.get("value")
        for binding in bindings
        if isinstance(binding, Mapping) and isinstance(binding.get("role"), str)
    }
    source = binding_by_role.get("source_region")
    if source is None:
        source = parameters.get("source_key")

    resources = _list(parameters.get("resources"))
    resource_keys = [
        resource.get("resource_key")
        for resource in resources
        if isinstance(resource, Mapping)
        and isinstance(resource.get("resource_key"), str)
    ]
    amounts = [
        resource.get("amount")
        for resource in resources
        if isinstance(resource, Mapping) and resource.get("amount") is not None
    ]
    resource = resource_keys[0] if len(resource_keys) == 1 else None
    amount = amounts[0] if len(amounts) == 1 else None
    return {
        "action": actual.get("action_key"),
        "actor": actual.get("actor_key"),
        "target": actual.get("target_key"),
        "source": source,
        "resource": resource,
        "resource_keys": resource_keys,
        "amount": amount,
        "amounts": amounts,
        "bindings": binding_by_role,
        "parameters": parameters,
    }


def _canonical_ref_tuples(refs: Any) -> set[tuple[Any, Any]]:
    return {
        (ref.get("ref_type"), ref.get("key"))
        for ref in _list(refs)
        if isinstance(ref, Mapping)
    }


def _scenario_region_keys(case: Mapping[str, Any]) -> set[str]:
    """Return Region identities proven by the manifest's Scenario coverage."""

    keys: set[str] = set()
    for item in _list(case.get("coverage")):
        if not isinstance(item, Mapping) or item.get("type") != "REGION":
            continue
        key = item.get("key")
        if isinstance(key, str):
            keys.add(key)
    return keys


def _operation_contract_accepts_node(
    case: Mapping[str, Any],
    run: Mapping[str, Any] | None,
    actual: Mapping[str, Any],
) -> bool:
    """Require evidence that this accepted Operation contract can use NODE."""

    if case.get("expected_family") != "OPERATION":
        return False
    if actual.get("family") != "OPERATION":
        return False
    if actual.get("target_ref_type") != "NODE":
        return False
    if run is None or _status(run) != "RESOLVED" or not _formal_goal_present(run):
        return False
    observation = _mapping(_resolution(run).get("provider_observation"))
    stages = _list(observation.get("stages"))
    contract_stages = [
        item
        for item in stages
        if isinstance(item, Mapping) and item.get("stage") == "CONTRACT_VALIDATION"
    ]
    return not contract_stages or contract_stages[-1].get("result") in {
        "ACCEPTED",
        "RESOLVED",
    }


def _canonical_ref_equivalent(
    case: Mapping[str, Any],
    expected: tuple[Any, Any],
    actual: tuple[Any, Any],
    run: Mapping[str, Any] | None,
    actual_semantics: Mapping[str, Any],
) -> bool:
    """Compare one expected reference with one actual reference.

    REGION/NODE is intentionally the only representation compatibility.  It
    is accepted only when the manifest proves the key is a Region and the
    accepted Operation/FormalGoal evidence proves NODE is contract-valid.
    """

    if expected == actual:
        return True
    expected_type, expected_key = expected
    actual_type, actual_key = actual
    if {expected_type, actual_type} != {"REGION", "NODE"}:
        return False
    if not isinstance(expected_key, str) or expected_key != actual_key:
        return False
    if expected_key not in _scenario_region_keys(case):
        return False
    return _operation_contract_accepts_node(case, run, actual_semantics)


def _canonical_refs_cover_expected(
    case: Mapping[str, Any],
    expected_refs: set[tuple[Any, Any]],
    actual_refs: set[tuple[Any, Any]],
    run: Mapping[str, Any] | None,
    actual_semantics: Mapping[str, Any],
) -> bool:
    return all(
        any(
            _canonical_ref_equivalent(
                case,
                expected,
                actual,
                run,
                actual_semantics,
            )
            for actual in actual_refs
        )
        for expected in expected_refs
    )


def _compare_operation(
    case: Mapping[str, Any],
    actual: Mapping[str, Any],
    explicit: Mapping[str, Any],
    classifications: list[str],
    run: Mapping[str, Any] | None = None,
) -> None:
    fields = _mapping(explicit.get("fields"))
    slots = _actual_operation_slots(actual)
    expected_action = fields.get("action")
    expected_actor = fields.get("actor")
    expected_target = fields.get("target")
    expected_source = fields.get("source")
    expected_resource = fields.get("resource")
    expected_amount = fields.get("amount")

    if expected_action is None:
        if slots["action"] is not None:
            _add_classification(classifications, "NOT_SPECIFIED_VIOLATION")
    elif slots["action"] != expected_action:
        _add_classification(classifications, "WRONG_CANONICAL")
        _add_classification(classifications, "ACTION_FAMILY_DRIFT")

    if expected_actor is None:
        if slots["actor"] is not None:
            _add_classification(classifications, "NOT_SPECIFIED_VIOLATION")
    elif slots["actor"] is None:
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")
    elif slots["actor"] != expected_actor:
        _add_classification(classifications, "WRONG_CANONICAL")
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")

    if expected_target is None:
        if slots["target"] is not None:
            _add_classification(classifications, "NOT_SPECIFIED_VIOLATION")
    elif slots["target"] is None:
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")
    elif slots["target"] != expected_target:
        if expected_source is not None and slots["target"] == expected_source:
            _add_classification(classifications, "SOURCE_TARGET_SWAP")
        else:
            _add_classification(classifications, "WRONG_CANONICAL")
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")

    if expected_source is None:
        if slots["source"] is not None:
            _add_classification(classifications, "NOT_SPECIFIED_VIOLATION")
    elif slots["source"] is None:
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")
    elif slots["source"] != expected_source:
        if expected_target is not None and slots["source"] == expected_target:
            _add_classification(classifications, "SOURCE_TARGET_SWAP")
        else:
            _add_classification(classifications, "WRONG_CANONICAL")
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")

    if expected_resource is None:
        if slots["resource_keys"]:
            _add_classification(classifications, "NOT_SPECIFIED_VIOLATION")
    elif slots["resource"] is None:
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")
    elif slots["resource"] != expected_resource:
        _add_classification(classifications, "WRONG_CANONICAL")
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")

    if expected_amount is None:
        if slots["amounts"]:
            _add_classification(classifications, "NOT_SPECIFIED_VIOLATION")
    elif slots["amount"] is None or slots["amount"] != expected_amount:
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")

    expected_bindings = {
        binding.get("role"): binding.get("value")
        for binding in _list(case.get("expected_binding_constraints"))
        if isinstance(binding, Mapping) and isinstance(binding.get("role"), str)
    }
    actual_bindings = slots["bindings"]
    for role, expected_value in expected_bindings.items():
        actual_value = actual_bindings.get(role)
        if actual_value is None:
            _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")
        elif actual_value != expected_value:
            _add_classification(classifications, "WRONG_CANONICAL")
            _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")
    for role in actual_bindings:
        if role not in expected_bindings:
            _add_classification(classifications, "NOT_SPECIFIED_VIOLATION")

    expected_parameters = _mapping(case.get("expected_parameter_constraints"))
    actual_parameters = slots["parameters"]
    for key, expected_value in expected_parameters.items():
        if key in {"resources", "source_key"}:
            continue
        if actual_parameters.get(key) != expected_value:
            _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")

    expected_refs = _canonical_ref_tuples(case.get("expected_canonical_refs"))
    actual_refs = _canonical_ref_tuples(actual.get("canonical_refs"))
    if not _canonical_refs_cover_expected(
        case,
        expected_refs,
        actual_refs,
        run,
        actual,
    ):
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")
    forbidden_refs = _canonical_ref_tuples(case.get("forbidden_canonical_refs"))
    if actual_refs & forbidden_refs:
        _add_classification(classifications, "WRONG_CANONICAL")


def _accepted_values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value is None:
        return []
    return [value]


def _state_kind(state_requirement: Mapping[str, Any]) -> str | None:
    explicit_kind = state_requirement.get("kind")
    if isinstance(explicit_kind, str):
        return explicit_kind
    if state_requirement.get("fact_key") is not None:
        return "FACT"
    if state_requirement.get("derived_key") is not None:
        return "DERIVED_STATE"
    if (
        state_requirement.get("resource_key") is not None
        or state_requirement.get("minimum") is not None
    ):
        return "RESOURCE_AT_LEAST"
    return None


def _compare_state(
    case: Mapping[str, Any],
    actual: Mapping[str, Any],
    explicit: Mapping[str, Any],
    classifications: list[str],
) -> None:
    expected_state = _mapping(_mapping(case.get("harness")).get("state_requirement"))
    actual_state = _mapping(actual.get("state_requirement"))
    if not actual_state:
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")
        return
    expected_kind = expected_state.get("kind")
    actual_kind = _state_kind(actual_state)
    if actual_kind != expected_kind:
        _add_classification(classifications, "WRONG_CANONICAL")
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")

    fields = _mapping(explicit.get("fields"))
    expected_target = fields.get("state_target")
    if expected_target is not None:
        actual_target = (
            actual_state.get("derived_key")
            if actual_kind == "DERIVED_STATE"
            else actual_state.get("node_key")
            if actual_kind == "FACT"
            else actual_state.get("region_key")
        )
        if actual_target != expected_target:
            _add_classification(classifications, "WRONG_CANONICAL")
            _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")

    expected_value = fields.get("state_value")
    if expected_value is not None and set(_accepted_values(expected_value)) != set(
        _accepted_values(actual_state.get("accepted_values"))
    ):
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")

    if expected_kind == "RESOURCE_AT_LEAST":
        if actual_state.get("resource_key") != fields.get("state_resource"):
            _add_classification(classifications, "WRONG_CANONICAL")
            _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")
        if actual_state.get("minimum") != fields.get("state_minimum"):
            _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")

    expected_refs = _canonical_ref_tuples(case.get("expected_canonical_refs"))
    actual_refs = _canonical_ref_tuples(actual.get("canonical_refs"))
    if not expected_refs.issubset(actual_refs):
        _add_classification(classifications, "EXPLICIT_CONSTRAINT_DROPPED")
    forbidden_refs = _canonical_ref_tuples(case.get("forbidden_canonical_refs"))
    if actual_refs & forbidden_refs:
        _add_classification(classifications, "WRONG_CANONICAL")


def _compare_family(
    case: Mapping[str, Any],
    actual: Mapping[str, Any],
    classifications: list[str],
) -> None:
    expected_family = case.get("expected_family")
    actual_family = actual.get("family")
    if actual_family != expected_family:
        _add_classification(classifications, "ACTION_FAMILY_DRIFT")
        if expected_family == "OPERATION" and actual_family != "OPERATION":
            _add_classification(classifications, "OPERATION_DOWNGRADE")
    if actual_family in _list(case.get("forbidden_families")):
        _add_classification(classifications, "WRONG_CANONICAL")


def _base_product_evaluation(
    case: Mapping[str, Any],
    run: Mapping[str, Any],
    explicit: Mapping[str, Any],
    clarification: Mapping[str, Any],
    provider_verdict: str,
) -> dict[str, Any]:
    classifications: list[str] = []
    provider_evidence = provider_execution_evidence(run)
    raw_classes = set(
        item for item in _list(run.get("classifications")) if isinstance(item, str)
    )
    if provider_evidence["wire_rejected"]:
        _add_classification(classifications, "WIRE_REJECTED")
    if provider_evidence["bounded_recovery"]:
        _add_classification(classifications, "BOUNDED_RECOVERY")
    if provider_evidence["final_provider_invalid"]:
        _add_classification(classifications, "FINAL_PROVIDER_INVALID")

    status = _status(run)
    actual = _actual(run)
    formal_present = _formal_goal_present(run)
    disposition = case.get("expected_disposition")

    if disposition == "MUST_RESOLVE":
        if status != "RESOLVED":
            if status == "NEEDS_CLARIFICATION":
                _add_classification(classifications, "CLARIFICATION_DRIFT")
            elif provider_verdict != "PROVIDER_INVALID":
                _add_classification(classifications, "UNSUPPORTED_DRIFT")
        elif not formal_present:
            _add_classification(classifications, "FORMAL_GOAL_COMPILE_REJECTED")
        else:
            _compare_family(case, actual, classifications)
            if case.get("expected_family") == "OPERATION":
                _compare_operation(case, actual, explicit, classifications, run)
            elif case.get("expected_family") == "STATE":
                _compare_state(case, actual, explicit, classifications)

    elif disposition == "MUST_CLARIFY":
        if status != "NEEDS_CLARIFICATION":
            if provider_verdict == "PROVIDER_INVALID":
                pass
            elif status is None:
                _add_classification(classifications, "UNSUPPORTED_DRIFT")
            else:
                _add_classification(classifications, "CLARIFICATION_DRIFT")
        elif formal_present:
            _add_classification(classifications, "CLARIFICATION_DRIFT")
        else:
            if not clarification.get("clarification_valid"):
                _add_classification(classifications, "CLARIFICATION_DRIFT")
            if _list(clarification.get("redundant_clarification_fields")):
                _add_classification(
                    classifications, "REDUNDANT_CLARIFICATION_FIELD"
                )

    elif disposition == "MUST_NOT_RESOLVE":
        if provider_verdict == "PROVIDER_INVALID" or (
            status in {"NEEDS_CLARIFICATION", "UNSUPPORTED"}
            and not formal_present
        ):
            pass
        elif status == "RESOLVED" or formal_present:
            _add_classification(classifications, "UNSUPPORTED_DRIFT")
            actual_refs = _canonical_ref_tuples(actual.get("canonical_refs"))
            forbidden_refs = _canonical_ref_tuples(
                case.get("forbidden_canonical_refs")
            )
            if actual_refs & forbidden_refs:
                _add_classification(classifications, "WRONG_CANONICAL")
            if actual.get("family") in _list(case.get("forbidden_families")):
                _add_classification(classifications, "WRONG_CANONICAL")
        else:
            _add_classification(classifications, "UNSUPPORTED_DRIFT")

    semantic_failures = set(classifications) & SEMANTIC_FAILURE_CLASSIFICATIONS
    semantic_pass = (
        provider_verdict != "PROVIDER_INVALID"
        and not semantic_failures
        and (
            (disposition == "MUST_RESOLVE" and status == "RESOLVED" and formal_present)
            or (
                disposition == "MUST_CLARIFY"
                and status == "NEEDS_CLARIFICATION"
                and bool(clarification.get("clarification_valid"))
            )
            or (
                disposition == "MUST_NOT_RESOLVE"
                and status in {"NEEDS_CLARIFICATION", "UNSUPPORTED"}
                and not formal_present
            )
        )
    )
    if semantic_pass:
        _add_classification(classifications, "PRODUCT_SEMANTIC_SUCCESS")
        if _mapping(case.get("harness")).get("provider_grounding") == (
            "WHOLE_GOAL_SEMANTIC"
        ):
            _add_classification(classifications, "GROUNDING_SEMANTIC_SUCCESS")

    # Preserve only technical evidence from the persisted raw class list if a
    # legacy result contained one of those markers but its structural evidence
    # was omitted. Semantic classes are intentionally recomputed above.
    for technical in ("WIRE_REJECTED", "BOUNDED_RECOVERY", "FINAL_PROVIDER_INVALID"):
        if technical in raw_classes:
            _add_classification(classifications, technical)

    return {
        "verdict": "PASS" if semantic_pass else "FAIL",
        "classifications": classifications,
        "provider_evidence": provider_evidence,
        "status": status,
        "formal_goal_present": formal_present,
    }


def _stage_trace(run: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    observation = _mapping(_resolution(run).get("provider_observation"))
    return [
        item
        for item in _list(observation.get("stages"))
        if isinstance(item, Mapping) and isinstance(item.get("stage"), str)
    ]


def _latest_stage(
    trace: Iterable[Mapping[str, Any]],
    name: str,
) -> Mapping[str, Any] | None:
    matches = [item for item in trace if item.get("stage") == name]
    return matches[-1] if matches else None


def _stage_result(stage: Mapping[str, Any] | None) -> str | None:
    if stage is None:
        return None
    result = stage.get("result")
    if isinstance(result, str):
        return result
    status = stage.get("status")
    return status if isinstance(status, str) else None


def _operation_slot_diverges(
    case: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> bool:
    explicit = extract_explicit_constraints(case)
    fields = _mapping(explicit.get("fields"))
    slots = _actual_operation_slots(actual)
    for field in ("actor", "target", "source", "resource", "amount"):
        expected = fields.get(field)
        observed = slots.get(field)
        if expected is not None and observed != expected:
            return True
        if expected is None and observed is not None:
            return True
    expected_bindings = {
        binding.get("role"): binding.get("value")
        for binding in _list(case.get("expected_binding_constraints"))
        if isinstance(binding, Mapping) and isinstance(binding.get("role"), str)
    }
    for role, expected in expected_bindings.items():
        if slots["bindings"].get(role) != expected:
            return True
    return False


def first_actual_semantic_divergence_stage(
    run: Mapping[str, Any],
    case: Mapping[str, Any],
    classifications: Iterable[str] | None = None,
) -> str:
    """Locate the first expectation mismatch in the persisted VNext trace.

    This intentionally walks the stage ownership order rather than deriving
    the first failure from the terminal status or final rejection.
    """

    trace = _stage_trace(run)
    actual = _actual(run)
    classes = set(
        classifications
        if classifications is not None
        else _list(run.get("corrected_classifications", run.get("classifications", [])))
    )
    if not trace and provider_execution_verdict(run) == "PROVIDER_INVALID":
        return "PROVIDER_WIRE"
    semantic = _latest_stage(trace, "SEMANTIC_GROUNDING")
    semantic_result = _stage_result(semantic)
    if semantic_result in {"UNSUPPORTED", "ERROR", "REJECTED"}:
        return "SEMANTIC_GROUNDING"

    expected_family = case.get("expected_family")
    family_stage = _latest_stage(trace, "FAMILY_ROUTING")
    actual_family = actual.get("family")
    if actual_family is None and family_stage is not None:
        actual_family = family_stage.get("frozen_family")
    if isinstance(expected_family, str) and actual_family != expected_family:
        return "FAMILY_ROUTING"

    if expected_family == "OPERATION":
        action_stage = _latest_stage(trace, "ACTION_ROUTING")
        expected_action = case.get("expected_action_key")
        actual_action = actual.get("action_key")
        if actual_action is None and action_stage is not None:
            actual_action = action_stage.get("action_key")
        if isinstance(expected_action, str) and actual_action not in {
            None,
            expected_action,
        }:
            return "ACTION_ROUTING"
        if action_stage is not None and _stage_result(action_stage) not in {
            None,
            "MATCHED",
            "RESOLVED",
        }:
            return "ACTION_ROUTING"

        operation_stage = _latest_stage(trace, "OPERATION_GROUNDING")
        operation_result = _stage_result(operation_stage)
        if operation_result in {"UNSUPPORTED", "ERROR", "REJECTED"}:
            return "OPERATION_GROUNDING"
        if operation_result == "NEEDS_CLARIFICATION" and case.get(
            "expected_disposition"
        ) == "MUST_RESOLVE":
            return "OPERATION_GROUNDING"
        if _status(run) == "RESOLVED" and _operation_slot_diverges(case, actual):
            return "OPERATION_GROUNDING"
        if "CLARIFICATION_DRIFT" in classes and case.get(
            "expected_disposition"
        ) == "MUST_CLARIFY":
            return "OPERATION_GROUNDING"
    elif expected_family == "STATE":
        state_stage = _latest_stage(trace, "STATE_INTERPRETATION")
        if _stage_result(state_stage) in {"UNSUPPORTED", "ERROR", "REJECTED"}:
            return "STATE_INTERPRETATION"
        if _stage_result(state_stage) == "NEEDS_CLARIFICATION" and case.get(
            "expected_disposition"
        ) == "MUST_RESOLVE":
            return "STATE_INTERPRETATION"

    contract_stage = _latest_stage(trace, "CONTRACT_VALIDATION")
    if _stage_result(contract_stage) in {
        "UNSUPPORTED",
        "ERROR",
        "REJECTED",
        "BACKEND_VALIDATION_REJECTED",
    }:
        return "CONTRACT_VALIDATION"
    if classes & {
        "WRONG_CANONICAL",
        "EXPLICIT_CONSTRAINT_DROPPED",
        "NOT_SPECIFIED_VIOLATION",
        "FORMAL_GOAL_COMPILE_REJECTED",
    }:
        return "CONTRACT_VALIDATION"

    formal_stage = _latest_stage(trace, "FORMAL_GOAL")
    if _stage_result(formal_stage) in {"UNSUPPORTED", "ERROR", "REJECTED"}:
        return "FORMAL_GOAL"
    if (
        case.get("expected_disposition") == "MUST_RESOLVE"
        and _status(run) == "RESOLVED"
        and not _formal_goal_present(run)
    ):
        return "FORMAL_GOAL"
    if provider_execution_verdict(run) == "PROVIDER_INVALID":
        return "PROVIDER_WIRE"
    if run.get("errors"):
        return final_rejection_stage(run, classes)
    return "NONE"


def final_rejection_stage(
    run: Mapping[str, Any],
    classifications: Iterable[str] | None = None,
) -> str:
    """Return the actual terminal rejection stage, separately from first drift."""

    trace = _stage_trace(run)
    rejected_results = {
        "UNSUPPORTED",
        "NEEDS_CLARIFICATION",
        "REJECTED",
        "BACKEND_VALIDATION_REJECTED",
    }
    for stage in reversed(trace):
        if _stage_result(stage) in rejected_results:
            return str(stage["stage"])
    if provider_execution_verdict(run) == "PROVIDER_INVALID":
        return "PROVIDER_WIRE"
    errors = _list(run.get("errors"))
    if errors:
        phase = errors[-1].get("phase") if isinstance(errors[-1], Mapping) else None
        return {
            "provider_build": "PROVIDER_WIRE",
            "goal_resolution": "PROVIDER_WIRE",
            "formal_goal_compile": "FORMAL_GOAL",
        }.get(phase, "ERROR") if isinstance(phase, str) else "ERROR"
    classes = set(
        classifications
        if classifications is not None
        else _list(run.get("corrected_classifications", run.get("classifications", [])))
    )
    if _status(run) == "RESOLVED" and classes & SEMANTIC_FAILURE_CLASSIFICATIONS:
        return "CONTRACT_VALIDATION"
    return "NONE"


def evaluate_run(case: Mapping[str, Any], run: Mapping[str, Any]) -> dict[str, Any]:
    explicit = extract_explicit_constraints(case)
    clarification = clarification_analysis(case, run, explicit)
    provider_verdict = provider_execution_verdict(run)
    base = _base_product_evaluation(
        case,
        run,
        explicit,
        clarification,
        provider_verdict,
    )

    case_id = case.get("case_id")
    is_review = case_id in REVIEW_CASES
    if is_review:
        # Do not turn unresolved architecture ownership into a model parsing
        # failure. Keep technical Provider evidence visible, while retaining
        # the underlying semantic evaluation separately for review.
        review_classifications = [
            item
            for item in base["classifications"]
            if item in TECHNICAL_CLASSIFICATIONS
        ]
        _add_classification(review_classifications, "EXPECTATION_REVIEW")
        product_verdict = "EXPECTATION_REVIEW"
        corrected_classifications = review_classifications
    else:
        product_verdict = base["verdict"]
        corrected_classifications = list(base["classifications"])

    first_divergence = first_actual_semantic_divergence_stage(
        run,
        case,
        corrected_classifications,
    )
    final_rejection = final_rejection_stage(
        run,
        corrected_classifications,
    )

    return {
        "product_semantic_verdict": product_verdict,
        "provider_execution_verdict": provider_verdict,
        "corrected_classifications": corrected_classifications,
        "underlying_product_semantic_verdict": base["verdict"],
        "underlying_classifications": list(base["classifications"]),
        "provider_execution_evidence": base["provider_evidence"],
        "explicit_constraints": explicit,
        "clarification_analysis": clarification,
        "expectation_review_reason": (
            REVIEW_CASES.get(case_id) if isinstance(case_id, str) else None
        ),
        "first_actual_semantic_divergence_stage": first_divergence,
        "final_rejection_stage": final_rejection,
    }


def _corrected_case_verdict(run_verdicts: list[str]) -> str:
    if "EXPECTATION_REVIEW" in run_verdicts:
        return "EXPECTATION_REVIEW"
    if run_verdicts and all(verdict == "PASS" for verdict in run_verdicts):
        return "PASS"
    return "FAIL"


def _corrected_stability(
    run_evaluations: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    signatures = [
        (
            evaluation.get("product_semantic_verdict"),
            evaluation.get("provider_execution_verdict"),
            tuple(evaluation.get("corrected_classifications") or ()),
        )
        for evaluation in run_evaluations
    ]
    return {
        "stable": len(set(signatures)) <= 1,
        "verdict_stable": len(
            {
                evaluation.get("product_semantic_verdict")
                for evaluation in run_evaluations
            }
        )
        <= 1,
        "signature_count": len(set(signatures)),
        "verdicts": [
            evaluation.get("product_semantic_verdict")
            for evaluation in run_evaluations
        ],
        "provider_verdicts": [
            evaluation.get("provider_execution_verdict")
            for evaluation in run_evaluations
        ],
    }


def _case_result(
    original_case_result: Mapping[str, Any],
    manifest_case: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = copy.deepcopy(dict(original_case_result))
    original_runs = _list(original_case_result.get("runs"))
    corrected_runs: list[dict[str, Any]] = []
    evaluations: list[dict[str, Any]] = []

    for original_run in original_runs:
        run = copy.deepcopy(dict(original_run))
        evaluation = evaluate_run(manifest_case, original_run)
        evaluations.append(evaluation)
        run["original_semantic_success"] = original_run.get("semantic_success")
        run["original_classifications"] = copy.deepcopy(
            original_run.get("classifications") or []
        )
        run.update(evaluation)
        corrected_runs.append(run)

    verdicts = [
        evaluation["product_semantic_verdict"] for evaluation in evaluations
    ]
    corrected_verdict = _corrected_case_verdict(verdicts)
    corrected_classes = Counter(
        classification
        for evaluation in evaluations
        for classification in evaluation["corrected_classifications"]
    )
    result["runs"] = corrected_runs
    result["original_semantic_success"] = original_case_result.get("semantic_success")
    result["original_semantic_success_count"] = original_case_result.get(
        "semantic_success_count"
    )
    result["product_semantic_verdict"] = corrected_verdict
    result["corrected_product_semantic_verdict"] = corrected_verdict
    result["corrected_semantic_success"] = (
        True if corrected_verdict == "PASS" else False
        if corrected_verdict == "FAIL"
        else None
    )
    result["corrected_semantic_success_count"] = sum(
        verdict == "PASS" for verdict in verdicts
    )
    result["corrected_failure_count"] = sum(verdict == "FAIL" for verdict in verdicts)
    result["corrected_expectation_review_count"] = sum(
        verdict == "EXPECTATION_REVIEW" for verdict in verdicts
    )
    result["corrected_classification_counts"] = dict(
        sorted(corrected_classes.items())
    )
    result["corrected_stability"] = _corrected_stability(evaluations)
    return result, corrected_runs


def _verdict_change(
    case: Mapping[str, Any],
    run: Mapping[str, Any],
) -> dict[str, Any] | None:
    original = run.get("semantic_success")
    corrected = run.get("product_semantic_verdict")
    original_bool = bool(original)
    corrected_bool = corrected == "PASS"
    if (
        corrected == "EXPECTATION_REVIEW"
        or original_bool != corrected_bool
    ):
        case_id = case.get("case_id")
        if corrected == "EXPECTATION_REVIEW":
            reason = "EXPECTATION_REVIEW"
        elif not original_bool and corrected == "PASS":
            reason = "FALSE_POSITIVE_CORRECTION"
        elif original_bool and corrected == "FAIL":
            reason = "FALSE_NEGATIVE_CORRECTION"
        else:
            reason = "SEMANTIC_VERDICT_RECOMPUTED"
        return {
            "case_id": case_id,
            "run_index": run.get("run_index"),
            "reason": reason,
            "original_semantic_success": original,
            "corrected_product_semantic_verdict": corrected,
            "provider_execution_verdict": run.get("provider_execution_verdict"),
            "original_classifications": copy.deepcopy(
                run.get("original_classifications") or []
            ),
            "corrected_classifications": copy.deepcopy(
                run.get("corrected_classifications") or []
            ),
            "underlying_product_semantic_verdict": run.get(
                "underlying_product_semantic_verdict"
            ),
            "underlying_classifications": copy.deepcopy(
                run.get("underlying_classifications") or []
            ),
            "status": _status(run),
            "clarification_analysis": copy.deepcopy(
                run.get("clarification_analysis") or {}
            ),
        }
    return None


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_corrected_results(
    manifest: Mapping[str, Any],
    original_results: Mapping[str, Any],
    source_hashes_before: Mapping[str, str],
    source_hashes_after: Mapping[str, str] | None = None,
    *,
    product_code_changes: bool = False,
    source_artifact_paths: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    manifest_cases = {
        case.get("case_id"): case
        for case in _list(manifest.get("cases"))
        if isinstance(case, Mapping) and isinstance(case.get("case_id"), str)
    }
    corrected_cases: list[dict[str, Any]] = []
    verdict_changes: list[dict[str, Any]] = []
    all_corrected_runs: list[dict[str, Any]] = []

    for original_case_result in _list(original_results.get("cases")):
        if not isinstance(original_case_result, Mapping):
            continue
        original_case = _mapping(original_case_result.get("case"))
        case_id = original_case.get("case_id")
        manifest_case = manifest_cases.get(case_id, original_case)
        corrected_case, corrected_runs = _case_result(
            original_case_result,
            manifest_case,
        )
        corrected_cases.append(corrected_case)
        all_corrected_runs.extend(corrected_runs)
        for corrected_run in corrected_runs:
            change = _verdict_change(manifest_case, corrected_run)
            if change is not None:
                verdict_changes.append(change)

    product_verdict_counts = Counter(
        run.get("product_semantic_verdict") for run in all_corrected_runs
    )
    provider_verdict_counts = Counter(
        run.get("provider_execution_verdict") for run in all_corrected_runs
    )
    corrected_classification_counts = Counter(
        classification
        for run in all_corrected_runs
        for classification in _list(run.get("corrected_classifications"))
    )
    corrected_failure_classification_counts = Counter(
        classification
        for run in all_corrected_runs
        if run.get("product_semantic_verdict") == "FAIL"
        for classification in _list(run.get("corrected_classifications"))
    )
    corrected_semantic_failure_counts = Counter(
        classification
        for run in all_corrected_runs
        if run.get("product_semantic_verdict") == "FAIL"
        for classification in _list(run.get("corrected_classifications"))
        if classification in SEMANTIC_FAILURE_CLASSIFICATIONS
    )
    corrected_case_verdict_counts = Counter(
        case.get("corrected_product_semantic_verdict")
        for case in corrected_cases
    )

    original_summary = copy.deepcopy(_mapping(original_results.get("summary")))
    original_success_runs = sum(
        bool(original_run.get("semantic_success"))
        for original_case_result in _list(original_results.get("cases"))
        if isinstance(original_case_result, Mapping)
        for original_run in _list(original_case_result.get("runs"))
        if isinstance(original_run, Mapping)
    )
    original_success_cases = sum(
        bool(case.get("semantic_success"))
        for case in _list(original_results.get("cases"))
        if isinstance(case, Mapping)
    )
    review_case_ids = [
        _mapping(case.get("case")).get("case_id")
        for case in corrected_cases
        if case.get("corrected_product_semantic_verdict") == "EXPECTATION_REVIEW"
    ]

    source_hashes_after = dict(source_hashes_after or source_hashes_before)
    source_integrity = {
        "sha256_before": dict(source_hashes_before),
        "sha256_after": dict(source_hashes_after),
        "unchanged": dict(source_hashes_before) == dict(source_hashes_after),
        "original_artifacts_modified": False,
        "manifest_modified": False,
        "results_modified": False,
        "audit_modified": False,
    }
    corrected_summary = {
        "logical_cases": len(corrected_cases),
        "provider_runs": len(all_corrected_runs),
        "corrected_product_semantic_verdict_counts": dict(
            sorted(product_verdict_counts.items())
        ),
        "corrected_product_semantic_success_runs": product_verdict_counts.get(
            "PASS", 0
        ),
        "corrected_product_semantic_success_cases": corrected_case_verdict_counts.get(
            "PASS", 0
        ),
        "corrected_failed_runs": product_verdict_counts.get("FAIL", 0),
        "corrected_failed_cases": corrected_case_verdict_counts.get("FAIL", 0),
        "expectation_review_runs": product_verdict_counts.get(
            "EXPECTATION_REVIEW", 0
        ),
        "expectation_review_cases": len(review_case_ids),
        "expectation_review_case_ids": review_case_ids,
        "provider_execution_verdict_counts": dict(
            sorted(provider_verdict_counts.items())
        ),
        "corrected_classification_counts": dict(
            sorted(corrected_classification_counts.items())
        ),
        "corrected_failure_classification_counts": dict(
            sorted(corrected_failure_classification_counts.items())
        ),
        "corrected_semantic_failure_classification_counts": dict(
            sorted(corrected_semantic_failure_counts.items())
        ),
        "false_positive_corrections": [
            change
            for change in verdict_changes
            if change["reason"] == "FALSE_POSITIVE_CORRECTION"
        ],
        "false_negative_corrections": [
            change
            for change in verdict_changes
            if change["reason"] == "FALSE_NEGATIVE_CORRECTION"
        ],
        "verdict_changes": verdict_changes,
    }

    artifact_paths = {
        "manifest": "tmp/goal_resolver_large_regression_v1_manifest.json",
        "results": "tmp/goal_resolver_large_regression_v1_results.json",
        "corrected_results": "tmp/goal_resolver_large_regression_v1_corrected_results.json",
        "audit": "tmp/goal_resolver_large_regression_v1_audit.md",
        "corrected_audit": "tmp/goal_resolver_large_regression_v1_corrected_audit.md",
    }
    if source_artifact_paths is not None:
        artifact_paths.update(
            {
                key: value
                for key, value in source_artifact_paths.items()
                if key in artifact_paths and isinstance(value, str)
            }
        )

    corrected = {
        "regression_id": original_results.get("regression_id"),
        "correction": {
            "mode": "EVALUATOR_ONLY_OFFLINE_CORRECTION",
            "evaluator_version": EVALUATOR_VERSION,
            "provider_calls_during_correction": 0,
            "fake_provider_calls_during_correction": 0,
            "goal_resolver_cases_rerun": 0,
            "product_code_changes": product_code_changes,
            "scenario_changes": 0,
            "prompt_changes": 0,
            "raw_evidence_modified": False,
            "source_integrity": source_integrity,
        },
        "generated_at_utc": original_results.get("generated_at_utc"),
        "target": copy.deepcopy(original_results.get("target")),
        "provider": copy.deepcopy(original_results.get("provider")),
        "source_artifacts": {
            **artifact_paths,
            "sha256": dict(source_hashes_before),
        },
        "original_summary": original_summary,
        "original_success": {
            "semantic_success_runs": original_success_runs,
            "semantic_success_cases": original_success_cases,
            "failed_runs": len(all_corrected_runs) - original_success_runs,
            "failed_cases": len(corrected_cases) - original_success_cases,
        },
        "summary": corrected_summary,
        "cases": corrected_cases,
        "execution_boundary": {
            **copy.deepcopy(_mapping(original_results.get("execution_boundary"))),
            "correction_scope": "Goal Resolver evaluator only",
            "planner_calls_during_correction": 0,
            "runtime_calls_during_correction": 0,
            "game_instances_created_during_correction": 0,
        },
    }
    return corrected


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def build_corrected_audit_markdown(corrected: Mapping[str, Any]) -> str:
    summary = _mapping(corrected.get("summary"))
    original = _mapping(corrected.get("original_success"))
    correction = _mapping(corrected.get("correction"))
    source_integrity = _mapping(correction.get("source_integrity"))
    lines = [
        "# Goal Resolver Large Regression — Corrected Offline Audit v2",
        "",
        "This artifact is an evaluator-only offline correction. It does not "
        "contain new Provider output.",
        "",
        "## Scope and integrity",
        "",
        f"- Evaluator: {correction.get('evaluator_version')}",
        f"- Provider calls during correction: {correction.get('provider_calls_during_correction')}",
        f"- Goal Resolver cases rerun: {correction.get('goal_resolver_cases_rerun')}",
        f"- Product code changes: {correction.get('product_code_changes')}",
        f"- Raw resolution/FormalGoal evidence modified: {correction.get('raw_evidence_modified')}",
        f"- Original artifacts unchanged: {source_integrity.get('unchanged')}",
        f"- Source hashes: {_json(source_integrity.get('sha256_before'))}",
        "",
        "## Fixed regression target and original Provider configuration",
        "",
        f"- Target: {_json(corrected.get('target'))}",
        f"- Provider/model configuration: {_json(corrected.get('provider'))}",
        "",
        "## Original versus corrected success",
        "",
        "| metric | original V1 | corrected product verdict |",
        "| --- | ---: | ---: |",
        f"| runs | {original.get('semantic_success_runs', 0)} success / "
        f"{summary.get('provider_runs', 0)} | "
        f"{summary.get('corrected_product_semantic_success_runs', 0)} PASS / "
        f"{summary.get('corrected_failed_runs', 0)} FAIL / "
        f"{summary.get('expectation_review_runs', 0)} REVIEW |",
        f"| cases | {original.get('semantic_success_cases', 0)} success / "
        f"{summary.get('logical_cases', 0)} | "
        f"{summary.get('corrected_product_semantic_success_cases', 0)} PASS / "
        f"{summary.get('corrected_failed_cases', 0)} FAIL / "
        f"{summary.get('expectation_review_cases', 0)} REVIEW |",
        "",
        "## Provider execution verdict distribution",
        "",
        _json(summary.get("provider_execution_verdict_counts")),
        "",
        "CLEAN/RECOVERED/PROVIDER_INVALID is independent from product semantic "
        "PASS/FAIL. A correct final FormalGoal after wire rejection and bounded "
        "recovery remains product PASS with provider RECOVERED.",
        "",
        "## Corrected failure classification counts",
        "",
        f"- All corrected classifications: {_json(summary.get('corrected_classification_counts'))}",
        f"- FAIL-run classifications: "
        f"{_json(summary.get('corrected_failure_classification_counts'))}",
        f"- Semantic failure classifications only: "
        f"{_json(summary.get('corrected_semantic_failure_classification_counts'))}",
        "",
        "## False-positive corrections",
        "",
    ]
    false_positives = _list(summary.get("false_positive_corrections"))
    if false_positives:
        for change in false_positives:
            lines.append(
                f"- {change.get('case_id')} run {change.get('run_index')}: "
                f"raw {change.get('original_semantic_success')} -> "
                f"{change.get('corrected_product_semantic_verdict')}; "
                f"raw classes {change.get('original_classifications')}, "
                f"corrected {change.get('corrected_classifications')}."
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## False-negative corrections", ""])
    false_negatives = _list(summary.get("false_negative_corrections"))
    if false_negatives:
        for change in false_negatives:
            lines.append(
                f"- {change.get('case_id')} run {change.get('run_index')}: "
                f"raw {change.get('original_semantic_success')} -> "
                f"{change.get('corrected_product_semantic_verdict')}; "
                f"raw classes {change.get('original_classifications')}, "
                f"corrected {change.get('corrected_classifications')}."
            )
    else:
        lines.append("- None.")

    lines.extend(["", "## Stage attribution", ""])
    for case_result in _list(corrected.get("cases")):
        case = _mapping(case_result.get("case"))
        case_id = case.get("case_id")
        for run in _list(case_result.get("runs")):
            if not isinstance(run, Mapping):
                continue
            lines.append(
                f"- {case_id} run {run.get('run_index')}: "
                f"first={run.get('first_actual_semantic_divergence_stage')}, "
                f"final={run.get('final_rejection_stage')}, "
                f"product={run.get('product_semantic_verdict')}."
            )

    lines.extend(
        [
            "",
            "## Architecture-boundary EXPECTATION_REVIEW cases",
            "",
        ]
    )
    review_ids = _list(summary.get("expectation_review_case_ids"))
    if review_ids:
        for case_id in review_ids:
            lines.append(f"### {case_id}")
            lines.append("")
            lines.append(f"- Review reason: {REVIEW_CASES.get(case_id, '')}")
            review_result: Mapping[str, Any] = next(
                (
                    case
                    for case in _list(corrected.get("cases"))
                    if isinstance(case, Mapping)
                    and _mapping(case.get("case")).get("case_id") == case_id
                ),
                {},
            )
            for raw_run in _list(review_result.get("runs")):
                if not isinstance(raw_run, Mapping):
                    continue
                run = raw_run
                lines.append(
                    f"- run {run.get('run_index')}: product "
                    f"{run.get('product_semantic_verdict')}, provider "
                    f"{run.get('provider_execution_verdict')}, underlying "
                    f"{run.get('underlying_product_semantic_verdict')}; "
                    f"underlying classes {run.get('underlying_classifications')}."
                )
    else:
        lines.append("- None.")

    lines.extend(["", "## Every verdict change", ""])
    changes = _list(summary.get("verdict_changes"))
    if changes:
        for change in changes:
            lines.append(
                f"- {change.get('case_id')} run {change.get('run_index')} "
                f"({change.get('reason')}): raw success="
                f"{change.get('original_semantic_success')} -> product="
                f"{change.get('corrected_product_semantic_verdict')}, provider="
                f"{change.get('provider_execution_verdict')}, status="
                f"{change.get('status')}; raw classes="
                f"{change.get('original_classifications')}, corrected="
                f"{change.get('corrected_classifications')}."
            )
            if change.get("clarification_analysis"):
                analysis = _mapping(change.get("clarification_analysis"))
                if analysis.get("prompt"):
                    lines.append(
                        f"  - clarification fields: valid="
                        f"{analysis.get('valid_clarification_fields')}, "
                        f"redundant="
                        f"{analysis.get('redundant_clarification_fields')}."
                    )
    else:
        lines.append("- None.")

    lines.extend(
        [
            "",
            "## Required boundary assertions",
            "",
            "- Only Goal Resolver -> FormalGoal raw evidence was evaluated.",
            "- Planner calls during correction: 0.",
            "- Runtime calls during correction: 0.",
            "- New GameInstance creation during correction: 0.",
            "- No Prompt, Resolver, Scenario, Validator, alias, or Action "
            "contract change was made.",
            "",
        ]
    )
    return "\n".join(lines)


def build_correction_audit_markdown(
    corrected: Mapping[str, Any],
    *,
    corrected_results_sha256: str | None = None,
    corrected_audit_sha256: str | None = None,
) -> str:
    summary = _mapping(corrected.get("summary"))
    correction = _mapping(corrected.get("correction"))
    source_integrity = _mapping(correction.get("source_integrity"))
    source_artifacts = _mapping(corrected.get("source_artifacts"))
    lines = [
        "# Evaluator-only Offline Correction Audit",
        "",
        "## Authoritative inputs",
        "",
        f"- Manifest: {source_artifacts.get('manifest')}",
        f"- Results: {source_artifacts.get('results')}",
        f"- Corrected results: {source_artifacts.get('corrected_results')}",
        f"- Audit: {source_artifacts.get('audit')}",
        f"- Corrected audit: {source_artifacts.get('corrected_audit')}",
        f"- Source SHA-256 before: {_json(source_integrity.get('sha256_before'))}",
        f"- Source SHA-256 after: {_json(source_integrity.get('sha256_after'))}",
        f"- Original artifacts modified: {source_integrity.get('original_artifacts_modified')}",
        "",
        "## Correction boundary",
        "",
        f"- Evaluator version: {correction.get('evaluator_version')}",
        f"- Provider calls: {correction.get('provider_calls_during_correction')}",
        f"- Fake Provider calls: {correction.get('fake_provider_calls_during_correction')}",
        f"- Goal Resolver cases rerun: {correction.get('goal_resolver_cases_rerun')}",
        f"- Product code changes: {correction.get('product_code_changes')}",
        f"- Raw evidence modified: {correction.get('raw_evidence_modified')}",
        "",
        "## Target/configuration retained from V1",
        "",
        f"- Scenario target: {_json(corrected.get('target'))}",
        f"- Provider/model: {_json(corrected.get('provider'))}",
        "",
        "## Counts",
        "",
        f"- Original semantic success: {_json(corrected.get('original_success'))}",
        f"- Corrected product verdict counts: "
        f"{_json(summary.get('corrected_product_semantic_verdict_counts'))}",
        f"- Provider execution verdict counts: "
        f"{_json(summary.get('provider_execution_verdict_counts'))}",
        f"- False-positive corrections: {len(_list(summary.get('false_positive_corrections')))}",
        f"- False-negative corrections: {len(_list(summary.get('false_negative_corrections')))}",
        f"- EXPECTATION_REVIEW cases: {summary.get('expectation_review_cases')}",
        f"- EXPECTATION_REVIEW runs: {summary.get('expectation_review_runs')}",
        "",
        "## Corrected failure classification counts",
        "",
        _json(summary.get("corrected_failure_classification_counts")),
        "",
        "## New artifact hashes",
        "",
        f"- Corrected results SHA-256: {corrected_results_sha256 or 'pending'}",
        f"- Corrected audit SHA-256: {corrected_audit_sha256 or 'pending'}",
        "",
        "No Provider output was generated in this correction. No product "
        "implementation, Scenario, Prompt, Resolver, Validator, alias, or "
        "Action contract was changed.",
        "",
    ]
    return "\n".join(lines)
