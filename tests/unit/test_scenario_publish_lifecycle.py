from collections.abc import Callable
from copy import deepcopy
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import Scenario, ScenarioDraft, ScenarioVersion
from app.scenarios.behavior_registry import STARFIRE_BEHAVIOR_DEFINITION
from app.scenarios.documents import ScenarioDefinitionDocument
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.serialization import scenario_content_hash
from app.scenarios.starfire.scenario import STARFIRE_SCENARIO_DEFINITION
from app.services.scenarios import ScenarioLifecycleError, ScenarioService


def _persisted_scenario(session: Session) -> Scenario:
    return ScenarioDefinitionRepository(session).persist_initial_draft(STARFIRE_SCENARIO_DEFINITION)


def _document() -> dict[str, Any]:
    return ScenarioDefinitionDocument.from_domain(STARFIRE_SCENARIO_DEFINITION).model_dump(
        mode="json"
    )


def _assert_error(error: pytest.ExceptionInfo[ScenarioLifecycleError], code: str) -> None:
    assert error.value.code == code


def test_draft_update_uses_optimistic_revision_and_resets_validation(
    session: Session,
) -> None:
    scenario = _persisted_scenario(session)
    service = ScenarioService(session)
    changed = _document()
    changed["world"]["name"] = "Starfire revised"

    draft = service.replace_draft(
        scenario.id,
        expected_revision=1,
        definition_document=changed,
    )

    assert draft.revision == 2
    assert draft.validation_status == "UNVALIDATED"
    assert draft.content_hash is None
    with pytest.raises(ScenarioLifecycleError) as caught:
        service.replace_draft(
            scenario.id,
            expected_revision=1,
            definition_document=_document(),
        )
    _assert_error(caught, "SCENARIO_DRAFT_CONFLICT")


@pytest.mark.parametrize(
    ("mutate", "issue_code"),
    [
        (
            lambda document: document["world"]["nodes"][0]["interaction_keys"].append(
                "missing_interaction"
            ),
            "SCENARIO_NODE_INTERACTION_NOT_FOUND",
        ),
        (
            lambda document: document["world"]["relations"][0].update(
                {"target_node_key": "missing_node"}
            ),
            "SCENARIO_RELATION_NODE_NOT_FOUND",
        ),
        (
            lambda document: document["objective_catalog"]["definitions"][0][
                "completion_requirements"
            ][0].update({"fact_key": "missing_fact"}),
            "SCENARIO_OBJECTIVE_FACT_NOT_FOUND",
        ),
        (
            lambda document: document["behavior_bundle"].update({"version": "missing"}),
            "SCENARIO_BEHAVIOR_BUNDLE_UNAVAILABLE",
        ),
        (
            lambda document: document["world"]["interactions"].append(
                {"key": "unsupported", "name": "Unsupported", "description": ""}
            ),
            "SCENARIO_INTERACTION_UNSUPPORTED",
        ),
    ],
)
def test_invalid_draft_records_actionable_diagnostics_and_cannot_publish(
    session: Session,
    mutate: Callable[[dict[str, Any]], None],
    issue_code: str,
) -> None:
    scenario = _persisted_scenario(session)
    document = _document()
    mutate(document)
    service = ScenarioService(session)
    service.replace_draft(
        scenario.id,
        expected_revision=1,
        definition_document=document,
    )

    validation = service.validate_draft(scenario.id)

    assert not validation.passed
    assert issue_code in {issue.code for issue in validation.issues}
    draft = session.get(ScenarioDraft, scenario.id)
    assert draft is not None
    assert draft.validation_status == "FAILED"
    assert issue_code in {issue["code"] for issue in draft.validation_errors}
    with pytest.raises(ScenarioLifecycleError) as caught:
        service.publish_draft(scenario.id, expected_revision=2)
    _assert_error(caught, "SCENARIO_DRAFT_INVALID")
    assert session.scalar(select(func.count()).select_from(ScenarioVersion)) == 0
    session.refresh(scenario)
    assert scenario.current_published_version_id is None


def test_publish_is_versioned_and_rejects_stale_revision_or_hash(session: Session) -> None:
    scenario = _persisted_scenario(session)
    service = ScenarioService(session)

    validation = service.validate_draft(scenario.id)
    expected_hash = scenario_content_hash(_document())
    assert validation.passed
    with pytest.raises(ScenarioLifecycleError) as stale:
        service.publish_draft(scenario.id, expected_revision=2)
    _assert_error(stale, "SCENARIO_DRAFT_CONFLICT")
    with pytest.raises(ScenarioLifecycleError) as changed:
        service.publish_draft(
            scenario.id,
            expected_revision=1,
            expected_content_hash="0" * 64,
        )
    _assert_error(changed, "SCENARIO_DRAFT_HASH_MISMATCH")

    result = service.publish_draft(
        scenario.id,
        expected_revision=1,
        expected_content_hash=expected_hash,
    )

    assert result.status == "PUBLISHED"
    assert result.version.version_number == 1
    assert result.version.content_hash == expected_hash
    assert result.version.behavior_bundle_key == "starfire"
    assert result.version.behavior_bundle_version == "1"
    session.refresh(scenario)
    assert scenario.status == "PUBLISHED"
    assert scenario.current_published_version_id == result.version.id


def test_semantically_identical_draft_does_not_create_garbage_version(
    session: Session,
) -> None:
    scenario = _persisted_scenario(session)
    service = ScenarioService(session)
    first = service.publish_draft(scenario.id, expected_revision=1)
    reordered = _document()
    reordered["world"]["nodes"].reverse()
    reordered["world"]["relations"].reverse()
    reordered["objective_catalog"]["definitions"].reverse()
    service.replace_draft(
        scenario.id,
        expected_revision=1,
        definition_document=reordered,
    )

    unchanged = service.publish_draft(scenario.id, expected_revision=2)

    assert unchanged.status == "NO_CHANGES"
    assert unchanged.version.id == first.version.id
    assert session.scalar(select(func.count()).select_from(ScenarioVersion)) == 1


def test_changed_valid_draft_publishes_next_version(session: Session) -> None:
    scenario = _persisted_scenario(session)
    service = ScenarioService(session)
    first = service.publish_draft(scenario.id, expected_revision=1)
    changed = deepcopy(first.version.snapshot_document)
    changed["world"]["name"] = "Starfire v2"
    service.replace_draft(
        scenario.id,
        expected_revision=1,
        definition_document=changed,
    )

    second = service.publish_draft(scenario.id, expected_revision=2)

    assert second.status == "PUBLISHED"
    assert second.version.version_number == 2
    assert second.version.id != first.version.id
    assert second.version.content_hash != first.version.content_hash
    assert session.scalar(select(func.count()).select_from(ScenarioVersion)) == 2


def test_behavior_bundle_metadata_does_not_duplicate_objective_content() -> None:
    assert not hasattr(STARFIRE_BEHAVIOR_DEFINITION, "objectives")
    assert not hasattr(STARFIRE_BEHAVIOR_DEFINITION, "objective_catalog")
