"""Seed built-in Scenario data through the same generic v2 publication path."""

from sqlalchemy.orm import Session

from app.infrastructure.db.models import Scenario
from app.scenarios.builtin import (
    LINJIANG_INFRASTRUCTURE_RECOVERY_V1,
    MEDICAL_EMERGENCY_V2,
    STARFIRE_V2,
    require_builtin_v2_version,
)


def seed_demo_world(db: Session) -> Scenario:
    version = require_builtin_v2_version(db, STARFIRE_V2)
    scenario = db.get(Scenario, version.scenario_id)
    assert scenario is not None
    return scenario


def seed_scenario_definitions(db: Session) -> None:
    require_builtin_v2_version(db, STARFIRE_V2)
    require_builtin_v2_version(db, MEDICAL_EMERGENCY_V2)
    require_builtin_v2_version(db, LINJIANG_INFRASTRUCTURE_RECOVERY_V1)
