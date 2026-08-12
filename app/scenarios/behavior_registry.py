"""Explicit allowlist for versioned Scenario behavior implementations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.domain.scenario import BehaviorBundleRef


@dataclass(frozen=True, slots=True)
class BehaviorBundleDefinition:
    """Capability metadata only; Objective content always comes from the snapshot."""

    ref: BehaviorBundleRef
    interaction_tools: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        normalized = {
            interaction: frozenset(tools) for interaction, tools in self.interaction_tools.items()
        }
        if any(not interaction.strip() or not tools for interaction, tools in normalized.items()):
            raise ValueError("behavior interaction mappings need keys and tools")
        object.__setattr__(self, "interaction_tools", MappingProxyType(normalized))

    @property
    def required_tool_names(self) -> frozenset[str]:
        return frozenset().union(*self.interaction_tools.values())


class BehaviorBundleRegistry:
    def __init__(self, bundles: tuple[BehaviorBundleDefinition, ...] = ()) -> None:
        self._bundles: dict[tuple[str, str], BehaviorBundleDefinition] = {}
        for bundle in bundles:
            self.register(bundle)

    def register(self, bundle: BehaviorBundleDefinition) -> None:
        identity = (bundle.ref.key, bundle.ref.version)
        if identity in self._bundles:
            raise ValueError(f"Duplicate behavior bundle: {identity}")
        self._bundles[identity] = bundle

    def get(self, ref: BehaviorBundleRef) -> BehaviorBundleDefinition | None:
        return self._bundles.get((ref.key, ref.version))

    def require(self, ref: BehaviorBundleRef) -> BehaviorBundleDefinition:
        bundle = self.get(ref)
        if bundle is None:
            raise KeyError(f"Behavior bundle is not registered: {ref.key}@{ref.version}")
        return bundle


STARFIRE_BEHAVIOR_DEFINITION = BehaviorBundleDefinition(
    ref=BehaviorBundleRef(key="starfire", version="1"),
    interaction_tools={
        "negotiate_support": frozenset({"negotiate_village_support"}),
        "reconnaissance": frozenset({"start_recon_operation"}),
        "clear_threat": frozenset({"start_military_operation"}),
        "disrupt_supply": frozenset({"start_military_operation"}),
        "repair": frozenset({"start_outpost_repair"}),
        "test_trade_route": frozenset({"start_trade_route_test"}),
    },
)

DEFAULT_BEHAVIOR_BUNDLES = BehaviorBundleRegistry((STARFIRE_BEHAVIOR_DEFINITION,))

__all__ = [
    "DEFAULT_BEHAVIOR_BUNDLES",
    "STARFIRE_BEHAVIOR_DEFINITION",
    "BehaviorBundleDefinition",
    "BehaviorBundleRegistry",
]
