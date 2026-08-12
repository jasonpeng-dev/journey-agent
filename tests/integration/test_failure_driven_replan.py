import asyncio
import json
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.agent.providers import MockModelProvider
from app.agent.task_orchestrator import TaskOrchestrator
from app.agent.types import Message, MockStep, ModelResponse, ToolCall, ToolDefinition
from app.core.config import Settings
from app.domain.enums import AgentStepStatus, AgentTaskStatus
from app.infrastructure.db.models import ConversationSession
from app.services.game import GameService, seed_id
from app.services.tasks import TaskService


class RecordingMockProvider(MockModelProvider):
    def __init__(self, steps: list[MockStep]):
        super().__init__(steps)
        self.planning_requests: list[dict[str, object]] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> ModelResponse:
        for message in messages:
            if message.content and "PLANNER_REQUEST_JSON:" in message.content:
                raw = message.content.split("PLANNER_REQUEST_JSON:", 1)[1].strip()
                request = json.loads(raw)
                self.planning_requests.append(request)
                if self.steps and self.steps[0].tool_calls:
                    self.steps[0].tool_calls[0].arguments["task_id"] = request["task_id"]
        return await super().complete(messages, tools)


def test_failure_reveals_knowledge_and_replans_without_changing_scope(
    session: Session,
) -> None:
    game = GameService(session)
    player = game.create_player("Failure driven replan")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    provider = RecordingMockProvider(
        [
            MockStep(
                tool_calls=[
                    ToolCall(
                        id="short-initial-plan",
                        name="create_task_plan",
                        arguments=_short_clear_plan(),
                    )
                ]
            )
        ]
    )
    orchestrator = TaskOrchestrator(session, provider, _settings())
    task, _run, event = asyncio.run(
        orchestrator.start(
            conversation,
            "Restore Starfire Outpost",
            "starfire_command",
        )
    )
    frozen = TaskService(session).require_frozen_scope(task)
    assert event == "PLANNED"
    assert task.current_plan_version == 1
    assert not game.scenario_known_state(player.id).node_known("enemy_north_supply_route")

    task, _run, event = asyncio.run(orchestrator.advance(task.id, conversation))
    assert event == "STEP_SUCCEEDED"
    task, _run, event = asyncio.run(orchestrator.advance(task.id, conversation))
    assert event == "WAITING_FOR_WORLD_EVENT"
    pending = TaskService(session).serialize(task)["pending_world_event"]
    assert isinstance(pending, dict)
    game.resolve_world_operation(UUID(str(pending["id"])), "failure-driven-resolution-001")
    session.commit()
    resources_after_failure = game.inspect_command_state(player.id)["resources"]

    task, _run, event = asyncio.run(orchestrator.advance(task.id, conversation))

    assert event == "REPLANNED"
    assert task.current_plan_version == 2
    assert task.replan_count == 1
    assert TaskService(session).require_frozen_scope(task) == frozen
    known = game.scenario_known_state(player.id)
    assert known.node_known("enemy_north_supply_route")
    assert known.fact_value("enemy_north_supply_route", "supply_status") == "ACTIVE"
    resources = game.inspect_command_state(player.id)["resources"]
    assert resources == resources_after_failure
    assert resources["soldiers_available"] == 282
    assert resources["soldiers_committed"] == 0
    assert len(provider.planning_requests) == 2
    initial_request, replan_request = provider.planning_requests
    assert initial_request["objective_scope"]["objective_keys"] == ["RESTORE_STARFIRE_OUTPOST"]
    assert replan_request["objective_scope"]["objective_keys"] == ["RESTORE_STARFIRE_OUTPOST"]
    assert replan_request["failure_code"] == "ENCOUNTER_DEFEAT"
    encoded_initial = json.dumps(initial_request, ensure_ascii=False)
    encoded_replan = json.dumps(replan_request, ensure_ascii=False)
    assert "enemy_north_supply_route" not in encoded_initial
    assert "supply_status" not in encoded_initial
    assert "enemy_north_supply_route" in encoded_replan
    assert "supply_status" in encoded_replan
    current_plan = TaskService(session).current_plan(task)
    assert current_plan is not None
    replanned_tools = {
        step.selected_tool_name for step in TaskService(session).plan_steps(current_plan.id)
    }
    assert "start_trade_route_test" not in replanned_tools


def test_exhausted_incomplete_plan_replans_instead_of_blocking(session: Session) -> None:
    game = GameService(session)
    player = game.create_player("Exhausted plan")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:shen_ce"),
    )
    session.add(conversation)
    session.flush()
    provider = RecordingMockProvider([])
    orchestrator = TaskOrchestrator(session, provider, _settings())
    task = TaskService(session).create_task(
        conversation,
        "Restore Starfire Outpost",
        "starfire_command",
    )
    orchestrator._ensure_goal_scope(task)
    session.flush()
    plan = TaskService(session).create_plan(
        task.id,
        "A legal horizon that has ended",
        [
            {
                "description": "Completed observation",
                "execution_type": "TOOL",
                "assigned_officer_key": "shen_ce",
                "action_intent": "VERIFY_AND_REPORT",
                "allowed_tool_names": ["inspect_command_state"],
                "selected_tool_name": "inspect_command_state",
                "tool_arguments": {},
                "expected_outcome": {"starfire_outpost_status": "OPERATIONAL"},
            }
        ],
        created_by_run_id=uuid4(),
    )
    step = TaskService(session).plan_steps(plan.id)[0]
    step.status = AgentStepStatus.SUCCEEDED
    session.flush()

    task, _run, event = asyncio.run(orchestrator.advance(task.id, conversation))

    assert event == "REPLANNED"
    assert task.status == AgentTaskStatus.ACTIVE
    assert task.current_plan_version == 2
    assert task.replan_count == 1
    assert provider.planning_requests[0]["failure_code"] == ("PLAN_EXHAUSTED_SCOPE_INCOMPLETE")


def _short_clear_plan() -> dict[str, object]:
    return {
        "task_id": "00000000-0000-0000-0000-000000000000",
        "objective_scope": {
            "scenario_key": "starfire_command",
            "catalog_version": "starfire-objectives-v1",
            "objective_keys": ["RESTORE_STARFIRE_OUTPOST"],
        },
        "strategy_summary": "先尝试以当前已知兵力清理山谷, 再根据结果调整。",
        "steps": [
            {
                "description": "韩烈尝试清理北方山谷",
                "execution_type": "TOOL",
                "assigned_officer_key": "han_lie",
                "action_intent": "CLEAR_VALLEY",
                "constraints": {},
                "allowed_tool_names": ["start_military_operation"],
                "selected_tool_name": "start_military_operation",
                "tool_arguments": {
                    "target_key": "northern_valley",
                    "troop_count": 160,
                    "mission_type": "CLEAR_VALLEY",
                    "strategy": "CAUTIOUS",
                },
                "expected_outcome": {
                    "status": "PENDING",
                    "operation_type": "MILITARY",
                },
            },
            {
                "description": "等待山谷行动结算",
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
            },
            {
                "description": "沈策核验驿站目标",
                "execution_type": "TOOL",
                "assigned_officer_key": "shen_ce",
                "action_intent": "VERIFY_AND_REPORT",
                "constraints": {},
                "allowed_tool_names": ["inspect_command_state"],
                "selected_tool_name": "inspect_command_state",
                "tool_arguments": {},
                "expected_outcome": {"starfire_outpost_status": "OPERATIONAL"},
            },
        ],
        "idempotency_key": "short-initial-plan-001",
    }


def _settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        planner_max_replans=2,
    )
