from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.planning import PlanValidator, build_planning_request
from app.agent.strategic_starfire_plans import initial_strategic_starfire_plan
from app.core.config import Settings
from app.infrastructure.db.models import NPC, ConversationSession, OfficerAppointment
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService
from app.tools.catalog import build_registry


def _context(session: Session):  # type: ignore[no-untyped-def]
    player = GameService(session).create_player("Strategic Planner")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    task = TaskService(session).create_task(
        conversation,
        "修复星火前哨并重新打通北方商路。",
        "starfire_command",
    )
    validator = PlanValidator(
        session,
        build_registry(),
        Settings(database_url="sqlite+pysqlite:///:memory:"),
    )
    return conversation, task, validator


def _codes(result) -> set[str]:  # type: ignore[no-untyped-def]
    return {item.code for item in result.errors}


def test_valid_strategic_plan_is_normalized_and_accepted(session: Session) -> None:
    conversation, task, validator = _context(session)

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=initial_strategic_starfire_plan(task.id),
    )

    assert result.status == "PASSED"
    assert result.normalized_arguments is not None
    assert result.normalized_arguments["task_id"] == str(task.id)
    assert result.normalized_arguments["idempotency_key"].endswith("-v1")


def test_plan_validates_each_step_against_its_assigned_officer(session: Session) -> None:
    conversation, task, validator = _context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    military = next(
        step
        for step in proposal["steps"]
        if step["selected_tool_name"] == "start_military_operation"
    )
    military["assigned_officer_key"] = "lu_ning"

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_TOOL_UNAUTHORIZED" in _codes(result)


def test_plan_rejects_invalid_appointment_authority_policy(session: Session) -> None:
    conversation, task, validator = _context(session)
    lu_ning = session.scalar(select(NPC).where(NPC.key == "lu_ning"))
    assert lu_ning is not None
    appointment = session.get(OfficerAppointment, (task.player_id, lu_ning.id))
    assert appointment is not None
    appointment.authority_overrides = {"max_food": -1}
    appointment.version += 1

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=initial_strategic_starfire_plan(task.id),
    )

    assert "PLAN_AUTHORITY_POLICY_INVALID" in _codes(result)


def test_world_wait_must_immediately_follow_its_operation(session: Session) -> None:
    conversation, task, validator = _context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    wait = proposal["steps"].pop(1)
    proposal["steps"].insert(3, wait)

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_WORLD_EVENT_PAIRING_INVALID" in _codes(result)


def test_world_wait_cannot_treat_defeat_as_success(session: Session) -> None:
    conversation, task, validator = _context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    military_index = next(
        index
        for index, step in enumerate(proposal["steps"])
        if step["selected_tool_name"] == "start_military_operation"
    )
    wait = proposal["steps"][military_index + 1]
    wait["resume_condition"]["success_outcomes"] = ["DEFEAT"]
    wait["expected_outcome"] = {"operation_result_in": ["DEFEAT"]}

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_WORLD_EVENT_OUTCOMES_INVALID" in _codes(result)


def test_operation_expected_type_must_match_selected_tool(session: Session) -> None:
    conversation, task, validator = _context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    recon = next(
        step for step in proposal["steps"] if step["selected_tool_name"] == "start_recon_operation"
    )
    recon["expected_outcome"]["operation_type"] = "MILITARY"

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_EXPECTED_OUTCOME_VALUE_INVALID" in _codes(result)


def test_planning_context_exposes_only_strategic_tools_and_fixed_contracts(
    session: Session,
) -> None:
    conversation, task, _validator = _context(session)

    request = build_planning_request(
        db=session,
        registry=build_registry(),
        settings=Settings(database_url="sqlite+pysqlite:///:memory:"),
        task=task,
        session=conversation,
        kind="PLAN",
    )

    tools = {item["name"]: item for item in request["allowed_tools"]}
    assert set(tools) == {
        "inspect_command_state",
        "start_recon_operation",
        "start_military_operation",
        "negotiate_village_support",
        "start_outpost_repair",
        "start_trade_route_test",
    }
    assert tools["start_recon_operation"]["required_expected_outcomes"] == {
        "operation_type": "RECONNAISSANCE",
        "status": "PENDING",
    }
    assert {officer["key"] for officer in request["officers"]} == {
        "shen_ce",
        "han_lie",
        "lu_ning",
    }
    assert "approved_resources" not in request


def test_plan_requires_final_shen_ce_verification(session: Session) -> None:
    conversation, task, validator = _context(session)
    proposal = deepcopy(initial_strategic_starfire_plan(task.id))
    proposal["steps"][-1]["assigned_officer_key"] = "han_lie"

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_FINAL_VERIFICATION_REQUIRED" in _codes(result)


def test_backend_controls_step_idempotency_keys(session: Session) -> None:
    conversation, task, validator = _context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    proposal["steps"][0]["tool_arguments"]["idempotency_key"] = "model-key-1234"

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_IDEMPOTENCY_SERVER_CONTROLLED" in _codes(result)
