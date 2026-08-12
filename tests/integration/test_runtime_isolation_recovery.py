from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    DecisionStatus,
    MemoryType,
    StepExecutionType,
    WorldOperationStatus,
)
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import (
    NPC,
    AgentPlan,
    AgentStep,
    Memory,
    Player,
    PlayerDecisionRequest,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.scenarios.starfire.scenario import STARFIRE_SCENARIO_DEFINITION
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game import GameService
from app.services.game_instances import GameInstanceService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.runtime_recovery import RuntimeRecoveryError, RuntimeRecoveryService
from app.services.scenarios import ScenarioService
from app.services.seed import seed_demo_world
from app.services.tasks import TaskService


def test_multi_instance_version_isolation_and_restart_recovery(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'runtime-recovery.db').as_posix()}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        seed_demo_world(db)
        scenario = ScenarioDefinitionRepository(db).persist_initial_draft(
            STARFIRE_SCENARIO_DEFINITION
        )
        scenario_service = ScenarioService(db)
        version_one = scenario_service.publish_draft(
            scenario.id,
            expected_revision=1,
        ).version
        changed = deepcopy(version_one.snapshot_document)
        changed["world"]["name"] = "Starfire isolated v2"
        changed["objective_catalog"]["definitions"][0]["description"] = (
            "Version two objective content"
        )
        scenario_service.replace_draft(
            scenario.id,
            expected_revision=1,
            definition_document=changed,
        )
        version_two = scenario_service.publish_draft(
            scenario.id,
            expected_revision=2,
        ).version
        player = Player(name="one-player-two-games")
        db.add(player)
        db.flush()
        initializer = RuntimeInitializationService(db)
        runtime_a = initializer.create(
            player_id=player.id,
            scenario_version_id=version_one.id,
            creation_key="instance-a",
        )
        runtime_b = initializer.create(
            player_id=player.id,
            scenario_version_id=version_two.id,
            creation_key="instance-b",
        )
        task_a = TaskService(db).create_task(
            runtime_a.session,
            "Secure the northern valley",
            "starfire_command",
        )
        task_b = TaskService(db).create_task(
            runtime_b.session,
            "Restore the Starfire outpost",
            "starfire_command",
        )
        strategist = db.get(NPC, task_a.owner_npc_id)
        general = db.scalar(select(NPC).where(NPC.key == "han_lie"))
        assert strategist is not None and general is not None
        db.add_all(
            [
                Memory(
                    player_id=player.id,
                    game_instance_id=runtime_a.instance.id,
                    npc_id=strategist.id,
                    type=MemoryType.WORLD_EVENT,
                    content="A-only memory",
                    source_session_id=runtime_a.session.id,
                ),
                Memory(
                    player_id=player.id,
                    game_instance_id=runtime_b.instance.id,
                    npc_id=strategist.id,
                    type=MemoryType.WORLD_EVENT,
                    content="B-only memory",
                    source_session_id=runtime_b.session.id,
                ),
            ]
        )
        _decision(db, task_a.id, player.id, runtime_a.instance.id, strategist.id)
        _decision(db, task_b.id, player.id, runtime_b.instance.id, strategist.id)
        scope_service = GameInstanceService(db)
        game_a = GameService(
            db,
            scope_service.load(GameInstanceId(runtime_a.instance.id)),
        )
        game_b = GameService(
            db,
            scope_service.load(GameInstanceId(runtime_b.instance.id)),
        )
        operation_a = game_a.start_recon_operation(
            player_id=player.id,
            officer_npc_id=general.id,
            task_id=None,
            source_step_id=None,
            target_key="northern_valley",
            troop_count=20,
            approach="CAUTIOUS",
            idempotency_key="same-operation-key",
        )
        operation_b = game_b.start_recon_operation(
            player_id=player.id,
            officer_npc_id=general.id,
            task_id=None,
            source_step_id=None,
            target_key="northern_valley",
            troop_count=20,
            approach="CAUTIOUS",
            idempotency_key="same-operation-key",
        )
        fresh = RuntimeRecoveryService(db).recover(GameInstanceId(runtime_a.instance.id))
        assert fresh.pending_operations[0].id == operation_a.id
        ids = {
            "player": player.id,
            "a": runtime_a.instance.id,
            "b": runtime_b.instance.id,
            "v1": version_one.id,
            "v2": version_two.id,
            "operation_a": operation_a.id,
            "operation_b": operation_b.id,
        }
        db.commit()
    engine.dispose()

    restarted_engine = create_engine(database_url)
    with Session(restarted_engine) as db:
        recovery = RuntimeRecoveryService(db)
        recovered_a = recovery.recover(GameInstanceId(ids["a"]))
        recovered_b = recovery.recover(GameInstanceId(ids["b"]))

        assert recovered_a.scope.player_id == recovered_b.scope.player_id == ids["player"]
        assert recovered_a.scope.scenario_version_id == ids["v1"]
        assert recovered_b.scope.scenario_version_id == ids["v2"]
        assert {task.goal_description for task in recovered_a.tasks} == {
            "Secure the northern valley"
        }
        assert {task.goal_description for task in recovered_b.tasks} == {
            "Restore the Starfire outpost"
        }
        assert {memory.content for memory in recovered_a.memories} == {"A-only memory"}
        assert {memory.content for memory in recovered_b.memories} == {"B-only memory"}
        assert len(recovered_a.decisions) == len(recovered_b.decisions) == 1
        assert {operation.id for operation in recovered_a.pending_operations} == {
            ids["operation_a"]
        }
        assert {operation.id for operation in recovered_b.pending_operations} == {
            ids["operation_b"]
        }
        version_one = ScenarioVersionRepository(db).load(ids["v1"])
        version_two = ScenarioVersionRepository(db).load(ids["v2"])
        assert version_one.definition.world.name != version_two.definition.world.name
        assert (
            version_one.definition.objective_definitions
            != version_two.definition.objective_definitions
        )
        game_a = GameService(db, recovered_a.scope)
        game_a.resolve_world_operation(ids["operation_a"], "restart-resolution")
        db.commit()

        recovered_a = recovery.recover(GameInstanceId(ids["a"]))
        recovered_b = recovery.recover(GameInstanceId(ids["b"]))
        assert recovered_a.pending_operations == ()
        assert recovered_b.pending_operations[0].status == WorldOperationStatus.PENDING
        assert (
            GameService(db, recovered_a.scope)
            .scenario_truth_state(ids["player"])
            .fact_value("northern_valley", "valley_intelligence")
            == "PARTIAL"
        )
        assert (
            GameService(db, recovered_b.scope)
            .scenario_truth_state(ids["player"])
            .fact_value("northern_valley", "valley_intelligence")
            == "INCOMPLETE"
        )
        memory_b = recovered_b.memories[0]
        memory_b.game_instance_id = ids["a"]
        db.commit()
        with pytest.raises(RuntimeRecoveryError) as corrupt:
            recovery.recover(GameInstanceId(ids["a"]))
        assert corrupt.value.code == "RUNTIME_GRAPH_OWNERSHIP_INVALID"
    restarted_engine.dispose()


def _decision(
    db: Session,
    task_id: UUID,
    player_id: UUID,
    game_instance_id: UUID,
    strategist_id: UUID,
) -> None:
    plan = AgentPlan(
        task_id=task_id,
        version=1,
        status=AgentPlanStatus.ACTIVE,
        strategy_summary="Decision isolation fixture",
        source="TEST",
        validation_status="PASSED",
        validation_errors=[],
    )
    db.add(plan)
    db.flush()
    step = AgentStep(
        plan_id=plan.id,
        sequence=1,
        description="Approve isolated action",
        execution_type=StepExecutionType.TOOL,
        status=AgentStepStatus.REQUIRES_PLAYER_DECISION,
        assigned_npc_id=strategist_id,
        selected_tool_name="inspect_command_state",
    )
    db.add(step)
    db.flush()
    db.add(
        PlayerDecisionRequest(
            player_id=player_id,
            game_instance_id=game_instance_id,
            task_id=task_id,
            step_id=step.id,
            requested_by_npc_id=strategist_id,
            status=DecisionStatus.PENDING,
            summary="Instance-only decision",
            options=[{"id": "APPROVE"}],
            action_tool_name="inspect_command_state",
            action_arguments={},
        )
    )
