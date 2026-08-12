from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.domain.enums import AgentStepStatus
from app.infrastructure.db.models import AgentRun, ConversationSession
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService


@pytest.mark.parametrize(
    ("valley", "outpost", "trade", "expected"),
    [
        ("SAFE", "OPERATIONAL", "OPEN", True),
        ("UNSAFE", "OPERATIONAL", "OPEN", False),
        ("SAFE", "DAMAGED", "OPEN", False),
        ("SAFE", "OPERATIONAL", "CLOSED", False),
    ],
)
def test_starfire_task_completion_requires_all_world_objectives(
    session: Session,
    valley: str,
    outpost: str,
    trade: str,
    expected: bool,
) -> None:
    game = GameService(session)
    player = game.create_player(f"Objective {valley} {outpost} {trade}")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    tasks = TaskService(session)
    task = tasks.create_task(conversation, "Verify the Starfire objective", "starfire_command")
    run = AgentRun(
        request_id=uuid4(),
        session_id=conversation.id,
        task_id=task.id,
        actor_npc_id=conversation.npc_id,
        model="unit-test",
        input_message="objective characterization",
        max_rounds=0,
    )
    session.add(run)
    session.flush()
    plan = tasks.create_plan(
        task.id,
        "Verify the final state",
        [
            {
                "description": "Verify final state",
                "execution_type": "TOOL",
                "assigned_officer_key": "shen_ce",
                "action_intent": "VERIFY_AND_REPORT",
                "selected_tool_name": "inspect_command_state",
                "allowed_tool_names": ["inspect_command_state"],
                "tool_arguments": {},
                "expected_outcome": {},
            }
        ],
        created_by_run_id=run.id,
    )
    step = tasks.plan_steps(plan.id)[0]
    step.status = AgentStepStatus.SUCCEEDED
    game.set_world_fact(player.id, "valley_security", {"status": valley})
    game.set_world_fact(player.id, "starfire_outpost_status", {"status": outpost})
    game.set_world_fact(player.id, "northern_trade_route_status", {"status": trade})

    assert tasks.finish_if_complete(task, plan) is expected
