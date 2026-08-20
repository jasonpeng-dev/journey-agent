"""Shared Knowledge-safe projection for Player and Planner consumers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.domain.enums import (
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceResourceState,
)


@dataclass(frozen=True, slots=True)
class RegionResourceKnowledgeView:
    region_key: str
    resource_inventory_visibility: ResourceInventoryVisibility
    resource_survey_completed: bool


@dataclass(frozen=True, slots=True)
class KnownResourcePoolView:
    pool_key: str
    resource_key: str
    region_key: str | None
    facility_key: str | None
    quantity: int
    availability: ResourcePoolAvailability
    availability_requirement: dict[str, Any] | None
    availability_requirement_status: str | None


class SharedKnowledgeProjection:
    """Project only gameplay Knowledge, never hidden runtime Truth."""

    def __init__(self, db: Session, scope: RuntimeScope, definition: ScenarioDefinitionV2) -> None:
        self.db = db
        self.scope = scope
        self.definition = definition
        self._region_states: dict[str, RegionResourceKnowledgeView] | None = None
        self._visible_pools: tuple[KnownResourcePoolView, ...] | None = None

    @property
    def region_keys(self) -> tuple[str, ...]:
        locality = self.definition.metadata.locality
        if not locality.enabled or locality.region_node_type_key is None:
            return ()
        return tuple(
            sorted(
                node.key
                for node in self.definition.world.nodes
                if node.node_type_key == locality.region_node_type_key
            )
        )

    def known_node_rows(self) -> tuple[GameInstanceNodeState, ...]:
        return tuple(
            self.db.scalars(
                select(GameInstanceNodeState).where(
                    GameInstanceNodeState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceNodeState.visibility == Visibility.KNOWN,
                )
            )
        )

    def known_fact_rows(self) -> tuple[GameInstanceFactState, ...]:
        known_nodes = {row.node_key for row in self.known_node_rows()}
        return tuple(
            row
            for row in self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceFactState.visibility == Visibility.KNOWN,
                )
            )
            if row.node_key in known_nodes
        )

    def known_relations(self) -> tuple[dict[str, Any], ...]:
        known_nodes = {row.node_key for row in self.known_node_rows()}
        return tuple(
            item.model_dump(mode="json")
            for item in self.definition.world.relations
            if item.source_node_key in known_nodes and item.target_node_key in known_nodes
        )

    def actor_rows(self) -> tuple[GameInstanceActor, ...]:
        """Return the shared active-Actor identity/location projection source."""

        return tuple(
            self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == self.scope.game_instance_id,
                    GameInstanceActor.status == "ACTIVE",
                )
            )
        )

    def region_states(self) -> dict[str, RegionResourceKnowledgeView]:
        if self._region_states is not None:
            return self._region_states
        rows: tuple[GameInstanceRegionResourceKnowledge, ...]
        try:
            rows = tuple(
                self.db.scalars(
                    select(GameInstanceRegionResourceKnowledge).where(
                        GameInstanceRegionResourceKnowledge.game_instance_id
                        == self.scope.game_instance_id
                    )
                )
            )
        except SQLAlchemyError:
            rows = ()
        by_key = {row.region_key: row for row in rows}
        self._region_states = {
            region_key: RegionResourceKnowledgeView(
                region_key=region_key,
                resource_inventory_visibility=_enum_value(
                    by_key.get(region_key),
                    "resource_inventory_visibility",
                    ResourceInventoryVisibility.VISIBLE,
                ),
                resource_survey_completed=bool(
                    getattr(by_key.get(region_key), "resource_survey_completed", True)
                ),
            )
            for region_key in self.region_keys
        }
        return self._region_states

    def visible_resource_pools(self) -> tuple[KnownResourcePoolView, ...]:
        if self._visible_pools is not None:
            return self._visible_pools
        try:
            rows = tuple(
                self.db.scalars(
                    select(GameInstanceResourceState).where(
                        GameInstanceResourceState.game_instance_id == self.scope.game_instance_id
                    )
                )
            )
        except SQLAlchemyError:
            rows = ()
        region_states = self.region_states()
        visible: list[KnownResourcePoolView] = []
        for row in rows:
            visibility = _enum_value(row, "visibility", ResourcePoolVisibility.VISIBLE)
            if visibility != ResourcePoolVisibility.VISIBLE:
                continue
            region_key = row.scope_node_key
            if region_key is not None:
                region_state = region_states.get(region_key)
                if (
                    region_state is not None
                    and region_state.resource_inventory_visibility
                    != ResourceInventoryVisibility.VISIBLE
                    and row.facility_key is None
                ):
                    continue
            visible.append(
                KnownResourcePoolView(
                    pool_key=row.pool_key,
                    resource_key=row.resource_key,
                    region_key=region_key,
                    facility_key=row.facility_key,
                    quantity=row.value,
                    availability=_enum_value(
                        row,
                        "availability",
                        ResourcePoolAvailability.AVAILABLE,
                    ),
                    availability_requirement=self.known_requirement(row.availability_requirement),
                    availability_requirement_status=self.requirement_status(
                        row.availability_requirement
                    ),
                )
            )
        self._visible_pools = tuple(
            sorted(
                visible, key=lambda item: (item.region_key or "", item.resource_key, item.pool_key)
            )
        )
        return self._visible_pools

    def resource_intelligence(self) -> dict[str, Any]:
        """Return region/resource aggregates plus visible Pool detail."""

        resource_names = {item.key: item.name for item in self.definition.world.resources}
        grouped: dict[tuple[str | None, str], list[KnownResourcePoolView]] = defaultdict(list)
        for pool in self.visible_resource_pools():
            grouped[(pool.region_key, pool.resource_key)].append(pool)
        regions: dict[str, dict[str, Any]] = {}
        for region_key, state in self.region_states().items():
            region_node = self.definition.world.node(region_key)
            resources: dict[str, Any] = {}
            for (pool_region, resource_key), pools in grouped.items():
                if pool_region != region_key:
                    continue
                resources[resource_key] = self._resource_summary(
                    pools,
                    resource_names[resource_key],
                )
            regions[region_key] = {
                "region_name": region_node.name if region_node is not None else region_key,
                "resource_inventory_visibility": state.resource_inventory_visibility.value,
                "resource_survey_completed": state.resource_survey_completed,
                "resources": resources,
            }
        global_resources: dict[str, Any] = {}
        for (global_region_key, resource_key), pools in grouped.items():
            if global_region_key is None:
                global_resources[resource_key] = self._resource_summary(
                    pools, resource_names[resource_key]
                )
        return {
            "total_regions": len(self.region_keys),
            "visible_region_count": sum(
                state.resource_inventory_visibility == ResourceInventoryVisibility.VISIBLE
                for state in self.region_states().values()
            ),
            "regions": regions,
            "global_resources": global_resources,
        }

    def planner_resources(self) -> dict[str, Any]:
        """Return a compact, knowledge-safe Planner resource projection."""

        intelligence = self.resource_intelligence()
        by_resource: dict[str, dict[str, Any]] = {}
        planner_regions: dict[str, dict[str, Any]] = {}
        for region_key, region in intelligence["regions"].items():
            planner_region = {key: value for key, value in region.items() if key != "resources"}
            planner_region["resources"] = {
                resource_key: self._planner_resource_summary(summary)
                for resource_key, summary in region["resources"].items()
            }
            planner_regions[region_key] = planner_region
            for resource_key, summary in region["resources"].items():
                planner_summary = self._planner_resource_summary(summary)
                resource = by_resource.setdefault(
                    resource_key,
                    {"regions": {}, "known_total": 0, "known_available": 0},
                )
                resource["regions"][region_key] = {
                    "known_total": planner_summary["known_total"],
                    "known_available": planner_summary["known_available"],
                    "pools": planner_summary["pools"],
                }
                resource["known_total"] += summary["known_total"]
                resource["known_available"] += summary["known_available"]
        for resource_key, summary in intelligence["global_resources"].items():
            planner_summary = self._planner_resource_summary(summary)
            resource = by_resource.setdefault(
                resource_key,
                {"regions": {}, "known_total": 0, "known_available": 0},
            )
            resource["global"] = planner_summary
            resource["known_total"] += summary["known_total"]
            resource["known_available"] += summary["known_available"]
        return {
            "regions": planner_regions,
            "resources": by_resource,
            "total_regions": intelligence["total_regions"],
            "visible_region_count": intelligence["visible_region_count"],
        }

    @staticmethod
    def _planner_resource_summary(summary: dict[str, Any]) -> dict[str, Any]:
        """Remove storage-only Pool identity from the Planner projection."""

        return {key: value for key, value in summary.items() if key != "pools"} | {
            "pools": [
                {key: value for key, value in pool.items() if key != "pool_key"}
                for pool in summary["pools"]
            ]
        }

    def associated_known_resources(self, facility_key: str) -> list[dict[str, Any]]:
        resource_names = {item.key: item.name for item in self.definition.world.resources}
        return [
            {
                "resource_key": pool.resource_key,
                "resource_name": resource_names.get(pool.resource_key, pool.resource_key),
                "facility_name": self._facility_name(pool.facility_key),
                "quantity": pool.quantity,
                "availability": pool.availability.value,
                "availability_requirement": pool.availability_requirement,
                "availability_requirement_status": pool.availability_requirement_status,
            }
            for pool in self.visible_resource_pools()
            if pool.facility_key == facility_key
        ]

    def _resource_summary(
        self,
        pools: list[KnownResourcePoolView],
        resource_name: str,
    ) -> dict[str, Any]:
        return {
            "resource_name": resource_name,
            "known_total": sum(pool.quantity for pool in pools),
            "known_available": sum(
                pool.quantity
                for pool in pools
                if pool.availability == ResourcePoolAvailability.AVAILABLE
            ),
            "pools": [
                {
                    "pool_key": pool.pool_key,
                    "quantity": pool.quantity,
                    "facility_key": pool.facility_key,
                    "facility_name": self._facility_name(pool.facility_key),
                    "availability": pool.availability.value,
                    **(
                        {"availability_requirement": pool.availability_requirement}
                        if pool.availability_requirement is not None
                        else {}
                    ),
                    **(
                        {"availability_requirement_status": pool.availability_requirement_status}
                        if pool.availability_requirement_status is not None
                        else {}
                    ),
                }
                for pool in pools
            ],
        }

    def _facility_name(self, facility_key: str | None) -> str | None:
        if facility_key is None:
            return None
        facility = self.definition.world.node(facility_key)
        return facility.name if facility is not None else None

    def known_requirement(self, raw: dict[str, Any] | None) -> dict[str, Any] | None:
        if not raw:
            return None
        node_key = raw.get("node_key")
        fact_key = raw.get("fact_key")
        if not isinstance(node_key, str) or not isinstance(fact_key, str):
            return None
        known = self.db.scalar(
            select(GameInstanceFactState.visibility).where(
                GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                GameInstanceFactState.node_key == node_key,
                GameInstanceFactState.fact_key == fact_key,
            )
        )
        if known != Visibility.KNOWN:
            return None
        result = dict(raw)
        fact_value = self.db.scalar(
            select(GameInstanceFactState.truth_value).where(
                GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                GameInstanceFactState.node_key == node_key,
                GameInstanceFactState.fact_key == fact_key,
            )
        )
        result["known_value"] = fact_value
        return result

    def requirement_status(self, raw: dict[str, Any] | None) -> str | None:
        """Expose only whether a declared unlock requirement is known."""

        if not raw:
            return None
        return "KNOWN" if self.known_requirement(raw) is not None else "UNKNOWN"


def _enum_value(row: object, attribute: str, default: Any) -> Any:
    value = getattr(row, attribute, default) if row is not None else default
    try:
        return type(default)(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "KnownResourcePoolView",
    "RegionResourceKnowledgeView",
    "SharedKnowledgeProjection",
]
