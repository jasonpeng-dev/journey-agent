from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.debug.snapshot_service import StrategicSnapshotService
from app.domain.enums import RunStatus
from app.infrastructure.db.models import AgentRun, AgentTask, ConversationSession
from app.scenarios.contracts import (
    GoalResolutionResult,
    ObjectiveResolutionStatus,
    ObjectiveScope,
)
from app.scenarios.starfire.objective_catalog import (
    STARFIRE_OBJECTIVE_CATALOG,
    StarfireObjectiveKey,
)
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService


def test_new_task_starts_unresolved_without_implicit_full(session: Session) -> None:
    _conversation, task = _task(session)

    assert task.objective_resolution_status == ObjectiveResolutionStatus.UNRESOLVED.value
    assert task.objective_scope_keys is None
    assert task.objective_catalog_version is None
    assert task.objective_frozen_at is None


def test_resolved_scope_is_confirmed_and_frozen_atomically(session: Session) -> None:
    _conversation, task = _task(session)
    service = TaskService(session)
    scope = STARFIRE_OBJECTIVE_CATALOG.scope([StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST])
    service.record_goal_resolution(
        task,
        GoalResolutionResult(
            status=ObjectiveResolutionStatus.RESOLVED,
            scope=scope,
            resolver_source="TEST_RESOLVER",
            resolver_version="v1",
        ),
    )

    frozen = service.confirm_and_freeze_scope(
        task,
        scope,
        confirmation_source="EXACT_GOAL",
        freeze_source="GOAL_RESOLUTION",
    )

    assert frozen == scope
    assert service.require_frozen_scope(task) == scope
    assert task.objective_resolution_status == ObjectiveResolutionStatus.CONFIRMED.value
    assert task.objective_confirmed_at == task.objective_frozen_at
    assert task.objective_confirmation_source == "EXACT_GOAL"
    assert task.objective_freeze_source == "GOAL_RESOLUTION"


def test_freeze_is_idempotent_for_same_scope_and_rejects_changes(session: Session) -> None:
    _conversation, task = _task(session)
    service = TaskService(session)
    restore = STARFIRE_OBJECTIVE_CATALOG.scope([StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST])
    secure = STARFIRE_OBJECTIVE_CATALOG.scope([StarfireObjectiveKey.SECURE_NORTHERN_VALLEY])
    service.record_goal_resolution(
        task,
        GoalResolutionResult(status=ObjectiveResolutionStatus.RESOLVED, scope=restore),
    )
    service.confirm_and_freeze_scope(
        task,
        restore,
        confirmation_source="TEST",
        freeze_source="TEST",
    )

    assert (
        service.confirm_and_freeze_scope(
            task,
            restore,
            confirmation_source="RETRY",
            freeze_source="RETRY",
        )
        == restore
    )
    with pytest.raises(AppError, match="cannot be changed") as changed:
        service.confirm_and_freeze_scope(
            task,
            secure,
            confirmation_source="TEST",
            freeze_source="TEST",
        )
    assert changed.value.code == "OBJECTIVE_SCOPE_FROZEN"
    with pytest.raises(AppError, match="cannot be resolved again") as resolved_again:
        service.record_goal_resolution(
            task,
            GoalResolutionResult(status=ObjectiveResolutionStatus.RESOLVED, scope=secure),
        )
    assert resolved_again.value.code == "OBJECTIVE_SCOPE_FROZEN"


def test_scope_requires_exact_registered_catalog_and_scenario(session: Session) -> None:
    _conversation, task = _task(session)
    service = TaskService(session)
    unknown_version = ObjectiveScope(
        scenario_key="starfire_command",
        catalog_version="missing-v9",
        objective_keys=(StarfireObjectiveKey.SECURE_NORTHERN_VALLEY.value,),
    )
    wrong_scenario = ObjectiveScope(
        scenario_key="another_scenario",
        catalog_version=STARFIRE_OBJECTIVE_CATALOG.catalog_version,
        objective_keys=(StarfireObjectiveKey.SECURE_NORTHERN_VALLEY.value,),
    )

    with pytest.raises(AppError) as unavailable:
        service.record_goal_resolution(
            task,
            GoalResolutionResult(
                status=ObjectiveResolutionStatus.RESOLVED,
                scope=unknown_version,
            ),
        )
    assert unavailable.value.code == "OBJECTIVE_CATALOG_VERSION_UNAVAILABLE"
    with pytest.raises(AppError) as mismatch:
        service.record_goal_resolution(
            task,
            GoalResolutionResult(
                status=ObjectiveResolutionStatus.RESOLVED,
                scope=wrong_scenario,
            ),
        )
    assert mismatch.value.code == "OBJECTIVE_SCOPE_SCENARIO_MISMATCH"


def test_serialization_round_trips_frozen_scope_and_provenance(session: Session) -> None:
    _conversation, task = _task(session)
    service = TaskService(session)
    scope = STARFIRE_OBJECTIVE_CATALOG.scope(
        [
            StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE,
            StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST,
        ]
    )
    service.record_goal_resolution(
        task,
        GoalResolutionResult(
            status=ObjectiveResolutionStatus.RESOLVED,
            scope=scope,
            resolver_source="EXACT_ALIAS",
            resolver_version="aliases-v1",
        ),
    )
    service.confirm_and_freeze_scope(
        task,
        scope,
        confirmation_source="EXACT_GOAL",
        freeze_source="GOAL_RESOLUTION",
    )
    session.commit()
    loaded = session.get(AgentTask, task.id)
    assert loaded is not None

    serialized = service.serialize(loaded)

    assert serialized["raw_goal"] == task.goal_description
    assert serialized["objective_resolution"]["status"] == "CONFIRMED"
    assert serialized["objective_resolution"]["resolver_source"] == "EXACT_ALIAS"
    assert serialized["objective_scope"]["objective_keys"] == list(scope.objective_keys)
    assert serialized["objective_scope"]["catalog_version"] == scope.catalog_version
    assert serialized["objective_scope"]["frozen"] is True


def test_trace_projects_the_same_frozen_scope_snapshot(session: Session) -> None:
    conversation, task = _task(session)
    service = TaskService(session)
    scope = STARFIRE_OBJECTIVE_CATALOG.scope([StarfireObjectiveKey.SECURE_NORTHERN_VALLEY])
    service.record_goal_resolution(
        task,
        GoalResolutionResult(status=ObjectiveResolutionStatus.RESOLVED, scope=scope),
    )
    service.confirm_and_freeze_scope(
        task,
        scope,
        confirmation_source="TEST",
        freeze_source="TEST",
    )
    session.add(
        AgentRun(
            request_id=uuid4(),
            session_id=conversation.id,
            task_id=task.id,
            status=RunStatus.COMPLETED,
            model="test-model",
            input_message="test",
            context_record_ids=[],
            model_rounds=[],
            max_rounds=1,
            actual_rounds=1,
            token_usage=0,
            purpose="PLAN",
        )
    )
    session.flush()

    trace = StrategicSnapshotService(
        session,
        Settings(database_url="sqlite+pysqlite:///:memory:"),
    )._trace(task)

    assert trace[0]["objective_scope"] == {
        "scenario_key": "starfire_command",
        "catalog_version": "starfire-objectives-v1",
        "objective_keys": ["SECURE_NORTHERN_VALLEY"],
        "frozen": True,
    }


def _task(session: Session) -> tuple[ConversationSession, AgentTask]:
    player = GameService(session).create_player(f"Scope lifecycle {uuid4()}")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    return conversation, TaskService(session).create_task(
        conversation,
        "Restore Starfire Outpost",
        "starfire_command",
    )
