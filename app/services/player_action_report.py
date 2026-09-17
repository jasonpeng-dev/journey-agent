"""Player-facing formatting for persisted action knowledge changes."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.api.schemas.phase_d import PublicKnowledgeChangeResponse
from app.domain.resources import resource_pool_initial_states
from app.domain.scenario_v2 import ActionBehavior, EffectKind, ScenarioDefinitionV2
from app.engine.locality import LocalityEngineError, region_for_node
from app.services.legacy_action_report_compatibility import (
    legacy_fact_change,
    uses_legacy_action_report,
)

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
        self.fact_definitions = {
            (node.key, fact.key): fact
            for node in definition.world.nodes
            for fact in node.facts
        }
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
        if action is None:
            return None
        reveals_region_facilities = action.behavior == ActionBehavior.REPAIR_COMMUNICATIONS or any(
            rule.action_key == action.key
            and any(
                effect.kind == EffectKind.REVEAL_TARGET_REGION_FACILITY_FACTS
                for effect in rule.effects
            )
            for rule in self.definition.rules
        )
        if not reveals_region_facilities:
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
            fact = self.fact_definitions.get((node_key, fact_key))
            if uses_legacy_action_report(self.definition):
                legacy = legacy_fact_change(fact_key, raw_value)
                if legacy is None:
                    return None
                return legacy
            return (
                fact.name if fact is not None else self._safe_name(raw_name, "已知状态"),
                self._display_value(raw_value, fact=fact),
            )
        if kind == "RELATION_REVEALED":
            relation_key = self._relation_type_key(key, raw_name)
            relation = self.definition.world.relation_type(relation_key)
            return (
                relation.name if relation is not None else self._safe_name(raw_name, "已知关系"),
                None,
            )
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
        if isinstance(raw_name, str) and raw_name:
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
    def _display_value(
        value: object,
        *,
        fact: object | None = None,
    ) -> str | int | bool | None:
        value_labels = getattr(fact, "value_labels", ())
        for item in value_labels:
            if type(item.value) is type(value) and item.value == value:
                return item.label
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
