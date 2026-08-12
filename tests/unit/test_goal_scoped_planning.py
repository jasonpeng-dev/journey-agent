import asyncio
import json
from copy import deepcopy
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.agent.planning import PlanValidator, build_planning_request
from app.agent.providers import MockModelProvider
from app.agent.task_orchestrator import TaskOrchestrator
from app.agent.types import Message, MockStep, ModelResponse, ToolCall, ToolDefinition
from app.core.config import Settings
from app.domain.enums import AgentTaskStatus
from app.infrastructure.db.models import ConversationSession
from app.scenarios.contracts import GoalResolutionResult, ObjectiveResolutionStatus
from app.scenarios.starfire.fallback_plans import initial_strategic_starfire_plan
from app.scenarios.starfire.objective_catalog import (
    FULL_STARFIRE_SCOPE,
    STARFIRE_OBJECTIVE_CATALOG,
    StarfireObjectiveKey,
)
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService
from app.tools.catalog import build_registry


def test_planning_request_uses_frozen_scope_and_backend_verification(
    session: Session,
) -> None:
    conversation, task, scope = _frozen_task(
        session,
        [StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST],
    )

    request = build_planning_request(
        db=session,
        registry=build_registry(),
        settings=_settings(),
        task=task,
        session=conversation,
        kind="PLAN",
    )

    assert request["objective_scope"]["objective_keys"] == ["RESTORE_STARFIRE_OUTPOST"]
    assert [item["key"] for item in request["objective_scope"]["objectives"]] == [
        "RESTORE_STARFIRE_OUTPOST"
    ]
    assert request["constraints"]["final_verification"] == ("BACKEND_SCOPED_OBJECTIVE_EVALUATOR")
    assert "required_final_step" not in request["constraints"]
    prerequisite_keys = {item["key"] for item in request["objective_scope"]["prerequisites"]}
    assert prerequisite_keys == {"valley_security_required"}
    assert list(scope.objective_keys) == ["RESTORE_STARFIRE_OUTPOST"]


def test_validator_rejects_plan_that_changes_frozen_scope(session: Session) -> None:
    conversation, task, restore_scope = _frozen_task(
        session,
        [StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST],
    )
    proposal = initial_strategic_starfire_plan(task.id, restore_scope)
    proposal["objective_scope"] = {
        "scenario_key": "starfire_command",
        "catalog_version": "starfire-objectives-v1",
        "objective_keys": ["FULL_NORTHERN_RECOVERY"],
    }

    result = PlanValidator(session, build_registry(), _settings()).validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert "PLAN_OBJECTIVE_SCOPE_MISMATCH" in {item.code for item in result.errors}
    assert TaskService(session).require_frozen_scope(task) == restore_scope


def test_validator_rejects_scope_external_terminal_step_with_repair_feedback(
    session: Session,
) -> None:
    conversation, task, restore_scope = _frozen_task(
        session,
        [StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST],
    )
    proposal = initial_strategic_starfire_plan(task.id, FULL_STARFIRE_SCOPE)
    proposal["objective_scope"] = {
        "scenario_key": restore_scope.scenario_key,
        "catalog_version": restore_scope.catalog_version,
        "objective_keys": list(restore_scope.objective_keys),
    }

    result = PlanValidator(session, build_registry(), _settings()).validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    scope_issue = next(
        item
        for item in result.errors
        if item.code == "PLAN_TERMINAL_EFFECT_OUTSIDE_OBJECTIVE_SCOPE"
    )
    assert result.status == "REJECTED"
    assert scope_issue.path.endswith("selected_tool_name")
    assert "northern_trade_route.trade_route_status" in scope_issue.message
    assert "paired wait" in scope_issue.message


def test_validator_allows_restore_prerequisites_and_supporting_actions(
    session: Session,
) -> None:
    conversation, task, restore_scope = _frozen_task(
        session,
        [StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST],
    )
    proposal = initial_strategic_starfire_plan(task.id, restore_scope)

    result = PlanValidator(session, build_registry(), _settings()).validate(
        task=task,
        session=conversation,
        tool_name="create_task_plan",
        arguments=proposal,
    )

    assert result.status == "PASSED"


def test_already_satisfied_scope_stops_before_provider_or_plan(session: Session) -> None:
    conversation = _conversation(session)
    game = GameService(session)
    game.set_world_fact(
        conversation.player_id,
        "starfire_outpost_status",
        {"status": "OPERATIONAL"},
    )
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
            "Restore Starfire Outpost",
            "starfire_command",
        )
    )

    assert event == "TASK_SUCCEEDED"
    assert run is None
    assert task.status == AgentTaskStatus.SUCCEEDED
    assert task.current_plan_version == 0
    assert len(provider.steps) == 1


def test_planner_retries_with_scope_validation_feedback(session: Session) -> None:
    conversation = _conversation(session)
    provider = _ScopeCorrectionProvider()

    task, run, event = asyncio.run(
        TaskOrchestrator(session, provider, _settings()).start(
            conversation,
            "Restore Starfire Outpost",
            "starfire_command",
        )
    )

    assert event == "PLANNED"
    assert run is not None
    assert run.actual_rounds == 2
    assert run.model_rounds[0]["plan_validation_status"] == "REJECTED"
    assert run.model_rounds[0]["plan_validation_errors"][0]["code"] == (
        "PLAN_TERMINAL_EFFECT_OUTSIDE_OBJECTIVE_SCOPE"
    )
    assert run.model_rounds[1]["plan_validation_status"] == "PASSED"
    assert any(
        "VALIDATION_ERRORS_JSON" in message.content
        and "PLAN_TERMINAL_EFFECT_OUTSIDE_OBJECTIVE_SCOPE" in message.content
        for message in provider.seen_messages[1]
    )
    assert all(
        step.selected_tool_name != "start_trade_route_test"
        for step in TaskService(session).plan_steps(TaskService(session).current_plan(task).id)
    )


def test_side_effects_do_not_mutate_scope(session: Session) -> None:
    _conversation_row, task, scope = _frozen_task(
        session,
        [StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST],
    )
    before = deepcopy(task.objective_scope_keys)
    GameService(session).set_world_fact(
        task.player_id,
        "northern_trade_route_status",
        {"status": "OPEN"},
    )

    assert task.objective_scope_keys == before
    assert TaskService(session).require_frozen_scope(task) == scope


def _frozen_task(
    session: Session,
    objective_keys: list[StarfireObjectiveKey],
):  # type: ignore[no-untyped-def]
    conversation = _conversation(session)
    tasks = TaskService(session)
    task = tasks.create_task(conversation, "Scoped test goal", "starfire_command")
    scope = STARFIRE_OBJECTIVE_CATALOG.scope(objective_keys)
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
    return conversation, task, scope


def _conversation(session: Session) -> ConversationSession:
    player = GameService(session).create_player(f"Scoped planning {uuid4()}")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    return conversation


def _settings() -> Settings:
    return Settings(database_url="sqlite+pysqlite:///:memory:")


class _ScopeCorrectionProvider:
    name = "scope-correction-model"

    def __init__(self) -> None:
        self.calls = 0
        self.seen_messages: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        del tools
        self.calls += 1
        self.seen_messages.append(list(messages))
        request_message = next(
            message.content
            for message in messages
            if message.content and "PLANNER_REQUEST_JSON:" in message.content
        )
        request = json.loads(request_message.split("PLANNER_REQUEST_JSON:", 1)[1])
        task_id = UUID(str(request["task_id"]))
        raw_scope = request["objective_scope"]
        restore_scope = STARFIRE_OBJECTIVE_CATALOG.scope(
            [StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST]
        )
        proposal = initial_strategic_starfire_plan(
            task_id,
            FULL_STARFIRE_SCOPE if self.calls == 1 else restore_scope,
        )
        proposal["objective_scope"] = {
            "scenario_key": raw_scope["scenario_key"],
            "catalog_version": raw_scope["catalog_version"],
            "objective_keys": raw_scope["objective_keys"],
        }
        return ModelResponse(
            tool_calls=[
                ToolCall(
                    id=f"scope-correction-{self.calls}",
                    name="create_task_plan",
                    arguments=proposal,
                )
            ],
            token_usage=10,
            model=self.name,
        )
