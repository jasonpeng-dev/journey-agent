"""Create deterministic browser-only PLAY history without calling a Provider."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from app.agent.generic import GenericAgentService, GenericGoalResolution
from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    StepExecutionType,
    WorldOperationStatus,
)
from app.domain.runtime_scope import GameInstanceId
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    AgentPlan,
    AgentStep,
    ConversationSession,
    GameInstance,
    GameInstanceFactState,
    PlayerExecutionCheckpoint,
    Scenario,
    ScenarioVersion,
    WorldOperation,
)
from app.infrastructure.db.session import SessionLocal
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import GameLifecycleService


SCENARIO_KEY = "linjiang_infrastructure_recovery_v2_0"


def create_fixture(kind: str) -> dict[str, str]:
    if kind not in {"presentation", "fork"}:
        raise ValueError(f"unsupported fixture kind: {kind}")

    db = SessionLocal()
    try:
        scenario = db.scalar(select(Scenario).where(Scenario.key == SCENARIO_KEY))
        if scenario is None or scenario.current_published_version_id is None:
            raise RuntimeError("current Linjiang production ScenarioVersion is unavailable")
        version = db.get(ScenarioVersion, scenario.current_published_version_id)
        if version is None:
            raise RuntimeError("current Linjiang production ScenarioVersion is unavailable")
        definition = ScenarioVersionRepository(db).load(version.id).definition
        objective = definition.objectives[0]
        action = next(item for item in definition.actions if item.key == "inspect")
        target = next(
            item for item in definition.world.nodes if item.key == "central_telecom_hub"
        )

        runtime = GameLifecycleService(db).create(
            scenario_version_id=version.id,
            idempotency_key=f"browser-smoke-{kind}-{uuid4()}",
        )
        db.flush()
        task = GenericAgentService(
            db,
            GameInstanceService(db).load(GameInstanceId(runtime.instance.id)),
        ).create_task(
            runtime.session,
            "E2E presentation history",
            resolved_goal=GenericGoalResolution(
                "RESOLVED",
                objective.key,
                (objective.key,),
            ),
            initialize_plan=False,
        )
        now = datetime.now(UTC)
        plan = AgentPlan(
            task_id=task.id,
            version=1,
            status=(
                AgentPlanStatus.ACTIVE
                if kind == "presentation"
                else AgentPlanStatus.SUCCEEDED
            ),
            strategy_summary="Deterministic browser presentation fixture",
            replan_reason=None,
            supersedes_plan_id=None,
            created_by_run_id=None,
            created_by_actor_key=runtime.session.actor_key,
            source="E2E_FIXTURE",
            planner_model=None,
            validation_status="PASSED",
            validation_errors=[],
            stop_reason="INFORMATION_BOUNDARY",
            created_at=now,
        )
        db.add(plan)
        db.flush()
        step = AgentStep(
            plan_id=plan.id,
            sequence=1,
            planner_step_id="e2e-inspect-step",
            description=action.name,
            execution_type=StepExecutionType.TOOL,
            status=AgentStepStatus.SUCCEEDED,
            assigned_actor_key=runtime.session.actor_key,
            action_intent=action.key,
            constraints={"fixture": True},
            allowed_tool_names=["execute_action"],
            selected_tool_name="execute_action",
            tool_arguments={
                "action_key": action.key,
                "target_key": target.key,
                "parameters": {},
            },
            expected_outcome={"outcome_code": "INSPECTED"},
            actual_result={"success": True},
            attempts=1,
            started_at=now,
            completed_at=now,
        )
        db.add(step)
        db.flush()

        changes = [
            {
                "kind": "FACT_REVEALED",
                "key": f"{target.key}.operational",
                "name": "operational",
                "value": False,
            },
            {
                "kind": "FACT_REVEALED",
                "key": f"{target.key}.power_supply",
                "name": "power_supply",
                "value": "AVAILABLE",
            },
        ]
        db.add(
            WorldOperation(
                player_id=runtime.instance.player_id,
                game_instance_id=runtime.instance.id,
                task_id=task.id,
                source_step_id=step.id,
                actor_key=runtime.session.actor_key,
                action_key=action.key,
                execution_mode="IMMEDIATE",
                target_key=target.key,
                status=WorldOperationStatus.RESOLVED,
                parameters={},
                outcome={
                    "success": True,
                    "outcome_code": "INSPECTED",
                    "knowledge_changes": changes,
                },
                idempotency_key=f"browser-smoke-operation-{uuid4()}",
                resolved_at=now,
            )
        )
        for fact_key in ("operational", "power_supply"):
            fact_state = db.get(
                GameInstanceFactState,
                (runtime.instance.id, target.key, fact_key),
            )
            if fact_state is not None:
                fact_state.visibility = Visibility.KNOWN

        task.current_plan_version = 1
        if kind == "presentation":
            task.status = AgentTaskStatus.WAITING_FOR_PLAYER_ACTION
            db.add(
                PlayerExecutionCheckpoint(
                    task_id=task.id,
                    game_instance_id=runtime.instance.id,
                    phase="AWAITING_DEBRIEF_ACK",
                    last_action_step_id=step.id,
                    version=1,
                )
            )
        else:
            task.status = AgentTaskStatus.SUCCEEDED
            task.completed_at = now
        db.flush()
        db.commit()
        return {"gameId": str(runtime.instance.id)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def append_post_fork_task(game_id: str) -> dict[str, str]:
    """Add one deterministic post-fork task without invoking planning."""

    db = SessionLocal()
    try:
        game = db.get(GameInstance, UUID(game_id))
        if game is None:
            raise RuntimeError("forked fixture GameInstance is unavailable")
        runtime = GameInstanceService(db).load(GameInstanceId(game.id))
        session = db.scalar(
            select(ConversationSession)
            .where(ConversationSession.game_instance_id == game.id)
            .order_by(ConversationSession.created_at, ConversationSession.id)
        )
        if session is None:
            raise RuntimeError("forked fixture ConversationSession is unavailable")
        version = db.get(ScenarioVersion, game.scenario_version_id)
        if version is None:
            raise RuntimeError("forked production ScenarioVersion is unavailable")
        definition = ScenarioVersionRepository(db).load(version.id).definition
        objective = definition.objectives[0]
        task = GenericAgentService(
            db,
            GameInstanceService(db).load(GameInstanceId(game.id)),
        ).create_task(
            session,
            "E2E post-fork task",
            resolved_goal=GenericGoalResolution(
                "RESOLVED",
                objective.key,
                (objective.key,),
            ),
            initialize_plan=False,
        )
        task.status = AgentTaskStatus.WAITING_FOR_PLAYER_ACTION
        db.commit()
        return {"taskId": str(task.id)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] in {"presentation", "fork"}:
        print(json.dumps(create_fixture(sys.argv[1])))
        return
    if len(sys.argv) == 3 and sys.argv[1] == "append":
        print(json.dumps(append_post_fork_task(sys.argv[2])))
        return
    raise ValueError(
        "usage: prepare_history_fixture.py presentation|fork | append GAME_ID"
    )


if __name__ == "__main__":
    main()