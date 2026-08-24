from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import ResourcePoolAvailability, ResourcePoolVisibility
from app.infrastructure.db.models import (
    AgentTask,
    ConversationMessage,
    ConversationSession,
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceRelationKnowledge,
    GameInstanceResourceState,
    Scenario,
    WorldOperation,
)


def _version_id(session: Session) -> str:
    scenario = session.scalar(select(Scenario).where(Scenario.key == "starfire_command"))
    assert scenario is not None and scenario.current_published_version_id is not None
    return str(scenario.current_published_version_id)


def _new_game(client: TestClient, session: Session, *, key: str | None = None) -> dict[str, object]:
    response = client.post(
        "/api/v1/games",
        json={
            "scenario_version_id": _version_id(session),
            "idempotency_key": key or str(uuid4()),
        },
    )
    assert response.status_code == 201
    return response.json()


def _archive(client: TestClient, game: dict[str, object]) -> dict[str, object]:
    response = client.post(
        f"/api/v1/games/{game['id']}/archive",
        json={"expected_runtime_revision": game["runtime_revision"]},
    )
    assert response.status_code == 200
    return response.json()


def _prepare_archived_game(client: TestClient, session: Session) -> dict[str, object]:
    game = _new_game(client, session)
    game_id = UUID(str(game["id"]))
    node = session.scalar(
        select(GameInstanceNodeState).where(GameInstanceNodeState.game_instance_id == game_id)
    )
    fact = session.scalar(
        select(GameInstanceFactState).where(GameInstanceFactState.game_instance_id == game_id)
    )
    resource = session.scalar(
        select(GameInstanceResourceState).where(
            GameInstanceResourceState.game_instance_id == game_id
        )
    )
    actor = session.scalar(
        select(GameInstanceActor).where(GameInstanceActor.game_instance_id == game_id)
    )
    assert node is not None and fact is not None and resource is not None and actor is not None
    fact.truth_value = {"observed": "after-fork"}
    resource.value += 7
    resource.visibility = ResourcePoolVisibility.HIDDEN
    resource.availability = ResourcePoolAvailability.UNAVAILABLE
    actor.current_node_key = node.node_key
    actor.command_reachability = "DISCONNECTED"
    actor.status = "DEGRADED"
    region = session.scalar(
        select(GameInstanceRegionResourceKnowledge).where(
            GameInstanceRegionResourceKnowledge.game_instance_id == game_id
        )
    )
    if region is not None:
        region.resource_survey_completed = False
    relation = session.scalar(
        select(GameInstanceRelationKnowledge).where(
            GameInstanceRelationKnowledge.game_instance_id == game_id
        )
    )
    if relation is not None:
        relation.visibility = "HIDDEN"
    session.commit()
    return _archive(client, game)


def test_archived_source_forks_exact_state_without_history(
    client: TestClient, session: Session
) -> None:
    source_summary = _prepare_archived_game(client, session)
    source_id = UUID(str(source_summary["id"]))
    source = session.get(GameInstance, source_id)
    assert source is not None
    source_version = source.scenario_version_id
    source_node = session.scalar(
        select(GameInstanceNodeState).where(GameInstanceNodeState.game_instance_id == source_id)
    )
    source_fact = session.scalar(
        select(GameInstanceFactState).where(GameInstanceFactState.game_instance_id == source_id)
    )
    source_resource = session.scalar(
        select(GameInstanceResourceState).where(
            GameInstanceResourceState.game_instance_id == source_id
        )
    )
    source_actor = session.scalar(
        select(GameInstanceActor).where(GameInstanceActor.game_instance_id == source_id)
    )
    assert source_node is not None and source_fact is not None
    assert source_resource is not None and source_actor is not None

    response = client.post(
        f"/api/v1/games/{source_id}/fork",
        json={"creation_key": "fork-one"},
    )
    assert response.status_code == 201
    target_id = UUID(response.json()["id"])
    target = session.get(GameInstance, target_id)
    assert target is not None
    assert target.player_id == source.player_id
    assert target.scenario_version_id == source_version
    assert target.status.value == "ACTIVE"
    assert target.runtime_revision == 1
    assert target.forked_from_game_instance_id == source_id
    assert target.current_node_key == source_actor.current_node_key

    target_node = session.get(GameInstanceNodeState, (target_id, source_node.node_key))
    target_fact = session.get(
        GameInstanceFactState, (target_id, source_fact.node_key, source_fact.fact_key)
    )
    target_resource = session.get(
        GameInstanceResourceState, (target_id, source_resource.resource_identity)
    )
    target_actor = session.get(GameInstanceActor, (target_id, source_actor.actor_key))
    assert target_node is not None and target_fact is not None
    assert target_resource is not None and target_actor is not None
    assert (target_node.status, target_node.visibility) == (
        source_node.status,
        source_node.visibility,
    )
    assert (target_fact.truth_value, target_fact.visibility) == (
        source_fact.truth_value,
        source_fact.visibility,
    )
    assert (target_resource.value, target_resource.visibility, target_resource.availability) == (
        source_resource.value,
        source_resource.visibility,
        source_resource.availability,
    )
    assert target_resource.reserved_value == 0
    assert (
        target_actor.current_node_key,
        target_actor.command_reachability,
        target_actor.status,
    ) == (
        source_actor.current_node_key,
        source_actor.command_reachability,
        source_actor.status,
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(ConversationSession)
            .where(ConversationSession.game_instance_id == target_id)
        )
        == 1
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(ConversationMessage)
            .join(
                ConversationSession,
                ConversationMessage.session_id == ConversationSession.id,
            )
            .where(ConversationSession.game_instance_id == target_id)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(AgentTask)
            .where(AgentTask.game_instance_id == target_id)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(WorldOperation)
            .where(WorldOperation.game_instance_id == target_id)
        )
        == 0
    )

    goal = client.post(
        f"/api/v1/games/{target_id}/goals",
        json={"goal": "gather valley intelligence", "idempotency_key": str(uuid4())},
    )
    assert goal.status_code == 200
    assert goal.json()["status"] == "ACCEPTED"


def test_same_archive_supports_multiple_forks_and_retry_is_idempotent(
    client: TestClient, session: Session
) -> None:
    source = _prepare_archived_game(client, session)
    source_id = source["id"]
    first = client.post(f"/api/v1/games/{source_id}/fork", json={"creation_key": "repeat-one"})
    retry = client.post(f"/api/v1/games/{source_id}/fork", json={"creation_key": "repeat-one"})
    second = client.post(f"/api/v1/games/{source_id}/fork", json={"creation_key": "repeat-two"})
    assert first.status_code == retry.status_code == second.status_code == 201
    assert first.json()["id"] == retry.json()["id"]
    assert first.json()["id"] != second.json()["id"]


def test_active_source_and_reused_key_conflicts_are_rejected(
    client: TestClient, session: Session
) -> None:
    active = _new_game(client, session)
    rejected = client.post(
        f"/api/v1/games/{active['id']}/fork", json={"creation_key": "active-source"}
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "FORK_SOURCE_NOT_ARCHIVED"

    source = _prepare_archived_game(client, session)
    first = client.post(f"/api/v1/games/{source['id']}/fork", json={"creation_key": "shared-key"})
    other = _prepare_archived_game(client, session)
    conflict = client.post(f"/api/v1/games/{other['id']}/fork", json={"creation_key": "shared-key"})
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "FORK_CREATION_KEY_REUSED"


def test_fork_failure_rolls_back_target_and_source_delete_keeps_target(
    client: TestClient, session: Session
) -> None:
    source_summary = _prepare_archived_game(client, session)
    source_id = UUID(str(source_summary["id"]))
    actor = session.scalar(
        select(GameInstanceActor).where(GameInstanceActor.game_instance_id == source_id)
    )
    assert actor is not None
    original_name = actor.name
    actor.name = "tampered archived actor"
    session.commit()
    failed = client.post(f"/api/v1/games/{source_id}/fork", json={"creation_key": "rollback-key"})
    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "FORK_SOURCE_RUNTIME_INVALID"
    assert (
        session.scalar(
            select(func.count())
            .select_from(GameInstance)
            .where(GameInstance.creation_key == "rollback-key")
        )
        == 0
    )

    actor.name = original_name
    session.commit()
    target = client.post(
        f"/api/v1/games/{source_id}/fork", json={"creation_key": "delete-source-key"}
    )
    assert target.status_code == 201
    target_id = UUID(target.json()["id"])
    assert client.delete(f"/api/v1/games/{source_id}").status_code == 204
    remaining = session.get(GameInstance, target_id)
    assert remaining is not None
    assert remaining.forked_from_game_instance_id is None
    assert client.get(f"/api/v1/games/{target_id}/play").status_code == 200


def test_fork_wrong_player_cannot_access_source(client: TestClient, session: Session) -> None:
    from app.services.game_fork import GameForkError, GameForkService

    source_summary = _prepare_archived_game(client, session)
    source = session.get(GameInstance, UUID(str(source_summary["id"])))
    assert source is not None
    try:
        GameForkService(session).materialize(
            source_game_instance_id=source.id,
            player_id=uuid4(),
            creation_key="wrong-player",
        )
    except GameForkError as exc:
        assert exc.code == "GAME_INSTANCE_NOT_FOUND"
    else:
        raise AssertionError("Fork should reject a source outside the supplied player scope")
