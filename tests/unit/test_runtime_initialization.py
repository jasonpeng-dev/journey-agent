from copy import deepcopy

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import GameInstanceStatus, NodeStatus, NPCRole
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    NPC,
    AgentTask,
    ConversationSession,
    GameInstance,
    GameInstanceBindingImmutableError,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceOfficerAppointment,
    GameInstanceResourceState,
    Player,
    ScenarioVersion,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.starfire.scenario import STARFIRE_SCENARIO_DEFINITION
from app.services.runtime_initialization import (
    RuntimeInitializationError,
    RuntimeInitializationService,
)
from app.services.scenarios import ScenarioService


def _player_and_version(session: Session) -> tuple[Player, ScenarioVersion]:
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(
        STARFIRE_SCENARIO_DEFINITION
    )
    version = (
        ScenarioService(session)
        .publish_draft(
            scenario.id,
            expected_revision=1,
        )
        .version
    )
    player = Player(name="runtime-player")
    session.add(player)
    session.flush()
    return player, version


def test_initialization_materializes_complete_runtime_without_empty_task(
    session: Session,
) -> None:
    player, version = _player_and_version(session)

    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="new-game-1",
    )

    assert runtime.created
    assert runtime.instance.status == GameInstanceStatus.ACTIVE
    assert runtime.instance.scenario_version_id == version.id
    assert runtime.instance.current_node_key == "capital_council"
    assert runtime.instance.runtime_revision == 1
    assert runtime.session.game_instance_id == runtime.instance.id
    nodes = session.scalars(
        select(GameInstanceNodeState).where(
            GameInstanceNodeState.game_instance_id == runtime.instance.id
        )
    ).all()
    assert len(nodes) == len(STARFIRE_SCENARIO_DEFINITION.world.nodes)
    start = next(node for node in nodes if node.node_key == "capital_council")
    assert start.status == NodeStatus.ENTERED
    hidden_supply = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "enemy_north_supply_route", "supply_status"),
    )
    assert hidden_supply is not None
    assert hidden_supply.visibility == Visibility.HIDDEN
    resources = {
        row.resource_key: row.value
        for row in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == runtime.instance.id
            )
        ).all()
    }
    assert resources == {"soldiers": 300, "food": 100, "gold": 80, "morale": 60}
    assert (
        session.scalar(
            select(func.count())
            .select_from(GameInstanceOfficerAppointment)
            .where(GameInstanceOfficerAppointment.game_instance_id == runtime.instance.id)
        )
        == 3
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(AgentTask)
            .where(AgentTask.game_instance_id == runtime.instance.id)
        )
        == 0
    )


def test_initialization_replay_is_idempotent_and_version_binding_cannot_change(
    session: Session,
) -> None:
    player, first_version = _player_and_version(session)
    service = RuntimeInitializationService(session)
    first = service.create(
        player_id=player.id,
        scenario_version_id=first_version.id,
        creation_key="stable-request",
    )

    replay = service.create(
        player_id=player.id,
        scenario_version_id=first_version.id,
        creation_key="stable-request",
    )

    assert not replay.created
    assert replay.instance.id == first.instance.id
    assert replay.session.id == first.session.id
    assert session.scalar(select(func.count()).select_from(GameInstance)) == 1
    assert session.scalar(select(func.count()).select_from(ConversationSession)) == 1

    scenario = ScenarioDefinitionRepository(session).find_scenario("starfire_command")
    assert scenario is not None
    changed = deepcopy(first_version.snapshot_document)
    changed["world"]["name"] = "Starfire v2"
    ScenarioService(session).replace_draft(
        scenario.id,
        expected_revision=1,
        definition_document=changed,
    )
    second_version = (
        ScenarioService(session)
        .publish_draft(
            scenario.id,
            expected_revision=2,
        )
        .version
    )
    with pytest.raises(RuntimeInitializationError) as caught:
        service.create(
            player_id=player.id,
            scenario_version_id=second_version.id,
            creation_key="stable-request",
        )
    assert caught.value.code == "RUNTIME_CREATION_KEY_REUSED"


def test_initialization_uses_explicit_old_version_not_current_published(
    session: Session,
) -> None:
    player, first_version = _player_and_version(session)
    scenario = ScenarioDefinitionRepository(session).find_scenario("starfire_command")
    assert scenario is not None
    changed = deepcopy(first_version.snapshot_document)
    changed["world"]["name"] = "Starfire current v2"
    ScenarioService(session).replace_draft(
        scenario.id,
        expected_revision=1,
        definition_document=changed,
    )
    ScenarioService(session).publish_draft(scenario.id, expected_revision=2)

    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=first_version.id,
        creation_key="bind-v1-explicitly",
    )

    assert runtime.instance.scenario_version_id == first_version.id


def test_failed_initialization_rolls_back_partial_instance_graph(session: Session) -> None:
    player, version = _player_and_version(session)
    strategists = session.scalars(select(NPC).where(NPC.role == NPCRole.STRATEGIST)).all()
    for strategist in strategists:
        strategist.enabled = False
    session.flush()

    with pytest.raises(RuntimeInitializationError) as caught:
        RuntimeInitializationService(session).create(
            player_id=player.id,
            scenario_version_id=version.id,
            creation_key="must-roll-back",
        )

    assert caught.value.code == "RUNTIME_STRATEGIST_UNAVAILABLE"
    assert (
        session.scalar(
            select(func.count())
            .select_from(GameInstance)
            .where(GameInstance.creation_key == "must-roll-back")
        )
        == 0
    )


def test_creation_binding_is_immutable_after_initialization(session: Session) -> None:
    player, version = _player_and_version(session)
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="immutable-creation-key",
    )
    session.commit()
    runtime.instance.creation_key = "changed"

    with pytest.raises(GameInstanceBindingImmutableError):
        session.flush()
