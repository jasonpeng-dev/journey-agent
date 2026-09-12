from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import ResolvedGoalDraftStatus
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import (
    GoalResolutionAttempt,
    ResolvedGoalDraft,
    ResolvedGoalDraftImmutableError,
)
from app.scenarios.builtin import require_builtin_v2_version
from app.scenarios.versions import ScenarioVersionRepository
from app.services.formal_goal import (
    FormalGoalPersistenceError,
    load_and_validate_formal_goal_contract,
)
from app.services.game_instances import GameInstanceService
from app.services.play import PlayOrchestrator
from tests.scenario_fixtures import GENERIC_TEST


def _foundation(session: Session):  # type: ignore[no-untyped-def]
    version = require_builtin_v2_version(session, GENERIC_TEST)
    from app.services.game_lifecycle import GameLifecycleService

    runtime = GameLifecycleService(session).create(
        scenario_version_id=version.id,
        idempotency_key=str(uuid4()),
    )
    session.flush()
    submission = PlayOrchestrator(session, GameInstanceId(runtime.instance.id)).submit_goal(
        "stabilize the patient", idempotency_key=str(uuid4())
    )
    assert submission.task is not None
    attempt = (
        session.query(GoalResolutionAttempt)
        .order_by(GoalResolutionAttempt.created_at.desc())
        .first()
    )
    assert attempt is not None
    task = submission.task
    draft = ResolvedGoalDraft(
        game_instance_id=runtime.instance.id,
        resolution_attempt_id=attempt.id,
        original_goal_text=task.goal_description,
        scenario_version_id=version.id,
        scenario_content_hash=version.content_hash,
        formal_goal_contract_schema_version=task.formal_goal_contract_schema_version,
        formal_goal_source_kind=task.formal_goal_source_kind,
        formal_goal_contract_json=task.formal_goal_contract_json,
        formal_goal_contract_hash=task.formal_goal_contract_hash,
        formal_goal_compiler_version=task.formal_goal_compiler_version,
        resolver_source=task.objective_resolver_source or "UNKNOWN",
        status=ResolvedGoalDraftStatus.READY,
    )
    session.add(draft)
    session.flush()
    return runtime, version, task, draft


def test_draft_persists_and_roundtrips_exact_formal_goal(session: Session) -> None:
    runtime, version, task, draft = _foundation(session)
    snapshot = ScenarioVersionRepository(session).load(version.id)

    contract = load_and_validate_formal_goal_contract(
        payload=draft.formal_goal_contract_json,
        contract_hash=draft.formal_goal_contract_hash,
        schema_version=draft.formal_goal_contract_schema_version,
        source_kind=draft.formal_goal_source_kind,
        scenario_version_id=draft.scenario_version_id,
        scenario_content_hash=draft.scenario_content_hash,
        compiler_version=draft.formal_goal_compiler_version,
        snapshot=snapshot,
    )

    assert draft.game_instance_id == runtime.instance.id
    assert contract.content_hash == task.formal_goal_contract_hash


def test_only_one_ready_draft_per_game(session: Session) -> None:
    _runtime, _version, _task, first = _foundation(session)
    second = ResolvedGoalDraft(
        game_instance_id=first.game_instance_id,
        resolution_attempt_id=uuid4(),
        original_goal_text=first.original_goal_text,
        scenario_version_id=first.scenario_version_id,
        scenario_content_hash=first.scenario_content_hash,
        formal_goal_contract_schema_version=first.formal_goal_contract_schema_version,
        formal_goal_source_kind=first.formal_goal_source_kind,
        formal_goal_contract_json=first.formal_goal_contract_json,
        formal_goal_contract_hash=first.formal_goal_contract_hash,
        formal_goal_compiler_version=first.formal_goal_compiler_version,
        resolver_source=first.resolver_source,
        status=ResolvedGoalDraftStatus.READY,
    )
    session.add(second)
    with pytest.raises(IntegrityError):
        session.flush()


def test_draft_payload_is_immutable_and_lifecycle_is_terminal(session: Session) -> None:
    _runtime, _version, task, draft = _foundation(session)
    session.commit()
    draft_id = draft.id
    task_id = task.id
    draft.original_goal_text = "changed"
    with pytest.raises(ResolvedGoalDraftImmutableError):
        session.flush()
    session.rollback()

    draft = session.get(ResolvedGoalDraft, draft_id)
    assert draft is not None
    draft.status = ResolvedGoalDraftStatus.CONFIRMED
    draft.confirmed_task_id = task_id
    draft.confirmed_at = datetime.now(UTC)
    session.flush()
    assert draft.confirmed_task_id == task_id

    draft.status = ResolvedGoalDraftStatus.SUPERSEDED
    with pytest.raises(ResolvedGoalDraftImmutableError):
        session.flush()


def test_common_formal_goal_loader_rejects_tamper_and_scenario_mismatch(
    session: Session,
) -> None:
    runtime, version, _task, draft = _foundation(session)
    snapshot = ScenarioVersionRepository(session).load(version.id)
    common = dict(
        payload=draft.formal_goal_contract_json,
        schema_version=draft.formal_goal_contract_schema_version,
        source_kind=draft.formal_goal_source_kind,
        scenario_version_id=draft.scenario_version_id,
        scenario_content_hash=draft.scenario_content_hash,
        compiler_version=draft.formal_goal_compiler_version,
        snapshot=snapshot,
    )
    with pytest.raises(FormalGoalPersistenceError, match="hash"):
        load_and_validate_formal_goal_contract(contract_hash="0" * 64, **common)
    with pytest.raises(FormalGoalPersistenceError, match="ScenarioVersion"):
        load_and_validate_formal_goal_contract(
            contract_hash=draft.formal_goal_contract_hash,
            **{**common, "scenario_version_id": uuid4()},
        )
    assert GameInstanceService(session).load(runtime.instance.id).scenario_version_id == version.id
