import asyncio

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.orchestrator import AgentOrchestrator
from app.agent.providers import MockModelProvider
from app.agent.types import MockStep, ToolCall
from app.core.config import Settings
from app.domain.enums import TerminationReason
from app.infrastructure.db.models import ConversationSession, Quest, ToolExecution
from app.services.game import GameService, seed_id


def make_session(db: Session) -> ConversationSession:
    with db.begin():
        player = GameService(db).create_player("Wukong")
        conversation = ConversationSession(player_id=player.id, npc_id=seed_id("npc:guanyin"))
        db.add(conversation)
    return conversation


def test_agent_executes_native_tool_calls_and_traces(session: Session) -> None:
    conversation = make_session(session)
    provider = MockModelProvider(
        [
            MockStep(
                tool_calls=[
                    ToolCall(
                        id="call-create-quest",
                        name="create_quest",
                        arguments={
                            "template_key": "clear_fire_foothills",
                            "difficulty": "NORMAL",
                            "narrative_title": "Calm the foothills",
                            "narrative_description": "Defeat the guardians.",
                            "idempotency_key": "agent-quest-0001",
                        },
                    )
                ]
            ),
            MockStep(content="The quest is now available."),
        ]
    )
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", model_provider="mock")
    content, run = asyncio.run(
        AgentOrchestrator(session, provider, settings).run(conversation, "I accept a trial.")
    )
    assert "available" in content
    assert run.termination_reason == TerminationReason.FINAL_RESPONSE
    assert session.scalar(select(Quest)) is not None
    trace = session.scalar(select(ToolExecution))
    assert trace is not None
    assert trace.before_state is not None
    assert trace.execution_status == "SUCCEEDED"


def test_tool_failure_cannot_be_reported_as_success(session: Session) -> None:
    conversation = make_session(session)
    provider = MockModelProvider(
        [
            MockStep(
                tool_calls=[
                    ToolCall(
                        id="bad-call",
                        name="create_quest",
                        arguments={"template_key": "missing"},
                    )
                ]
            ),
            MockStep(content="Success! I created it."),
        ]
    )
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", model_provider="mock")
    content, _run = asyncio.run(
        AgentOrchestrator(session, provider, settings).run(conversation, "Create it.")
    )
    assert "not completed" in content
    assert "Success" not in content


def test_agent_stops_at_tool_limit(session: Session) -> None:
    conversation = make_session(session)
    calls = [
        ToolCall(id=f"call-{index}", name="get_player_state", arguments={}) for index in range(4)
    ]
    provider = MockModelProvider([MockStep(tool_calls=calls)])
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", model_provider="mock")
    _content, run = asyncio.run(
        AgentOrchestrator(session, provider, settings).run(conversation, "Loop.")
    )
    assert run.termination_reason == TerminationReason.TOOL_LIMIT


def test_explicit_tool_request_is_retried_before_verified_answer(session: Session) -> None:
    conversation = make_session(session)
    provider = MockModelProvider(
        [
            MockStep(content="You can enter the next area."),
            MockStep(
                tool_calls=[
                    ToolCall(
                        id="required-nodes-call",
                        name="get_available_nodes",
                        arguments={},
                    )
                ]
            ),
            MockStep(content="I verified the available nodes."),
        ]
    )
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", model_provider="mock")

    content, run = asyncio.run(
        AgentOrchestrator(session, provider, settings).run(
            conversation,
            "Use get_available_nodes to inspect which nodes I can enter.",
        )
    )

    assert content == "I verified the available nodes."
    assert run.actual_rounds == 3
    assert run.termination_reason == TerminationReason.FINAL_RESPONSE
    trace = session.scalar(
        select(ToolExecution).where(ToolExecution.tool_call_id == "required-nodes-call")
    )
    assert trace is not None
    assert trace.execution_status == "SUCCEEDED"
