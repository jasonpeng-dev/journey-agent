import asyncio
from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.planning import PlanValidator, build_planning_request
from app.agent.providers import MockModelProvider
from app.agent.starfire_plans import initial_starfire_plan
from app.agent.strategic_starfire_plans import (
    initial_strategic_starfire_plan,
    recovery_strategic_starfire_plan,
    state_aware_strategic_recovery_plan,
)
from app.agent.task_orchestrator import TaskOrchestrator
from app.agent.task_router import TaskRouter
from app.agent.types import MockStep
from app.core.config import Settings
from app.domain.enums import AgentStepStatus, AgentTaskStatus
from app.infrastructure.db.models import NPC, ConversationSession, OfficerAppointment
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService
from app.tools.catalog import build_registry


def _planning_context(session: Session, npc_key: str = "captain_aria"):
    player = GameService(session).create_player(f"Planner-{npc_key}")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id(f"npc:{npc_key}"),
    )
    session.add(conversation)
    session.flush()
    task = TaskService(session).create_task(
        conversation,
        "Restore Starfire Outpost and obtain safe access.",
        "starfire_outpost",
    )
    session.flush()
    validator = PlanValidator(
        session,
        build_registry(),
        Settings(database_url="sqlite+pysqlite:///:memory:"),
    )
    return conversation, task, validator


def _codes(result) -> set[str]:  # type: ignore[no-untyped-def]
    return {item.code for item in result.errors}


def _strategic_planning_context(session: Session):
    player = GameService(session).create_player("Strategic Planner")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    task = TaskService(session).create_task(
        conversation,
        "Restore Starfire Outpost and reopen the northern trade route.",
        "starfire_command",
    )
    validator = PlanValidator(
        session,
        build_registry(),
        Settings(database_url="sqlite+pysqlite:///:memory:"),
    )
    return conversation, task, validator


def test_valid_model_plan_is_normalized_and_accepted(session: Session) -> None:
    conversation, task, validator = _planning_context(session)

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=initial_starfire_plan(task.id),
    )

    assert result.status == "PASSED"
    assert result.normalized_arguments is not None
    assert result.normalized_arguments["task_id"] == str(task.id)
    assert result.normalized_arguments["idempotency_key"].endswith("-v1")


def test_plan_rejects_unknown_tool_and_invalid_nested_arguments(session: Session) -> None:
    conversation, task, validator = _planning_context(session)
    proposal = initial_starfire_plan(task.id)
    proposal["steps"][0]["selected_tool_name"] = "patch_database"
    proposal["steps"][1]["tool_arguments"]["difficulty"] = "IMPOSSIBLE"

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_TOOL_NOT_ALLOWED" in _codes(result)
    assert "PLAN_TOOL_ARGUMENTS_INVALID" in _codes(result)


def test_plan_rejects_tool_not_authorized_for_current_npc(session: Session) -> None:
    conversation, task, validator = _planning_context(session, "guanyin")

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=initial_starfire_plan(task.id),
    )

    assert "PLAN_TOOL_UNAUTHORIZED" in _codes(result)


def test_plan_rejects_invalid_wait_and_step_limit(session: Session) -> None:
    conversation, task, _ = _planning_context(session)
    validator = PlanValidator(
        session,
        build_registry(),
        Settings(
            database_url="sqlite+pysqlite:///:memory:",
            planner_max_steps=8,
        ),
    )
    proposal = initial_starfire_plan(task.id)
    proposal["steps"][3]["resume_condition"]["encounter_key"] = "missing_encounter"
    proposal["steps"].append(deepcopy(proposal["steps"][0]))
    proposal["steps"][-1]["description"] = "Extra inspection"

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_ENCOUNTER_UNKNOWN" in _codes(result)
    assert "PLAN_STEP_LIMIT_EXCEEDED" in _codes(result)


def test_replan_rejects_a_completed_write_from_the_previous_plan(session: Session) -> None:
    conversation, task, validator = _planning_context(session)
    initial = initial_starfire_plan(task.id)
    plan = TaskService(session).create_plan(
        task.id,
        initial["strategy_summary"],
        initial["steps"],
        created_by_run_id=seed_id("planning-test-run"),
    )
    create_quest_step = TaskService(session).plan_steps(plan.id)[1]
    create_quest_step.status = AgentStepStatus.SUCCEEDED
    session.flush()
    proposal = initial_starfire_plan(task.id)
    proposal["replan_reason"] = "ENCOUNTER_DEFEAT"
    proposal["idempotency_key"] = f"task-replan-{task.id}-v2"

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="replan_task",
        arguments=proposal,
        replan_reason="ENCOUNTER_DEFEAT",
    )

    assert "PLAN_REPEATS_COMPLETED_WRITE" in _codes(result)


def test_replan_allows_same_world_operation_after_its_wait_failed(
    session: Session,
) -> None:
    conversation, task, validator = _strategic_planning_context(session)
    initial = initial_strategic_starfire_plan(task.id)
    first_clear = next(
        step
        for step in initial["steps"]
        if step["selected_tool_name"] == "start_military_operation"
    )
    first_clear["tool_arguments"] = {
        "target_key": "ambush_valley",
        "troop_count": 160,
        "mission_type": "CLEAR_VALLEY",
        "strategy": "CAUTIOUS",
    }
    plan = TaskService(session).create_plan(
        task.id,
        initial["strategy_summary"],
        initial["steps"],
        created_by_run_id=seed_id("failed-operation-retry-run"),
    )
    old_steps = TaskService(session).plan_steps(plan.id)
    old_clear = next(
        step for step in old_steps if step.selected_tool_name == "start_military_operation"
    )
    old_wait = next(step for step in old_steps if step.sequence == old_clear.sequence + 1)
    old_clear.status = AgentStepStatus.SUCCEEDED
    old_wait.status = AgentStepStatus.FAILED
    old_wait.failure_code = "ENCOUNTER_DEFEAT"
    game = GameService(session)
    game.set_world_fact(task.player_id, "valley_intelligence", {"status": "COMPLETE"})
    game.set_world_fact(task.player_id, "village_support", {"status": "GUIDE"})
    game.set_world_fact(task.player_id, "enemy_supply_route", {"status": "ACTIVE"})
    world = game.inspect_command_state(task.player_id)["world"]
    assert isinstance(world, dict)
    proposal = state_aware_strategic_recovery_plan(
        task.id,
        next_version=2,
        reason="ENCOUNTER_DEFEAT",
        world=world,
    )

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="replan_task",
        arguments=proposal,
        replan_reason="ENCOUNTER_DEFEAT",
    )

    assert result.status == "PASSED"
    assert "PLAN_REPEATS_COMPLETED_WRITE" not in _codes(result)


def test_replan_rejects_a_strategic_effect_already_verified_in_world(
    session: Session,
) -> None:
    conversation, task, validator = _strategic_planning_context(session)
    initial = initial_strategic_starfire_plan(task.id)
    TaskService(session).create_plan(
        task.id,
        initial["strategy_summary"],
        initial["steps"],
        created_by_run_id=seed_id("satisfied-effect-run"),
    )
    GameService(session).set_world_fact(task.player_id, "valley_security", {"status": "SAFE"})
    proposal = recovery_strategic_starfire_plan(
        task.id,
        next_version=2,
        reason="WORLD_STATE_CHANGED",
    )

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="replan_task",
        arguments=proposal,
        replan_reason="WORLD_STATE_CHANGED",
    )

    assert "PLAN_REPEATS_SATISFIED_EFFECT" in _codes(result)


def test_task_router_separates_queries_from_supported_complex_goals() -> None:
    router = TaskRouter()

    query = router.route("What is the current Starfire Outpost status?")
    goal = router.route("Help me restore Starfire Outpost and obtain safe access.")

    assert query.mode == "CONVERSATION"
    assert goal.mode == "STRUCTURED_TASK"
    assert goal.scenario_key == "starfire_outpost"


def test_strategic_plan_validates_each_step_against_its_assigned_officer(
    session: Session,
) -> None:
    conversation, task, validator = _strategic_planning_context(session)
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


def test_strategic_plan_rejects_an_officer_with_an_invalid_authority_policy(
    session: Session,
) -> None:
    conversation, task, validator = _strategic_planning_context(session)
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


def test_world_wait_must_reference_an_operation_start_step(session: Session) -> None:
    conversation, task, validator = _strategic_planning_context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    recon_wait = next(
        step
        for step in proposal["steps"]
        if step.get("resume_condition")
        and step["resume_condition"].get("source_step_sequence") == 2
    )
    recon_wait["resume_condition"]["source_step_sequence"] = 1

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_WORLD_EVENT_SOURCE_INVALID" in _codes(result)


def test_world_wait_cannot_treat_a_defeat_as_success(session: Session) -> None:
    conversation, task, validator = _strategic_planning_context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    military_wait = next(
        step
        for step in proposal["steps"]
        if step.get("resume_condition")
        and step["resume_condition"].get("source_step_sequence") == 4
    )
    military_wait["resume_condition"]["success_outcomes"] = ["DEFEAT"]
    military_wait["expected_outcome"] = {"operation_result_in": ["DEFEAT"]}

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_WORLD_EVENT_OUTCOMES_INVALID" in _codes(result)


def test_operation_expected_type_must_match_the_selected_tool(
    session: Session,
) -> None:
    conversation, task, validator = _strategic_planning_context(session)
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


def test_strategic_planning_context_exposes_fixed_outcome_contracts(
    session: Session,
) -> None:
    conversation, task, _validator = _strategic_planning_context(session)

    request = build_planning_request(
        db=session,
        registry=build_registry(),
        settings=Settings(database_url="sqlite+pysqlite:///:memory:"),
        task=task,
        session=conversation,
        kind="PLAN",
    )
    tools = {tool["name"]: tool for tool in request["allowed_tools"]}

    assert set(tools) == {
        "inspect_command_state",
        "start_recon_operation",
        "start_military_operation",
        "negotiate_village_support",
        "start_outpost_repair",
        "start_trade_route_test",
    }
    assert request["constraints"]["strategic_initial_plan_blueprint"]["exact_step_count"] == 10
    assert request["constraints"]["strategic_initial_plan_blueprint"]["ordered_phases"][-1] == (
        "inspect_command_state with action_intent=VERIFY_AND_REPORT"
    )

    assert tools["start_recon_operation"]["required_expected_outcomes"] == {
        "operation_type": "RECONNAISSANCE",
        "status": "PENDING",
    }
    assert tools["start_military_operation"]["required_expected_outcomes"] == {
        "operation_type": "MILITARY",
        "status": "PENDING",
    }
    assert tools["start_recon_operation"]["world_wait_success_outcomes"] == [
        "PARTIAL_SUCCESS",
        "VICTORY",
    ]
    assert tools["start_outpost_repair"]["world_wait_success_outcomes"] == ["COMPLETED"]
    assert request["constraints"]["required_final_step"] == {
        "execution_type": "TOOL",
        "assigned_officer_key": "shen_ce",
        "action_intent": "VERIFY_AND_REPORT",
        "allowed_tool_names": ["inspect_command_state"],
        "selected_tool_name": "inspect_command_state",
        "tool_arguments": {},
        "expected_outcome": {
            "valley_security": "SAFE",
            "northern_trade_route_status": "OPEN",
        },
        "resume_condition": None,
    }


def test_replan_context_explains_trade_support_recovery(
    session: Session,
) -> None:
    conversation, task, _validator = _strategic_planning_context(session)

    request = build_planning_request(
        db=session,
        registry=build_registry(),
        settings=Settings(database_url="sqlite+pysqlite:///:memory:"),
        task=task,
        session=conversation,
        kind="REPLAN",
        replan_reason="TRADE_SUPPORT_REQUIRED",
    )

    assert request["failure_code"] == "TRADE_SUPPORT_REQUIRED"
    assert "Acquire GUIDE or SUPPLIES support first" in request["replan_guidance"]


def test_defeat_replan_hides_already_satisfied_strategic_phases(
    session: Session,
) -> None:
    conversation, task, _validator = _strategic_planning_context(session)
    game = GameService(session)
    game.set_world_fact(task.player_id, "valley_intelligence", {"status": "COMPLETE"})
    game.set_world_fact(task.player_id, "village_support", {"status": "GUIDE"})
    game.set_world_fact(task.player_id, "enemy_supply_route", {"status": "ACTIVE"})

    request = build_planning_request(
        db=session,
        registry=build_registry(),
        settings=Settings(database_url="sqlite+pysqlite:///:memory:"),
        task=task,
        session=conversation,
        kind="REPLAN",
        replan_reason="ENCOUNTER_DEFEAT",
    )
    tools = {tool["name"] for tool in request["allowed_tools"]}

    assert "start_recon_operation" not in tools
    assert "negotiate_village_support" not in tools
    assert tools == {
        "inspect_command_state",
        "start_military_operation",
        "start_outpost_repair",
        "start_trade_route_test",
    }
    blueprint = request["constraints"]["strategic_replan_blueprint"]
    assert blueprint["completed_effects_do_not_repeat"]["reconnaissance"] is True
    assert blueprint["completed_effects_do_not_repeat"]["village_trade_support"] is True
    assert blueprint["ordered_remaining_phases"][0] == (
        "start_military_operation with mission_type=DISRUPT_SUPPLY"
    )


def test_trade_support_recovery_plan_retries_only_the_missing_suffix(
    session: Session,
) -> None:
    _conversation, task, _validator = _strategic_planning_context(session)

    proposal = recovery_strategic_starfire_plan(
        task.id,
        next_version=2,
        reason="TRADE_SUPPORT_REQUIRED",
    )
    assert [step["selected_tool_name"] for step in proposal["steps"]] == [
        "negotiate_village_support",
        "start_trade_route_test",
        None,
        "inspect_command_state",
    ]
    assert proposal["steps"][0]["tool_arguments"] == {
        "food_offer": 20,
        "requested_support": "GUIDE",
    }
    assert proposal["steps"][2]["resume_condition"]["source_step_sequence"] == 2


def test_invalid_model_replan_uses_state_aware_strategic_fallback(
    session: Session,
) -> None:
    conversation, task, _validator = _strategic_planning_context(session)
    plan = TaskService(session).create_plan(
        task.id,
        "The first valley operation failed.",
        [
            {
                "description": "Wait for the failed valley operation",
                "execution_type": "WAIT_FOR_WORLD_EVENT",
                "assigned_officer_key": "han_lie",
                "action_intent": "WAIT_FOR_OPERATION",
                "constraints": {},
                "allowed_tool_names": [],
                "selected_tool_name": None,
                "tool_arguments": {},
                "expected_outcome": {"operation_result_in": ["VICTORY"]},
                "resume_condition": {
                    "type": "WORLD_OPERATION",
                    "source_step_sequence": 1,
                    "success_outcomes": ["VICTORY"],
                },
            }
        ],
        created_by_run_id=seed_id("invalid-replan-fallback-run"),
        source="MODEL_PLANNER",
    )
    failed = TaskService(session).plan_steps(plan.id)[0]
    failed.status = AgentStepStatus.FAILED
    failed.failure_code = "ENCOUNTER_DEFEAT"
    task.status = AgentTaskStatus.ACTIVE
    task.last_error_code = "ENCOUNTER_DEFEAT"
    game = GameService(session)
    game.set_world_fact(task.player_id, "valley_intelligence", {"status": "COMPLETE"})
    game.set_world_fact(task.player_id, "village_support", {"status": "GUIDE"})
    game.set_world_fact(task.player_id, "enemy_supply_route", {"status": "ACTIVE"})
    session.commit()
    orchestrator = TaskOrchestrator(
        session,
        MockModelProvider(
            [
                MockStep(content="invalid recovery without a tool call"),
                MockStep(content="still invalid"),
            ]
        ),
        Settings(database_url="sqlite+pysqlite:///:memory:", model_provider="mock"),
    )

    replanned_task, run, event = asyncio.run(orchestrator.advance(task.id, conversation))

    assert event == "REPLANNED"
    assert run is not None
    assert run.model == "deterministic-baseline"
    assert replanned_task.current_plan_version == 2
    current = TaskService(session).current_plan(replanned_task)
    assert current is not None
    assert current.source == "DETERMINISTIC_RECOVERY_FALLBACK"
    assert current.replan_reason == "ENCOUNTER_DEFEAT"
    tools = [step.selected_tool_name for step in TaskService(session).plan_steps(current.id)]
    assert "start_recon_operation" not in tools
    assert "negotiate_village_support" not in tools
    assert tools[:3] == ["start_military_operation", None, "start_military_operation"]


def test_strategic_plan_requires_a_final_shen_ce_verification(session: Session) -> None:
    conversation, task, validator = _strategic_planning_context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    proposal["steps"].pop()

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_FINAL_VERIFICATION_REQUIRED" in _codes(result)


def test_operation_plan_cannot_predict_an_id_or_a_different_target(
    session: Session,
) -> None:
    conversation, task, validator = _strategic_planning_context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    recon = next(
        step for step in proposal["steps"] if step["selected_tool_name"] == "start_recon_operation"
    )
    recon["expected_outcome"]["operation_id"] = "fake-operation"
    recon["expected_outcome"]["target_key"] = "ambush_valley"

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_EXPECTED_OUTCOME_INVALID" in _codes(result)
    assert "PLAN_EXPECTED_OUTCOME_VALUE_INVALID" in _codes(result)


def test_village_support_expectation_must_match_the_deterministic_offer(
    session: Session,
) -> None:
    conversation, task, validator = _strategic_planning_context(session)
    initial = initial_strategic_starfire_plan(task.id)
    TaskService(session).create_plan(
        task.id,
        initial["strategy_summary"],
        initial["steps"],
        created_by_run_id=seed_id("strategic-planning-test-run"),
    )
    proposal = recovery_strategic_starfire_plan(
        task.id,
        next_version=2,
        reason="ENCOUNTER_DEFEAT",
    )
    negotiation = proposal["steps"][0]
    negotiation["tool_arguments"] = {
        "food_offer": 20,
        "requested_support": "GUIDE",
    }
    negotiation["expected_outcome"] = {
        "village_support": "SUPPLIES",
        "food_remaining": 999,
        "fact_version": 999,
    }

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="replan_task",
        arguments=proposal,
        replan_reason="ENCOUNTER_DEFEAT",
    )

    assert "PLAN_EXPECTED_OUTCOME_INVALID" in _codes(result)
    assert "PLAN_EXPECTED_OUTCOME_VALUE_INVALID" in _codes(result)


def test_plan_cannot_start_a_second_operation_before_waiting_for_the_first(
    session: Session,
) -> None:
    conversation, task, validator = _strategic_planning_context(session)
    proposal = initial_strategic_starfire_plan(task.id)
    recon_wait = proposal["steps"].pop(2)
    proposal["steps"].insert(4, recon_wait)
    recon_wait["resume_condition"]["source_step_sequence"] = 2

    result = validator.validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_WORLD_EVENT_PAIRING_INVALID" in _codes(result)


def test_task_router_recognizes_a_chinese_strategic_command() -> None:
    route = TaskRouter().route("恢复星火驿站, 并重新开放北方商路。")
    query = TaskRouter().route("北方商路现在是什么状态?")

    assert route.mode == "STRUCTURED_TASK"
    assert route.reason_code == "STRATEGIC_OFFICER_COMMAND"
    assert route.scenario_key == "starfire_command"
    assert query.mode == "CONVERSATION"
