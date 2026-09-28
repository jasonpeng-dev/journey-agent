from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.presentation import validate_presentation_profile
from app.infrastructure.db.models import (
    ScenarioPresentationProfile,
    ScenarioPresentationProfileRevision,
)
from app.services.presentation_profiles import (
    PresentationProfileLifecycleError,
    PresentationProfileService,
)
from app.services.scenarios import ScenarioService
from tests.scenario_fixtures import GENERIC_TEST, create_test_scenario


def _scenario(session: Session, *, key: str = "presentation_case"):
    return create_test_scenario(
        session,
        GENERIC_TEST,
        key=key,
        name="Presentation Case",
    )


def _profile_document(template: str = "compact") -> dict[str, object]:
    return {
        "schema_version": 1,
        "template": template,
        "global_display": {
            "density": "COMPACT",
            "summary_slot": "HEADER",
            "semantic_order": ["NAME", "STATUS"],
        },
        "world_entities": {
            "entity_detail": "DETAIL",
            "knowledge_level": "A+B",
            "resource_order": ["NAME", "AMOUNT", "STATUS"],
        },
        "family_overrides": [
            {
                "node_family": "FACILITY",
                "default_open": "COMPACT",
            }
        ],
        "semantic_overrides": [
            {
                "semantic_key": "facility",
                "summary_slot": "BOTH",
            }
        ],
    }


def test_default_profile_is_lazy_sparse_and_has_immutable_history(session: Session) -> None:
    scenario = _scenario(session)
    service = PresentationProfileService(session)

    current = service.get_current(scenario.id)
    session.commit()

    assert current.revision == 1
    assert current.profile_document == {
        "schema_version": 1,
        "template": "standard",
        "family_overrides": [],
        "semantic_overrides": [],
    }
    history = service.list_revisions(scenario.id)
    assert [item.revision for item in history] == [1]

    history[0].profile_document = {"schema_version": 1, "template": "compact"}
    with pytest.raises(RuntimeError, match="immutable"):
        session.flush()
    session.rollback()
    persisted = session.get(ScenarioPresentationProfileRevision, history[0].id)
    assert persisted is not None
    assert persisted.profile_document["template"] == "standard"


def test_profile_replace_restore_conflict_and_history_are_revisioned(session: Session) -> None:
    scenario = _scenario(session)
    service = PresentationProfileService(session)

    service.get_current(scenario.id)
    session.commit()
    replaced = service.replace(
        scenario.id,
        expected_revision=1,
        profile_document=_profile_document(),
    )
    session.commit()
    assert replaced.revision == 2
    assert replaced.profile_document["template"] == "compact"

    with pytest.raises(PresentationProfileLifecycleError) as conflict:
        service.replace(
            scenario.id,
            expected_revision=1,
            profile_document=_profile_document("detailed"),
        )
    assert conflict.value.code == "SCENARIO_PRESENTATION_PROFILE_CONFLICT"
    session.rollback()

    detailed = service.replace(
        scenario.id,
        expected_revision=2,
        profile_document=_profile_document("detailed"),
    )
    session.commit()
    restored = service.restore(
        scenario.id,
        expected_revision=detailed.revision,
        revision=1,
    )
    session.commit()

    assert restored.revision == 4
    assert restored.profile_document["template"] == "standard"
    assert [item.revision for item in service.list_revisions(scenario.id)] == [4, 3, 2, 1]


def test_profile_save_does_not_change_draft_or_published_version(session: Session) -> None:
    scenario = _scenario(session, key="presentation_publish_case")
    service = PresentationProfileService(session)
    current = service.get_current(scenario.id)
    session.commit()
    draft = ScenarioService(session).get_draft(scenario.id)
    draft_revision = draft.revision
    version = (
        ScenarioService(session)
        .publish_draft(
            scenario.id,
            expected_revision=draft_revision,
        )
        .version
    )
    session.commit()

    before_version_id = scenario.current_published_version_id
    before_hash = version.content_hash
    saved = service.replace(
        scenario.id,
        expected_revision=current.revision,
        profile_document=_profile_document(),
    )
    session.commit()

    assert saved.revision == 2
    assert scenario.current_published_version_id == before_version_id
    assert session.get(ScenarioPresentationProfile, scenario.id).revision == 2
    assert (
        session.scalar(
            select(func.count())
            .select_from(ScenarioPresentationProfileRevision)
            .where(ScenarioPresentationProfileRevision.scenario_id == scenario.id)
        )
        == 2
    )
    assert session.get(type(version), version.id).content_hash == before_hash


def test_profile_delete_cascades_and_forbidden_payloads_fail(session: Session) -> None:
    scenario = _scenario(session, key="presentation_delete_case")
    service = PresentationProfileService(session)
    service.get_current(scenario.id)
    session.commit()

    invalid = deepcopy(_profile_document())
    invalid["entity_overrides"] = {"patient_one": {"default_open": "FULL"}}
    with pytest.raises(ValidationError):
        validate_presentation_profile(invalid)

    scenario_id = scenario.id
    session.delete(scenario)
    session.flush()
    session.expire_all()
    assert session.get(ScenarioPresentationProfile, scenario_id) is None
    assert (
        session.scalar(
            select(func.count())
            .select_from(ScenarioPresentationProfileRevision)
            .where(ScenarioPresentationProfileRevision.scenario_id == scenario_id)
        )
        == 0
    )
