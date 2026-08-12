from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agent.types import ToolCall, ToolContext
from app.core.errors import AppError
from app.domain.enums import NodeStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    NPC,
    AgentTask,
    ConversationSession,
    GameInstance,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceOfficerAppointment,
    GameInstanceResourceState,
    Player,
    WorldOperation,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.runtime_binding import scenario_binding_for_task
from app.scenarios.starfire.scenario import STARFIRE_SCENARIO_DEFINITION
from app.services.game import GameService
from app.services.game_instances import GameInstanceService
from app.services.scenarios import ScenarioService
from app.services.tasks import TaskService
from app.tools.catalog import build_registry
from app.tools.executor import ToolExecutor


def _instance_pair(session: Session) -> tuple[Player, GameInstance, GameInstance]:
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
    player = Player(name="shared-player")
    session.add(player)
    session.flush()
    service = GameInstanceService(session)
    first = service.create(player_id=player.id, scenario_version_id=version.id)
    second = service.create(player_id=player.id, scenario_version_id=version.id)
    for instance in (first, second):
        session.add_all(
            [
                GameInstanceNodeState(
                    game_instance_id=instance.id,
                    node_key="capital_council",
                    status=NodeStatus.ENTERED,
                    visibility=Visibility.KNOWN,
                ),
                GameInstanceFactState(
                    game_instance_id=instance.id,
                    node_key="northern_valley",
                    fact_key="valley_security",
                    truth_value="THREATENED",
                    visibility=Visibility.KNOWN,
                ),
                *(
                    GameInstanceResourceState(
                        game_instance_id=instance.id,
                        resource_key=key,
                        value=value,
                    )
                    for key, value in {
                        "soldiers": 300,
                        "food": 100,
                        "gold": 80,
                        "morale": 60,
                    }.items()
                ),
            ]
        )
    session.flush()
    return player, first, second


def test_truth_projection_and_resources_are_instance_scoped(session: Session) -> None:
    player, first, second = _instance_pair(session)
    scope_service = GameInstanceService(session)
    first_game = GameService(session, scope_service.load(GameInstanceId(first.id)))
    second_game = GameService(session, scope_service.load(GameInstanceId(second.id)))

    first_game.set_world_fact(
        player.id,
        "valley_security",
        {"status": "SAFE"},
    )
    first_resources = first_game._domain_state(player.id)
    first_resources.food -= 20
    session.flush()

    assert first_game.get_world_fact(player.id, "valley_security") == {"status": "SAFE"}
    assert second_game.get_world_fact(player.id, "valley_security") == {}
    assert (
        first_game.scenario_truth_state(player.id).fact_value("northern_valley", "valley_security")
        == "SAFE"
    )
    assert (
        second_game.scenario_truth_state(player.id).fact_value("northern_valley", "valley_security")
        == "THREATENED"
    )
    assert first_game.inspect_command_state(player.id)["food"] == 80
    assert second_game.inspect_command_state(player.id)["food"] == 100


def test_operation_idempotency_key_is_unique_per_instance(session: Session) -> None:
    player, first, second = _instance_pair(session)
    officer = session.scalar(select(NPC).where(NPC.key == "han_lie"))
    assert officer is not None
    common = {
        "player_id": player.id,
        "officer_npc_id": officer.id,
        "operation_type": "RECONNAISSANCE",
        "target_key": "northern_valley",
        "parameters": {},
        "idempotency_key": "same-idempotency-key",
    }
    session.add_all(
        [
            WorldOperation(game_instance_id=first.id, **common),
            WorldOperation(game_instance_id=second.id, **common),
        ]
    )
    session.flush()
    session.add(WorldOperation(game_instance_id=first.id, **common))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_session_and_task_inherit_exact_instance_ownership(session: Session) -> None:
    player, first, _second = _instance_pair(session)
    strategist = session.scalar(select(NPC).where(NPC.key == "shen_ce"))
    assert strategist is not None
    session.add(
        GameInstanceOfficerAppointment(
            game_instance_id=first.id,
            npc_id=strategist.id,
        )
    )
    conversation = ConversationSession(
        player_id=player.id,
        game_instance_id=first.id,
        npc_id=strategist.id,
    )
    session.add(conversation)
    session.flush()

    task = TaskService(session).create_task(
        conversation,
        "Secure the northern valley",
        "starfire_command",
    )

    assert task.game_instance_id == first.id
    assert scenario_binding_for_task(session, task).snapshot.id == first.scenario_version_id
    other_session = ConversationSession(
        player_id=player.id,
        game_instance_id=_second.id,
        npc_id=strategist.id,
    )
    session.add(other_session)
    session.flush()
    with pytest.raises(AppError) as caught:
        TaskService(session).bind_session(task, other_session)
    assert getattr(caught.value, "code", None) == "TASK_INSTANCE_MISMATCH"


def test_versioned_objective_catalog_uses_snapshot_content(session: Session) -> None:
    player, first, _second = _instance_pair(session)
    strategist = session.scalar(select(NPC).where(NPC.key == "shen_ce"))
    assert strategist is not None
    task = AgentTask(
        player_id=player.id,
        game_instance_id=first.id,
        owner_npc_id=strategist.id,
        origin_session_id=uuid4(),
        last_session_id=uuid4(),
        goal_description="test",
        scenario_key="starfire_command",
    )
    binding = scenario_binding_for_task(session, task)
    persisted = binding.objective_catalog.definitions

    assert persisted == binding.snapshot.definition.objective_definitions
    assert not hasattr(binding, "behavior_objective_catalog")


def test_tool_context_rejects_session_from_another_instance(session: Session) -> None:
    player, first, second = _instance_pair(session)
    officer = session.scalar(select(NPC).where(NPC.key == "shen_ce"))
    assert officer is not None
    conversation = ConversationSession(
        player_id=player.id,
        game_instance_id=first.id,
        npc_id=officer.id,
    )
    session.add(conversation)
    session.flush()
    wrong_scope = GameInstanceService(session).load(GameInstanceId(second.id))

    result = ToolExecutor(session, build_registry()).execute(
        ToolContext(
            player_id=player.id,
            npc_id=officer.id,
            session_id=conversation.id,
            agent_run_id=uuid4(),
            message_id=uuid4(),
            scenario_key="starfire_command",
            runtime_scope=wrong_scope,
        ),
        ToolCall(id="scope-mismatch", name="unknown", arguments={}),
    )

    assert result.code == "RUNTIME_SCOPE_SESSION_MISMATCH"
