"""Player-facing strategic roadmap derived from public Scenario metadata and Knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.scenario_v2 import (
    ObjectiveDefinitionV2,
    ObjectiveRequirementV2,
    ScenarioDefinitionV2,
    StrictScalar,
)


class MissionRoadmapStageStatus(StrEnum):
    COMPLETED = "COMPLETED"
    CURRENT = "CURRENT"
    PENDING = "PENDING"


@dataclass(frozen=True, slots=True)
class MissionRoadmapStage:
    key: str
    name: str
    description: str
    status: MissionRoadmapStageStatus
    objective_key: str | None


@dataclass(frozen=True, slots=True)
class MissionRoadmap:
    stages: tuple[MissionRoadmapStage, ...]


@dataclass(frozen=True, slots=True)
class _StageDefinition:
    key: str
    name: str
    description: str
    requirements: tuple[ObjectiveRequirementV2, ...]
    objective_key: str | None


class MissionRoadmapProjector:
    """Build a non-executable roadmap without consulting hidden Truth or Action validation."""

    def project(
        self,
        definition: ScenarioDefinitionV2,
        objective_scope_keys: tuple[str, ...],
        known_facts: dict[tuple[str, str], StrictScalar],
    ) -> MissionRoadmap:
        objectives = {item.key: item for item in definition.objectives}
        ordered: list[_StageDefinition] = []
        emitted: set[str] = set()
        visiting: set[str] = set()

        def add_objective(objective: ObjectiveDefinitionV2) -> None:
            stage_key = f"objective:{objective.key}"
            if stage_key in emitted or objective.key in visiting:
                return
            visiting.add(objective.key)
            for subsumed_key in objective.subsumes:
                subsumed = objectives.get(subsumed_key)
                if subsumed is not None:
                    add_objective(subsumed)
            for prerequisite in objective.prerequisites:
                dependency = self._matching_objective(
                    definition,
                    prerequisite.requirements,
                    excluded_key=objective.key,
                )
                if dependency is not None:
                    add_objective(dependency)
                    continue
                prerequisite_key = f"prerequisite:{objective.key}:{prerequisite.key}"
                if prerequisite_key not in emitted:
                    ordered.append(
                        _StageDefinition(
                            key=prerequisite_key,
                            name=prerequisite.description,
                            description=" ".join(
                                requirement.description for requirement in prerequisite.requirements
                            ),
                            requirements=prerequisite.requirements,
                            objective_key=None,
                        )
                    )
                    emitted.add(prerequisite_key)
            visiting.remove(objective.key)
            if stage_key not in emitted:
                ordered.append(
                    _StageDefinition(
                        key=stage_key,
                        name=objective.name,
                        description=objective.description,
                        requirements=objective.completion_requirements,
                        objective_key=objective.key,
                    )
                )
                emitted.add(stage_key)

        for objective_key in objective_scope_keys:
            objective = objectives.get(objective_key)
            if objective is not None:
                add_objective(objective)

        completed = [self._satisfied(item.requirements, known_facts) for item in ordered]
        current_index = next((index for index, done in enumerate(completed) if not done), None)
        return MissionRoadmap(
            stages=tuple(
                MissionRoadmapStage(
                    key=item.key,
                    name=item.name,
                    description=item.description,
                    status=(
                        MissionRoadmapStageStatus.COMPLETED
                        if completed[index]
                        else MissionRoadmapStageStatus.CURRENT
                        if index == current_index
                        else MissionRoadmapStageStatus.PENDING
                    ),
                    objective_key=item.objective_key,
                )
                for index, item in enumerate(ordered)
            )
        )

    @staticmethod
    def _matching_objective(
        definition: ScenarioDefinitionV2,
        requirements: tuple[ObjectiveRequirementV2, ...],
        *,
        excluded_key: str,
    ) -> ObjectiveDefinitionV2 | None:
        matches: list[ObjectiveDefinitionV2] = []
        for candidate in definition.objectives:
            if candidate.key == excluded_key:
                continue
            if all(
                any(
                    completion.node_key == requirement.node_key
                    and completion.fact_key == requirement.fact_key
                    and bool(
                        set(completion.accepted_values).intersection(requirement.accepted_values)
                    )
                    for completion in candidate.completion_requirements
                )
                for requirement in requirements
            ):
                matches.append(candidate)
        return min(matches, key=lambda item: len(item.completion_requirements), default=None)

    @staticmethod
    def _satisfied(
        requirements: tuple[ObjectiveRequirementV2, ...],
        known_facts: dict[tuple[str, str], StrictScalar],
    ) -> bool:
        return all(
            known_facts.get((requirement.node_key, requirement.fact_key))
            in requirement.accepted_values
            for requirement in requirements
        )


__all__ = [
    "MissionRoadmap",
    "MissionRoadmapProjector",
    "MissionRoadmapStage",
    "MissionRoadmapStageStatus",
]
