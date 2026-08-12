"""Immutable definitions shared by persisted Scenario drafts and versions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from app.domain.world import WorldDefinition
from app.scenarios.contracts import ObjectiveDefinition, ObjectiveKey


@dataclass(frozen=True, slots=True)
class BehaviorBundleRef:
    """Exact executable behavior implementation required by a ScenarioVersion."""

    key: str
    version: str

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.version.strip():
            raise ValueError("behavior bundle key and version must not be blank")


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    """Complete author-owned Scenario content before publication metadata is added."""

    world: WorldDefinition
    objective_catalog_version: str
    objectives: tuple[ObjectiveDefinition, ...]
    behavior_bundle: BehaviorBundleRef

    def __post_init__(self) -> None:
        object.__setattr__(self, "objectives", tuple(self.objectives))
        if not self.objective_catalog_version.strip():
            raise ValueError("objective catalog version must not be blank")
        keys = [objective.key for objective in self.objectives]
        if not keys:
            raise ValueError("scenario definition must contain at least one objective")
        if len(set(keys)) != len(keys):
            raise ValueError("scenario objectives must use unique keys")

    @property
    def objective_definitions(self) -> Mapping[ObjectiveKey, ObjectiveDefinition]:
        return MappingProxyType({objective.key: objective for objective in self.objectives})


@dataclass(frozen=True, slots=True)
class ScenarioVersionSnapshot:
    """Verified immutable published definition addressed by its exact version ID."""

    id: UUID
    scenario_id: UUID
    version_number: int
    schema_version: int
    content_hash: str
    published_at: datetime
    definition: ScenarioDefinition

    def __post_init__(self) -> None:
        if self.version_number < 1 or self.schema_version < 1:
            raise ValueError("published Scenario version numbers must be positive")
        if len(self.content_hash) != 64:
            raise ValueError("published Scenario content hash must be SHA-256")


__all__ = ["BehaviorBundleRef", "ScenarioDefinition", "ScenarioVersionSnapshot"]
