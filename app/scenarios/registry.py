"""Minimal read-only registry for built-in scenario world definitions."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.domain.world import WorldDefinition
from app.scenarios.starfire.compatibility import (
    canonical_node_key,
    legacy_target_supports_interaction,
)
from app.scenarios.starfire.definition import STARFIRE_WORLD

NodeKeyResolver = Callable[[str], str]
TargetInteractionGuard = Callable[[str, str], bool]


@dataclass(frozen=True, slots=True)
class ScenarioWorldBinding:
    """Connect a pure world definition to scenario-specific compatibility rules."""

    world: WorldDefinition
    resolve_node_key: NodeKeyResolver
    raw_target_supports_interaction: TargetInteractionGuard


SCENARIO_WORLDS: Mapping[str, ScenarioWorldBinding] = MappingProxyType(
    {
        STARFIRE_WORLD.key: ScenarioWorldBinding(
            world=STARFIRE_WORLD,
            resolve_node_key=canonical_node_key,
            raw_target_supports_interaction=legacy_target_supports_interaction,
        )
    }
)


def scenario_world(scenario_key: str) -> ScenarioWorldBinding | None:
    return SCENARIO_WORLDS.get(scenario_key)
