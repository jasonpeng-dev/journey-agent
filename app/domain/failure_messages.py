"""Author-facing messages derived from canonical failure evidence.

The formatter never reads Scenario-specific keys or authored recovery text.
Callers may provide a display-name map built from the current Scenario
definition; stable keys remain the deterministic fallback when a name is not
available.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.failures import (
    FactEvidence,
    FailureEvent,
    FailureKind,
    GenericEvidence,
    ResourceEvidence,
    TransportEvidence,
)


def format_failure_message(
    event: FailureEvent,
    *,
    display_names: Mapping[str, str] | None = None,
) -> str:
    """Render one canonical event with safe, generic author-facing wording."""

    names = display_names or {}
    evidence = event.evidence
    if event.kind == FailureKind.RESOURCE_INSUFFICIENT and isinstance(evidence, ResourceEvidence):
        resource = _name(names, evidence.resource_key, "资源")
        required = _value(evidence.required)
        available = _value(evidence.available)
        return f"{resource}数量不足, 需要 {required}, 当前已知可用 {available}"
    if event.kind == FailureKind.PRECONDITION_UNMET and isinstance(evidence, FactEvidence):
        fact = _fact_name(names, evidence.node_key, evidence.fact_key)
        required = _value(evidence.required)
        if not required and evidence.accepted_values:
            required = "/".join(_value(item) for item in evidence.accepted_values)
        return f"{fact}未满足条件, 需要 {required}"
    if event.kind == FailureKind.TRAVEL_BLOCKED and isinstance(evidence, TransportEvidence):
        transport = _name(names, evidence.transport_key, "运输通道")
        source = _name(names, evidence.source_region, evidence.source_region or "起点")
        target = _name(names, evidence.target_region, evidence.target_region or "终点")
        return f"无法通过{transport}从{source}前往{target}"
    if event.kind == FailureKind.KNOWLEDGE_UNKNOWN:
        return "执行所需的信息尚未获知"
    if event.kind == FailureKind.ALREADY_COMPLETED:
        return "该操作已经完成"
    if event.kind == FailureKind.LOCALITY_INVALID:
        return "当前行动位置不满足要求"
    if event.kind == FailureKind.ACTOR_UNAVAILABLE:
        return "当前参与者不可用"
    if event.kind == FailureKind.TARGET_INVALID:
        return "目标不满足行动要求"
    if event.kind == FailureKind.AUTHORITY_BLOCKED:
        return "当前参与者没有执行该行动的权限"
    if event.kind == FailureKind.PARAMETER_INVALID:
        return "行动参数不符合要求"
    if isinstance(evidence, GenericEvidence):
        return f"{event.kind.value}: 条件未满足"
    return event.message or f"{event.kind.value}: 条件未满足"


def _fact_name(names: Mapping[str, str], node_key: str | None, fact_key: str | None) -> str:
    if node_key and fact_key:
        combined = f"{node_key}.{fact_key}"
        fallback = f"{_name(names, node_key, node_key)} · {_name(names, fact_key, fact_key)}"
        return names.get(combined, fallback)
    return _name(names, fact_key, "事实条件")


def _name(names: Mapping[str, str], key: str | None, fallback: str) -> str:
    if key is None:
        return fallback
    return names.get(key, key)


def _value(value: Any) -> str:
    if value is None:
        return "当前值"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


__all__ = ["format_failure_message"]
