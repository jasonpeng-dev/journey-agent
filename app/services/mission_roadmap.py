"""Player-facing strategic roadmap derived from public Scenario metadata and Knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.scenario_v2 import (
    ObjectiveDefinitionV2,
    ObjectiveRequirementKind,
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
    requirements: tuple[dict[str, object], ...] = ()


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
        known_resources: dict[str, object] | None = None,
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

        resources = known_resources or {}
        visible = [self._visible(item.requirements, known_facts) for item in ordered]
        completed = [self._satisfied(item, known_facts, resources) for item in visible]
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
                    requirements=tuple(
                        self._project_requirement(requirement, resources)
                        for requirement in visible[index]
                    ),
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
                    completion.fact_ref is not None
                    and completion.fact_ref == requirement.fact_ref
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
        known_resources: dict[str, object],
    ) -> bool:
        if not requirements:
            return False
        for requirement in requirements:
            if requirement.kind == ObjectiveRequirementKind.FACT:
                assert requirement.node_key is not None and requirement.fact_key is not None
                if known_facts.get((requirement.node_key, requirement.fact_key)) not in (
                    requirement.accepted_values
                ):
                    return False
                continue
            current = MissionRoadmapProjector._known_resource_amount(requirement, known_resources)
            assert requirement.minimum is not None
            if current < requirement.minimum:
                return False
        return True

    @staticmethod
    def _visible(
        requirements: tuple[ObjectiveRequirementV2, ...],
        known_facts: dict[tuple[str, str], StrictScalar],
    ) -> tuple[ObjectiveRequirementV2, ...]:
        return tuple(
            requirement
            for requirement in requirements
            if requirement.knowledge_gate is None
            or known_facts.get(
                (
                    requirement.knowledge_gate.node_key,
                    requirement.knowledge_gate.fact_key,
                )
            )
            in requirement.knowledge_gate.accepted_values
        )

    @staticmethod
    def _known_resource_amount(
        requirement: ObjectiveRequirementV2, known_resources: dict[str, object]
    ) -> int:
        assert requirement.resource_key is not None and requirement.region_key is not None
        raw = known_resources.get(requirement.resource_key, {})
        if not isinstance(raw, dict):
            return 0
        region = raw.get("regions", {})
        if not isinstance(region, dict):
            return 0
        summary = region.get(requirement.region_key, {})
        return int(summary.get("known_available", 0)) if isinstance(summary, dict) else 0

    @staticmethod
    def _project_requirement(
        requirement: ObjectiveRequirementV2, known_resources: dict[str, object]
    ) -> dict[str, object]:
        result = requirement.model_dump(mode="json", exclude={"knowledge_gate"})
        if requirement.kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST:
            result["current_known_available"] = MissionRoadmapProjector._known_resource_amount(
                requirement, known_resources
            )
        return result


__all__ = [
    "MissionRoadmap",
    "MissionRoadmapProjector",
    "MissionRoadmapStage",
    "MissionRoadmapStageStatus",
]
