from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.enums import AgentTaskStatus
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import (
    AgentTask,
    ConversationSession,
    GameInstance,
    GameInstanceFactState,
    GameInstanceResourceState,
    Scenario,
)
from app.services.game_instances import GameInstanceService


def _version_id(session: Session) -> str:
    scenario = session.scalar(select(Scenario).where(Scenario.key == "starfire_command"))
    assert scenario is not None and scenario.current_published_version_id is not None
    return str(scenario.current_published_version_id)


def _new_game(client: TestClient, session: Session) -> dict[str, object]:
    response = client.post(
        "/api/v1/games",
        json={"scenario_version_id": _version_id(session), "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 201
    return response.json()


def _completed_task(session: Session, game_id: UUID) -> AgentTask:
    conversation = session.scalar(
        select(ConversationSession).where(ConversationSession.game_instance_id == game_id)
    )
    assert conversation is not None
    task = GenericAgentService(
        session, GameInstanceService(session).load(GameInstanceId(game_id))
    ).create_task(conversation, "gather valley intelligence")
    task.status = AgentTaskStatus.SUCCEEDED
    task.completed_at = datetime.now(UTC)
    session.flush()
    return task


def test_checkpoint_copies_runtime_and_stable_history_without_mutating_source(
    client: TestClient, session: Session
) -> None:
    source_summary = _new_game(client, session)
    source_id = UUID(str(source_summary["id"]))
    source_revision = int(source_summary["runtime_revision"])
    source = session.get(GameInstance, source_id)
    assert source is not None
    fact = session.scalar(
        select(GameInstanceFactState).where(GameInstanceFactState.game_instance_id == source_id)
    )
    resource = session.scalar(
        select(GameInstanceResourceState).where(
            GameInstanceResourceState.game_instance_id == source_id
        )
    )
    assert fact is not None and resource is not None
    fact.truth_value = {"checkpoint": True}
    resource.value += 4
    task = _completed_task(session, source_id)
    session.commit()

    response = client.post(
        f"/api/v1/games/{source_id}/checkpoint",
        json={
            "expected_runtime_revision": source_revision,
            "creation_key": "checkpoint-one",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    target_id = UUID(payload["id"])
    target = session.get(GameInstance, target_id)
    source = session.get(GameInstance, source_id)
    assert target is not None and source is not None
    assert source.status.value == "ACTIVE"
    assert source.runtime_revision == source_revision
    assert target.status.value == "ARCHIVED"
    assert target.runtime_revision == 1
    assert target.scenario_version_id == source.scenario_version_id
    assert target.checkpointed_from_game_instance_id == source_id
    assert target.checkpoint_source_runtime_revision == source_revision
    assert target.forked_from_game_instance_id is None
    assert target.inherited_task_count == 0
    assert payload["is_checkpoint"] is True

    copied_fact = session.get(GameInstanceFactState, (target_id, fact.node_key, fact.fact_key))
    copied_resource = session.get(
        GameInstanceResourceState, (target_id, resource.resource_identity)
    )
    assert copied_fact is not None and copied_resource is not None
    assert copied_fact.truth_value == fact.truth_value
    assert copied_resource.value == resource.value
    assert copied_resource.reserved_value == 0
    assert copied_fact.version == 1
    assert copied_resource.version == 1
    copied_task = session.scalar(select(AgentTask).where(AgentTask.game_instance_id == target_id))
    assert copied_task is not None
    assert copied_task.id != task.id
    assert copied_task.status == AgentTaskStatus.SUCCEEDED

    retry = client.post(
        f"/api/v1/games/{source_id}/checkpoint",
        json={
            "expected_runtime_revision": source_revision,
            "creation_key": "checkpoint-one",
        },
    )
    assert retry.status_code == 201
    assert retry.json()["id"] == str(target_id)
    duplicate = client.post(
        f"/api/v1/games/{source_id}/checkpoint",
        json={
            "expected_runtime_revision": source_revision,
            "creation_key": "checkpoint-two",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CHECKPOINT_ALREADY_EXISTS"

    assert (
        session.scalar(
            select(func.count())
            .select_from(GameInstance)
            .where(
                GameInstance.checkpointed_from_game_instance_id == source_id,
                GameInstance.checkpoint_source_runtime_revision == source_revision,
            )
        )
        == 1
    )


def test_checkpoint_rejects_active_task_without_changing_source(
    client: TestClient, session: Session
) -> None:
    source_summary = _new_game(client, session)
    source_id = UUID(str(source_summary["id"]))
    task = _completed_task(session, source_id)
    task.status = AgentTaskStatus.ACTIVE
    session.commit()

    response = client.post(
        f"/api/v1/games/{source_id}/checkpoint",
        json={
            "expected_runtime_revision": int(source_summary["runtime_revision"]),
            "creation_key": "unstable-checkpoint",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GAME_INSTANCE_ARCHIVE_TASK_ACTIVE"
    source = session.get(GameInstance, source_id)
    assert source is not None and source.status.value == "ACTIVE"
    assert session.scalar(select(func.count()).select_from(GameInstance)) == 1
