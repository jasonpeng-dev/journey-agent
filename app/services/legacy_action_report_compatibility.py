"""Compatibility for the pre-metadata action-report DTO.

The live action-report formatter is authored-metadata driven.  This adapter is
kept deliberately separate for persisted Linjiang payloads and old fixtures
that predate typed Fact value labels.  New Scenario definitions never enter
this path.
"""

from __future__ import annotations

from app.domain.scenario_v2 import ScenarioDefinitionV2

LEGACY_SCENARIO_KEY = "linjiang_infrastructure_recovery_v2_0"

_FACT_LABELS = {
    "operational": "设备状态",
    "power_supply": "供电状态",
    "emergency_power": "应急供电",
    "passable": "通行状态",
    "heavy_engineering_support": "重型工程支援",
    "heavy_engineering_support_ready": "重型工程支援状态",
}

_FACT_VALUE_LABELS: dict[tuple[str, object], str] = {
    ("operational", True): "正常",
    ("operational", False): "待修复",
    ("power_supply", True): "已供电",
    ("power_supply", False): "未供电",
    ("power_supply", "AVAILABLE"): "已供电",
    ("power_supply", "UNAVAILABLE"): "未供电",
    ("emergency_power", True): "已恢复",
    ("emergency_power", False): "未恢复",
    ("passable", True): "可通行",
    ("passable", False): "已阻断",
    ("heavy_engineering_support", True): "可用",
    ("heavy_engineering_support", False): "不可用",
    ("heavy_engineering_support", "AVAILABLE"): "可用",
    ("heavy_engineering_support", "UNAVAILABLE"): "不可用",
    ("heavy_engineering_support_ready", True): "已部署",
    ("heavy_engineering_support_ready", False): "未部署",
}


def uses_legacy_action_report(definition: ScenarioDefinitionV2) -> bool:
    """Return whether a definition is an old persisted-report compatibility case."""

    return definition.metadata.key == LEGACY_SCENARIO_KEY


def legacy_fact_change(
    fact_key: str,
    value: object,
) -> tuple[str, str | int | bool | None] | None:
    """Return the old player label, or ``None`` for a hidden legacy fact."""

    label = _FACT_LABELS.get(fact_key)
    if label is None:
        return None
    return label, _FACT_VALUE_LABELS.get((fact_key, value), _generic_legacy_value(value))


def _generic_legacy_value(value: object) -> str | int | bool | None:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return {
            "VISIBLE": "已可见",
            "HIDDEN": "未知",
            "KNOWN": "已知",
            "UNKNOWN": "未知",
            "AVAILABLE": "可用",
            "UNAVAILABLE": "暂不可用",
            "PASSABLE": "可通行",
            "BLOCKED": "已阻断",
        }.get(value, "已知状态")
    return None


__all__ = ["legacy_fact_change", "uses_legacy_action_report"]
