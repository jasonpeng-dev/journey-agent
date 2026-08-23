"""Generic Region/Facility/Transport semantics for opted-in Scenarios."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.domain.scenario_v2 import (
    ActionBehavior,
    ActionDefinitionV2,
    ActionLocality,
    ActionTargetKind,
    ResourceScopeKind,
    ResourceScopeV2,
    ScenarioDefinitionV2,
    StrictScalar,
)

if TYPE_CHECKING:
    from app.engine.rules import DeclarativeRuleState


class LocalityEngineError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})


def locality_enabled(definition: ScenarioDefinitionV2) -> bool:
    return definition.metadata.locality.enabled


def region_for_node(definition: ScenarioDefinitionV2, node_key: str) -> str:
    """Resolve a Region, or a Facility's declared ``located_in`` Region."""

    node = definition.world.node(node_key)
    if node is None:
        raise LocalityEngineError(
            "LOCALITY_NODE_NOT_FOUND",
            "The locality Node does not exist",
            details={"required": "NODE_EXISTS", "actual": "NOT_FOUND", "node_key": node_key},
        )
    contract = definition.metadata.locality
    if node.node_type_key == contract.region_node_type_key:
        return node_key
    if node.node_type_key != contract.facility_node_type_key:
        raise LocalityEngineError(
            "LOCALITY_REGION_UNRESOLVED",
            "Only Region or Facility Nodes have a current Region in this Scenario",
            details={
                "required": "REGION_OR_FACILITY",
                "actual": node.node_type_key,
                "node_key": node_key,
            },
        )
    assert contract.located_in_relation_type_key is not None
    matches = sorted(
        relation.target_node_key
        for relation in definition.world.relations
        if relation.source_node_key == node_key
        and relation.relation_type_key == contract.located_in_relation_type_key
        and _is_region(definition, relation.target_node_key)
    )
    if len(matches) != 1:
        raise LocalityEngineError(
            "LOCALITY_FACILITY_REGION_INVALID",
            "A Facility must be located in exactly one Region",
            details={
                "required": "EXACTLY_ONE_FACILITY_REGION",
                "actual": matches,
                "target_key": node_key,
            },
        )
    return matches[0]


def transport_endpoints(definition: ScenarioDefinitionV2, transport_key: str) -> tuple[str, str]:
    node = definition.world.node(transport_key)
    contract = definition.metadata.locality
    if node is None or node.node_type_key != contract.transport_node_type_key:
        raise LocalityEngineError(
            "LOCALITY_TRANSPORT_INVALID",
            "The target is not a Transport Node",
            details={
                "required": "TRANSPORT",
                "actual": node.node_type_key if node is not None else "NOT_FOUND",
                "target_key": transport_key,
            },
        )
    assert contract.transport_endpoint_relation_type_key is not None
    endpoints = sorted(
        relation.target_node_key
        for relation in definition.world.relations
        if relation.source_node_key == transport_key
        and relation.relation_type_key == contract.transport_endpoint_relation_type_key
        and _is_region(definition, relation.target_node_key)
    )
    if len(endpoints) != 2 or endpoints[0] == endpoints[1]:
        raise LocalityEngineError(
            "LOCALITY_TRANSPORT_ENDPOINTS_INVALID",
            "A Transport must connect exactly two distinct Regions",
            details={
                "required": "TWO_DISTINCT_REGION_ENDPOINTS",
                "actual": endpoints,
                "transport_key": transport_key,
            },
        )
    return endpoints[0], endpoints[1]


def transport_between(
    definition: ScenarioDefinitionV2,
    source_region_key: str,
    target_region_key: str,
) -> str:
    if source_region_key == target_region_key:
        raise LocalityEngineError(
            "LOCALITY_TRAVEL_SAME_REGION",
            "Travel requires a different target Region",
            details={
                "required": "DIFFERENT_REGION",
                "actual": source_region_key,
                "source_region": source_region_key,
                "target_region": target_region_key,
            },
        )
    contract = definition.metadata.locality
    candidates: list[str] = []
    for node in definition.world.nodes:
        if node.node_type_key != contract.transport_node_type_key:
            continue
        try:
            endpoints = transport_endpoints(definition, node.key)
        except LocalityEngineError:
            continue
        if set(endpoints) == {source_region_key, target_region_key}:
            candidates.append(node.key)
    if not candidates:
        raise LocalityEngineError(
            "LOCALITY_ROUTE_NOT_FOUND",
            "No one-hop Transport connects the source and target Regions",
            details={
                "required": "ONE_HOP_TRANSPORT",
                "actual": "ROUTE_NOT_FOUND",
                "source_region": source_region_key,
                "target_region": target_region_key,
            },
        )
    return sorted(candidates)[0]


def resolve_resource_scope(
    definition: ScenarioDefinitionV2,
    scope: ResourceScopeV2 | None,
    *,
    actor_current_node_key: str | None,
    target_node_key: str,
) -> str | None:
    if scope is None:
        return None
    if scope.kind == ResourceScopeKind.EXPLICIT:
        assert scope.node_key is not None
        return scope.node_key
    if actor_current_node_key is None:
        raise LocalityEngineError(
            "LOCALITY_ACTOR_REGION_REQUIRED",
            "A Region-scoped Resource requires an Actor current Region",
            details={"required": "ACTOR_REGION", "actual": "UNKNOWN"},
        )
    if scope.kind == ResourceScopeKind.ACTOR_CURRENT_REGION:
        return _require_region(definition, actor_current_node_key)
    return region_for_node(definition, target_node_key)


def validate_action_locality(
    definition: ScenarioDefinitionV2,
    action: ActionDefinitionV2,
    *,
    actor_current_node_key: str,
    target_node_key: str,
    parameters: Mapping[str, StrictScalar],
    target_actor_node_key: str | None = None,
) -> str | None:
    """Validate static locality and return a connector for travel-like actions.

    This function intentionally never reads passability.  Hidden passability
    is an execution-time fact and must not turn a valid Plan into a rejected
    Plan during preflight.
    """

    if not locality_enabled(definition):
        return None
    actor_region = _require_region(definition, actor_current_node_key)
    if action.target_kind == ActionTargetKind.ACTOR:
        if target_actor_node_key is None:
            raise LocalityEngineError(
                "LOCALITY_ACTOR_TARGET_REQUIRED",
                "An Actor target must have a current location",
                details={
                    "required": "TARGET_ACTOR_REGION",
                    "actual": "UNKNOWN",
                    "target_key": target_node_key,
                },
            )
        if action.locality != ActionLocality.ACTOR_REGION:
            raise LocalityEngineError(
                "LOCALITY_ACTOR_TARGET_INVALID",
                "An Actor target requires ACTOR_REGION locality",
                details={"required": "ACTOR_REGION", "actual": action.locality.value},
            )
        target_actor_region = region_for_node(definition, target_actor_node_key)
        if actor_region != target_actor_region:
            raise LocalityEngineError(
                "LOCALITY_ACTOR_REGION_INVALID",
                "The command relay Actor must be in the target Actor's Region",
                retryable=True,
                details={
                    "required": "SAME_REGION",
                    "actual": {
                        "actor_region": actor_region,
                        "target_region": target_actor_region,
                    },
                    "source_region": actor_region,
                    "target_region": target_actor_region,
                },
            )
        return None
    target = definition.world.node(target_node_key)
    if target is None:
        raise LocalityEngineError(
            "LOCALITY_TARGET_NOT_FOUND",
            "The locality target does not exist",
            details={
                "required": "TARGET_EXISTS",
                "actual": "NOT_FOUND",
                "target_key": target_node_key,
            },
        )

    if action.behavior == ActionBehavior.TRAVEL:
        target_region = _require_region(definition, target_node_key)
        return _require_connector(definition, actor_region, target_region)
    if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
        if target.node_type_key != definition.metadata.locality.region_node_type_key:
            raise LocalityEngineError(
                "LOCALITY_TRANSPORT_TARGET_INVALID",
                "Transport destination must be a Region Node",
                details={
                    "required": "REGION",
                    "actual": target.node_type_key,
                    "target_key": target_node_key,
                },
            )
        target_region = _require_region(definition, target_node_key)
        if actor_region == target_region:
            raise LocalityEngineError(
                "LOCALITY_TRAVEL_SAME_REGION",
                "Transport requires a different destination Region",
                details={
                    "required": "DIFFERENT_REGION",
                    "actual": {
                        "actor_region": actor_region,
                        "target_region": target_region,
                    },
                    "source_region": actor_region,
                    "target_region": target_region,
                },
            )
        return _require_connector(definition, actor_region, target_region)

    if action.locality == ActionLocality.REGION:
        target_region = _require_region(definition, target_node_key)
        if actor_region != target_region:
            raise LocalityEngineError(
                "LOCALITY_ACTOR_REGION_INVALID",
                "The Actor must be in the target Region",
                retryable=True,
                details={
                    "required": "SAME_REGION",
                    "actual": {
                        "actor_region": actor_region,
                        "target_region": target_region,
                    },
                    "source_region": actor_region,
                    "target_region": target_region,
                },
            )
    elif action.locality == ActionLocality.FACILITY_REGION:
        target_region = _require_facility_region(definition, target_node_key)
        if actor_region != target_region:
            raise LocalityEngineError(
                "LOCALITY_ACTOR_REGION_INVALID",
                "The Actor must be in the Facility's Region",
                retryable=True,
                details={
                    "required": "FACILITY_REGION",
                    "actual": {
                        "actor_region": actor_region,
                        "target_region": target_region,
                    },
                    "source_region": actor_region,
                    "target_region": target_region,
                },
            )
    elif action.locality == ActionLocality.TRANSPORT_ENDPOINT:
        endpoints = transport_endpoints(definition, target_node_key)
        if actor_region not in endpoints:
            raise LocalityEngineError(
                "LOCALITY_TRANSPORT_ENDPOINT_INVALID",
                "The Actor must be in one endpoint Region of the Transport",
                retryable=True,
                details={
                    "required": "TRANSPORT_ENDPOINT",
                    "actual": {
                        "actor_region": actor_region,
                        "transport_endpoints": list(endpoints),
                    },
                    "source_region": actor_region,
                    "transport_key": target_node_key,
                },
            )
    elif action.locality == ActionLocality.LOCAL_TARGET:
        if target.node_type_key == definition.metadata.locality.facility_node_type_key:
            if actor_region != _require_facility_region(definition, target_node_key):
                raise LocalityEngineError(
                    "LOCALITY_ACTOR_REGION_INVALID",
                    "The Actor must be in the Facility's Region",
                    retryable=True,
                    details={
                        "required": "LOCAL_TARGET",
                        "actual": {
                            "actor_region": actor_region,
                            "target_region": _require_facility_region(definition, target_node_key),
                        },
                        "source_region": actor_region,
                        "target_region": _require_facility_region(definition, target_node_key),
                    },
                )
        elif target.node_type_key == definition.metadata.locality.transport_node_type_key:
            if actor_region not in transport_endpoints(definition, target_node_key):
                raise LocalityEngineError(
                    "LOCALITY_TRANSPORT_ENDPOINT_INVALID",
                    "The Actor must be in one endpoint Region of the Transport",
                    retryable=True,
                    details={
                        "required": "LOCAL_TARGET",
                        "actual": {
                            "actor_region": actor_region,
                            "transport_endpoints": list(
                                transport_endpoints(definition, target_node_key)
                            ),
                        },
                        "source_region": actor_region,
                        "transport_key": target_node_key,
                    },
                )
        else:
            raise LocalityEngineError(
                "LOCALITY_TARGET_INVALID",
                "LOCAL_TARGET requires a Facility or Transport target",
                details={"required": "LOCAL_TARGET", "actual": target.node_type_key},
            )
    return None


def passability_fact(
    definition: ScenarioDefinitionV2,
    transport_key: str,
    state: DeclarativeRuleState,
) -> tuple[str, bool] | None:
    fact_key = definition.metadata.locality.passability_fact_key
    if fact_key is None:
        return None
    fact = state.facts.get((transport_key, fact_key))
    if fact is None:
        raise LocalityEngineError(
            "LOCALITY_PASSABILITY_FACT_MISSING",
            "The Transport passability Fact is missing from Instance state",
        )
    if not isinstance(fact.value, bool):
        raise LocalityEngineError(
            "LOCALITY_PASSABILITY_FACT_INVALID",
            "The Transport passability Fact must be boolean",
        )
    return fact_key, fact.value


def _is_region(definition: ScenarioDefinitionV2, node_key: str) -> bool:
    node = definition.world.node(node_key)
    return bool(
        node is not None and node.node_type_key == definition.metadata.locality.region_node_type_key
    )


def _require_region(definition: ScenarioDefinitionV2, node_key: str) -> str:
    if not _is_region(definition, node_key):
        node = definition.world.node(node_key)
        raise LocalityEngineError(
            "LOCALITY_REGION_REQUIRED",
            "The action target must be a Region Node",
            details={
                "required": "REGION",
                "actual": node.node_type_key if node is not None else "NOT_FOUND",
                "target_key": node_key,
            },
        )
    return node_key


def _require_facility_region(definition: ScenarioDefinitionV2, node_key: str) -> str:
    node = definition.world.node(node_key)
    if node is None or node.node_type_key != definition.metadata.locality.facility_node_type_key:
        raise LocalityEngineError(
            "LOCALITY_FACILITY_REQUIRED",
            "The action target must be a Facility Node",
            details={
                "required": "FACILITY",
                "actual": node.node_type_key if node is not None else "NOT_FOUND",
                "target_key": node_key,
            },
        )
    return region_for_node(definition, node_key)


def _require_connector(
    definition: ScenarioDefinitionV2,
    source_region_key: str,
    target_region_key: str,
) -> str:
    return transport_between(definition, source_region_key, target_region_key)


__all__ = [
    "LocalityEngineError",
    "locality_enabled",
    "passability_fact",
    "region_for_node",
    "resolve_resource_scope",
    "transport_between",
    "transport_endpoints",
    "validate_action_locality",
]
