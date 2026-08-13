from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import ConversationSession, GameInstance, Scenario, WorldOperation
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import GameLifecycleError
from app.services.generic_game import GenericGameService


def _published_version_id(session: Session) -> str:
    scenario = session.scalar(select(Scenario).where(Scenario.key == "starfire_command"))
    assert scenario is not None and scenario.current_published_version_id is not None
    return str(scenario.current_published_version_id)


def test_games_bind_exact_version_and_instances_are_isolated(
    client: TestClient, session: Session
) -> None:
    version_id = _published_version_id(session)
    first = client.post(
        "/api/v1/games",
        json={"scenario_version_id": version_id, "idempotency_key": str(uuid4())},
    )
    second = client.post(
        "/api/v1/games",
        json={"scenario_version_id": version_id, "idempotency_key": str(uuid4())},
    )

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["scenario_version_id"] == version_id
    assert second.json()["scenario_version_id"] == version_id
    assert len(client.get("/api/v1/games").json()) == 2
    loaded = client.get(f"/api/v1/games/{first.json()['id']}")
    assert loaded.status_code == 200
    assert loaded.json()["scenario_version_id"] == first.json()["scenario_version_id"]
    missing = client.get(f"/api/v1/games/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "GAME_INSTANCE_NOT_FOUND"


def test_abandon_cancels_unsettled_operation_and_archive_is_read_only(
    client: TestClient, session: Session
) -> None:
    created = client.post(
        "/api/v1/games",
        json={
            "scenario_version_id": _published_version_id(session),
            "idempotency_key": str(uuid4()),
        },
    ).json()
    game_id = UUID(created["id"])
    game = session.get(GameInstance, game_id)
    assert game is not None
    scope = GameInstanceService(session).load(GameInstanceId(game.id))
    conversation = session.scalar(
        select(ConversationSession).where(ConversationSession.game_instance_id == game.id)
    )
    assert conversation is not None
    agent = GenericAgentService(session, scope)
    task = agent.create_task(conversation, "gather valley intelligence")
    agent.execute_next(task)
    operation = session.scalar(select(WorldOperation).where(WorldOperation.task_id == task.id))
    assert operation is not None and operation.status.value == "PENDING"
    revision_before_abandon = game.runtime_revision

    abandoned = client.post(f"/api/v1/games/{game.id}/tasks/{task.id}/abandon")
    assert abandoned.status_code == 200
    session.refresh(operation)
    assert task.status.value == "ABORTED"
    assert operation.status.value == "CANCELLED"
    assert game.runtime_revision == revision_before_abandon
    history = client.get(f"/api/v1/games/{game.id}/history")
    assert history.status_code == 200
    assert history.json()["tasks"] == [
        {"id": str(task.id), "goal": task.goal_description, "status": "ABORTED"}
    ]
    assert history.json()["operations"][0]["status"] == "CANCELLED"

    archived = client.post(f"/api/v1/games/{game.id}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    assert client.post(f"/api/v1/games/{game.id}/archive").status_code == 200
    assert client.get("/api/v1/games?archived=true").json()[0]["id"] == str(game.id)
    with pytest.raises(GameLifecycleError, match="Only an active GameInstance"):
        GenericGameService(session, scope).execute(
            actor_key="han_lie",
            action_key="recon_valley",
            target_node_key="northern_valley",
            parameters={},
        )


def test_database_enforces_one_non_terminal_task_per_instance(session: Session) -> None:
    names = {item["name"] for item in inspect(session.bind).get_indexes("agent_tasks")}
    assert "uq_agent_tasks_instance_active" in names
