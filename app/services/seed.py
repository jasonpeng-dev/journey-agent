"""Seed the current built-in Scenario without mutating existing versions."""

from sqlalchemy.orm import Session

from app.infrastructure.db.models import Scenario
from app.scenarios.builtin import (
    LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    ensure_builtin_scenario,
)


def seed_demo_world(db: Session) -> Scenario:
    return ensure_builtin_scenario(db, LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0)


def seed_scenario_definitions(db: Session) -> None:
    seed_demo_world(db)
