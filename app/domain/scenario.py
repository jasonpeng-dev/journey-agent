"""Immutable definitions shared by persisted Scenario drafts and versions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

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


__all__ = ["BehaviorBundleRef", "ScenarioDefinition"]
