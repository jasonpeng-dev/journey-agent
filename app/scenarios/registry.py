"""Minimal read-only registry for built-in scenario world definitions."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.domain.world import WorldDefinition
from app.scenarios.contracts import (
    ScenarioFallbackPlans,
    ScenarioObjectiveEvaluator,
    ScenarioPlanningPolicy,
)
from app.scenarios.starfire.compatibility import (
    canonical_node_key,
    legacy_target_supports_interaction,
)
from app.scenarios.starfire.definition import STARFIRE_WORLD
from app.scenarios.starfire.fallback_plans import STARFIRE_FALLBACK_PLANS
from app.scenarios.starfire.objectives import STARFIRE_OBJECTIVES
from app.scenarios.starfire.planning_policy import STARFIRE_PLANNING_POLICY

NodeKeyResolver = Callable[[str], str]
TargetInteractionGuard = Callable[[str, str], bool]


@dataclass(frozen=True, slots=True)
class ScenarioWorldBinding:
    """Connect a pure world definition to scenario-specific compatibility rules."""

    world: WorldDefinition
    resolve_node_key: NodeKeyResolver
    raw_target_supports_interaction: TargetInteractionGuard


@dataclass(frozen=True, slots=True)
class ScenarioBinding(ScenarioWorldBinding):
    """Bind a world to its small set of scenario-specific runtime policies."""

    planning_policy: ScenarioPlanningPolicy
    objective_evaluator: ScenarioObjectiveEvaluator
    fallback_plans: ScenarioFallbackPlans


STARFIRE_SCENARIO = ScenarioBinding(
    world=STARFIRE_WORLD,
    resolve_node_key=canonical_node_key,
    raw_target_supports_interaction=legacy_target_supports_interaction,
    planning_policy=STARFIRE_PLANNING_POLICY,
    objective_evaluator=STARFIRE_OBJECTIVES,
    fallback_plans=STARFIRE_FALLBACK_PLANS,
)

SCENARIOS: Mapping[str, ScenarioBinding] = MappingProxyType({STARFIRE_WORLD.key: STARFIRE_SCENARIO})


SCENARIO_WORLDS: Mapping[str, ScenarioWorldBinding] = MappingProxyType(dict(SCENARIOS))


def scenario_world(scenario_key: str) -> ScenarioWorldBinding | None:
    return SCENARIO_WORLDS.get(scenario_key)


def scenario_binding(scenario_key: str) -> ScenarioBinding | None:
    return SCENARIOS.get(scenario_key)
