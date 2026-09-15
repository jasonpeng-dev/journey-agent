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
    # The exact repository loader may verify both the current semantic hash
    # and a safe historical raw-payload hash. Manual/unit snapshots leave
    # this empty and are validated against the current semantic hash.
    verified_content_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.version_number < 1 or self.schema_version != 2:
            raise ValueError("published Scenario versions must use schema v2")
        if len(self.content_hash) != 64:
            raise ValueError("published Scenario content hash must be SHA-256")
        if self.verified_content_hashes and self.content_hash not in self.verified_content_hashes:
            raise ValueError("published Scenario content hash must be verified")
        if any(len(value) != 64 for value in self.verified_content_hashes):
            raise ValueError("verified Scenario content hashes must be SHA-256")


__all__ = ["ScenarioVersionSnapshot"]
