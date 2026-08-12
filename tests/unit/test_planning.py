from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.planning import PlanValidator, build_planning_request
from app.core.config import Settings
from app.infrastructure.db.models import (
    NPC,
    AgentRun,
    AgentStep,
    ConversationSession,
    OfficerAppointment,
)
from app.scenarios.starfire.fallback_plans import initial_strategic_starfire_plan
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


def test_plan_validation_builds_known_state_once_for_all_steps(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation, task, validator = _context(session)
    original = GameService.scenario_known_state
    calls = 0

    def counted_known_state(game: GameService, player_id):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original(game, player_id)

    monkeypatch.setattr(GameService, "scenario_known_state", counted_known_state)

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=initial_strategic_starfire_plan(task.id),
    )

    assert result.status == "PASSED"
    assert calls == 1


def test_new_starfire_plan_uses_canonical_explicit_targets(session: Session) -> None:
    _conversation, task, _validator = _context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    tool_arguments = {
        step["selected_tool_name"]: step["tool_arguments"]
        for step in proposal["steps"]
        if step["selected_tool_name"]
    }

    assert tool_arguments["start_recon_operation"]["target_key"] == "northern_valley"
    assert tool_arguments["start_military_operation"]["target_key"] == "northern_valley"
    assert tool_arguments["start_outpost_repair"]["target_key"] == "starfire_outpost"
    assert tool_arguments["start_trade_route_test"] == {"target_key": "northern_trade_route"}


def test_legacy_plan_targets_validate_without_rewriting_raw_arguments(
    session: Session,
) -> None:
    conversation, task, validator = _context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    recon = next(
        step for step in proposal["steps"] if step["selected_tool_name"] == "start_recon_operation"
    )
    military = next(
        step
        for step in proposal["steps"]
        if step["selected_tool_name"] == "start_military_operation"
    )
    repair = next(
        step for step in proposal["steps"] if step["selected_tool_name"] == "start_outpost_repair"
    )
    trade = next(
        step for step in proposal["steps"] if step["selected_tool_name"] == "start_trade_route_test"
    )
    recon["tool_arguments"]["target_key"] = "valley_entrance"
    military["tool_arguments"]["target_key"] = "ambush_valley"
    repair["tool_arguments"].pop("target_key")
    trade["tool_arguments"] = {"route_key": "northern_trade_route"}

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert result.status == "PASSED"
    assert result.normalized_arguments is not None
    normalized_steps = result.normalized_arguments["steps"]
    normalized = {
        step["selected_tool_name"]: step["tool_arguments"]
        for step in normalized_steps
        if step["selected_tool_name"]
    }
    assert normalized["start_recon_operation"]["target_key"] == "valley_entrance"
    assert normalized["start_military_operation"]["target_key"] == "ambush_valley"
    assert "target_key" not in normalized["start_outpost_repair"]
    assert normalized["start_trade_route_test"] == {"route_key": "northern_trade_route"}


def test_legacy_plan_arguments_remain_raw_when_steps_are_persisted(session: Session) -> None:
    conversation, task, validator = _context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    repair = next(
        step for step in proposal["steps"] if step["selected_tool_name"] == "start_outpost_repair"
    )
    trade = next(
        step for step in proposal["steps"] if step["selected_tool_name"] == "start_trade_route_test"
    )
    repair["tool_arguments"].pop("target_key")
    trade["tool_arguments"] = {"route_key": "northern_trade_route"}
    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )
    assert result.status == "PASSED"
    assert result.normalized_arguments is not None
    run = AgentRun(
        request_id=uuid4(),
        session_id=conversation.id,
        model="unit-test",
        input_message="legacy plan persistence",
        max_rounds=1,
    )
    session.add(run)
    session.flush()

    plan = TaskService(session).create_plan(
        task.id,
        str(result.normalized_arguments["strategy_summary"]),
        list(result.normalized_arguments["steps"]),
        created_by_run_id=run.id,
    )
    persisted = {
        step.selected_tool_name: step.tool_arguments
        for step in session.scalars(select(AgentStep).where(AgentStep.plan_id == plan.id))
        if step.selected_tool_name is not None
    }

    assert "target_key" not in persisted["start_outpost_repair"]
    assert persisted["start_trade_route_test"] == {"route_key": "northern_trade_route"}


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
    assert "target_key" in tools["start_outpost_repair"]["planning_parameters"]["required"]
    trade_properties = tools["start_trade_route_test"]["planning_parameters"]["properties"]
    assert "target_key" in trade_properties
    assert "route_key" not in trade_properties
    assert "northern_trade_route" in tools["start_trade_route_test"]["description"]
    canonical_facts = request["constraints"]["canonical_facts"]
    assert canonical_facts["northern_valley.valley_security"] == "UNSAFE"
    assert canonical_facts["starfire_outpost.outpost_status"] == "DAMAGED"
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


@pytest.mark.parametrize(
    "missing_tool",
    [
        "start_military_operation",
        "start_outpost_repair",
        "start_trade_route_test",
    ],
)
def test_initial_plan_requires_starfire_goal_coverage(
    session: Session,
    missing_tool: str,
) -> None:
    conversation, task, validator = _context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    proposal["steps"] = [
        step for step in proposal["steps"] if step["selected_tool_name"] != missing_tool
    ]

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_GOAL_COVERAGE_INCOMPLETE" in _codes(result)


def test_initial_plan_rejects_trade_before_repair(session: Session) -> None:
    conversation, task, validator = _context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    repair_index = next(
        index
        for index, step in enumerate(proposal["steps"])
        if step["selected_tool_name"] == "start_outpost_repair"
    )
    trade_index = next(
        index
        for index, step in enumerate(proposal["steps"])
        if step["selected_tool_name"] == "start_trade_route_test"
    )
    proposal["steps"][repair_index], proposal["steps"][trade_index] = (
        proposal["steps"][trade_index],
        proposal["steps"][repair_index],
    )

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_STEP_ORDER_INVALID" in _codes(result)


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
