"""Player-facing formatting for persisted action knowledge changes."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.api.schemas.phase_d import PublicKnowledgeChangeResponse
from app.domain.resources import resource_pool_initial_states
from app.domain.scenario_v2 import ActionBehavior, ScenarioDefinitionV2
from app.engine.locality import LocalityEngineError, region_for_node

_FACT_LABELS = {
    "operational": "设备状态",
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

    def format_changes(
        self,
        payload: object,
        *,
        action_key: str | None = None,
        target_key: str | None = None,
    ) -> list[PublicKnowledgeChangeResponse]:
        if not isinstance(payload, list):
            return []

        raw_changes = [raw for raw in payload if isinstance(raw, dict)]
        batch_context = self._batch_facility_context(raw_changes, action_key, target_key)
        changes: list[PublicKnowledgeChangeResponse] = []
        summary_emitted = False
        for raw in raw_changes:
            kind = raw.get("kind")
            key = raw.get("key")
            if not isinstance(kind, str) or not isinstance(key, str):
                continue
            if batch_context is not None and self._is_batch_facility_fact(raw, batch_context[1]):
                if not summary_emitted:
                    changes.append(
                        self._batch_facility_summary(batch_context[0], len(batch_context[1]))
                    )
                    summary_emitted = True
                continue
            formatted = self._format_change(kind, key, raw.get("name"), raw.get("value"))
            if formatted is None:
                continue
            name, value = formatted
            try:
                changes.append(
                    PublicKnowledgeChangeResponse.model_validate(
                        {"kind": kind, "key": key, "name": name, "value": value}
                    )
                )
            except ValueError:
                continue
        return changes

    def _batch_facility_context(
        self,
        payload: list[dict[object, object]],
        action_key: str | None,
        target_key: str | None,
    ) -> tuple[str, set[str]] | None:
        """Return the Region/facilities for a semantic batch reveal action.

        The action behavior is the structured marker for the current
        ``REGION_FACILITY_KNOWLEDGE`` effect.  This deliberately does not
        inspect a Scenario-specific action key or infer a batch from the
        number of facts in the payload.
        """

        if action_key is None or target_key is None:
            return None
        action = next((item for item in self.definition.actions if item.key == action_key), None)
        if action is None or action.behavior != ActionBehavior.REPAIR_COMMUNICATIONS:
            return None

        target_region = self._safe_region_for_node(target_key)
        facility_node_type = self.definition.metadata.locality.facility_node_type_key
        facility_keys: set[str] = set()
        facility_regions: dict[str, str] = {}
        for raw in payload:
            if raw.get("kind") != "FACT_REVEALED":
                continue
            key = raw.get("key")
            if not isinstance(key, str) or "." not in key:
                continue
            node_key = key.rsplit(".", maxsplit=1)[0]
            node = self.definition.world.node(node_key)
            if node is None or node.node_type_key != facility_node_type:
                continue
            region_key = self._safe_region_for_node(node_key)
            if region_key is None or (target_region is not None and region_key != target_region):
                continue
            facility_keys.add(node_key)
            facility_regions[node_key] = region_key

        if not facility_keys:
            return None
        regions = set(facility_regions.values())
        if target_region is not None and target_region in regions:
            return target_region, facility_keys
        if len(regions) == 1:
            return next(iter(regions)), facility_keys
        return None

    @staticmethod
    def _is_batch_facility_fact(raw: dict[object, object], facility_keys: set[str]) -> bool:
        if raw.get("kind") != "FACT_REVEALED":
            return False
        key = raw.get("key")
        if not isinstance(key, str) or "." not in key:
            return False
        return key.rsplit(".", maxsplit=1)[0] in facility_keys

    def _batch_facility_summary(
        self,
        region_key: str,
        facility_count: int,
    ) -> PublicKnowledgeChangeResponse:
        region_name = self.node_names.get(region_key) or "目标区域"
        return PublicKnowledgeChangeResponse(
            # Keep the existing public union stable: the UI renders this as
            # one knowledge item, while the persisted payload remains raw.
            kind="FACT_REVEALED",
            key=f"{region_key}.facility_knowledge_summary",
            name=f"已同步{region_name} {facility_count} 处设施状态。",
            value=None,
        )

    def _safe_region_for_node(self, node_key: str) -> str | None:
        try:
            return region_for_node(self.definition, node_key)
        except LocalityEngineError:
            return None

    def _format_change(
        self,
        kind: str,
        key: str,
        raw_name: object,
        raw_value: object,
    ) -> tuple[str, str | int | bool | None] | None:
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
            node_key = key.rsplit(".", maxsplit=1)[0]
            if not self._should_display_fact(node_key, fact_key):
                return None
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

    def _should_display_fact(self, node_key: str, fact_key: str) -> bool:
        """Keep only player-useful facts in an action knowledge report."""

        if fact_key == "repair_profile":
            return False
        if fact_key not in {"power_generation_capable", "generation_capable"}:
            return True
        node = self.definition.world.node(node_key)
        fact = node.fact(fact_key) if node is not None else None
        # The capability fact is present on every Facility for a shared
        # gameplay contract.  Only a definition that advertises the
        # capability as true is a genuine generation facility; ordinary
        # facilities' false value is not player-facing information.
        return fact is not None and fact.initial_value is True

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
    *,
    action_key: str | None = None,
    target_key: str | None = None,
) -> list[PublicKnowledgeChangeResponse]:
    """Format only the already-emitted action knowledge delta for players."""

    return PlayerActionReportFormatter(definition).format_changes(
        payload,
        action_key=action_key,
        target_key=target_key,
    )


__all__ = ["PlayerActionReportFormatter", "format_player_knowledge_changes"]
