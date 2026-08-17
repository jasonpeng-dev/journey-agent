"""Player-safe spatial display projections.

This module is deliberately read-only.  It turns the generic Scenario
locality contract into labels that are useful to a Player without adding any
gameplay semantics or exposing hidden Truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.scenario_v2 import (
    ActionBehavior,
    ActionDefinitionV2,
    ActionLocality,
    ScenarioDefinitionV2,
    StrictScalar,
)
from app.engine.locality import (
    LocalityEngineError,
    locality_enabled,
    region_for_node,
    transport_endpoints,
)


@dataclass(frozen=True)
class SpatialNodeProjection:
    key: str
    name: str
    node_type_key: str
    region_key: str | None = None
    region_name: str | None = None
    endpoint_region_keys: tuple[str, ...] = ()
    endpoint_region_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class SpatialResourceProjection:
    scope_node_key: str | None = None
    scope_node_name: str | None = None
    scope_region_key: str | None = None
    scope_region_name: str | None = None


@dataclass(frozen=True)
class ActionLocationProjection:
    kind: str
    summary: str
    detail: str | None = None


class SpatialDisplayProjector:
    """Build stable, localized display metadata from one exact Scenario."""

    def __init__(self, definition: ScenarioDefinitionV2) -> None:
        self.definition = definition
        self._nodes = {node.key: node for node in definition.world.nodes}
        self._resource_names = {
            resource.key: resource.name for resource in definition.world.resources
        }
        contract = definition.metadata.locality
        self._region_type = contract.region_node_type_key
        self._facility_type = contract.facility_node_type_key
        self._transport_type = contract.transport_node_type_key

    def node(self, node_key: str) -> SpatialNodeProjection | None:
        node = self._nodes.get(node_key)
        if node is None:
            return None

        region_key: str | None = None
        region_name: str | None = None
        endpoint_region_keys: tuple[str, ...] = ()
        endpoint_region_names: tuple[str, ...] = ()
        if locality_enabled(self.definition):
            if node.node_type_key == self._region_type:
                region_key = node.key
            elif node.node_type_key == self._facility_type:
                region_key = self._safe_region_for_node(node.key)
            elif node.node_type_key == self._transport_type:
                endpoint_region_keys = self._safe_transport_endpoints(node.key)
                endpoint_region_names = tuple(
                    self._node_name(key) for key in endpoint_region_keys
                )
            if region_key is not None:
                region_name = self._node_name(region_key)

        return SpatialNodeProjection(
            key=node.key,
            name=node.name,
            node_type_key=node.node_type_key,
            region_key=region_key,
            region_name=region_name,
            endpoint_region_keys=endpoint_region_keys,
            endpoint_region_names=endpoint_region_names,
        )

    def resource_scope(self, scope_node_key: str | None) -> SpatialResourceProjection:
        if scope_node_key is None:
            return SpatialResourceProjection()
        node = self.node(scope_node_key)
        if node is None:
            return SpatialResourceProjection(scope_node_key=scope_node_key)
        return SpatialResourceProjection(
            scope_node_key=scope_node_key,
            scope_node_name=node.name,
            scope_region_key=node.region_key,
            scope_region_name=node.region_name,
        )

    def action_location(
        self,
        action: ActionDefinitionV2 | None,
        *,
        target_node_key: str,
        source_node_key: str | None = None,
        parameters: Mapping[str, StrictScalar] | None = None,
        compact: bool = False,
    ) -> ActionLocationProjection | None:
        """Return one common location label for every Player-facing action view."""

        target = self.node(target_node_key)
        if target is None:
            return None
        action = action
        values = parameters or {}

        if action is not None and action.behavior in (
            ActionBehavior.TRAVEL,
            ActionBehavior.TRANSPORT_RESOURCE,
        ):
            source_region = self._region_projection(source_node_key)
            target_region = self._region_projection(target_node_key)
            if source_region is not None and target_region is not None:
                detail = None
                if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
                    resource_key = values.get("resource_key")
                    amount = values.get("amount")
                    resource_name = self._resource_names.get(str(resource_key))
                    if resource_name is not None and isinstance(amount, int) and not isinstance(
                        amount, bool
                    ):
                        detail = f"{resource_name} \u00d7{amount}"
                return ActionLocationProjection(
                    kind="ROUTE",
                    summary=f"{source_region.name} → {target_region.name}",
                    detail=detail,
                )
            return ActionLocationProjection(kind="NODE", summary=target.name)

        if target.endpoint_region_names and (
            action is None
            or action.locality == ActionLocality.TRANSPORT_ENDPOINT
            or target.node_type_key == self._transport_type
        ):
            return ActionLocationProjection(
                kind="TRANSPORT",
                summary=(
                    target.name
                    if compact
                    else f"{target.name} · {' ↔ '.join(target.endpoint_region_names)}"
                ),
            )

        if target.region_name is not None and target.node_type_key == self._facility_type:
            return ActionLocationProjection(
                kind="FACILITY",
                summary=f"{target.region_name} · {target.name}",
            )
        if target.region_name is not None and target.node_type_key == self._region_type:
            return ActionLocationProjection(kind="REGION", summary=target.region_name)
        if target.region_name is not None:
            return ActionLocationProjection(kind="NODE", summary=target.region_name)
        return ActionLocationProjection(kind="NODE", summary=target.name)

    def _region_projection(self, node_key: str | None) -> SpatialNodeProjection | None:
        if node_key is None:
            return None
        projection = self.node(node_key)
        if projection is None:
            return None
        if projection.region_key is not None:
            return SpatialNodeProjection(
                key=projection.region_key,
                name=projection.region_name or projection.region_key,
                node_type_key=self._region_type or "region",
                region_key=projection.region_key,
                region_name=projection.region_name or projection.region_key,
            )
        return None

    def _safe_region_for_node(self, node_key: str) -> str | None:
        try:
            return region_for_node(self.definition, node_key)
        except LocalityEngineError:
            return None

    def _safe_transport_endpoints(self, node_key: str) -> tuple[str, ...]:
        try:
            return transport_endpoints(self.definition, node_key)
        except LocalityEngineError:
            return ()

    def _node_name(self, node_key: str) -> str:
        return self._nodes[node_key].name


__all__ = [
    "ActionLocationProjection",
    "SpatialDisplayProjector",
    "SpatialNodeProjection",
    "SpatialResourceProjection",
]
