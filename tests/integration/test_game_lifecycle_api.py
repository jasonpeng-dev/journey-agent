from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.enums import DecisionStatus
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentPlan,
    AgentStep,
    AgentTask,
    ConversationMessage,
    ConversationSession,
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceMemoryEvent,
    GameInstanceNodeState,
    GameInstanceResourceState,
    PlayerExecutionCheckpoint,
    Scenario,
    ScenarioVersion,
    WorldOperation,
)
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import GameLifecycleError
from app.services.generic_game import GenericGameService

pytestmark = pytest.mark.legacy_scenario


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

    archived = client.post(
        f"/api/v1/games/{game.id}/archive",
        json={"expected_runtime_revision": game.runtime_revision},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    archived_again = client.post(
        f"/api/v1/games/{game.id}/archive",
        json={"expected_runtime_revision": archived.json()["runtime_revision"]},
    )
    assert archived_again.status_code == 409
    assert archived_again.json()["error"]["code"] == "GAME_INSTANCE_TRANSITION_INVALID"
    assert client.get("/api/v1/games?status=archived").json()[0]["id"] == str(game.id)
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


def test_permanent_delete_removes_only_instance_scope_and_preserves_version(
    client: TestClient, session: Session
) -> None:
    version_id = UUID(_published_version_id(session))
    first = client.post(
        "/api/v1/games",
        json={"scenario_version_id": str(version_id), "idempotency_key": str(uuid4())},
    ).json()
    second = client.post(
        "/api/v1/games",
        json={"scenario_version_id": str(version_id), "idempotency_key": str(uuid4())},
    ).json()
    game_id = UUID(first["id"])
    other_id = UUID(second["id"])
    scope = GameInstanceService(session).load(GameInstanceId(game_id))
    conversation = session.scalar(
        select(ConversationSession).where(ConversationSession.game_instance_id == game_id)
    )
    assert conversation is not None
    task = GenericAgentService(session, scope).create_task(
        conversation, "gather valley intelligence"
    )
    step = GenericAgentService(session, scope).execute_next(task)
    assert step is not None
    session.add(
        PlayerExecutionCheckpoint(
            task_id=task.id,
            game_instance_id=game_id,
            phase="AWAITING_ACTION_ACK",
            last_action_step_id=step.id,
        )
    )
    session.add(
        ActionDecisionRequest(
            player_id=task.player_id,
            game_instance_id=game_id,
            task_id=task.id,
            source_step_id=step.id,
            actor_key=step.assigned_actor_key,
            action_key=str(step.tool_arguments["action_key"]),
            target_key=str(step.tool_arguments["target_key"]),
            parameters=dict(step.tool_arguments["parameters"]),
            idempotency_key=str(uuid4()),
            status=DecisionStatus.PENDING,
            reason_code="TEST_PENDING",
            policy_details={},
        )
    )
    session.flush()
    task_id = task.id
    plan_ids = tuple(session.scalars(select(AgentPlan.id).where(AgentPlan.task_id == task_id)))
    session.commit()

    deleted = client.delete(f"/api/v1/games/{game_id}")
    assert deleted.status_code == 204
    session.expire_all()
    assert client.get(f"/api/v1/games/{game_id}").status_code == 404
    assert client.get(f"/api/v1/games/{other_id}").status_code == 200
    assert session.get(ScenarioVersion, version_id) is not None
    assert session.get(GameInstance, game_id) is None
    assert session.get(AgentTask, task_id) is None
    assert all(session.get(AgentPlan, plan_id) is None for plan_id in plan_ids)
    instance_models = (
        GameInstanceNodeState,
        GameInstanceFactState,
        GameInstanceResourceState,
        GameInstanceActor,
        GameInstanceMemoryEvent,
        ConversationSession,
        WorldOperation,
        ActionDecisionRequest,
        PlayerExecutionCheckpoint,
    )
    for model in instance_models:
        assert (
            session.scalar(
                select(func.count()).select_from(model).where(model.game_instance_id == game_id)
            )
            == 0
        )
    assert (
        session.scalar(
            select(func.count())
            .select_from(AgentStep)
            .join(AgentPlan, AgentStep.plan_id == AgentPlan.id)
            .where(AgentPlan.task_id == task_id)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(ConversationMessage)
            .join(ConversationSession, ConversationMessage.session_id == ConversationSession.id)
            .where(ConversationSession.game_instance_id == game_id)
        )
        == 0
    )
    assert all(item["id"] != str(game_id) for item in client.get("/api/v1/games").json())
    with Session(session.bind) as restarted_session:
        assert restarted_session.get(GameInstance, game_id) is None
        assert restarted_session.get(GameInstance, other_id) is not None


def test_archived_game_can_be_permanently_deleted(client: TestClient, session: Session) -> None:
    created = client.post(
        "/api/v1/games",
        json={
            "scenario_version_id": _published_version_id(session),
            "idempotency_key": str(uuid4()),
        },
    ).json()
    game_id = created["id"]
    game = client.get(f"/api/v1/games/{game_id}").json()
    assert (
        client.post(
            f"/api/v1/games/{game_id}/archive",
            json={"expected_runtime_revision": game["runtime_revision"]},
        ).status_code
        == 200
    )
    assert client.delete(f"/api/v1/games/{game_id}").status_code == 204
    assert all(game["id"] != game_id for game in client.get("/api/v1/games?status=archived").json())
