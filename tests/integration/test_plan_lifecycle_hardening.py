import asyncio
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.agent.providers import MockModelProvider
from app.agent.task_orchestrator import TaskOrchestrator
from app.core.config import Settings
from app.debug.snapshot_service import StrategicSnapshotService
from app.domain.enums import AgentStepStatus, AgentTaskStatus
from app.infrastructure.db.models import AgentRun, ConversationSession
from app.scenarios.contracts import GoalResolutionResult, ObjectiveResolutionStatus
from app.scenarios.starfire.objective_catalog import (
    STARFIRE_OBJECTIVE_CATALOG,
    StarfireObjectiveKey,
)
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService


def test_settled_successful_operation_completes_wait_before_early_stop(
    session: Session,
) -> None:
    game = GameService(session)
    player = game.create_player("Wait lifecycle hardening")
    game.set_world_fact(player.id, "valley_security", {"status": "SAFE"})
    game.unlock_node(player.id, "starfire_outpost")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    tasks = TaskService(session)
    task = tasks.create_task(conversation, "Restore Starfire Outpost", "starfire_command")
    scope = STARFIRE_OBJECTIVE_CATALOG.scope([StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST])
    tasks.record_goal_resolution(
        task,
        GoalResolutionResult(status=ObjectiveResolutionStatus.RESOLVED, scope=scope),
    )
    tasks.confirm_and_freeze_scope(
        task,
        scope,
        confirmation_source="TEST",
        freeze_source="TEST",
    )
    run = AgentRun(
        request_id=uuid4(),
        session_id=conversation.id,
        task_id=task.id,
        model="unit-test",
        input_message="wait lifecycle",
        max_rounds=0,
    )
    session.add(run)
    session.flush()
    plan = tasks.create_plan(
        task.id,
        "Repair, settle the operation, then leave a genuinely future report step",
        [
            {
                "description": "Repair the outpost",
                "execution_type": "TOOL",
                "assigned_officer_key": "lu_ning",
                "action_intent": "REPAIR_OUTPOST",
                "allowed_tool_names": ["start_outpost_repair"],
                "selected_tool_name": "start_outpost_repair",
                "tool_arguments": {
                    "target_key": "starfire_outpost",
                    "repair_level": "TEMPORARY",
                    "food_commitment": 20,
                    "gold_commitment": 20,
                },
                "expected_outcome": {
                    "status": "PENDING",
                    "operation_type": "CONSTRUCTION",
                },
            },
            {
                "description": "Wait for construction completion",
                "execution_type": "WAIT_FOR_WORLD_EVENT",
                "assigned_officer_key": "lu_ning",
                "action_intent": "WAIT_FOR_OPERATION",
                "allowed_tool_names": [],
                "selected_tool_name": None,
                "tool_arguments": {},
                "expected_outcome": {"operation_result_in": ["COMPLETED"]},
                "resume_condition": {
                    "type": "WORLD_OPERATION",
                    "source_step_sequence": 1,
                    "success_outcomes": ["COMPLETED"],
                },
            },
            {
                "description": "Optional future report",
                "execution_type": "TOOL",
                "assigned_officer_key": "shen_ce",
                "action_intent": "REPORT_ONLY",
                "allowed_tool_names": ["inspect_command_state"],
                "selected_tool_name": "inspect_command_state",
                "tool_arguments": {},
                "expected_outcome": {"starfire_outpost_status": "OPERATIONAL"},
            },
        ],
        created_by_run_id=run.id,
    )
    orchestrator = TaskOrchestrator(session, MockModelProvider(), _settings())

    _task, _run, event = asyncio.run(orchestrator.advance(task.id, conversation))
    assert event == "STEP_SUCCEEDED", tasks.plan_steps(plan.id)[0].actual_result
    _task, _run, event = asyncio.run(orchestrator.advance(task.id, conversation))
    assert event == "WAITING_FOR_WORLD_EVENT"
    pending = tasks.serialize(task)["pending_world_event"]
    assert isinstance(pending, dict)
    game.resolve_world_operation(UUID(str(pending["id"])), "wait-lifecycle-resolution")
    session.commit()

    task, _run, event = asyncio.run(orchestrator.advance(task.id, conversation))

    steps = tasks.plan_steps(plan.id)
    assert event == "TASK_SUCCEEDED"
    assert task.status == AgentTaskStatus.SUCCEEDED
    assert steps[1].status == AgentStepStatus.SUCCEEDED
    assert steps[1].actual_result is not None
    assert steps[1].actual_result["status"] == "RESOLVED"
    assert steps[2].status == AgentStepStatus.SKIPPED
    assert steps[2].actual_result == {"skip_reason": "OBJECTIVE_SCOPE_SATISFIED"}
    snapshot = StrategicSnapshotService(session, _settings()).build(
        conversation.id,
        include_trace=False,
        include_hidden_truth=False,
    )
    assert snapshot["early_stop"] == {
        "triggered": True,
        "reason": "OBJECTIVE_SCOPE_SATISFIED",
        "skipped_future_step_count": 1,
        "skipped_future_steps": [
            {
                "plan_version": 1,
                "step_sequence": 3,
                "description": "Optional future report",
                "execution_type": "TOOL",
                "selected_tool_name": "inspect_command_state",
            }
        ],
    }
    pair = snapshot["operation_wait_pairs"][0]
    assert pair["operation_step_sequence"] == 1
    assert pair["wait_step_sequence"] == 2
    assert pair["wait_status"] == "SUCCEEDED"
    assert pair["wait_result"]["status"] == "RESOLVED"
    outside_scope = snapshot["objective_evaluation"]["outside_scope_state"]
    assert {
        "node_key": "northern_trade_route",
        "fact_key": "trade_route_status",
        "actual_value": "CLOSED",
        "scope_relation": "OUTSIDE_CURRENT_SCOPE",
    } in outside_scope
    assert any(item["kind"] == "EARLY_STOP" for item in snapshot["timeline"])
    assert game.inspect_command_state(player.id)["world"]["northern_trade_route_status"] == (
        "CLOSED"
    )


def _settings() -> Settings:
    return Settings(database_url="sqlite+pysqlite:///:memory:")
