"""Complete persistable Starfire Scenario definition."""

from app.domain.scenario import BehaviorBundleRef, ScenarioDefinition
from app.scenarios.starfire.definition import STARFIRE_WORLD
from app.scenarios.starfire.objective_catalog import STARFIRE_OBJECTIVE_CATALOG

STARFIRE_BEHAVIOR_BUNDLE = BehaviorBundleRef(key="starfire", version="1")

STARFIRE_SCENARIO_DEFINITION = ScenarioDefinition(
    world=STARFIRE_WORLD,
    objective_catalog_version=STARFIRE_OBJECTIVE_CATALOG.catalog_version,
    objectives=tuple(STARFIRE_OBJECTIVE_CATALOG.definitions.values()),
    behavior_bundle=STARFIRE_BEHAVIOR_BUNDLE,
)

__all__ = ["STARFIRE_BEHAVIOR_BUNDLE", "STARFIRE_SCENARIO_DEFINITION"]
