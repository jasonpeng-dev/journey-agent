"""Generic, idempotent upgrades for a current editable Scenario Draft."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import Scenario, ScenarioDraft
from app.services.scenarios import ScenarioLifecycleError, ScenarioService


@dataclass(frozen=True, slots=True)
class ScenarioDraftUpgradeResult:
    scenario_id: str
    changed: bool
    revision_before: int
    revision_after: int
    relation_type_count: int
    relation_count: int
    quick_input_count: int
    objective_count: int


def upgrade_scenario_draft_to_current_definition(
    db: Session,
    *,
    target_scenario_id: UUID,
    expected_revision: int,
    canonical: ScenarioDefinitionV2,
) -> ScenarioDraftUpgradeResult:
    """Apply current authored semantic catalogs to one explicitly selected Draft.

    The canonical definition supplies only the authored relation-type catalog and
    quick inputs. The target Draft remains the source of truth for its identity,
    relations, and all other scenario content.
    """

    scenario = db.get(Scenario, target_scenario_id)
    if scenario is None:
        raise ScenarioLifecycleError(
            "SCENARIO_NOT_FOUND",
            "The target Scenario does not exist",
        )
    draft = db.get(ScenarioDraft, target_scenario_id)
    if draft is None:
        raise ScenarioLifecycleError(
            "SCENARIO_DRAFT_NOT_FOUND",
            "The target Scenario has no editable Draft",
        )
    if draft.revision != expected_revision:
        raise ScenarioLifecycleError(
            "SCENARIO_DRAFT_CONFLICT",
            "The target Draft revision changed before the upgrade",
        )

    before = deepcopy(draft.definition_document)
    document = deepcopy(before)
    world = document.get("world")
    goal_resolution = document.get("goal_resolution")
    if not isinstance(world, dict) or not isinstance(goal_resolution, dict):
        raise ScenarioLifecycleError(
            "SCENARIO_DRAFT_UPGRADE_SHAPE_INVALID",
            "The Draft lacks World or Goal Resolution authoring data",
        )

    relation_instances = deepcopy(world.get("relations"))
    world["relation_types"] = [
        item.model_dump(mode="json") for item in canonical.world.relation_types
    ]
    goal_resolution["quick_inputs"] = list(canonical.goal_resolution.quick_inputs)
    if not canonical.objectives:
        document.pop("objectives", None)
    if world.get("relations") != relation_instances:
        raise ScenarioLifecycleError(
            "SCENARIO_DRAFT_UPGRADE_RELATIONS_CHANGED",
            "The Draft upgrade must not rewrite Relation instances",
        )

    # Fail before persistence if the exact transformed current document is not
    # valid. Historical snapshots are never passed to replace_draft.
    validated = ScenarioDefinitionV2.model_validate(document)
    changed = document != before
    revision_after = draft.revision
    if changed:
        draft = ScenarioService(db).replace_draft(
            target_scenario_id,
            expected_revision=expected_revision,
            definition_document=document,
        )
        revision_after = draft.revision

    return ScenarioDraftUpgradeResult(
        scenario_id=str(target_scenario_id),
        changed=changed,
        revision_before=expected_revision,
        revision_after=revision_after,
        relation_type_count=len(validated.world.relation_types),
        relation_count=len(validated.world.relations),
        quick_input_count=len(validated.goal_resolution.quick_inputs),
        objective_count=len(validated.objectives),
    )


__all__ = [
    "ScenarioDraftUpgradeResult",
    "upgrade_scenario_draft_to_current_definition",
]
