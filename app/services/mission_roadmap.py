"""Player-facing strategic roadmap derived from public Scenario metadata and Knowledge."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from app.domain.formal_goal import FormalGoalContractV1, FormalGoalSourceKind
from app.domain.scenario_v2 import (
    DerivedStateDependencyV2,
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

    def project_formal_goal(
        self,
        definition: ScenarioDefinitionV2,
        contract: FormalGoalContractV1,
        known_facts: dict[tuple[str, str], StrictScalar],
        known_resources: dict[str, object] | None = None,
        known_derived: dict[str, StrictScalar | None] | None = None,
        *,
        goal_description: str = "",
    ) -> MissionRoadmap:
        """Project one frozen contract without making the roadmap authoritative.

        PREDEFINED contracts retain the authored prerequisite/subsumption
        presentation.  AD_HOC_DYNAMIC contracts receive one flat Goal stage;
        their backend identities are preserved on each visible requirement.
        Hidden gated requirements are omitted exactly as they are from the
        ordinary Objective roadmap.
        """

        if contract.source_kind == FormalGoalSourceKind.PREDEFINED:
            roadmap = self.project(
                definition,
                tuple(item.objective_key for item in contract.predefined_objectives),
                known_facts,
                known_resources,
                known_derived,
            )
            identities = {
                (item.source_objective_key, item.source_requirement_key): item.identity
                for item in contract.completion_requirements
            }
            stages: list[MissionRoadmapStage] = []
            for stage in roadmap.stages:
                projected_requirements: list[dict[str, object]] = []
                for requirement in stage.requirements:
                    raw_key = requirement.get("key")
                    requirement_key = raw_key if isinstance(raw_key, str) else None
                    source_key = (
                        stage.objective_key,
                        requirement_key,
                    )
                    identity = identities.get(source_key)
                    if identity is None:
                        identity = f"planning/{stage.key}/{source_key[1] or 'requirement'}"
                    projected = dict(requirement)
                    projected["identity"] = identity
                    projected["key"] = identity
                    if "kind" not in projected:
                        projected["kind"] = (
                            "RESOURCE_AT_LEAST" if "resource_key" in projected else "FACT"
                        )
                    projected_requirements.append(projected)
                stages.append(
                    MissionRoadmapStage(
                        key=stage.key,
                        name=stage.name,
                        description=stage.description,
                        status=stage.status,
                        objective_key=stage.objective_key,
                        requirements=tuple(projected_requirements),
                    )
                )
            return MissionRoadmap(stages=tuple(stages))

        requirements = tuple(contract.completion_requirements)
        visible = tuple(
            item
            for item in requirements
            if item.requirement.knowledge_gate is None
            or known_facts.get(
                (
                    item.requirement.knowledge_gate.node_key,
                    item.requirement.knowledge_gate.fact_key,
                )
            )
            in item.requirement.knowledge_gate.accepted_values
        )
        visible_requirements = self._visible(
            self._expand_derived_requirements(
                definition,
                tuple(item.requirement for item in visible),
                known_facts,
            ),
            known_facts,
        )
        identities_by_requirement_key = {item.requirement.key: item.identity for item in visible}
        resources = known_resources or {}
        completed = self._satisfied(
            visible_requirements,
            known_facts,
            resources,
            known_derived or {},
        )
        return MissionRoadmap(
            stages=(
                MissionRoadmapStage(
                    key=f"goal:{contract.content_hash[:16]}",
                    name=goal_description.strip() or "Custom Goal",
                    description="Player-visible typed Goal requirements.",
                    status=(
                        MissionRoadmapStageStatus.COMPLETED
                        if completed
                        else MissionRoadmapStageStatus.CURRENT
                    ),
                    objective_key=None,
                    requirements=tuple(
                        self._project_requirement(
                            requirement,
                            resources,
                            identity=identities_by_requirement_key.get(
                                requirement.key,
                                f"planning/goal/{requirement.key}",
                            ),
                            definition=definition,
                            known_derived=known_derived or {},
                        )
                        for requirement in visible_requirements
                    ),
                ),
            )
        )

    def project(
        self,
        definition: ScenarioDefinitionV2,
        objective_scope_keys: tuple[str, ...],
        known_facts: dict[tuple[str, str], StrictScalar],
        known_resources: dict[str, object] | None = None,
        known_derived: dict[str, StrictScalar | None] | None = None,
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
        visible = [
            self._visible(
                self._expand_derived_requirements(definition, item.requirements, known_facts),
                known_facts,
            )
            for item in ordered
        ]
        completed = [
            self._satisfied(item, known_facts, resources, known_derived or {}) for item in visible
        ]
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
                        self._project_requirement(
                            requirement,
                            resources,
                            definition=definition,
                            known_derived=known_derived or {},
                        )
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

    @classmethod
    def _expand_derived_requirements(
        cls,
        definition: ScenarioDefinitionV2,
        requirements: tuple[ObjectiveRequirementV2, ...],
        known_facts: dict[tuple[str, str], StrictScalar],
    ) -> tuple[ObjectiveRequirementV2, ...]:
        """Add public Derived State inputs whose own Knowledge gates are open.

        A roadmap may show a public Derived State before all of its inputs are
        known.  Its authored dependencies become player-facing only when the
        dependency gate is satisfied in Knowledge.  This mirrors the planner
        relevance boundary without exposing a gated Scenario input early.
        """

        expanded: list[ObjectiveRequirementV2] = []
        emitted: set[tuple[object, ...]] = set()
        visiting: set[str] = set()

        def requirement_identity(requirement: ObjectiveRequirementV2) -> tuple[object, ...]:
            gate = requirement.knowledge_gate
            return (
                requirement.kind,
                requirement.node_key,
                requirement.fact_key,
                requirement.accepted_values,
                requirement.region_key,
                requirement.resource_key,
                requirement.minimum,
                requirement.derived_key,
                (
                    (gate.node_key, gate.fact_key, gate.accepted_values)
                    if gate is not None
                    else None
                ),
            )

        def is_visible(requirement: ObjectiveRequirementV2) -> bool:
            gate = requirement.knowledge_gate
            return (
                gate is None
                or known_facts.get((gate.node_key, gate.fact_key)) in gate.accepted_values
            )

        def append(requirement: ObjectiveRequirementV2) -> None:
            identity = requirement_identity(requirement)
            if identity in emitted:
                return
            emitted.add(identity)
            expanded.append(requirement)
            if (
                requirement.kind != ObjectiveRequirementKind.DERIVED_STATE
                or not is_visible(requirement)
                or requirement.derived_key is None
            ):
                return
            state = definition.derived_state_definitions.get(requirement.derived_key)
            if state is None or state.key in visiting:
                return
            visiting.add(state.key)
            for index, dependency in enumerate(state.dependencies):
                append(cls._derived_dependency_requirement(state.key, index, dependency))
            visiting.remove(state.key)

        for requirement in requirements:
            append(requirement)
        return tuple(expanded)

    @staticmethod
    def _derived_dependency_requirement(
        derived_key: str,
        index: int,
        dependency: DerivedStateDependencyV2,
    ) -> ObjectiveRequirementV2:
        """Convert one authored Derived input to the public roadmap shape."""

        digest = hashlib.sha256(
            repr((derived_key, index, dependency.model_dump(mode="json"))).encode()
        ).hexdigest()[:16]
        return ObjectiveRequirementV2(
            key=f"derived_dependency_{digest}",
            kind=ObjectiveRequirementKind(dependency.kind.value),
            node_key=dependency.node_key,
            fact_key=dependency.fact_key,
            accepted_values=dependency.accepted_values,
            region_key=dependency.region_key,
            resource_key=dependency.resource_key,
            minimum=dependency.minimum,
            derived_key=dependency.derived_key,
            knowledge_gate=dependency.knowledge_gate,
            description="Derived State dependency.",
        )

    @staticmethod
    def _satisfied(
        requirements: tuple[ObjectiveRequirementV2, ...],
        known_facts: dict[tuple[str, str], StrictScalar],
        known_resources: dict[str, object],
        known_derived: dict[str, StrictScalar | None],
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
            if requirement.kind == ObjectiveRequirementKind.DERIVED_STATE:
                assert requirement.derived_key is not None
                if known_derived.get(requirement.derived_key) not in requirement.accepted_values:
                    return False
                continue
            current = MissionRoadmapProjector._known_resource_amount(requirement, known_resources)
            assert requirement.minimum is not None
            if (
                MissionRoadmapProjector._known_resource_status(requirement, known_resources)
                == "UNKNOWN"
            ):
                return False
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
    def _known_resource_status(
        requirement: ObjectiveRequirementV2,
        known_resources: dict[str, object],
    ) -> str:
        assert requirement.resource_key is not None and requirement.region_key is not None
        raw = known_resources.get(requirement.resource_key, {})
        if not isinstance(raw, dict):
            return "UNKNOWN"
        regions = raw.get("regions", {})
        if not isinstance(regions, dict):
            return "UNKNOWN"
        region = regions.get(requirement.region_key)
        if not isinstance(region, dict):
            return "UNKNOWN"
        inventory_visibility = region.get("resource_inventory_visibility")
        survey_completed = region.get("resource_survey_completed")
        if inventory_visibility is not None and inventory_visibility != "VISIBLE":
            return "UNKNOWN"
        if survey_completed is not None and survey_completed is not True:
            return "UNKNOWN"
        if "known_available" not in region:
            return "UNKNOWN"
        return "KNOWN_ZERO" if int(region.get("known_available", 0)) == 0 else "KNOWN"

    @staticmethod
    def _project_requirement(
        requirement: ObjectiveRequirementV2,
        known_resources: dict[str, object],
        *,
        identity: str | None = None,
        definition: ScenarioDefinitionV2 | None = None,
        known_derived: dict[str, StrictScalar | None] | None = None,
    ) -> dict[str, object]:
        result = requirement.model_dump(mode="json", exclude={"knowledge_gate"})
        result["kind"] = requirement.kind.value
        if identity is not None:
            result["identity"] = identity
            result["key"] = identity
        if definition is not None:
            result["description"] = MissionRoadmapProjector._dynamic_requirement_description(
                definition,
                requirement,
            )
        if requirement.kind == ObjectiveRequirementKind.RESOURCE_AT_LEAST:
            status = MissionRoadmapProjector._known_resource_status(
                requirement,
                known_resources,
            )
            result["current_known_available"] = MissionRoadmapProjector._known_resource_amount(
                requirement, known_resources
            )
            result["knowledge_status"] = status
            if status == "UNKNOWN":
                result["current_known_available"] = None
        elif requirement.kind == ObjectiveRequirementKind.DERIVED_STATE:
            assert requirement.derived_key is not None
            current = (known_derived or {}).get(requirement.derived_key)
            result["current_known_value"] = current
            result["knowledge_status"] = "UNKNOWN" if current is None else "KNOWN"
        return result

    @staticmethod
    def _dynamic_requirement_description(
        definition: ScenarioDefinitionV2,
        requirement: ObjectiveRequirementV2,
    ) -> str:
        """Render dynamic requirements from authored display metadata only."""

        if requirement.kind == ObjectiveRequirementKind.FACT:
            if requirement.node_key is not None and requirement.fact_key is not None:
                node = definition.world.node(requirement.node_key)
                fact = node.fact(requirement.fact_key) if node is not None else None
                if node is not None and fact is not None:
                    return f"{node.name}: {fact.name} reaches the requested state."
            return "The requested Fact reaches the requested state."

        if requirement.kind == ObjectiveRequirementKind.DERIVED_STATE:
            if requirement.derived_key is not None:
                state = definition.derived_state_definitions.get(requirement.derived_key)
                if state is not None:
                    return state.description or f"{state.name} reaches the requested state."
            return "The requested world capability reaches the requested state."

        if requirement.region_key is not None and requirement.resource_key is not None:
            region = definition.world.node(requirement.region_key)
            resource = next(
                (
                    item
                    for item in definition.world.resources
                    if item.key == requirement.resource_key
                ),
                None,
            )
            if region is not None and resource is not None and requirement.minimum is not None:
                return f"{region.name}: {resource.name} reaches at least {requirement.minimum}."
        return "The requested resource reserve reaches its target."


__all__ = [
    "MissionRoadmap",
    "MissionRoadmapProjector",
    "MissionRoadmapStage",
    "MissionRoadmapStageStatus",
]
