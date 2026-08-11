from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.types import ToolCall, ToolContext
from app.core.errors import AppError
from app.domain.enums import AgentStepStatus, AgentTaskStatus
from app.infrastructure.db.models import (
    AgentRun,
    AgentTask,
    ConversationSession,
    ToolExecution,
)
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService
from app.tools.catalog import build_registry
from app.tools.executor import ToolExecutor


def _task_context(session: Session) -> tuple[AgentTask, ConversationSession, AgentRun]:
    player = GameService(session).create_player("Task Rules")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:captain_aria"),
    )
    session.add(conversation)
    session.flush()
    task = TaskService(session).create_task(
        conversation,
        "Restore Starfire Outpost through an auditable plan.",
        "starfire_outpost",
    )
    run = AgentRun(
        request_id=uuid4(),
        session_id=conversation.id,
        task_id=task.id,
        model="unit-test",
        input_message="plan",
        max_rounds=1,
    )
    session.add(run)
    session.flush()
    return task, conversation, run


def test_plan_rejects_an_unregistered_execution_capability(session: Session) -> None:
    task, _, run = _task_context(session)

    with pytest.raises(AppError) as exc_info:
        TaskService(session).create_plan(
            task.id,
            "Attempt an unsafe plan.",
            [
                {
                    "description": "Bypass the executor",
                    "execution_type": "TOOL",
                    "selected_tool_name": "patch_database",
                    "tool_arguments": {},
                    "expected_outcome": {},
                }
            ],
            created_by_run_id=run.id,
        )

    assert exc_info.value.code == "PLAN_TOOL_NOT_ALLOWED"
    session.rollback()


def test_step_tool_arguments_are_bound_to_the_audited_plan(session: Session) -> None:
    task, conversation, planning_run = _task_context(session)
    plan = TaskService(session).create_plan(
        task.id,
        "Request approved assistance.",
        [
            {
                "description": "Request assistance",
                "execution_type": "TOOL",
                "selected_tool_name": "request_npc_assistance",
                "tool_arguments": {},
                "expected_outcome": {"assistance_active": True},
            }
        ],
        created_by_run_id=planning_run.id,
    )
    step = TaskService(session).plan_steps(plan.id)[0]
    execution_run = AgentRun(
        request_id=uuid4(),
        session_id=conversation.id,
        task_id=task.id,
        plan_id=plan.id,
        step_id=step.id,
        model="unit-test",
        input_message="execute",
        max_rounds=1,
    )
    session.add(execution_run)
    session.commit()
    expected = {"idempotency_key": "expected-task-key"}

    result = ToolExecutor(session, build_registry()).execute(
        ToolContext(
            player_id=conversation.player_id,
            npc_id=conversation.npc_id,
            session_id=conversation.id,
            agent_run_id=execution_run.id,
            message_id=uuid4(),
            task_id=task.id,
            plan_id=plan.id,
            step_id=step.id,
            planned_arguments=expected,
        ),
        ToolCall(
            id="argument-tamper",
            name="request_npc_assistance",
            arguments={"idempotency_key": "different-task-key"},
        ),
    )

    session.refresh(task)
    session.refresh(step)
    trace = session.scalar(
        select(ToolExecution).where(ToolExecution.tool_call_id == "argument-tamper")
    )
    assert result.code == "STEP_ARGUMENT_MISMATCH"
    assert task.status == AgentTaskStatus.BLOCKED
    assert step.status == AgentStepStatus.BLOCKED
    assert trace is not None
    assert trace.authorization_status == "DENIED"
    assert trace.execution_status == "FAILED"
