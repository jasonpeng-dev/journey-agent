"""Immutable published ScenarioVersion snapshot contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.scenario_v2 import ScenarioDefinitionV2


@dataclass(frozen=True, slots=True)
class ScenarioVersionSnapshot:
    """Verified immutable v2 definition addressed by its exact version ID."""

    id: UUID
    scenario_id: UUID
    version_number: int
    schema_version: int
    content_hash: str
    published_at: datetime
    definition: ScenarioDefinitionV2

    def __post_init__(self) -> None:
        if self.version_number < 1 or self.schema_version != 2:
            raise ValueError("published Scenario versions must use schema v2")
        if len(self.content_hash) != 64:
            raise ValueError("published Scenario content hash must be SHA-256")


__all__ = ["ScenarioVersionSnapshot"]
