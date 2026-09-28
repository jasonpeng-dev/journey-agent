from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, update
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.enums import DecisionStatus, WorldOperationStatus
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
    ScenarioVersion,
    WorldOperation,
)
from app.scenarios.builtin import require_builtin_v2_version
from app.scenarios.migration import preview_v2_to_v3
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import GameLifecycleError
from app.services.generic_game import GenericGameService
from app.services.scenarios import ScenarioService
from tests.scenario_fixtures import GENERIC_TEST


def _published_version_id(session: Session) -> str:
    return str(require_builtin_v2_version(session, GENERIC_TEST).id)


def _v3_document(key: str, quick_inputs: list[str]) -> dict[str, Any]:
    document = preview_v2_to_v3(GENERIC_TEST.model_dump(mode="json")).target_document
    document["metadata"]["key"] = key
    document["metadata"]["name"] = key.replace("_", " ").title()
    document["world"]["key"] = key
    document["world"]["name"] = document["metadata"]["name"]
    document["goal_resolution"]["quick_inputs"] = quick_inputs
    return document


def _publish_v3(session: Session, key: str, quick_inputs: list[str]) -> tuple[Any, Any]:
    service = ScenarioService(session)
    scenario = service.create_from_authored_document(
        key=key,
        name=key.replace("_", " ").title(),
        definition_document=_v3_document(key, quick_inputs),
    )
    session.flush()
    draft = service.get_draft(scenario.id)
    published = service.publish_draft(scenario.id, expected_revision=draft.revision)
    session.commit()
    return scenario, published.version


def test_v3_published_version_creates_game_and_bootstrap_uses_exact_quick_inputs(
    client: TestClient, session: Session
) -> None:
    scenario, version = _publish_v3(
        session, "v3_game_creation_contract", ["Repair the relay", "Secure the depot"]
    )

    created = client.post(
        "/api/v1/games",
        json={
            "scenario_id": str(scenario.id),
            "scenario_version_id": str(version.id),
            "idempotency_key": str(uuid4()),
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["scenario_id"] == str(scenario.id)
    assert payload["scenario_version_id"] == str(version.id)
    play = client.get(f"/api/v1/games/{payload['id']}/play")
    assert play.status_code == 200, play.text
    assert play.json()["scenario_metadata"]["quick_inputs"] == [
        "Repair the relay",
        "Secure the depot",
    ]

    # A Draft UUID is not a published immutable ScenarioVersion and cannot
    # be used as a gameplay pin.
    draft = ScenarioService(session).get_draft(scenario.id)
    unpublished = client.post(
        "/api/v1/games",
        json={
            "scenario_id": str(scenario.id),
            "scenario_version_id": str(draft.scenario_id),
            "idempotency_key": str(uuid4()),
        },
    )
    assert unpublished.status_code == 404
    assert unpublished.json()["error"]["code"] == "SCENARIO_VERSION_NOT_FOUND"


def test_game_creation_rejects_version_from_selected_other_scenario(
    client: TestClient, session: Session
) -> None:
    first, _ = _publish_v3(session, "v3_selected_scenario", ["first"])
    other, other_version = _publish_v3(session, "v3_other_scenario", ["other"])

    response = client.post(
        "/api/v1/games",
        json={
            "scenario_id": str(first.id),
            "scenario_version_id": str(other_version.id),
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SCENARIO_VERSION_SCENARIO_MISMATCH"
    assert other.id != first.id


def test_game_creation_returns_typed_error_for_corrupt_published_version(
    client: TestClient, session: Session
) -> None:
    scenario, version = _publish_v3(session, "v3_corrupt_version_contract", ["goal"])
    session.execute(
        update(ScenarioVersion)
        .where(ScenarioVersion.id == version.id)
        .values(content_hash="0" * 64)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    session.expire_all()

    response = client.post(
        "/api/v1/games",
        json={
            "scenario_id": str(scenario.id),
            "scenario_version_id": str(version.id),
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SCENARIO_VERSION_HASH_MISMATCH"


def test_historical_v3_game_keeps_pinned_quick_inputs_after_later_publish(
    client: TestClient, session: Session
) -> None:
    scenario, first = _publish_v3(session, "v3_historical_quick_inputs", ["version one"])
    service = ScenarioService(session)

    draft = service.get_draft(scenario.id)
    second_document = deepcopy(draft.definition_document)
    second_document["goal_resolution"]["quick_inputs"] = ["version two"]
    second_draft = service.replace_draft(
        scenario.id,
        expected_revision=draft.revision,
        definition_document=second_document,
    )
    second = service.publish_draft(scenario.id, expected_revision=second_draft.revision).version
    session.commit()

    first_game = client.post(
        "/api/v1/games",
        json={
            "scenario_id": str(scenario.id),
            "scenario_version_id": str(first.id),
            "idempotency_key": str(uuid4()),
        },
    ).json()
    second_game = client.post(
        "/api/v1/games",
        json={
            "scenario_id": str(scenario.id),
            "scenario_version_id": str(second.id),
            "idempotency_key": str(uuid4()),
        },
    ).json()

    third_draft = service.get_draft(scenario.id)
    third_document = deepcopy(third_draft.definition_document)
    third_document["goal_resolution"]["quick_inputs"] = ["version three"]
    third_draft = service.replace_draft(
        scenario.id,
        expected_revision=third_draft.revision,
        definition_document=third_document,
    )
    service.publish_draft(scenario.id, expected_revision=third_draft.revision)
    session.commit()

    first_state = client.get(f"/api/v1/games/{first_game['id']}/play")
    second_state = client.get(f"/api/v1/games/{second_game['id']}/play")
    assert first_state.json()["scenario_metadata"]["quick_inputs"] == ["version one"]
    assert second_state.json()["scenario_metadata"]["quick_inputs"] == ["version two"]


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
    assert first.json()["scenario_name"] == GENERIC_TEST.metadata.name
    assert second.json()["scenario_name"] == GENERIC_TEST.metadata.name
    assert len(client.get("/api/v1/games").json()) == 2
    loaded = client.get(f"/api/v1/games/{first.json()['id']}")
    assert loaded.status_code == 200
    assert loaded.json()["scenario_version_id"] == first.json()["scenario_version_id"]
    assert loaded.json()["scenario_name"] == GENERIC_TEST.metadata.name
    player_state = client.get(f"/api/v1/games/{first.json()['id']}/play")
    assert player_state.status_code == 200
    payload = player_state.json()
    assert payload["game"]["scenario_name"] == GENERIC_TEST.metadata.name
    assert payload["scenario_metadata"]["quick_inputs"] == list(
        GENERIC_TEST.goal_resolution.quick_inputs
    )
    assert payload["scenario_metadata"]["goal_presets"] == []
    assert "definition_document" not in payload
    assert "world" not in payload
    assert "actions" not in payload
    assert "rules" not in payload
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
    task = agent.create_task(conversation, "stabilize the patient")
    agent.execute_next(task)
    operation = session.scalar(select(WorldOperation).where(WorldOperation.task_id == task.id))
    assert operation is not None
    operation.status = WorldOperationStatus.PENDING
    session.flush()
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
            actor_key="doctor_lee",
            action_key="treat_patient",
            target_node_key="patient_one",
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
    task = GenericAgentService(session, scope).create_task(conversation, "stabilize the patient")
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
