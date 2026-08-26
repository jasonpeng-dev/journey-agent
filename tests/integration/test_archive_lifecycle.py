from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.enums import AgentTaskStatus, DecisionStatus, WorldOperationStatus
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentTask,
    ConversationSession,
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceResourceState,
    WorldOperation,
)
from app.services.game_instances import GameInstanceService

pytestmark = pytest.mark.legacy_scenario


def _version_id(session: Session) -> str:
    from app.infrastructure.db.models import Scenario

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


def _archive(client: TestClient, game: dict[str, object], revision: int | None = None):
    expected = int(game["runtime_revision"] if revision is None else revision)
    return client.post(
        f"/api/v1/games/{game['id']}/archive",
        json={"expected_runtime_revision": expected},
    )


def test_stable_active_archive_preserves_world_state_and_becomes_read_only(
    client: TestClient, session: Session
) -> None:
    game = _new_game(client, session)
    game_id = UUID(str(game["id"]))
    before = (
        session.get(GameInstanceNodeState, (game_id, "northern_valley")),
        session.scalar(
            select(GameInstanceFactState).where(GameInstanceFactState.game_instance_id == game_id)
        ),
        session.scalar(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == game_id
            )
        ),
        session.scalar(
            select(GameInstanceActor).where(GameInstanceActor.game_instance_id == game_id)
        ),
    )
    response = _archive(client, game)
    assert response.status_code == 200
    assert response.json()["status"] == "ARCHIVED"
    assert response.json()["runtime_revision"] == int(game["runtime_revision"]) + 1
    session.expire_all()
    after = (
        session.get(GameInstanceNodeState, (game_id, "northern_valley")),
        session.scalar(
            select(GameInstanceFactState).where(GameInstanceFactState.game_instance_id == game_id)
        ),
        session.scalar(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == game_id
            )
        ),
        session.scalar(
            select(GameInstanceActor).where(GameInstanceActor.game_instance_id == game_id)
        ),
    )
    assert before[0] is not None and after[0] is not None
    assert (after[0].status, after[0].visibility) == (before[0].status, before[0].visibility)
    assert before[1] is not None and after[1] is not None
    assert (after[1].truth_value, after[1].visibility) == (
        before[1].truth_value,
        before[1].visibility,
    )
    assert before[2] is not None and after[2] is not None
    assert (after[2].value, after[2].reserved_value) == (before[2].value, before[2].reserved_value)
    assert before[3] is not None and after[3] is not None
    assert (after[3].actor_key, after[3].current_node_key) == (
        before[3].actor_key,
        before[3].current_node_key,
    )

    readonly = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "gather valley intelligence", "idempotency_key": str(uuid4())},
    )
    assert readonly.status_code == 409
    assert readonly.json()["error"]["code"] == "GAME_INSTANCE_READ_ONLY"


def test_archive_requires_matching_revision_and_does_not_archive(
    client: TestClient, session: Session
) -> None:
    game = _new_game(client, session)
    response = _archive(client, game, revision=int(game["runtime_revision"]) + 1)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GAME_INSTANCE_CONFLICT"
    assert client.get(f"/api/v1/games/{game['id']}").json()["status"] == "ACTIVE"


def test_archive_rejects_active_task_without_cleanup(client: TestClient, session: Session) -> None:
    game = _new_game(client, session)
    game_id = UUID(str(game["id"]))
    scope = GameInstanceService(session).load(GameInstanceId(game_id))
    conversation = session.scalar(
        select(ConversationSession).where(ConversationSession.game_instance_id == game_id)
    )
    assert conversation is not None
    task = GenericAgentService(session, scope).create_task(
        conversation, "gather valley intelligence"
    )
    session.flush()
    task_id = task.id
    session.commit()
    response = _archive(client, game)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GAME_INSTANCE_ARCHIVE_TASK_ACTIVE"
    task = session.get(AgentTask, task_id)
    assert task is not None and task.status.value == "ACTIVE"
    assert session.get(GameInstance, game_id).status.value == "ACTIVE"


def test_archive_rejects_pending_operation_without_cleanup(
    client: TestClient, session: Session
) -> None:
    game = _new_game(client, session)
    game_id = UUID(str(game["id"]))
    scope = GameInstanceService(session).load(GameInstanceId(game_id))
    conversation = session.scalar(
        select(ConversationSession).where(ConversationSession.game_instance_id == game_id)
    )
    assert conversation is not None
    agent = GenericAgentService(session, scope)
    task = agent.create_task(conversation, "gather valley intelligence")
    agent.execute_next(task)
    operation = session.scalar(select(WorldOperation).where(WorldOperation.task_id == task.id))
    assert operation is not None and operation.status.value == "PENDING"
    task.status = AgentTaskStatus.SUCCEEDED
    operation_id = operation.id
    session.commit()
    response = _archive(client, game)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GAME_INSTANCE_ARCHIVE_OPERATION_PENDING"
    operation = session.get(WorldOperation, operation_id)
    assert operation is not None
    assert operation.status.value == "PENDING"
    assert session.get(GameInstance, game_id).status.value == "ACTIVE"


def test_archive_rejects_pending_decision_without_cleanup(
    client: TestClient, session: Session
) -> None:
    game = _new_game(client, session)
    game_id = UUID(str(game["id"]))
    scope = GameInstanceService(session).load(GameInstanceId(game_id))
    conversation = session.scalar(
        select(ConversationSession).where(ConversationSession.game_instance_id == game_id)
    )
    assert conversation is not None
    agent = GenericAgentService(session, scope)
    task = agent.create_task(conversation, "gather valley intelligence")
    step = agent.execute_next(task)
    assert step is not None
    decision = ActionDecisionRequest(
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
    session.add(decision)
    session.flush()
    task.status = AgentTaskStatus.SUCCEEDED
    operation = session.scalar(select(WorldOperation).where(WorldOperation.task_id == task.id))
    assert operation is not None
    operation.status = WorldOperationStatus.RESOLVED
    decision_id = decision.id
    session.commit()
    response = _archive(client, game)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GAME_INSTANCE_ARCHIVE_DECISION_PENDING"
    decision = session.get(ActionDecisionRequest, decision_id)
    assert decision is not None and decision.status == DecisionStatus.PENDING
    assert session.get(GameInstance, game_id).status.value == "ACTIVE"


def test_archive_rejects_nonzero_reservation(client: TestClient, session: Session) -> None:
    game = _new_game(client, session)
    game_id = UUID(str(game["id"]))
    resource = session.scalar(
        select(GameInstanceResourceState).where(
            GameInstanceResourceState.game_instance_id == game_id
        )
    )
    assert resource is not None
    resource.value = max(resource.value, 1)
    resource.reserved_value = 1
    session.flush()
    resource_identity = resource.resource_identity
    session.commit()
    response = _archive(client, game)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GAME_INSTANCE_ARCHIVE_RESERVATION_ACTIVE"
    resource = session.get(GameInstanceResourceState, (game_id, resource_identity))
    assert resource is not None
    assert resource.reserved_value == 1
    assert session.get(GameInstance, game_id).status.value == "ACTIVE"
