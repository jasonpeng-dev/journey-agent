"""Explicit adapters for legacy Starfire keys and persisted projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.domain.world import FactDefinition
from app.scenarios.starfire.definition import STARFIRE_WORLD

LEGACY_NODE_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "valley_entrance": "northern_valley",
        "ambush_valley": "northern_valley",
    }
)


@dataclass(frozen=True, slots=True)
class CanonicalFactRef:
    node_key: str
    fact_key: str


@dataclass(frozen=True, slots=True)
class LegacySupplyProjection:
    truth_status: str
    known: bool


LEGACY_FACT_REFS: Mapping[str, CanonicalFactRef] = MappingProxyType(
    {
        "village_support": CanonicalFactRef("north_village", "village_support"),
        "valley_intelligence": CanonicalFactRef("northern_valley", "valley_intelligence"),
        "valley_security": CanonicalFactRef("northern_valley", "valley_security"),
        "enemy_supply_route": CanonicalFactRef(
            "enemy_north_supply_route",
            "supply_status",
        ),
        "starfire_outpost_status": CanonicalFactRef("starfire_outpost", "outpost_status"),
        "northern_trade_route_status": CanonicalFactRef(
            "northern_trade_route",
            "trade_route_status",
        ),
    }
)


def canonical_node_key(key: str) -> str:
    """Resolve a historical Starfire node key without changing the audited input."""

    return LEGACY_NODE_ALIASES.get(key, key)


def canonical_fact_ref(legacy_key: str) -> CanonicalFactRef | None:
    return LEGACY_FACT_REFS.get(legacy_key)


def project_legacy_supply_status(status: str) -> LegacySupplyProjection:
    """Separate legacy UNKNOWN knowledge from the canonical supply truth."""

    if status == "UNKNOWN":
        return LegacySupplyProjection(truth_status="ACTIVE", known=False)
    if status == "ACTIVE":
        return LegacySupplyProjection(truth_status="ACTIVE", known=True)
    if status == "DISRUPTED":
        return LegacySupplyProjection(truth_status="DISRUPTED", known=True)
    raise ValueError(f"Unsupported legacy enemy supply status: {status}")


def initial_resource_values() -> dict[str, int]:
    return {resource.key: resource.initial_value for resource in STARFIRE_WORLD.resources}


def initial_legacy_world_facts() -> dict[str, dict[str, object]]:
    """Project canonical initial truth into the existing flat persistence contract."""

    return {
        "valley_intelligence": {"status": _fact("northern_valley", "valley_intelligence")},
        "enemy_supply_route": {"status": "UNKNOWN"},
        "valley_security": {"status": _fact("northern_valley", "valley_security")},
        "village_support": {"status": _fact("north_village", "village_support")},
        "starfire_outpost_status": {"status": _fact("starfire_outpost", "outpost_status")},
        "northern_trade_route_status": {
            "status": _fact("northern_trade_route", "trade_route_status")
        },
    }


def _fact(node_key: str, fact_key: str) -> object:
    node = STARFIRE_WORLD.node(node_key)
    fact: FactDefinition | None = node.fact(fact_key) if node is not None else None
    if fact is None:
        raise RuntimeError(f"Starfire definition is missing {node_key}.{fact_key}")
    return fact.initial_value
