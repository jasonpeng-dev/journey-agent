from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.enums import CommandReachability
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import (
    AgentTask,
    GameInstanceActor,
    GameInstanceResourceState,
    Player,
)
from app.scenarios.builtin import require_builtin_v2_version
from app.services.game_instances import GameInstanceService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.runtime_recovery import RuntimeRecoveryService
from tests.scenario_fixtures import GENERIC_TEST, LINJIANG_V2_TEST, predefined_goal_resolution


def test_dual_scenario_runtime_state_isolation_and_recovery(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'dual-v2.db').as_posix()}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        generic_version = require_builtin_v2_version(db, GENERIC_TEST)
        linjiang_version = require_builtin_v2_version(db, LINJIANG_V2_TEST)
        assert generic_version.id != linjiang_version.id

        player = Player(name="dual-scenario-player")
        db.add(player)
        db.flush()

        initializer = RuntimeInitializationService(db)
        generic_runtime = initializer.create(
            player_id=player.id,
            scenario_version_id=generic_version.id,
            creation_key="dual-generic",
        )
        linjiang_runtime = initializer.create(
            player_id=player.id,
            scenario_version_id=linjiang_version.id,
            creation_key="dual-linjiang",
        )
        assert (
            generic_runtime.instance.player_id == linjiang_runtime.instance.player_id == player.id
        )
        assert generic_runtime.instance.id != linjiang_runtime.instance.id
        player_id = player.id
        generic_version_id = generic_version.id
        linjiang_version_id = linjiang_version.id

        scopes = GameInstanceService(db)
        generic_scope = scopes.load(GameInstanceId(generic_runtime.instance.id))
        linjiang_scope = scopes.load(GameInstanceId(linjiang_runtime.instance.id))
        assert generic_scope.scenario_version_id == generic_version.id
        assert linjiang_scope.scenario_version_id == linjiang_version.id

        generic_task = GenericAgentService(db, generic_scope).create_task(
            generic_runtime.session,
            "stabilize the patient",
            initialize_plan=False,
        )
        linjiang_task = GenericAgentService(db, linjiang_scope).create_task(
            linjiang_runtime.session,
            "restore central communications",
            resolved_goal=predefined_goal_resolution("restore_central_communication_capability"),
            initialize_plan=False,
        )
        generic_actor = db.get(
            GameInstanceActor,
            (generic_runtime.instance.id, generic_runtime.session.actor_key),
        )
        linjiang_actor = db.get(
            GameInstanceActor,
            (linjiang_runtime.instance.id, linjiang_runtime.session.actor_key),
        )
        generic_resource = db.scalar(
            select(GameInstanceResourceState)
            .where(GameInstanceResourceState.game_instance_id == generic_runtime.instance.id)
            .order_by(GameInstanceResourceState.resource_identity)
        )
        linjiang_resource = db.scalar(
            select(GameInstanceResourceState)
            .where(GameInstanceResourceState.game_instance_id == linjiang_runtime.instance.id)
            .order_by(GameInstanceResourceState.resource_identity)
        )
        assert generic_actor is not None and linjiang_actor is not None
        assert generic_resource is not None and linjiang_resource is not None
        generic_resource_identity = generic_resource.resource_identity
        linjiang_resource_identity = linjiang_resource.resource_identity
        generic_resource_value = generic_resource.value
        linjiang_resource_value = linjiang_resource.value

        generic_actor.command_reachability = CommandReachability.DISCONNECTED.value
        generic_resource.value += 7
        generic_task.replan_count = 2
        db.flush()
        assert linjiang_actor.command_reachability == CommandReachability.ONLINE.value
        assert linjiang_resource.value == linjiang_resource_value
        assert linjiang_task.replan_count == 0

        linjiang_actor.command_reachability = CommandReachability.DISCONNECTED.value
        linjiang_resource.value += 11
        linjiang_task.replan_count = 3
        db.flush()
        assert generic_actor.command_reachability == CommandReachability.DISCONNECTED.value
        assert generic_resource.value == generic_resource_value + 7
        assert generic_task.replan_count == 2
        db.commit()

        generic_id = generic_runtime.instance.id
        linjiang_id = linjiang_runtime.instance.id
        generic_task_id = generic_task.id
        linjiang_task_id = linjiang_task.id
    engine.dispose()

    restarted = create_engine(database_url)
    with Session(restarted) as db:
        recovery = RuntimeRecoveryService(db)
        generic_recovered = recovery.recover(GameInstanceId(generic_id))
        linjiang_recovered = recovery.recover(GameInstanceId(linjiang_id))

        assert generic_recovered.scope.player_id == linjiang_recovered.scope.player_id == player_id
        assert generic_recovered.scope.scenario_version_id == generic_version_id
        assert linjiang_recovered.scope.scenario_version_id == linjiang_version_id
        assert {task.id for task in generic_recovered.tasks} == {generic_task_id}
        assert {task.id for task in linjiang_recovered.tasks} == {linjiang_task_id}
        assert all(task.game_instance_id == generic_id for task in generic_recovered.tasks)
        assert all(task.game_instance_id == linjiang_id for task in linjiang_recovered.tasks)

        generic_actor = db.get(
            GameInstanceActor,
            (generic_id, generic_recovered.sessions[0].actor_key),
        )
        linjiang_actor = db.get(
            GameInstanceActor,
            (linjiang_id, linjiang_recovered.sessions[0].actor_key),
        )
        generic_resource = db.get(
            GameInstanceResourceState,
            (generic_id, generic_resource_identity),
        )
        linjiang_resource = db.get(
            GameInstanceResourceState,
            (linjiang_id, linjiang_resource_identity),
        )
        generic_task = db.get(AgentTask, generic_task_id)
        linjiang_task = db.get(AgentTask, linjiang_task_id)
        assert generic_actor is not None and linjiang_actor is not None
        assert generic_resource is not None and linjiang_resource is not None
        assert generic_task is not None and linjiang_task is not None
        assert generic_actor.command_reachability == CommandReachability.DISCONNECTED.value
        assert linjiang_actor.command_reachability == CommandReachability.DISCONNECTED.value
        assert generic_resource.value == generic_resource_value + 7
        assert linjiang_resource.value == linjiang_resource_value + 11
        assert generic_task.replan_count == 2
        assert linjiang_task.replan_count == 3
        assert generic_task.game_instance_id == generic_id
        assert linjiang_task.game_instance_id == linjiang_id
    restarted.dispose()
