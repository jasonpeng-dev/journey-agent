from copy import deepcopy
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.enums import AgentTaskStatus, WorldOperationStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import (
    AgentTask,
    GameInstanceFactState,
    GameInstanceResourceState,
    Player,
    WorldOperation,
)
from app.scenarios.builtin import require_builtin_v2_version
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.runtime_recovery import RuntimeRecoveryService
from tests.scenario_fixtures import MEDICAL_TEST, STARFIRE_TEST


def _drive(
    db: Session,
    agent: GenericAgentService,
    actions: GenericActionService,
    task: AgentTask,
) -> None:
    for index in range(30):
        agent.execute_next(task)
        pending = db.scalar(
            select(WorldOperation)
            .where(
                WorldOperation.game_instance_id == agent.scope.game_instance_id,
                WorldOperation.status == WorldOperationStatus.PENDING,
            )
            .order_by(WorldOperation.created_at.desc())
        )
        if pending is not None:
            actions.resolve_operation(pending.id, resolution_key=f"e2e-event-{index}")
        if task.status == AgentTaskStatus.SUCCEEDED:
            return
    raise AssertionError("Generic Agent did not complete within the deterministic bound")


def test_two_different_v2_games_run_isolated_and_recover_exact_versions(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'dual-v2.db').as_posix()}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        starfire_version = require_builtin_v2_version(db, STARFIRE_TEST)
        medical_version = require_builtin_v2_version(db, MEDICAL_TEST)
        player = Player(name="dual-scenario-player")
        db.add(player)
        db.flush()
        initializer = RuntimeInitializationService(db)
        starfire_runtime = initializer.create(
            player_id=player.id,
            scenario_version_id=starfire_version.id,
            creation_key="dual-starfire",
        )
        medical_runtime = initializer.create(
            player_id=player.id,
            scenario_version_id=medical_version.id,
            creation_key="dual-medical",
        )
        scopes = GameInstanceService(db)
        starfire_scope = scopes.load(GameInstanceId(starfire_runtime.instance.id))
        medical_scope = scopes.load(GameInstanceId(medical_runtime.instance.id))
        starfire_agent = GenericAgentService(db, starfire_scope)
        medical_agent = GenericAgentService(db, medical_scope)
        starfire_task = starfire_agent.create_task(
            starfire_runtime.session, "secure the northern valley"
        )
        medical_task = medical_agent.create_task(medical_runtime.session, "stabilize the patient")

        _drive(
            db,
            starfire_agent,
            GenericActionService(db, starfire_scope),
            starfire_task,
        )
        _drive(
            db,
            medical_agent,
            GenericActionService(db, medical_scope),
            medical_task,
        )

        assert starfire_task.status == medical_task.status == AgentTaskStatus.SUCCEEDED
        assert starfire_task.replan_count >= 1
        assert medical_task.replan_count == 0
        assert (
            db.get(
                GameInstanceFactState,
                (starfire_runtime.instance.id, "northern_valley", "valley_security"),
            ).truth_value
            == "SAFE"
        )
        assert (
            db.get(
                GameInstanceFactState,
                (medical_runtime.instance.id, "patient_one", "stable"),
            ).truth_value
            is True
        )
        assert (
            db.get(
                GameInstanceResourceState,
                (starfire_runtime.instance.id, "soldiers"),
            )
            is not None
        )
        assert (
            db.get(
                GameInstanceResourceState,
                (starfire_runtime.instance.id, "medicine"),
            )
            is None
        )
        assert (
            db.get(
                GameInstanceResourceState,
                (medical_runtime.instance.id, "medicine"),
            ).value
            == 3
        )
        assert (
            db.get(
                GameInstanceResourceState,
                (medical_runtime.instance.id, "soldiers"),
            )
            is None
        )

        changed_payload = deepcopy(STARFIRE_TEST.model_dump(mode="json"))
        changed_payload["metadata"]["name"] = "Starfire Later Version"
        changed_payload["world"]["name"] = "Starfire Later Version"
        later_version = require_builtin_v2_version(
            db, ScenarioDefinitionV2.model_validate(changed_payload)
        )
        assert later_version.id != starfire_version.id
        pending = (
            GenericActionService(db, starfire_scope)
            .execute_action(
                actor_key="han_lie",
                action_key="recon_valley",
                target_key="northern_valley",
                parameters={"troop_count": 30, "approach": "CAUTIOUS"},
                idempotency_key="restart-pending-operation",
            )
            .operation
        )
        assert pending.status == WorldOperationStatus.PENDING
        ids = {
            "player": player.id,
            "starfire_instance": starfire_runtime.instance.id,
            "medical_instance": medical_runtime.instance.id,
            "starfire_version": starfire_version.id,
            "medical_version": medical_version.id,
            "starfire_task": starfire_task.id,
            "medical_task": medical_task.id,
            "pending": pending.id,
        }
        db.commit()
    engine.dispose()

    restarted = create_engine(database_url)
    with Session(restarted) as db:
        recovery = RuntimeRecoveryService(db)
        starfire = recovery.recover(GameInstanceId(ids["starfire_instance"]))
        medical = recovery.recover(GameInstanceId(ids["medical_instance"]))

        assert starfire.scope.player_id == medical.scope.player_id == ids["player"]
        assert starfire.scope.scenario_version_id == ids["starfire_version"]
        assert medical.scope.scenario_version_id == ids["medical_version"]
        assert {actor.actor_key for actor in starfire.actors} == {
            "shen_ce",
            "han_lie",
            "lu_ning",
        }
        assert {actor.actor_key for actor in medical.actors} == {
            "doctor_lee",
            "nurse_ana",
        }
        assert {event.event_key for event in medical.generic_memories} == {"patient_stabilized"}
        assert starfire.generic_memories == ()
        assert {operation.id for operation in starfire.pending_operations} == {ids["pending"]}
        assert medical.pending_operations == ()
        assert db.get(AgentTask, ids["starfire_task"]).status == AgentTaskStatus.SUCCEEDED
        assert db.get(AgentTask, ids["medical_task"]).status == AgentTaskStatus.SUCCEEDED
        assert (
            GenericAgentService(db, starfire.scope)
            .evaluate(db.get(AgentTask, ids["starfire_task"]))
            .completed
        )
        GenericActionService(db, starfire.scope).resolve_operation(
            ids["pending"], resolution_key="restart-recovery-event"
        )
        db.commit()
        assert (
            RuntimeRecoveryService(db)
            .recover(GameInstanceId(ids["starfire_instance"]))
            .pending_operations
            == ()
        )
        assert (
            GenericAgentService(db, medical.scope)
            .evaluate(db.get(AgentTask, ids["medical_task"]))
            .completed
        )
    restarted.dispose()
