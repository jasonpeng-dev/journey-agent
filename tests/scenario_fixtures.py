"""Scenario documents used only by generic architecture tests."""

from pathlib import Path
from typing import Any

import yaml

from app.agent.generic import GenericGoalResolution
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.services.scenarios import ScenarioService

_ROOT = Path(__file__).resolve().parents[1]


def load_test_scenario(path: Path) -> ScenarioDefinitionV2:
    payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Test Scenario fixture must contain one document")
    return ScenarioDefinitionV2.model_validate(payload)


GENERIC_TEST = load_test_scenario(Path(__file__).with_name("fixtures") / "generic_contract.yaml")
LINJIANG_V2_TEST = load_test_scenario(
    _ROOT / "app" / "scenarios" / "data" / "linjiang_infrastructure_recovery_v2_0.yaml"
)


def create_test_scenario(
    session,
    definition: ScenarioDefinitionV2,
    *,
    key: str,
    name: str,
):
    """Persist a disposable scenario fixture for tests that need a runtime row."""

    scenario = ScenarioService(session).create_from_definition(
        key=key,
        name=name,
        definition=definition,
    )
    session.commit()
    return scenario


def predefined_goal_resolution(objective_key: str) -> GenericGoalResolution:
    """Provide an explicit authored resolution for non-routing runtime tests.

    Current catalog-enabled Scenarios intentionally require the Dynamic Goal
    pipeline for natural-language input.  Tests that exercise the existing
    Objective-backed planning/runtime behavior can still supply the already
    resolved authored contract explicitly.
    """

    return GenericGoalResolution(
        status="RESOLVED",
        objective_key=objective_key,
        objective_keys=(objective_key,),
        source="DETERMINISTIC",
    )


__all__ = [
    "GENERIC_TEST",
    "LINJIANG_V2_TEST",
    "create_test_scenario",
    "load_test_scenario",
    "predefined_goal_resolution",
]
