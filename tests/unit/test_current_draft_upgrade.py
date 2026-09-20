from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import ScenarioDraft, ScenarioVersion
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0
from app.scenarios.current_draft_upgrade import upgrade_current_linjiang_draft
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.scenarios import ScenarioLifecycleError, ScenarioService
from tests.scenario_fixtures import LINJIANG_LEGACY_OBJECTIVES_TEST


def _published_legacy_linjiang(session: Session):  # type: ignore[no-untyped-def]
    document = LINJIANG_LEGACY_OBJECTIVES_TEST.model_dump(mode="json")
    document["world"]["relation_types"] = []
    document["goal_resolution"].pop("quick_inputs", None)
    legacy_definition = ScenarioDefinitionV2.model_validate(document)
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(
        legacy_definition
    )
    published = ScenarioService(session).publish_draft(
        scenario.id,
        expected_revision=1,
    ).version
    session.flush()
    return scenario, published


def test_current_linjiang_draft_upgrade_is_targeted_idempotent_and_version_safe(
    session: Session,
) -> None:
    scenario, published = _published_legacy_linjiang(session)
    draft_before = deepcopy(session.get(ScenarioDraft, scenario.id).definition_document)
    snapshot_before = deepcopy(published.snapshot_document)
    hash_before = published.content_hash

    result = upgrade_current_linjiang_draft(
        session,
        expected_revision=1,
        canonical=LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )
    session.flush()

    assert result.changed is True
    assert (result.revision_before, result.revision_after) == (1, 2)
    assert (
        result.relation_type_count,
        result.relation_count,
        result.quick_input_count,
        result.objective_count,
    ) == (4, 48, 6, 0)
    draft = session.get(ScenarioDraft, scenario.id)
    assert draft is not None
    assert draft.definition_document["world"]["relations"] == draft_before["world"][
        "relations"
    ]
    assert [item["key"] for item in draft.definition_document["world"]["relation_types"]] == [
        item.key for item in LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0.world.relation_types
    ]
    assert "objectives" not in draft.definition_document

    session.refresh(published)
    assert published.snapshot_document == snapshot_before
    assert published.content_hash == hash_before
    assert session.scalar(
        select(func.count())
        .select_from(ScenarioVersion)
        .where(ScenarioVersion.scenario_id == scenario.id)
    ) == 1

    repeated = upgrade_current_linjiang_draft(
        session,
        expected_revision=2,
        canonical=LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )
    assert repeated.changed is False
    assert (repeated.revision_before, repeated.revision_after) == (2, 2)


def test_current_linjiang_draft_upgrade_rejects_stale_revision(session: Session) -> None:
    _published_legacy_linjiang(session)

    with pytest.raises(ScenarioLifecycleError) as conflict:
        upgrade_current_linjiang_draft(
            session,
            expected_revision=99,
            canonical=LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        )

    assert conflict.value.code == "SCENARIO_DRAFT_CONFLICT"
