from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.enums import AgentTaskStatus
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import AgentTask, ConversationSession, GameInstance, Scenario
from app.services.game_instances import GameInstanceService

pytestmark = pytest.mark.legacy_scenario


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


def _completed_task(session: Session, game_id: UUID, goal: str) -> AgentTask:
    session_row = session.scalar(
        select(ConversationSession).where(ConversationSession.game_instance_id == game_id)
    )
    assert session_row is not None
    task = GenericAgentService(
        session, GameInstanceService(session).load(GameInstanceId(game_id))
    ).create_task(session_row, goal)
    task.status = AgentTaskStatus.SUCCEEDED
    task.completed_at = datetime.now(UTC)
    session.flush()
    return task


def _archive(client: TestClient, summary: dict[str, object]) -> dict[str, object]:
    response = client.post(
        f"/api/v1/games/{summary['id']}/archive",
        json={"expected_runtime_revision": summary["runtime_revision"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_ordinary_archive_fork_inherits_history_and_new_task_boundary(
    client: TestClient, session: Session
) -> None:
    source = _new_game(client, session)
    source_id = UUID(str(source["id"]))
    first_task = _completed_task(session, source_id, "gather valley intelligence")
    session.commit()
    archived = _archive(client, source)

    response = client.post(
        f"/api/v1/games/{archived['id']}/fork", json={"creation_key": "history-fork"}
    )
    assert response.status_code == 201, response.text
    target_id = UUID(response.json()["id"])
    target = session.get(GameInstance, target_id)
    assert target is not None
    assert target.status.value == "ACTIVE"
    assert target.inherited_task_count == 1
    copied_task = session.scalar(select(AgentTask).where(AgentTask.game_instance_id == target_id))
    assert copied_task is not None
    assert copied_task.id != first_task.id
    assert copied_task.status == AgentTaskStatus.SUCCEEDED

    new_task_response = client.post(
        f"/api/v1/games/{target_id}/goals",
        json={"goal": "gather valley intelligence", "idempotency_key": "new-target-task"},
    )
    assert new_task_response.status_code == 200, new_task_response.text
    assert (
        session.scalar(
            select(func.count())
            .select_from(AgentTask)
            .where(AgentTask.game_instance_id == target_id)
        )
        == 2
    )
    state = client.get(f"/api/v1/games/{target_id}/play")
    assert state.status_code == 200
    assert [item["sequence"] for item in state.json()["task_history"]] == [1, 2]
    assert state.json()["game"]["inherited_task_count"] == 1

    assert client.delete(f"/api/v1/games/{source_id}").status_code == 204
    remaining = session.get(GameInstance, target_id)
    assert remaining is not None and remaining.forked_from_game_instance_id is None
    assert (
        session.scalar(
            select(func.count())
            .select_from(AgentTask)
            .where(AgentTask.game_instance_id == target_id)
        )
        == 2
    )
    assert client.get(f"/api/v1/games/{target_id}/play").status_code == 200


def test_checkpoint_fork_inherits_history_but_not_checkpoint_provenance(
    client: TestClient, session: Session
) -> None:
    source = _new_game(client, session)
    source_id = UUID(str(source["id"]))
    _completed_task(session, source_id, "gather valley intelligence")
    session.commit()

    checkpoint_response = client.post(
        f"/api/v1/games/{source_id}/checkpoint",
        json={
            "expected_runtime_revision": source["runtime_revision"],
            "creation_key": "nested-checkpoint",
        },
    )
    assert checkpoint_response.status_code == 201
    checkpoint_id = UUID(checkpoint_response.json()["id"])

    fork_response = client.post(
        f"/api/v1/games/{checkpoint_id}/fork", json={"creation_key": "nested-fork"}
    )
    assert fork_response.status_code == 201, fork_response.text
    fork_id = UUID(fork_response.json()["id"])
    fork = session.get(GameInstance, fork_id)
    assert fork is not None
    assert fork.forked_from_game_instance_id == checkpoint_id
    assert fork.checkpointed_from_game_instance_id is None
    assert fork.checkpoint_source_runtime_revision is None
    assert fork.inherited_task_count == 1
    assert (
        session.scalar(
            select(func.count()).select_from(AgentTask).where(AgentTask.game_instance_id == fork_id)
        )
        == 1
    )

    assert client.delete(f"/api/v1/games/{checkpoint_id}").status_code == 204
    remaining = session.get(GameInstance, fork_id)
    assert remaining is not None and remaining.forked_from_game_instance_id is None
    assert client.get(f"/api/v1/games/{fork_id}/play").status_code == 200
