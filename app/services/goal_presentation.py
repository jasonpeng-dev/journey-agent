# ruff: noqa: RUF001
"""Deterministic, player-safe presentation for resolved and terminal Goal results."""

from __future__ import annotations

from collections.abc import Mapping

from app.agent.generic import GenericGoalResolution
from app.domain.action_invocation import canonical_action_invocation_contract
from app.domain.formal_goal import (
    FormalGoalActionCompletedRequirementV1,
    FormalGoalContract,
)
from app.domain.scenario_v2 import (
    ActionBehavior,
    ObjectiveRequirementKind,
    ScenarioDefinitionV2,
    transport_resource_entries,
)

SYSTEM_FAILURE_TEXT = "目标解析暂时失败，请重新解析。"


def present_resolved_goal(
    contract: FormalGoalContract,
    definition: ScenarioDefinitionV2,
    *,
    provider_observation: Mapping[str, object] | None = None,
) -> str:
    """Project a frozen contract using public Scenario display metadata only.

    ``provider_observation`` is optional provenance from the resolver.  It is
    consulted only to confirm whether a role was explicitly grounded; the
    canonical FormalGoal remains the source of every displayed value.
    """

    objective_names = {item.key: item.name for item in definition.objectives}
    if contract.predefined_objectives:
        names = [objective_names[item.objective_key] for item in contract.predefined_objectives]
        return f"已解析目标：{'；'.join(names)}。"

    fragments: list[str] = []
    for item in contract.completion_requirements:
        requirement = item.requirement
        if isinstance(requirement, FormalGoalActionCompletedRequirementV1):
            fragments.append(
                _present_operation(
                    requirement,
                    definition,
                    explicit_roles=_explicit_roles(provider_observation),
                )
            )
        elif requirement.kind == ObjectiveRequirementKind.FACT:
            assert requirement.node_key is not None and requirement.fact_key is not None
            node = next(item for item in definition.world.nodes if item.key == requirement.node_key)
            fact = next(item for item in node.facts if item.key == requirement.fact_key)
            values = "或".join(
                _value_text(value, enum=fact.value_type.value == "ENUM")
                for value in requirement.accepted_values
            )
            fragments.append(f"{node.name}的{fact.name}；期望：{values}")
        elif requirement.kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST:
            assert requirement.region_key is not None
            assert requirement.resource_key is not None and requirement.minimum is not None
            fragments.append(
                f"{_public_name(definition, requirement.region_key, 'REGION')}的"
                f"{_public_name(definition, requirement.resource_key, 'RESOURCE')}；"
                f"期望：不少于{requirement.minimum}个"
            )
        else:
            assert requirement.derived_key is not None
            derived = definition.derived_state_definitions[requirement.derived_key]
            values = "或".join(
                _value_text(value, enum=derived.value_type.value == "ENUM")
                for value in requirement.accepted_values
            )
            fragments.append(f"{derived.name}；期望：{values}")
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
    raw_field = allowed_fields[0] if allowed_fields else _first_unresolved_role(roles)
    field = _semantic_role(raw_field)
    slot = roles.get(field, {}) if field else {}
    surface = _trusted_surface(slot.get("surface"), raw_goal)
    ref_type = str(slot.get("ref_type") or slot.get("expected_type") or "")

    if code == "FAMILY_AMBIGUOUS" or family == "AMBIGUOUS":
        return "无法确定你希望达成一个状态，还是执行一个具体操作，请更明确地描述目标。"
    if code == "ACTION_AMBIGUOUS":
        return "你的描述可能对应多个可执行操作，请进一步说明要执行哪一项操作。"
    if code == "ACTION_UNRESOLVED":
        return "尚未确定你希望执行哪一个操作，请更明确地说明操作后重新解析。"
    if code == "ACTION_NO_MATCH":
        return "当前场景中没有找到与你描述相符的可执行操作，请调整目标的表达方式后重新解析。"
    if code == "TARGET_AMBIGUOUS":
        return "你的目标可能对应多个场景对象，请说明具体是哪一个。"
    if code == "STATE_INTERPRETATION_AMBIGUOUS":
        return "无法确定你希望对象达到什么具体状态，请更明确地描述期望结果。"
    if code in {
        "STATE_INTERPRETATION_UNSUPPORTED",
        "STATE_REQUIREMENT_NOT_MINIMAL",
        "STATE_OPERATION_EQUIVALENCE_CONFLICT",
    }:
        return (
            "已理解你希望达到的状态，但该状态无法表示为当前场景的合法可验证目标，"
            "请调整目标描述后重新解析。"
        )
    if code == "NO_PUBLIC_GROUNDING":
        if resolution.status == "NEEDS_CLARIFICATION":
            return "无法确定你描述的目标，请提供更明确的名称后重新解析。"
        return "当前场景不支持将该要求作为公开目标，请调整目标的表达方式后重新解析。"
    if code == "TARGET_UNRESOLVED":
        return _unresolved_role_text(None, "目标对象")
    if code == "ACTOR_UNRESOLVED":
        return _unresolved_role_text(None, "执行者")
    if code == "SOURCE_UNRESOLVED":
        return _unresolved_role_text(None, "起点/来源")
    if code == "RESOURCE_UNRESOLVED":
        return _unresolved_role_text(None, "资源")
    if code == "AMOUNT_UNRESOLVED":
        return "请补充明确数量后重新解析。"
    if (
        code in {"PUBLIC_REFERENCE_AMBIGUOUS", "DETERMINISTIC_PUBLIC_REFERENCE_AMBIGUOUS"}
        and not field
    ):
        return "你的描述可能对应多个场景对象，请说明具体是哪一个。"
    publicity_kind = _publicity_kind(code)
    if publicity_kind is not None:
        return _unknown_identity_text(surface, publicity_kind)
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
    if code.startswith("FORMAL_GOAL_"):
        return "当前要求不受此场景或操作支持，请调整目标中的对象或要求后重新解析。"

    if code in {"UNKNOWN_PUBLIC_RESOURCE", "PUBLIC_RESOURCE_NOT_FOUND"}:
        return _unknown_identity_text(surface, "资源")
    if code in {"UNKNOWN_PUBLIC_LOCATION", "PUBLIC_LOCATION_NOT_FOUND"}:
        return _unknown_identity_text(surface, "地点")
    if code in {"EXPLICIT_ACTOR_ACTION_CONFLICT", "ACTOR_ACTION_INCOMPATIBLE"}:
        actor_surface = _trusted_surface(
            roles.get("actor", {}).get("surface"),
            raw_goal,
        )
        if actor_surface:
            return f"你指定的「{actor_surface}」无法执行这个操作，请调整执行者或重新描述目标。"
        return "你指定的执行者无法执行这个操作，请调整执行者或重新描述目标。"
    if code in {"PUBLIC_RELATION_STATIC_CONFLICT", "SOURCE_TARGET_RELATION_CONFLICT"}:
        source_surface = _trusted_surface(
            roles.get("source", {}).get("surface"),
            raw_goal,
        )
        target_surface = _trusted_surface(
            roles.get("target", {}).get("surface"),
            raw_goal,
        )
        if source_surface and target_surface:
            return (
                f"你指定的「{source_surface}」无法对「{target_surface}」执行此操作，"
                "请调整起点或目标后重新解析。"
            )
        return "你指定的起点和目标关系不符合该操作在当前场景中的要求，请调整后重新解析。"
    if code in {"EXPLICIT_CONSTRAINT_UNRESOLVED", "PUBLIC_REFERENCE_AMBIGUOUS"} or field:
        if field == "amount":
            return "请补充明确数量后重新解析。"
        if field == "actor":
            return _unresolved_role_text(surface, "执行者")
        if field == "source":
            return _unresolved_role_text(surface, "起点/来源")
        if field == "resource":
            return _unresolved_role_text(surface, "资源")
        if field == "target":
            return _unresolved_role_text(surface, "目标对象")
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
    *,
    explicit_roles: Mapping[str, Mapping[str, object]] | None = None,
) -> str:
    action = next(item for item in definition.actions if item.key == requirement.action_key)
    bindings = {item.role: item.value for item in requirement.binding_constraints}
    parameters: Mapping[str, object] = requirement.parameter_constraints or {}
    action_contract = canonical_action_invocation_contract(action)
    if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
        transport = _present_transport_operation(
            requirement,
            definition,
            action_contract,
            bindings,
            parameters,
            explicit_roles,
        )
        if transport is not None:
            return transport
    actor = (
        _public_name(definition, requirement.actor_key, "ACTOR")
        if requirement.actor_key and _role_is_explicit(explicit_roles, "actor")
        else None
    )
    target = (
        _public_name(
            definition,
            requirement.target_key,
            _operation_target_reference_type(action_contract),
        )
        if requirement.target_key
        else None
    )
    fragments = [action.name]
    if target:
        fragments.append(f"目标：{target}")
    requirements: list[str] = []
    if actor:
        requirements.append(f"由{actor}执行")
    source = _operation_source(
        requirement,
        definition,
        action_contract,
        bindings,
        parameters,
        explicit_roles=explicit_roles,
    )
    if source is not None:
        requirements.append(f"起点为{source}")
    requirements.extend(
        _present_operation_parameters(
            definition,
            action_contract,
            parameters,
            skip_source=True,
            explicit_roles=explicit_roles,
        )
    )
    text = "；".join(fragments)
    if requirements:
        text += f"；要求：{'，'.join(requirements)}"
    return text


def _present_transport_operation(
    requirement: FormalGoalActionCompletedRequirementV1,
    definition: ScenarioDefinitionV2,
    action_contract: Mapping[str, object],
    bindings: Mapping[str, object],
    parameters: Mapping[str, object],
    explicit_roles: Mapping[str, Mapping[str, object]] | None,
) -> str | None:
    target = requirement.target_key
    target_text = _public_name(definition, target, "REGION") if target else None
    try:
        entries = transport_resource_entries(parameters)
    except (TypeError, ValueError):
        return None
    resource_text = "、".join(
        f"{amount}个{_public_name(definition, resource, 'RESOURCE')}"
        for resource, amount in entries
    )
    destination = f"向{target_text}" if target_text else "向目标地点"
    text = f"{destination}运输{resource_text}"
    source = _operation_source(
        requirement,
        definition,
        action_contract,
        bindings,
        parameters,
        explicit_roles=explicit_roles,
    )
    if source is not None:
        text = f"从{source}{text}"
    if requirement.actor_key and _role_is_explicit(explicit_roles, "actor"):
        actor = _public_name(definition, requirement.actor_key, "ACTOR")
        text = f"由{actor}{text}"
    return text


def _operation_source(
    requirement: FormalGoalActionCompletedRequirementV1,
    definition: ScenarioDefinitionV2,
    action_contract: Mapping[str, object],
    bindings: Mapping[str, object],
    parameters: Mapping[str, object],
    *,
    explicit_roles: Mapping[str, Mapping[str, object]] | None,
) -> str | None:
    relation = action_contract.get("relation_semantics")
    source_slot: str | None = None
    source_channel: str | None = None
    if isinstance(relation, Mapping):
        raw_slot = relation.get("source_slot_key")
        raw_channel = relation.get("source_storage_channel")
        if isinstance(raw_slot, str) and isinstance(raw_channel, str):
            source_slot = raw_slot
            source_channel = raw_channel
    if source_slot is None:
        for raw in action_contract.get("bindings", ()), action_contract.get("parameters", ()):
            if not isinstance(raw, (list, tuple)):
                continue
            for spec in raw:
                if isinstance(spec, Mapping) and spec.get("logical_role") == "source":
                    key = spec.get("slot_key")
                    channel = spec.get("storage_channel")
                    if isinstance(key, str) and isinstance(channel, str):
                        source_slot = key
                        source_channel = channel
                        break
            if source_slot is not None:
                break
    if source_slot is None or source_channel is None:
        return None
    if not _role_is_explicit(explicit_roles, "source"):
        return None
    source_key: object | None = (
        bindings.get(source_slot)
        if source_channel == "binding"
        else parameters.get(source_slot)
        if source_channel == "parameter"
        else None
    )
    if not isinstance(source_key, str):
        return None
    source_type = _operation_slot_reference_type(action_contract, source_slot, source_channel)
    return _public_name(definition, source_key, source_type) if source_type else None


def _operation_slot_reference_type(
    action_contract: Mapping[str, object],
    slot_key: str,
    storage_channel: str,
) -> str | None:
    group = action_contract.get("bindings" if storage_channel == "binding" else "parameters", ())
    if not isinstance(group, (list, tuple)):
        return None
    for spec in group:
        if isinstance(spec, Mapping) and spec.get("slot_key") == slot_key:
            value = spec.get("semantic_reference_type") or spec.get("expected_type")
            return value if isinstance(value, str) else None
    return None


def _role_is_explicit(
    explicit_roles: Mapping[str, Mapping[str, object]] | None,
    role: str,
) -> bool:
    if explicit_roles is None:
        return True
    evidence = explicit_roles.get(role)
    if not isinstance(evidence, Mapping):
        return True
    status = evidence.get("status")
    return status != "NOT_SPECIFIED"


def _present_operation_parameters(
    definition: ScenarioDefinitionV2,
    action_contract: Mapping[str, object],
    parameters: Mapping[str, object],
    *,
    skip_source: bool,
    explicit_roles: Mapping[str, Mapping[str, object]] | None = None,
) -> list[str]:
    result: list[str] = []
    specs = action_contract.get("parameters", ())
    if not isinstance(specs, (list, tuple)):
        return result
    for spec in specs:
        if not isinstance(spec, Mapping):
            continue
        key = spec.get("slot_key")
        if not isinstance(key, str) or key not in parameters:
            continue
        if skip_source and spec.get("logical_role") == "source":
            continue
        role = _parameter_semantic_role(spec)
        if role is not None and not _role_is_explicit(explicit_roles, role):
            continue
        value = parameters[key]
        if spec.get("semantic_reference_type") == "RESOURCE" and isinstance(value, str):
            value_text = _public_name(definition, value, "RESOURCE")
        elif spec.get("semantic_reference_type") in {"NODE", "REGION", "ACTOR"} and isinstance(
            value, str
        ):
            value_text = _public_name(
                definition,
                value,
                str(spec.get("semantic_reference_type")),
            )
        elif spec.get("expected_type") == "ENUM":
            value_text = _value_text(value, enum=True)
        else:
            value_text = _value_text(value)
        label = _parameter_label(spec, key)
        result.append(f"{label}为{value_text}")
    return result


def _public_name(definition: ScenarioDefinitionV2, key: str, ref_type: str) -> str:
    if ref_type in {"NODE", "REGION", "FACILITY"}:
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


def _operation_target_reference_type(action_contract: Mapping[str, object]) -> str:
    target = action_contract.get("target")
    if isinstance(target, Mapping):
        semantic_type = target.get("semantic_reference_type") or target.get("expected_type")
        if isinstance(semantic_type, str):
            return semantic_type
    return "NODE"


def _parameter_semantic_role(spec: Mapping[str, object]) -> str | None:
    logical_role = spec.get("logical_role")
    if isinstance(logical_role, str) and logical_role in {
        "actor",
        "source",
        "target",
        "resource",
        "amount",
    }:
        return logical_role
    semantic_type = spec.get("semantic_reference_type")
    if semantic_type == "RESOURCE":
        return "resource"
    if spec.get("scalar_value_type") == "INTEGER":
        return "amount"
    return None


def _parameter_label(spec: Mapping[str, object], key: str) -> str:
    name = spec.get("name")
    if isinstance(name, str) and name.strip():
        return name
    role_labels = {
        "actor": "执行者",
        "source": "起点/来源",
        "target": "目标对象",
        "resource": "资源",
        "amount": "数量",
    }
    role = _parameter_semantic_role(spec)
    return role_labels.get(role, "参数") if role is not None else "参数"


def _publicity_kind(code: str) -> str | None:
    """Map current public-projection rejection codes to player nouns."""

    if code in {
        "FORMAL_GOAL_DYNAMIC_RESOURCE_NOT_PUBLIC",
        "FORMAL_GOAL_UNKNOWN_RESOURCE",
    }:
        return "资源"
    if code in {
        "FORMAL_GOAL_DYNAMIC_REGION_NOT_PUBLIC",
        "FORMAL_GOAL_UNKNOWN_REGION",
        "FORMAL_GOAL_DYNAMIC_BINDING_NOT_PUBLIC",
        "FORMAL_GOAL_INVALID_REGION",
    }:
        return "地点"
    if code in {
        "FORMAL_GOAL_DYNAMIC_NODE_NOT_PUBLIC",
        "FORMAL_GOAL_DYNAMIC_ENTITY_NOT_PUBLIC",
        "FORMAL_GOAL_DYNAMIC_TARGET_NOT_PUBLIC",
        "FORMAL_GOAL_UNKNOWN_NODE",
        "FORMAL_GOAL_UNKNOWN_TARGET",
        "FORMAL_GOAL_TARGET_INVALID",
    }:
        return "目标对象"
    if code in {
        "FORMAL_GOAL_DYNAMIC_ACTOR_NOT_PUBLIC",
        "FORMAL_GOAL_UNKNOWN_ACTOR",
    }:
        return "执行者"
    return None


def _value_text(value: object, *, enum: bool = False) -> str:
    if value is True:
        return "满足"
    if value is False:
        return "不满足"
    if isinstance(value, str):
        labels = {
            "AVAILABLE": "可用",
            "UNAVAILABLE": "不可用",
            "ONLINE": "在线",
            "OFFLINE": "离线",
            "KNOWN": "已知",
            "HIDDEN": "隐藏",
            "PASSABLE": "可通行",
            "IMPASSABLE": "不可通行",
            "UNKNOWN": "未知",
            "CONNECTED": "已连接",
            "DISCONNECTED": "未连接",
            "SAFE": "安全",
            "FAST": "快速",
            "INFECTION": "感染",
        }
        return labels.get(value, "已指定" if enum else value)
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


def _semantic_role(field: str | None) -> str | None:
    if field is None:
        return None
    return {
        "actor": "actor",
        "actor_key": "actor",
        "source": "source",
        "source_key": "source",
        "source_region": "source",
        "origin": "source",
        "target": "target",
        "target_key": "target",
        "destination": "target",
        "destination_region": "target",
        "resource": "resource",
        "resource_key": "resource",
        "amount": "amount",
        "quantity": "amount",
    }.get(field, field)


def _unresolved_role_text(surface: str | None, label: str) -> str:
    if surface:
        return f"无法确定你指定的「{surface}」对应的{label}，请提供更明确名称后重新解析。"
    return f"无法确定你指定的{label}，请提供更明确名称后重新解析。"


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
