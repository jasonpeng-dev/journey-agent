"""Player-facing formatting for persisted action knowledge changes."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.api.schemas.phase_d import PublicKnowledgeChangeResponse
from app.domain.resources import resource_pool_initial_states
from app.domain.scenario_v2 import ScenarioDefinitionV2

_FACT_LABELS = {
    "operational": "运行状态",
    "power_supply": "供电状态",
    "power_generation_capable": "发电能力",
    "generation_capable": "发电能力",
    "emergency_power": "应急供电",
    "passable": "通行状态",
    "heavy_engineering_support": "重型工程支援",
    "heavy_engineering_support_ready": "重型工程支援状态",
    "repair_profile": "设施类型",
}

_FACT_VALUE_LABELS: dict[tuple[str | None, object], str] = {
    ("operational", True): "运行中",
    ("operational", False): "未运行",
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
    ("power_generation_capable", True): "具备",
    ("power_generation_capable", False): "不具备",
    ("generation_capable", True): "具备",
    ("generation_capable", False): "不具备",
}

_ENUM_VALUE_LABELS = {
    "VISIBLE": "已可见",
    "HIDDEN": "未知",
    "KNOWN": "已知",
    "UNKNOWN": "未知",
    "AVAILABLE": "可用",
    "UNAVAILABLE": "暂不可用",
    "PASSABLE": "可通行",
    "BLOCKED": "已阻断",
}

_RELATION_LABELS = {
    "supplies_power_to": "可供电",
    "contains": "包含目标",
    "supports": "提供系统支援",
    "reveals": "可发现目标",
    "unlocks": "可解锁目标",
    "enables": "支持目标行动",
}

_MACHINE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")


@dataclass(frozen=True, slots=True)
class _ResourceIdentity:
    resource_key: str
    region_key: str | None
    pool_key: str


class PlayerActionReportFormatter:
    """Resolve persisted action deltas into knowledge-safe player text."""

    def __init__(self, definition: ScenarioDefinitionV2) -> None:
        self.definition = definition
        self.resource_names = {item.key: item.name for item in definition.world.resources}
        self.node_names = {item.key: item.name for item in definition.world.nodes}
        self.resource_pools = tuple(resource_pool_initial_states(definition))

    def format_changes(self, payload: object) -> list[PublicKnowledgeChangeResponse]:
        if not isinstance(payload, list):
            return []

        changes: list[PublicKnowledgeChangeResponse] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            kind = raw.get("kind")
            key = raw.get("key")
            if not isinstance(kind, str) or not isinstance(key, str):
                continue
            name, value = self._format_change(kind, key, raw.get("name"), raw.get("value"))
            try:
                changes.append(
                    PublicKnowledgeChangeResponse.model_validate(
                        {"kind": kind, "key": key, "name": name, "value": value}
                    )
                )
            except ValueError:
                continue
        return changes

    def _format_change(
        self,
        kind: str,
        key: str,
        raw_name: object,
        raw_value: object,
    ) -> tuple[str, str | int | bool | None]:
        if kind == "RESOURCE_INVENTORY_REVEALED":
            return "资源库存信息", self._display_value(raw_value)
        if kind == "RESOURCE_SURVEY_COMPLETED":
            if raw_value is True:
                return "资源调查已完成", None
            if raw_value is False:
                return "资源调查未完成", None
            return "资源调查", self._display_value(raw_value)
        if kind == "RESOURCE_DISCOVERED":
            identity = self._parse_resource_identity(key)
            resource_name = (
                self.resource_names.get(identity.resource_key) if identity is not None else None
            ) or self._safe_name(raw_name, "已知资源")
            source_name = self._resource_source_name(identity)
            label = f"{source_name} · {resource_name}" if source_name else resource_name
            return label, self._quantity_value(raw_value)
        if kind == "FACT_REVEALED":
            fact_key = key.rsplit(".", maxsplit=1)[-1]
            return (
                _FACT_LABELS.get(fact_key, self._safe_name(raw_name, "已知状态")),
                self._display_value(raw_value, fact_key=fact_key),
            )
        if kind == "RELATION_REVEALED":
            relation_key = self._relation_type_key(key, raw_name)
            return _RELATION_LABELS.get(relation_key, "已知关系"), None
        if kind == "NODE_REVEALED":
            return self.node_names.get(key, self._safe_name(raw_name, "已知地点")), None
        return self._safe_name(raw_name, "已知信息"), self._display_value(raw_value)

    def _parse_resource_identity(self, key: str) -> _ResourceIdentity | None:
        parts = key.split("@")
        if len(parts) == 1:
            resource_key, region_key, pool_key = parts[0], None, "default"
        elif len(parts) == 2:
            resource_key, region_key = parts
            pool_key = "default"
        elif len(parts) == 3:
            resource_key, region_key, pool_key = parts
        else:
            return None
        if resource_key not in self.resource_names:
            return None
        return _ResourceIdentity(resource_key, region_key or None, pool_key)

    def _resource_source_name(self, identity: _ResourceIdentity | None) -> str | None:
        if identity is None:
            return None
        pool = next(
            (
                item
                for item in self.resource_pools
                if item.resource_key == identity.resource_key
                and item.pool_key == identity.pool_key
                and (item.region_key or None) == identity.region_key
            ),
            None,
        )
        if pool is not None and pool.facility_key:
            facility_name = self.node_names.get(pool.facility_key)
            if facility_name:
                return facility_name
        return self.node_names.get(identity.region_key or "")

    @staticmethod
    def _relation_type_key(key: str, raw_name: object) -> str:
        if isinstance(raw_name, str) and raw_name in _RELATION_LABELS:
            return raw_name
        parts = key.split("__")
        return parts[1] if len(parts) == 3 else ""

    @staticmethod
    def _safe_name(value: object, fallback: str) -> str:
        if isinstance(value, str) and value and not _MACHINE_KEY.fullmatch(value):
            return value
        return fallback

    @staticmethod
    def _quantity_value(value: object) -> str | int | bool | None:
        if isinstance(value, (bool, int)):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return PlayerActionReportFormatter._display_value(value)

    @staticmethod
    def _display_value(value: object, *, fact_key: str | None = None) -> str | int | bool | None:
        if (fact_key, value) in _FACT_VALUE_LABELS:
            return _FACT_VALUE_LABELS[(fact_key, value)]
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return _ENUM_VALUE_LABELS.get(
                value,
                PlayerActionReportFormatter._safe_name(value, "已知状态"),
            )
        return None


def format_player_knowledge_changes(
    payload: object,
    definition: ScenarioDefinitionV2,
) -> list[PublicKnowledgeChangeResponse]:
    """Format only the already-emitted action knowledge delta for players."""

    return PlayerActionReportFormatter(definition).format_changes(payload)


__all__ = ["PlayerActionReportFormatter", "format_player_knowledge_changes"]
