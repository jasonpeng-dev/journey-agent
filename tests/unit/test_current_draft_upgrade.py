from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import ScenarioDraft, ScenarioVersion
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0
from app.scenarios.current_draft_upgrade import (
    upgrade_scenario_draft_to_current_definition,
)
from app.services.scenarios import ScenarioLifecycleError, ScenarioService
from tests.scenario_fixtures import (
    GENERIC_TEST,
    LINJIANG_LEGACY_OBJECTIVES_TEST,
)


def _legacy_definition(
    definition: ScenarioDefinitionV2,
) -> ScenarioDefinitionV2:
    document = definition.model_dump(mode="json")
    document["world"]["relation_types"] = []
    document["goal_resolution"].pop("quick_inputs", None)
    return ScenarioDefinitionV2.model_validate(document)


def _published_legacy(
    session: Session,
    definition: ScenarioDefinitionV2,
    *,
    key: str,
    name: str,
):
    scenario = ScenarioService(session).create_from_definition(
        key=key,
        name=name,
        definition=definition,
    )
    published = (
        ScenarioService(session)
        .publish_draft(
            scenario.id,
            expected_revision=1,
        )
        .version
    )
    session.flush()
    return scenario, published


def _generic_current_source() -> ScenarioDefinitionV2:
    document = GENERIC_TEST.model_dump(mode="json")
    document["world"]["relation_types"] = [
        {
            "key": "contains",
            "name": "包含",
            "description": "空间包含关系",
        }
    ]
    document["goal_resolution"]["quick_inputs"] = [
        "诊断患者",
        "稳定病情",
    ]
    document.pop("objectives", None)
    return ScenarioDefinitionV2.model_validate(document)


def test_upgrade_targets_one_generic_scenario_and_preserves_sibling_and_versions(
    session: Session,
) -> None:
    target, published = _published_legacy(
        session,
        _legacy_definition(GENERIC_TEST),
        key="generic_upgrade_target",
        name="Generic upgrade target",
    )
    sibling, sibling_published = _published_legacy(
        session,
        _legacy_definition(GENERIC_TEST),
        key="generic_upgrade_sibling",
        name="Generic upgrade sibling",
    )
    source = _generic_current_source()
    target_before = deepcopy(session.get(ScenarioDraft, target.id).definition_document)
    sibling_before = deepcopy(session.get(ScenarioDraft, sibling.id).definition_document)
    published_snapshot_before = deepcopy(published.snapshot_document)
    published_hash_before = published.content_hash
    sibling_published_snapshot_before = deepcopy(sibling_published.snapshot_document)
    sibling_published_hash_before = sibling_published.content_hash

    result = upgrade_scenario_draft_to_current_definition(
        session,
        target_scenario_id=target.id,
        expected_revision=1,
        canonical=source,
    )
    session.flush()

    assert result.scenario_id == str(target.id)
    assert result.changed is True
    assert (result.revision_before, result.revision_after) == (1, 2)
    assert (
        result.relation_type_count,
        result.relation_count,
        result.quick_input_count,
        result.objective_count,
    ) == (1, 2, 2, 0)

    expected_target = deepcopy(target_before)
    expected_target["world"]["relation_types"] = source.model_dump(mode="json")["world"][
        "relation_types"
    ]
    expected_target["goal_resolution"]["quick_inputs"] = [
        "诊断患者",
        "稳定病情",
    ]
    expected_target.pop("objectives", None)
    target_draft = session.get(ScenarioDraft, target.id)
    assert target_draft is not None
    assert target_draft.definition_document == expected_target
    assert (
        target_draft.definition_document["world"]["relations"]
        == target_before["world"]["relations"]
    )

    sibling_draft = session.get(ScenarioDraft, sibling.id)
    assert sibling_draft is not None
    assert sibling_draft.revision == 1
    assert sibling_draft.definition_document == sibling_before
    session.refresh(published)
    session.refresh(sibling_published)
    assert published.snapshot_document == published_snapshot_before
    assert published.content_hash == published_hash_before
    assert sibling_published.snapshot_document == sibling_published_snapshot_before
    assert sibling_published.content_hash == sibling_published_hash_before
    assert (
        session.scalar(
            select(func.count())
            .select_from(ScenarioVersion)
            .where(ScenarioVersion.scenario_id == target.id)
        )
        == 1
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(ScenarioVersion)
            .where(ScenarioVersion.scenario_id == sibling.id)
        )
        == 1
    )

    repeated = upgrade_scenario_draft_to_current_definition(
        session,
        target_scenario_id=target.id,
        expected_revision=2,
        canonical=source,
    )
    assert repeated.changed is False
    assert (repeated.revision_before, repeated.revision_after) == (2, 2)


def test_upgrade_uses_explicit_identity_and_rejects_stale_revision(
    session: Session,
) -> None:
    target, _ = _published_legacy(
        session,
        _legacy_definition(GENERIC_TEST),
        key="generic_explicit_identity",
        name="Generic explicit identity",
    )

    with pytest.raises(ScenarioLifecycleError) as conflict:
        upgrade_scenario_draft_to_current_definition(
            session,
            target_scenario_id=target.id,
            expected_revision=99,
            canonical=_generic_current_source(),
        )
    assert conflict.value.code == "SCENARIO_DRAFT_CONFLICT"

    with pytest.raises(ScenarioLifecycleError) as missing:
        upgrade_scenario_draft_to_current_definition(
            session,
            target_scenario_id=uuid4(),
            expected_revision=1,
            canonical=_generic_current_source(),
        )
    assert missing.value.code == "SCENARIO_NOT_FOUND"


def test_source_without_migration_catalog_data_is_safe_and_does_not_retire_objectives(
    session: Session,
) -> None:
    target, _ = _published_legacy(
        session,
        _legacy_definition(GENERIC_TEST),
        key="generic_source_without_catalog",
        name="Generic source without catalog",
    )
    legacy_objective_keys = [objective.key for objective in GENERIC_TEST.objectives]

    result = upgrade_scenario_draft_to_current_definition(
        session,
        target_scenario_id=target.id,
        expected_revision=1,
        canonical=GENERIC_TEST,
    )

    assert result.changed is True
    assert result.relation_type_count == 0
    assert result.quick_input_count == 0
    assert result.objective_count == len(legacy_objective_keys)
    draft = session.get(ScenarioDraft, target.id)
    assert draft is not None
    assert [item["key"] for item in draft.definition_document["objectives"]] == (
        legacy_objective_keys
    )


def test_linjiang_source_produces_current_semantic_counts_without_hardcoded_target(
    session: Session,
) -> None:
    target, _ = _published_legacy(
        session,
        _legacy_definition(LINJIANG_LEGACY_OBJECTIVES_TEST),
        key="linjiang_upgrade_fixture_target",
        name="Linjiang upgrade fixture target",
    )

    result = upgrade_scenario_draft_to_current_definition(
        session,
        target_scenario_id=target.id,
        expected_revision=1,
        canonical=LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    )

    assert (
        result.relation_type_count,
        result.relation_count,
        result.quick_input_count,
        result.objective_count,
    ) == (4, 48, 6, 0)
