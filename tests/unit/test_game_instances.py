from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.domain.enums import GameInstanceStatus
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import (
    GameInstanceBindingImmutableError,
    Player,
    ScenarioVersion,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.starfire.scenario import STARFIRE_SCENARIO_DEFINITION
from app.services.game_instances import GameInstanceError, GameInstanceService
from app.services.scenarios import ScenarioService


def _player(session: Session, name: str = "instance-player") -> Player:
    player = Player(name=name)
    session.add(player)
    session.flush()
    return player


def _published_versions(
    session: Session, *, include_v2: bool = False
) -> tuple[ScenarioVersion, ...]:
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(
        STARFIRE_SCENARIO_DEFINITION
    )
    service = ScenarioService(session)
    first = service.publish_draft(scenario.id, expected_revision=1).version
    if not include_v2:
        return (first,)
    changed = deepcopy(first.snapshot_document)
    changed["world"]["name"] = "Starfire v2"
    service.replace_draft(
        scenario.id,
        expected_revision=1,
        definition_document=changed,
    )
    second = service.publish_draft(scenario.id, expected_revision=2).version
    return first, second


def test_player_can_create_multiple_independent_instances_for_same_version(
    session: Session,
) -> None:
    player = _player(session)
    (version,) = _published_versions(session)
    service = GameInstanceService(session)

    first = service.create(player_id=player.id, scenario_version_id=version.id)
    second = service.create(player_id=player.id, scenario_version_id=version.id)

    assert first.id != second.id
    assert first.player_id == second.player_id == player.id
    assert first.scenario_version_id == second.scenario_version_id == version.id
    assert first.status == second.status == GameInstanceStatus.PENDING_INITIALIZATION
    assert first.current_node_key is None
    assert first.runtime_revision == 0


def test_instance_scope_remains_on_v1_after_v2_becomes_current(session: Session) -> None:
    player = _player(session)
    first_version, second_version = _published_versions(session, include_v2=True)
    service = GameInstanceService(session)
    first = service.create(
        player_id=player.id,
        scenario_version_id=first_version.id,
    )
    second = service.create(
        player_id=player.id,
        scenario_version_id=second_version.id,
    )

    first_scope = service.load(GameInstanceId(first.id))
    second_scope = service.load(GameInstanceId(second.id))

    assert first_scope.scenario_version_id == first_version.id
    assert second_scope.scenario_version_id == second_version.id
    assert first_scope.game_instance_id != second_scope.game_instance_id


def test_instance_creation_and_resolution_fail_closed_for_missing_bindings(
    session: Session,
) -> None:
    service = GameInstanceService(session)
    with pytest.raises(GameInstanceError) as missing_player:
        service.create(player_id=uuid4(), scenario_version_id=uuid4())
    assert missing_player.value.code == "GAME_INSTANCE_PLAYER_NOT_FOUND"
    with pytest.raises(GameInstanceError) as missing_instance:
        service.load(GameInstanceId(uuid4()))
    assert missing_instance.value.code == "GAME_INSTANCE_NOT_FOUND"


def test_instance_binding_is_immutable_in_orm(session: Session) -> None:
    player = _player(session)
    (version,) = _published_versions(session)
    instance = GameInstanceService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
    )
    session.commit()
    instance.scenario_version_id = uuid4()

    with pytest.raises(GameInstanceBindingImmutableError):
        session.flush()
    session.rollback()


def test_lifecycle_transition_is_validated_and_revision_guarded(session: Session) -> None:
    player = _player(session)
    (version,) = _published_versions(session)
    service = GameInstanceService(session)
    instance = service.create(player_id=player.id, scenario_version_id=version.id)

    active = service.transition(
        instance.id,
        expected_runtime_revision=0,
        new_status=GameInstanceStatus.ACTIVE,
    )

    assert active.status == GameInstanceStatus.ACTIVE
    assert active.runtime_revision == 1
    with pytest.raises(GameInstanceError) as stale:
        service.transition(
            instance.id,
            expected_runtime_revision=0,
            new_status=GameInstanceStatus.SUSPENDED,
        )
    assert stale.value.code == "GAME_INSTANCE_CONFLICT"
    with pytest.raises(GameInstanceError) as invalid:
        service.transition(
            instance.id,
            expected_runtime_revision=1,
            new_status=GameInstanceStatus.PENDING_INITIALIZATION,
        )
    assert invalid.value.code == "GAME_INSTANCE_TRANSITION_INVALID"
