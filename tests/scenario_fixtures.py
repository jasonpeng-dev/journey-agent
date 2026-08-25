"""Scenario documents used only by generic architecture tests."""

from pathlib import Path
from typing import Any

import yaml

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.services.scenarios import ScenarioService

_ROOT = Path(__file__).resolve().parents[1]


def load_test_scenario(path: Path) -> ScenarioDefinitionV2:
    payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Test Scenario fixture must contain one document")
    return ScenarioDefinitionV2.model_validate(payload)


STARFIRE_TEST = load_test_scenario(_ROOT / "migrations" / "fixtures" / "c800_legacy_starfire.yaml")
MEDICAL_TEST = load_test_scenario(Path(__file__).with_name("fixtures") / "generic_medical.yaml")
LINJIANG_V1_TEST = load_test_scenario(
    Path(__file__).with_name("fixtures") / "generic_linjiang_v1.yaml"
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


__all__ = [
    "LINJIANG_V1_TEST",
    "MEDICAL_TEST",
    "STARFIRE_TEST",
    "create_test_scenario",
    "load_test_scenario",
]
