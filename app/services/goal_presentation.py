# ruff: noqa: RUF001
"""Deterministic, player-safe presentation for resolved and terminal Goal results."""

from __future__ import annotations

from collections.abc import Mapping

from app.agent.generic import GenericGoalResolution
from app.domain.formal_goal import (
    FormalGoalActionCompletedRequirementV1,
    FormalGoalContract,
)
from app.domain.scenario_v2 import (
    ActionBehavior,
    ObjectiveRequirementKind,
    ScenarioDefinitionV2,
)

SYSTEM_FAILURE_TEXT = "目标解析暂时失败，请重新解析。"


def present_resolved_goal(
    contract: FormalGoalContract,
    definition: ScenarioDefinitionV2,
) -> str:
    """Project a frozen contract using public Scenario display metadata only."""

    objective_names = {item.key: item.name for item in definition.objectives}
    if contract.predefined_objectives:
        names = [objective_names[item.objective_key] for item in contract.predefined_objectives]
        return f"已解析目标：{'；'.join(names)}。"

    fragments: list[str] = []
    for item in contract.completion_requirements:
        requirement = item.requirement
        if isinstance(requirement, FormalGoalActionCompletedRequirementV1):
            fragments.append(_present_operation(requirement, definition))
        elif requirement.kind == ObjectiveRequirementKind.FACT:
            assert requirement.node_key is not None and requirement.fact_key is not None
            node = next(item for item in definition.world.nodes if item.key == requirement.node_key)
            fact = next(item for item in node.facts if item.key == requirement.fact_key)
            values = "或".join(_value_text(value) for value in requirement.accepted_values)
            fragments.append(f"使{node.name}的{fact.name}达到{values}")
        elif requirement.kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST:
            assert requirement.region_key is not None
            assert requirement.resource_key is not None and requirement.minimum is not None
            fragments.append(
                f"使{_public_name(definition, requirement.region_key, 'REGION')}的"
                f"{_public_name(definition, requirement.resource_key, 'RESOURCE')}不少于"
                f"{requirement.minimum}个"
            )
        else:
            assert requirement.derived_key is not None
            derived = definition.derived_state_definitions[requirement.derived_key]
            values = "或".join(_value_text(value) for value in requirement.accepted_values)
            fragments.append(f"使{derived.name}达到{values}")
    return f"已解析目标：{'；'.join(fragments)}。"


def present_failed_goal(
    resolution: GenericGoalResolution,
    raw_goal: str,
) -> str:
    """Render typed terminal evidence without interpreting Goal keywords."""

    observation = resolution.provider_observation or {}
    code = str(observation.get("rejection_code") or resolution.source)
    family = str(observation.get("frozen_family") or "")
    roles = _explicit_roles(observation)
    allowed = observation.get("allowed_clarification_fields")
    allowed_fields = tuple(str(item) for item in allowed) if isinstance(allowed, list) else ()

    if code == "FAMILY_AMBIGUOUS" or family == "AMBIGUOUS":
        return "无法确定你希望达成一个状态，还是执行一个具体操作，请更明确地描述目标。"
    if code in {"ACTION_NO_MATCH", "ACTION_UNRESOLVED"}:
        return "当前场景中没有找到与你描述相符的可执行操作，请调整目标的表达方式后重新解析。"
    if code in {
        "GOAL_UNREPRESENTABLE",
        "OPERATION_LOCK_UNREPRESENTABLE",
        "FORMAL_GOAL_GROUNDED_OPERATION_REQUIRED",
        "CANONICAL_IDENTITY_CONFLICT",
    }:
        if family == "STATE":
            return "当前场景无法把这个要求表示为可验证的目标状态，请更具体地描述希望达到的结果。"
        return "当前场景无法将这个操作转换为可执行目标，请调整目标中的对象或要求后重新解析。"
    if code in {"DERIVED_STATE_UNSUPPORTED", "DERIVED_STATE_UNAVAILABLE"}:
        return "当前场景无法将这个目标对应到已定义的状态，请更具体地描述希望恢复的对象或能力。"
    if code.startswith("FORMAL_GOAL_") and family == "STATE":
        return "当前场景无法将这个要求转换为可验证的目标状态，请调整目标描述后重新解析。"

    field = allowed_fields[0] if allowed_fields else _first_unresolved_role(roles)
    slot = roles.get(field, {}) if field else {}
    surface = _trusted_surface(slot.get("surface"), raw_goal)
    ref_type = str(slot.get("ref_type") or slot.get("expected_type") or "")
    if code in {"UNKNOWN_PUBLIC_RESOURCE", "PUBLIC_RESOURCE_NOT_FOUND"}:
        return _unknown_identity_text(surface, "资源")
    if code in {"UNKNOWN_PUBLIC_LOCATION", "PUBLIC_LOCATION_NOT_FOUND"}:
        return _unknown_identity_text(surface, "地点")
    if code in {"EXPLICIT_ACTOR_ACTION_CONFLICT", "ACTOR_ACTION_INCOMPATIBLE"}:
        if surface:
            return f"你指定的「{surface}」无法执行这个操作，请调整执行者或重新描述目标。"
        return "你指定的执行者无法执行这个操作，请调整执行者或重新描述目标。"
    if code in {"PUBLIC_RELATION_STATIC_CONFLICT", "SOURCE_TARGET_RELATION_CONFLICT"}:
        return "你指定的起点和目标关系不符合该操作在当前场景中的要求，请调整后重新解析。"
    if code in {"EXPLICIT_CONSTRAINT_UNRESOLVED", "PUBLIC_REFERENCE_AMBIGUOUS"} or field:
        if field == "amount":
            return "未能确定你希望达到的资源数量，请补充明确数量后重新解析。"
        label = _field_label(field, ref_type)
        if surface:
            return f"未能确定你说的「{surface}」具体对应哪个{label}，请使用更明确的名称后重新解析。"
        return f"未能确定你描述的{label}具体对应哪个对象，请使用更明确的名称后重新解析。"
    if family == "STATE":
        return "未能确定你希望该对象达到什么具体状态，请更明确地描述期望结果。"
    if resolution.status == "UNSUPPORTED":
        return "当前场景中没有找到与你描述相符的可执行目标，请调整目标的表达方式后重新解析。"
    return "目标描述还不够明确，请补充必要信息后重新解析。"


def _present_operation(
    requirement: FormalGoalActionCompletedRequirementV1,
    definition: ScenarioDefinitionV2,
) -> str:
    action = next(item for item in definition.actions if item.key == requirement.action_key)
    bindings = {item.role: item.value for item in requirement.binding_constraints}
    parameters: Mapping[str, object] = requirement.parameter_constraints or {}
    if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
        source = bindings.get("source_region") or bindings.get("source")
        target = bindings.get("destination_region") or bindings.get("target_region")
        resource = parameters.get("resource_key")
        amount = parameters.get("amount")
        if isinstance(source, str) and isinstance(target, str) and isinstance(resource, str):
            amount_text = f"{amount}个" if isinstance(amount, int) else ""
            return (
                f"从{_public_name(definition, source, 'REGION')}向"
                f"{_public_name(definition, target, 'REGION')}运输"
                f"{amount_text}{_public_name(definition, resource, 'RESOURCE')}"
            )
    actor = (
        _public_name(definition, requirement.actor_key, "ACTOR")
        if requirement.actor_key
        else None
    )
    target = (
        _public_name(definition, requirement.target_key, "NODE")
        if requirement.target_key
        else None
    )
    prefix = f"由{actor}" if actor else ""
    suffix = f"目标为{target}" if target else ""
    return f"{prefix}执行{action.name}{suffix}"


def _public_name(definition: ScenarioDefinitionV2, key: str, ref_type: str) -> str:
    if ref_type in {"NODE", "REGION"}:
        match = next((item.name for item in definition.world.nodes if item.key == key), None)
    elif ref_type == "RESOURCE":
        match = next((item.name for item in definition.world.resources if item.key == key), None)
    elif ref_type == "ACTOR":
        match = next(
            (item.name for item in definition.actors.actor_profiles if item.key == key),
            None,
        )
    else:
        match = None
    if match:
        return match
    public_term = next(
        (
            item.term
            for item in definition.public_references
            if item.ref_key == key and item.ref_type.value == ref_type
        ),
        None,
    )
    return public_term or "场景中的指定对象"


def _value_text(value: object) -> str:
    if value is True:
        return "已达成"
    if value is False:
        return "未达成"
    return str(value)


def _explicit_roles(observation: object) -> dict[str, dict[str, object]]:
    if isinstance(observation, dict):
        direct = observation.get("explicit_role_evidence")
        if isinstance(direct, dict):
            return {
                str(key): value
                for key, value in direct.items()
                if isinstance(value, dict)
            }
        for value in observation.values():
            nested = _explicit_roles(value)
            if nested:
                return nested
    elif isinstance(observation, list):
        for value in observation:
            nested = _explicit_roles(value)
            if nested:
                return nested
    return {}


def _first_unresolved_role(roles: dict[str, dict[str, object]]) -> str | None:
    return next(
        (key for key, value in roles.items() if value.get("status") == "UNRESOLVED"),
        None,
    )


def _trusted_surface(value: object, raw_goal: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized_surface = " ".join(value.casefold().replace("_", " ").split())
    normalized_goal = " ".join(raw_goal.casefold().replace("_", " ").split())
    return value.strip() if normalized_surface and normalized_surface in normalized_goal else None


def _field_label(field: str | None, ref_type: str) -> str:
    labels = {
        "actor": "执行者",
        "source": "起点",
        "source_region": "起点",
        "target": "目标对象",
        "destination_region": "目标地点",
        "resource": "资源",
        "resource_key": "资源",
    }
    if field in labels:
        return labels[field]
    return {"RESOURCE": "资源", "REGION": "地点", "NODE": "对象", "ACTOR": "执行者"}.get(
        ref_type, "对象"
    )


def _unknown_identity_text(surface: str | None, kind: str) -> str:
    if surface:
        return f"「{surface}」无法对应到当前场景中的{kind}，请使用场景中存在的{kind}名称。"
    return f"你描述的{kind}无法对应到当前场景中的公开对象，请使用场景中存在的{kind}名称。"


__all__ = [
    "SYSTEM_FAILURE_TEXT",
    "present_failed_goal",
    "present_resolved_goal",
]
