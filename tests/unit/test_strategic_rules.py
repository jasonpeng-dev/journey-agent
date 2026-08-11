from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.authority import evaluate_authority
from app.agent.types import ToolCall, ToolContext
from app.core.errors import AppError
from app.domain.enums import AgentStepStatus, AgentTaskStatus, AuthorityOutcome
from app.infrastructure.db.models import (
    NPC,
    AgentRun,
    ConversationSession,
    OfficerAppointment,
    PlayerDecisionRequest,
    PlayerDomainState,
    WorldOperation,
)
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService
from app.tools.catalog import build_registry
from app.tools.executor import ToolExecutor


def _run(session: Session, conversation: ConversationSession, label: str) -> AgentRun:
    session.flush()
    run = AgentRun(
        request_id=uuid4(),
        session_id=conversation.id,
        model="unit-test",
        input_message=label,
        max_rounds=1,
    )
    session.add(run)
    session.commit()
    return run


def test_parameter_authority_distinguishes_autonomy_from_approval(
    session: Session,
) -> None:
    han = session.scalar(select(NPC).where(NPC.key == "han_lie"))
    lu = session.scalar(select(NPC).where(NPC.key == "lu_ning"))
    assert han is not None and lu is not None

    within = evaluate_authority(
        han,
        "start_military_operation",
        {"troop_count": 200},
    )
    over = evaluate_authority(
        han,
        "start_military_operation",
        {"troop_count": 201},
    )
    food_within = evaluate_authority(
        lu,
        "negotiate_village_support",
        {"food_offer": 30},
    )
    food_over = evaluate_authority(
        lu,
        "negotiate_village_support",
        {"food_offer": 31},
    )
    aggressive = evaluate_authority(
        han,
        "start_military_operation",
        {"troop_count": 120, "strategy": "AGGRESSIVE"},
    )

    assert within.outcome == AuthorityOutcome.ALLOW
    assert food_within.outcome == AuthorityOutcome.ALLOW
    assert over.outcome == AuthorityOutcome.REQUIRE_PLAYER_DECISION
    assert food_over.outcome == AuthorityOutcome.REQUIRE_PLAYER_DECISION
    assert aggressive.outcome == AuthorityOutcome.REQUIRE_PLAYER_DECISION
    assert aggressive.reason_code == "HIGH_RISK_ACTION_REQUIRES_APPROVAL"
    invalid_policy = evaluate_authority(
        lu,
        "negotiate_village_support",
        {"food_offer": 100},
        authority_overrides={"max_food": -1},
        policy_version=2,
    )
    assert invalid_policy.outcome == AuthorityOutcome.DENY
    assert invalid_policy.reason_code == "AUTHORITY_POLICY_INVALID"


def test_business_preflight_rejects_unavailable_troops_before_player_approval(
    session: Session,
) -> None:
    game = GameService(session)
    player = game.create_player("Preflight Troop Lord")
    domain = session.get(PlayerDomainState, player.id)
    han = session.get(NPC, seed_id("npc:han_lie"))
    appointment = session.get(
        OfficerAppointment,
        (player.id, seed_id("npc:han_lie")),
    )
    assert domain is not None and han is not None and appointment is not None
    domain.soldiers_total = 280
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    planning_run = _run(session, conversation, "preflight plan")
    task = TaskService(session).create_task(
        conversation,
        "Clear the valley with the requested force.",
        "starfire_command",
    )
    arguments = {
        "target_key": "ambush_valley",
        "troop_count": 282,
        "mission_type": "CLEAR_VALLEY",
        "strategy": "CAUTIOUS",
        "idempotency_key": "preflight-unavailable-troops-0001",
    }
    plan = TaskService(session).create_plan(
        task.id,
        "Han Lie attempts the assigned valley operation.",
        [
            {
                "description": "Clear the valley with 282 soldiers",
                "execution_type": "TOOL",
                "assigned_officer_key": "han_lie",
                "action_intent": "CLEAR_VALLEY",
                "selected_tool_name": "start_military_operation",
                "allowed_tool_names": ["start_military_operation"],
                "tool_arguments": {
                    key: value for key, value in arguments.items() if key != "idempotency_key"
                },
                "expected_outcome": {"operation_type": "MILITARY", "status": "PENDING"},
            }
        ],
        created_by_run_id=planning_run.id,
    )
    step = TaskService(session).plan_steps(plan.id)[0]
    execution_run = _run(session, conversation, "preflight execution")
    execution_run.task_id = task.id
    execution_run.plan_id = plan.id
    execution_run.step_id = step.id
    execution_run.actor_npc_id = han.id
    execution_run.officer_profile_version = han.profile_version
    execution_run.authority_policy_version = appointment.version
    session.commit()

    result = ToolExecutor(session, build_registry()).execute(
        ToolContext(
            player_id=player.id,
            npc_id=han.id,
            session_id=conversation.id,
            agent_run_id=execution_run.id,
            message_id=uuid4(),
            task_id=task.id,
            plan_id=plan.id,
            step_id=step.id,
            planned_arguments=arguments,
        ),
        ToolCall(
            id="preflight-unavailable-troops",
            name="start_military_operation",
            arguments=arguments,
        ),
    )

    assert result.code == "SOLDIERS_UNAVAILABLE"
    assert result.retryable is True
    assert step.status == AgentStepStatus.FAILED
    assert (
        session.scalar(
            select(func.count())
            .select_from(PlayerDecisionRequest)
            .where(PlayerDecisionRequest.task_id == task.id)
        )
        == 0
    )


def test_strategic_preflight_covers_resources_and_world_prerequisites(
    session: Session,
) -> None:
    game = GameService(session)
    player = game.create_player("Preflight Resource Lord")
    domain = session.get(PlayerDomainState, player.id)
    assert domain is not None
    domain.food = 10
    player.gold = 10
    game.set_world_fact(player.id, "valley_security", {"status": "SAFE"})

    with pytest.raises(AppError) as food_error:
        game.preflight_village_support(player_id=player.id, food_offer=20)
    assert food_error.value.code == "SUPPLY_INSUFFICIENT"

    with pytest.raises(AppError) as repair_error:
        game.preflight_outpost_repair(
            player_id=player.id,
            food_commitment=20,
            gold_commitment=20,
        )
    assert repair_error.value.code == "RESOURCE_INSUFFICIENT"

    with pytest.raises(AppError) as trade_error:
        game.preflight_trade_route_test(player_id=player.id)
    assert trade_error.value.code == "STARFIRE_OUTPOST_OFFLINE"


def test_appointment_override_controls_the_effective_authority_policy(
    session: Session,
) -> None:
    player = GameService(session).create_player("Delegated Limit Lord")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    han = session.get(NPC, seed_id("npc:han_lie"))
    appointment = session.get(
        OfficerAppointment,
        (player.id, seed_id("npc:han_lie")),
    )
    assert han is not None and appointment is not None
    appointment.authority_overrides = {"max_troops": 50}
    appointment.version = 4
    planning_run = _run(session, conversation, "plan")
    task_service = TaskService(session)
    task = task_service.create_task(
        conversation,
        "Reconnoiter the valley under the current delegation.",
        "starfire_command",
    )
    plan = task_service.create_plan(
        task.id,
        "Han Lie reconnoiters within his appointed authority.",
        [
            {
                "description": "Reconnoiter the valley entrance",
                "execution_type": "TOOL",
                "assigned_officer_key": "han_lie",
                "action_intent": "RECON",
                "selected_tool_name": "start_recon_operation",
                "allowed_tool_names": ["start_recon_operation"],
                "tool_arguments": {
                    "target_key": "valley_entrance",
                    "troop_count": 60,
                    "approach": "CAUTIOUS",
                },
                "expected_outcome": {"operation_status": "PENDING"},
            }
        ],
        created_by_run_id=planning_run.id,
    )
    step = task_service.plan_steps(plan.id)[0]
    execution_run = _run(session, conversation, "recon")
    execution_run.task_id = task.id
    execution_run.plan_id = plan.id
    execution_run.step_id = step.id
    execution_run.actor_npc_id = han.id
    execution_run.officer_profile_version = han.profile_version
    execution_run.authority_policy_version = appointment.version
    session.commit()
    arguments = {
        "target_key": "valley_entrance",
        "troop_count": 60,
        "approach": "CAUTIOUS",
        "idempotency_key": "appointment-limit-recon-0001",
    }

    result = ToolExecutor(session, build_registry()).execute(
        ToolContext(
            player_id=player.id,
            npc_id=han.id,
            session_id=conversation.id,
            agent_run_id=execution_run.id,
            message_id=uuid4(),
            task_id=task.id,
            plan_id=plan.id,
            step_id=step.id,
            planned_arguments=arguments,
        ),
        ToolCall(
            id="appointment-limit-recon",
            name="start_recon_operation",
            arguments=arguments,
        ),
    )

    assert result.code == "PLAYER_APPROVAL_REQUIRED"
    decision = session.scalar(
        select(PlayerDecisionRequest).where(
            PlayerDecisionRequest.task_id == task.id,
        )
    )
    assert decision is not None
    assert decision.policy_snapshot["policy_version"] == 4
    assert decision.policy_snapshot["exceeded_limits"] == [
        {"field": "troop_count", "requested": 60, "limit": 50}
    ]


def test_idempotency_key_cannot_be_reused_for_a_different_tool(
    session: Session,
) -> None:
    player = GameService(session).create_player("Idempotency Lord")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:han_lie"),
    )
    session.add(conversation)
    session.commit()
    first_run = _run(session, conversation, "recon")
    executor = ToolExecutor(session, build_registry())
    key = "shared-operation-key-0001"

    first = executor.execute(
        ToolContext(
            player_id=player.id,
            npc_id=conversation.npc_id,
            session_id=conversation.id,
            agent_run_id=first_run.id,
            message_id=uuid4(),
        ),
        ToolCall(
            id="first-recon",
            name="start_recon_operation",
            arguments={
                "target_key": "valley_entrance",
                "troop_count": 60,
                "approach": "CAUTIOUS",
                "idempotency_key": key,
            },
        ),
    )
    assert first.ok

    second_run = _run(session, conversation, "military")
    second = executor.execute(
        ToolContext(
            player_id=player.id,
            npc_id=conversation.npc_id,
            session_id=conversation.id,
            agent_run_id=second_run.id,
            message_id=uuid4(),
        ),
        ToolCall(
            id="second-military",
            name="start_military_operation",
            arguments={
                "target_key": "ambush_valley",
                "troop_count": 60,
                "mission_type": "CLEAR_VALLEY",
                "strategy": "CAUTIOUS",
                "idempotency_key": key,
            },
        ),
    )

    assert second.code == "IDEMPOTENCY_KEY_REUSED"
    assert session.scalar(select(func.count()).select_from(WorldOperation)) == 1
    domain = session.get(PlayerDomainState, player.id)
    assert domain is not None
    assert domain.soldiers_committed == 60

    with pytest.raises(AppError) as exc_info:
        GameService(session).start_military_operation(
            player_id=player.id,
            officer_npc_id=conversation.npc_id,
            task_id=None,
            source_step_id=None,
            target_key="ambush_valley",
            troop_count=60,
            mission_type="CLEAR_VALLEY",
            strategy="CAUTIOUS",
            idempotency_key=key,
        )
    assert exc_info.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_trade_resolution_rechecks_world_prerequisites(session: Session) -> None:
    service = GameService(session)
    player = service.create_player("Changing World Lord")
    service.set_world_fact(player.id, "valley_security", {"status": "SAFE"})
    service.set_world_fact(
        player.id,
        "starfire_outpost_status",
        {"status": "OPERATIONAL"},
    )
    service.set_world_fact(player.id, "village_support", {"status": "GUIDE"})
    operation = service.start_trade_route_test(
        player_id=player.id,
        officer_npc_id=seed_id("npc:lu_ning"),
        task_id=None,
        source_step_id=None,
        route_key="northern_trade_route",
        idempotency_key="trade-state-change-0001",
    )
    service.set_world_fact(player.id, "valley_security", {"status": "UNSAFE"})

    resolved = service.resolve_world_operation(operation.id, "resolve-trade-change-0001")

    assert resolved.outcome is not None
    assert resolved.outcome["result"] == "FAILED"
    assert resolved.outcome["failure_code"] == "WORLD_STATE_CHANGED"
    assert service.get_world_fact(player.id, "northern_trade_route_status")["status"] == "CLOSED"
    route = next(
        state for node, state in service.list_nodes(player.id) if node.key == "northern_trade_route"
    )
    assert route.status.value == "LOCKED"


def test_player_action_wait_has_a_distinct_resume_state(session: Session) -> None:
    service = GameService(session)
    player = service.create_player("Player Action Lord")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    planning_run = _run(session, conversation, "plan")
    task = TaskService(session).create_task(
        conversation,
        "等待主公亲自取得北境村落的向导支持。",
        "starfire_command",
    )
    plan = TaskService(session).create_plan(
        task.id,
        "The strategist waits for an action only the player can perform.",
        [
            {
                "description": "Wait for the player to obtain village support",
                "execution_type": "WAIT_FOR_PLAYER_ACTION",
                "expected_outcome": {"player_action": "COMPLETED"},
                "resume_condition": {
                    "type": "PLAYER_ACTION",
                    "fact_key": "village_support",
                    "field": "status",
                    "equals": "GUIDE",
                },
            }
        ],
        created_by_run_id=planning_run.id,
    )
    step = TaskService(session).plan_steps(plan.id)[0]

    waiting = TaskService(session).evaluate_wait(task, step)
    assert waiting == "WAITING_FOR_PLAYER_ACTION"
    assert task.status == AgentTaskStatus.WAITING_FOR_PLAYER_ACTION
    assert step.status == AgentStepStatus.WAITING_FOR_PLAYER_ACTION

    service.set_world_fact(player.id, "village_support", {"status": "GUIDE"})
    resumed = TaskService(session).evaluate_wait(task, step)
    assert resumed == "RESUMED"
    assert task.status == AgentTaskStatus.ACTIVE
    assert step.status == AgentStepStatus.SUCCEEDED
    assert step.actual_result is not None
    assert step.actual_result["player_action"] == "COMPLETED"


def test_session_owner_cannot_impersonate_the_assigned_step_actor(
    session: Session,
) -> None:
    player = GameService(session).create_player("Actor Boundary Lord")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    planning_run = _run(session, conversation, "plan")
    task = TaskService(session).create_task(
        conversation,
        "Restore Starfire Outpost and reopen the northern trade route.",
        "starfire_command",
    )
    plan = TaskService(session).create_plan(
        task.id,
        "Han Lie performs the assigned inspection.",
        [
            {
                "description": "Han Lie inspects the command state",
                "execution_type": "TOOL",
                "assigned_officer_key": "han_lie",
                "selected_tool_name": "inspect_command_state",
                "allowed_tool_names": ["inspect_command_state"],
                "tool_arguments": {},
                "expected_outcome": {"soldiers_total_min": 1},
            }
        ],
        created_by_run_id=planning_run.id,
    )
    step = TaskService(session).plan_steps(plan.id)[0]
    run = _run(session, conversation, "impersonate")

    result = ToolExecutor(session, build_registry()).execute(
        ToolContext(
            player_id=player.id,
            npc_id=conversation.npc_id,
            session_id=conversation.id,
            agent_run_id=run.id,
            message_id=uuid4(),
            task_id=task.id,
            plan_id=plan.id,
            step_id=step.id,
            planned_arguments={},
        ),
        ToolCall(
            id="wrong-step-actor",
            name="inspect_command_state",
            arguments={},
        ),
    )

    assert result.code == "STEP_ACTOR_MISMATCH"
    assert task.status == AgentTaskStatus.BLOCKED
    assert step.status == AgentStepStatus.BLOCKED


def test_a_revoked_strategist_cannot_own_a_new_command(session: Session) -> None:
    player = GameService(session).create_player("Revoked Strategist Lord")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    appointment = session.get(
        OfficerAppointment,
        (player.id, seed_id("npc:shen_ce")),
    )
    assert appointment is not None
    appointment.status = "REVOKED"

    with pytest.raises(AppError) as exc_info:
        TaskService(session).create_task(
            conversation,
            "Restore Starfire Outpost and reopen the northern trade route.",
            "starfire_command",
        )

    assert exc_info.value.code == "COMMAND_OWNER_INVALID"


def test_executor_refreshes_a_concurrently_revoked_appointment(
    session: Session,
) -> None:
    player = GameService(session).create_player("Concurrent Revocation Lord")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:han_lie"),
    )
    session.add(conversation)
    run = _run(session, conversation, "inspect after concurrent revocation")
    cached = session.get(
        OfficerAppointment,
        (player.id, seed_id("npc:han_lie")),
    )
    assert cached is not None and cached.status == "ACTIVE"

    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    with factory() as other:
        current = other.get(
            OfficerAppointment,
            (player.id, seed_id("npc:han_lie")),
        )
        assert current is not None
        current.status = "REVOKED"
        current.version += 1
        other.commit()

    assert cached.status == "ACTIVE"
    result = ToolExecutor(session, build_registry()).execute(
        ToolContext(
            player_id=player.id,
            npc_id=conversation.npc_id,
            session_id=conversation.id,
            agent_run_id=run.id,
            message_id=uuid4(),
        ),
        ToolCall(
            id="inspect-after-revocation",
            name="inspect_command_state",
            arguments={},
        ),
    )

    assert result.code == "OFFICER_NOT_APPOINTED"
    assert cached.status == "REVOKED"
    assert run.authority_policy_version == cached.version
