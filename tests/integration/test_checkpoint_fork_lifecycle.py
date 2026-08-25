from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.enums import AgentTaskStatus, GameInstanceStatus
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import (
    AgentTask,
    ConversationSession,
    GameInstance,
    Scenario,
)
from app.services.game_instances import GameInstanceService


def _version_id(session: Session) -> str:
    scenario = session.scalar(select(Scenario).where(Scenario.key == "starfire_command"))
    assert scenario is not None and scenario.current_published_version_id is not None
    return str(scenario.current_published_version_id)


def _new_game(client: TestClient, session: Session, key: str | None = None) -> dict[str, object]:
    response = client.post(
        "/api/v1/games",
        json={
            "scenario_version_id": _version_id(session),
            "idempotency_key": key or str(uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _completed_task(session: Session, game_id: UUID, goal: str) -> AgentTask:
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


def _checkpoint(client: TestClient, game: dict[str, object], key: str) -> dict[str, object]:
    response = client.post(
        f"/api/v1/games/{game['id']}/checkpoint",
        json={
            "expected_runtime_revision": game["runtime_revision"],
            "creation_key": key,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _fork(client: TestClient, game_id: str, key: str) -> dict[str, object]:
    response = client.post(f"/api/v1/games/{game_id}/fork", json={"creation_key": key})
    assert response.status_code == 201, response.text
    return response.json()


def test_nested_checkpoint_fork_lifecycle_preserves_history_and_delete_independence(
    client: TestClient, session: Session
) -> None:
    source = _new_game(client, session, "nested-source")
    source_id = UUID(str(source["id"]))
    _completed_task(session, source_id, "task one")
    _completed_task(session, source_id, "task two")
    session.commit()

    checkpoint = _checkpoint(client, source, "nested-checkpoint-one")
    checkpoint_id = UUID(str(checkpoint["id"]))
    source_row = session.get(GameInstance, source_id)
    assert source_row is not None
    assert source_row.status == GameInstanceStatus.ACTIVE
    assert source_row.runtime_revision == int(source["runtime_revision"])
    assert checkpoint["status"] == "ARCHIVED"
    assert checkpoint["is_checkpoint"] is True
    assert checkpoint["inherited_task_count"] == 0

    fork = _fork(client, str(checkpoint_id), "nested-fork-one")
    fork_id = UUID(str(fork["id"]))
    fork_row = session.get(GameInstance, fork_id)
    assert fork_row is not None
    assert fork_row.status == GameInstanceStatus.ACTIVE
    assert fork_row.inherited_task_count == 2
    assert fork_row.forked_from_game_instance_id == checkpoint_id
    assert (
        session.scalar(
            select(func.count()).select_from(AgentTask).where(AgentTask.game_instance_id == fork_id)
        )
        == 2
    )

    _completed_task(session, fork_id, "task three")
    _completed_task(session, fork_id, "task four")
    session.commit()
    checkpoint_two = _checkpoint(client, fork, "nested-checkpoint-two")
    checkpoint_two_id = UUID(str(checkpoint_two["id"]))
    assert checkpoint_two["status"] == "ARCHIVED"
    assert checkpoint_two["inherited_task_count"] == 0
    assert (
        session.scalar(
            select(func.count())
            .select_from(AgentTask)
            .where(AgentTask.game_instance_id == checkpoint_two_id)
        )
        == 4
    )

    final_fork = _fork(client, str(checkpoint_two_id), "nested-fork-two")
    final_fork_id = UUID(str(final_fork["id"]))
    assert final_fork["inherited_task_count"] == 4
    new_task = client.post(
        f"/api/v1/games/{final_fork_id}/goals",
        json={"goal": "gather valley intelligence", "idempotency_key": "nested-task-five"},
    )
    assert new_task.status_code == 200, new_task.text
    history = client.get(f"/api/v1/games/{final_fork_id}/play")
    assert history.status_code == 200
    assert [item["sequence"] for item in history.json()["task_history"]] == [1, 2, 3, 4, 5]
    assert history.json()["game"]["inherited_task_count"] == 4

    assert client.delete(f"/api/v1/games/{source_id}").status_code == 204
    session.expire_all()
    checkpoint_row = session.get(GameInstance, checkpoint_id)
    assert checkpoint_row is not None
    assert checkpoint_row.checkpointed_from_game_instance_id is None
    assert client.delete(f"/api/v1/games/{checkpoint_id}").status_code == 204
    session.expire_all()
    fork_row = session.get(GameInstance, fork_id)
    assert fork_row is not None
    assert fork_row.forked_from_game_instance_id is None
    assert client.delete(f"/api/v1/games/{checkpoint_two_id}").status_code == 204
    session.expire_all()
    final_fork_row = session.get(GameInstance, final_fork_id)
    assert final_fork_row is not None
    assert final_fork_row.forked_from_game_instance_id is None
    assert client.get(f"/api/v1/games/{final_fork_id}/play").status_code == 200


def test_same_final_runtime_state_with_new_revision_allows_second_checkpoint(
    client: TestClient, session: Session
) -> None:
    source = _new_game(client, session, "revision-source")
    source_id = UUID(str(source["id"]))
    initial_node = session.get(GameInstance, source_id)
    assert initial_node is not None
    initial_current_node = initial_node.current_node_key
    first = _checkpoint(client, source, "revision-checkpoint-one")

    GameInstanceService(session).transition(
        source_id,
        expected_runtime_revision=int(source["runtime_revision"]),
        new_status=GameInstanceStatus.SUSPENDED,
    )
    session.commit()
    GameInstanceService(session).transition(
        source_id,
        expected_runtime_revision=int(source["runtime_revision"]) + 1,
        new_status=GameInstanceStatus.ACTIVE,
    )
    session.commit()
    source_row = session.get(GameInstance, source_id)
    assert source_row is not None
    assert source_row.status == GameInstanceStatus.ACTIVE
    assert source_row.current_node_key == initial_current_node
    assert source_row.runtime_revision == int(source["runtime_revision"]) + 2

    second = _checkpoint(
        client,
        {
            "id": str(source_id),
            "runtime_revision": source_row.runtime_revision,
        },
        "revision-checkpoint-two",
    )
    assert first["id"] != second["id"]
    assert first["checkpoint_source_runtime_revision"] == int(source["runtime_revision"])
    assert second["checkpoint_source_runtime_revision"] == source_row.runtime_revision
