import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.goal_resolution import StarfireGoalResolver
from app.agent.providers import MockModelProvider
from app.agent.task_orchestrator import TaskOrchestrator
from app.agent.types import MockStep, ToolCall
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.enums import AgentTaskStatus
from app.infrastructure.db.models import ConversationSession, PlayerDecisionRequest
from app.scenarios.contracts import ObjectiveResolutionStatus
from app.scenarios.starfire.objective_catalog import (
    STARFIRE_OBJECTIVE_CATALOG,
    StarfireObjectiveKey,
)
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        ("只侦察北方山谷, 收集情报", ("GATHER_VALLEY_INTELLIGENCE",)),
        ("守住北方山谷", ("SECURE_NORTHERN_VALLEY",)),
        ("重建星火驿站", ("RESTORE_STARFIRE_OUTPOST",)),
        ("重新打通北方商路", ("OPEN_NORTHERN_TRADE_ROUTE",)),
        ("完整恢复整个北方", ("FULL_NORTHERN_RECOVERY",)),
        (
            "侦察山谷并修复星火前哨",
            ("GATHER_VALLEY_INTELLIGENCE", "RESTORE_STARFIRE_OUTPOST"),
        ),
    ],
)
def test_resolver_maps_explicit_single_and_multi_goals(
    goal: str,
    expected: tuple[str, ...],
) -> None:
    result = _resolver().resolve(goal)

    assert result.status == ObjectiveResolutionStatus.RESOLVED
    assert result.scope is not None
    assert result.scope.objective_keys == expected


def test_resolver_distinguishes_ambiguous_and_unsupported_goals() -> None:
    ambiguous = _resolver().resolve("处理一下北方局势")
    unsupported = _resolver().resolve("在南海建立一支舰队")

    assert ambiguous.status == ObjectiveResolutionStatus.NEEDS_CLARIFICATION
    assert len(ambiguous.candidate_scopes) == 5
    assert ambiguous.clarification_prompt
    assert unsupported.status == ObjectiveResolutionStatus.UNSUPPORTED
    assert unsupported.scope is None
    assert unsupported.candidate_scopes == ()


def test_resolver_rejects_invented_or_non_candidate_objectives() -> None:
    ambiguous = _resolver().resolve("处理一下北方局势")

    with pytest.raises(AppError) as invented:
        _resolver().confirm_candidate(ambiguous.candidate_scopes, ["INVENTED_OBJECTIVE"])
    assert invented.value.code == "GOAL_RESOLUTION_OUTPUT_INVALID"
    with pytest.raises(AppError) as combined:
        _resolver().confirm_candidate(
            ambiguous.candidate_scopes,
            [
                StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE.value,
                StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST.value,
            ],
        )
    assert combined.value.code == "GOAL_CLARIFICATION_SELECTION_INVALID"


def test_ambiguous_and_unsupported_tasks_never_enter_planning(session: Session) -> None:
    for goal, expected_event in (
        ("Handle the northern situation", "GOAL_CLARIFICATION_REQUIRED"),
        ("Build a fleet in the southern seas", "GOAL_UNSUPPORTED"),
    ):
        conversation = _conversation(session)
        provider = MockModelProvider(
            steps=[
                MockStep(
                    tool_calls=[ToolCall(id="must-not-run", name="create_task_plan", arguments={})]
                )
            ]
        )
        task, run, event = asyncio.run(
            TaskOrchestrator(session, provider, _settings()).start(
                conversation,
                goal,
                "starfire_command",
            )
        )

        assert event == expected_event
        assert run is None
        assert task.current_plan_version == 0
        assert len(provider.steps) == 1
        assert TaskService(session).current_plan(task) is None
        assert task.objective_scope_keys is None
        task.status = AgentTaskStatus.SUCCEEDED
        session.flush()


def test_exact_goal_is_confirmed_and_frozen_before_planning(session: Session) -> None:
    conversation = _conversation(session)
    task, run, event = asyncio.run(
        TaskOrchestrator(session, MockModelProvider(), _settings()).start(
            conversation,
            "Restore Starfire Outpost",
            "starfire_command",
        )
    )

    assert event == "PLANNED"
    assert run is not None
    assert task.objective_resolution_status == "CONFIRMED"
    assert TaskService(session).require_frozen_scope(task).objective_keys == (
        "RESTORE_STARFIRE_OUTPOST",
    )
    assert task.current_plan_version == 1


def test_clarification_uses_its_own_lifecycle_and_then_allows_planning(
    session: Session,
) -> None:
    conversation = _conversation(session)
    orchestrator = TaskOrchestrator(session, MockModelProvider(), _settings())
    task, _run, event = asyncio.run(
        orchestrator.start(
            conversation,
            "Handle the northern situation",
            "starfire_command",
        )
    )
    assert event == "GOAL_CLARIFICATION_REQUIRED"

    clarification = orchestrator.clarify_goal(
        task,
        objective_keys=["RESTORE_STARFIRE_OUTPOST"],
        clarification_text=None,
    )
    task, run, planned = asyncio.run(orchestrator.advance(task.id, conversation))

    assert clarification == "GOAL_CONFIRMED"
    assert planned == "PLANNED"
    assert run is not None
    assert TaskService(session).require_frozen_scope(task).objective_keys == (
        "RESTORE_STARFIRE_OUTPOST",
    )
    assert (
        session.scalar(
            select(PlayerDecisionRequest).where(PlayerDecisionRequest.task_id == task.id)
        )
        is None
    )


def _resolver() -> StarfireGoalResolver:
    return StarfireGoalResolver(STARFIRE_OBJECTIVE_CATALOG)


def _conversation(session: Session) -> ConversationSession:
    player = GameService(session).create_player(f"Goal resolver {uuid4()}")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    return conversation


def _settings() -> Settings:
    return Settings(database_url="sqlite+pysqlite:///:memory:")
