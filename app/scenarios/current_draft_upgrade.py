"""Narrow, idempotent upgrades for the current editable Linjiang Draft."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import Scenario, ScenarioDraft
from app.services.scenarios import ScenarioLifecycleError, ScenarioService

LINJIANG_SCENARIO_KEY = "linjiang_infrastructure_recovery_v2_0"


@dataclass(frozen=True, slots=True)
class CurrentDraftUpgradeResult:
    scenario_id: str
    changed: bool
    revision_before: int
    revision_after: int
    relation_type_count: int
    relation_count: int
    quick_input_count: int
    objective_count: int


def upgrade_current_linjiang_draft(
    db: Session,
    *,
    expected_revision: int,
    canonical: ScenarioDefinitionV2,
) -> CurrentDraftUpgradeResult:
    """Apply only current authored catalogs while preserving all other Draft content."""

    if canonical.metadata.key != LINJIANG_SCENARIO_KEY:
        raise ScenarioLifecycleError(
            "SCENARIO_DRAFT_UPGRADE_IDENTITY_MISMATCH",
            "The canonical definition is not the Linjiang Scenario",
        )
    scenario = db.scalar(select(Scenario).where(Scenario.key == LINJIANG_SCENARIO_KEY))
    if scenario is None:
        raise ScenarioLifecycleError(
            "SCENARIO_NOT_FOUND",
            "The Linjiang Scenario does not exist",
        )
    draft = db.get(ScenarioDraft, scenario.id)
    if draft is None:
        raise ScenarioLifecycleError(
            "SCENARIO_DRAFT_NOT_FOUND",
            "The Linjiang Scenario has no editable Draft",
        )
    if draft.revision != expected_revision:
        raise ScenarioLifecycleError(
            "SCENARIO_DRAFT_CONFLICT",
            "The Linjiang Draft revision changed before the upgrade",
        )

    before = deepcopy(draft.definition_document)
    document = deepcopy(before)
    world = document.get("world")
    goal_resolution = document.get("goal_resolution")
    if not isinstance(world, dict) or not isinstance(goal_resolution, dict):
        raise ScenarioLifecycleError(
            "SCENARIO_DRAFT_UPGRADE_SHAPE_INVALID",
            "The Linjiang Draft lacks World or Goal Resolution authoring data",
        )

    relation_instances = deepcopy(world.get("relations"))
    world["relation_types"] = [
        item.model_dump(mode="json") for item in canonical.world.relation_types
    ]
    goal_resolution["quick_inputs"] = list(canonical.goal_resolution.quick_inputs)
    document.pop("objectives", None)
    if world.get("relations") != relation_instances:
        raise ScenarioLifecycleError(
            "SCENARIO_DRAFT_UPGRADE_RELATIONS_CHANGED",
            "The Draft upgrade must not rewrite Relation instances",
        )

    # Fail before persistence if the exact transformed current document is not
    # valid. Historical snapshots are never passed to replace_draft.
    validated = ScenarioDefinitionV2.model_validate(document)
    if validated.metadata.key != LINJIANG_SCENARIO_KEY:
        raise ScenarioLifecycleError(
            "SCENARIO_DRAFT_UPGRADE_IDENTITY_MISMATCH",
            "The Draft document is not the Linjiang Scenario",
        )

    changed = document != before
    revision_after = draft.revision
    if changed:
        draft = ScenarioService(db).replace_draft(
            scenario.id,
            expected_revision=expected_revision,
            definition_document=document,
        )
        revision_after = draft.revision

    return CurrentDraftUpgradeResult(
        scenario_id=str(scenario.id),
        changed=changed,
        revision_before=expected_revision,
        revision_after=revision_after,
        relation_type_count=len(validated.world.relation_types),
        relation_count=len(validated.world.relations),
        quick_input_count=len(validated.goal_resolution.quick_inputs),
        objective_count=len(validated.objectives),
    )


__all__ = [
    "LINJIANG_SCENARIO_KEY",
    "CurrentDraftUpgradeResult",
    "upgrade_current_linjiang_draft",
]
