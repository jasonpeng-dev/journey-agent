"""Planning-only projection of the frozen Formal Goal contract.

The legacy planning helpers still use ``ObjectiveDefinitionV2`` as their
internal shape.  This adapter lets them consume a Formal Goal without making
the authored Objective catalog authoritative again.  It is never persisted
and it carries only the typed requirements and planning compatibility that are
already present in the frozen contract.
"""

from __future__ import annotations

import hashlib

from app.domain.formal_goal import FormalGoalContractV1, FormalGoalSourceKind
from app.domain.scenario_v2 import (
    ObjectiveDefinitionV2,
    ObjectivePrerequisiteV2,
    ScenarioDefinitionV2,
)


def formal_goal_planning_objectives(
    contract: FormalGoalContractV1,
    definition: ScenarioDefinitionV2 | None = None,
    *,
    goal_description: str = "Formal Goal",
) -> tuple[ObjectiveDefinitionV2, ...]:
    """Return a transient typed projection for existing planning algorithms.

    PREDEFINED contracts retain one projection per authored Objective so their
    planning prerequisites and display guidance stay grouped.  AD_HOC goals
    are one implicit-AND projection; their requirement keys are remapped to
    stable local keys because the reusable Objective requirement model requires
    unique keys within one Objective.  The Formal Goal identities remain the
    persisted authority and are not replaced by these local keys.
    """

    if contract.source_kind == FormalGoalSourceKind.PREDEFINED:
        authored = definition.objective_definitions if definition is not None else {}
        prerequisites_by_objective: dict[str, list[ObjectivePrerequisiteV2]] = {}
        for item in contract.planning_compatibility.prerequisites:
            prerequisites_by_objective.setdefault(item.objective_key, []).append(item.prerequisite)
        result: list[ObjectiveDefinitionV2] = []
        for source in contract.predefined_objectives:
            requirements = tuple(
                item.requirement
                for item in contract.completion_requirements
                if item.source_objective_key == source.objective_key
            )
            if not requirements:
                continue
            original = authored.get(source.objective_key)
            result.append(
                ObjectiveDefinitionV2(
                    key=source.objective_key,
                    name=original.name if original is not None else source.objective_key,
                    description=(
                        original.description if original is not None else source.objective_key
                    ),
                    completion_requirements=requirements,
                    prerequisites=tuple(
                        sorted(
                            prerequisites_by_objective.get(source.objective_key, []),
                            key=lambda item: item.key,
                        )
                    ),
                    planning_guidance=(
                        original.planning_guidance if original is not None else None
                    ),
                )
            )
        return tuple(result)

    requirements = tuple(
        item.requirement
        for item in sorted(contract.completion_requirements, key=lambda item: item.identity)
    )
    projected_requirements = tuple(
        requirement.model_copy(update={"key": _local_requirement_key(identity)})
        for identity, requirement in zip(
            sorted(item.identity for item in contract.completion_requirements),
            requirements,
            strict=True,
        )
    )
    return (
        ObjectiveDefinitionV2(
            key="dynamic_goal",
            name="Dynamic Goal",
            description=goal_description[:4000] or "Dynamic Goal",
            completion_requirements=projected_requirements,
        ),
    )


def _local_requirement_key(identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"dynamic_requirement_{digest}"


__all__ = ["formal_goal_planning_objectives"]
