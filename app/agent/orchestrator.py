from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.providers import ProviderFailure
from app.agent.types import Message, ModelProvider, ToolContext, ToolDefinition
from app.core.config import Settings
from app.domain.enums import MessageRole, RunStatus, TerminationReason
from app.infrastructure.db.models import (
    NPC,
    AgentRun,
    ConversationMessage,
    ConversationSession,
    Memory,
    PlayerNPCRelationship,
)
from app.tools.catalog import build_registry
from app.tools.executor import ToolExecutor


class AgentOrchestrator:
    def __init__(self, db: Session, provider: ModelProvider, settings: Settings):
        self.db = db
        self.provider = provider
        self.settings = settings
        self.registry = build_registry()

    async def run(self, session: ConversationSession, user_content: str) -> tuple[str, AgentRun]:
        user_message = ConversationMessage(
            session_id=session.id,
            role=MessageRole.USER,
            content=user_content,
        )
        self.db.add(user_message)
        run = AgentRun(
            request_id=uuid4(),
            session_id=session.id,
            model=self.provider.name,
            input_message=user_content,
            max_rounds=self.settings.agent_max_rounds,
            purpose="CONVERSATION",
        )
        self.db.add(run)
        self.db.commit()
        messages, context_ids = self._build_context(session.id)
        run.context_record_ids = context_ids
        messages.append(Message(role="user", content=user_content))
        required_tools = self._explicit_tool_requirements(user_content)
        called_tools: set[str] = set()
        missing_tool_retries = 0
        tool_count = 0
        invalid_count = 0
        any_tool_failure = False
        rounds: list[dict[str, object]] = []
        termination = TerminationReason.MAX_ROUNDS
        final = "I could not safely complete that request within the allowed rounds."
        for round_number in range(1, self.settings.agent_max_rounds + 1):
            try:
                response = await asyncio.wait_for(
                    self.provider.complete(messages, self._conversation_tools()),
                    timeout=self.settings.model_timeout_seconds,
                )
            except TimeoutError:
                termination = TerminationReason.MODEL_TIMEOUT
                final = "The model timed out; no unverified action was accepted."
                break
            except ProviderFailure:
                termination = TerminationReason.PROVIDER_ERROR
                final = "The model provider is unavailable; no action was taken."
                break
            run.actual_rounds = round_number
            run.token_usage += response.token_usage
            round_trace: dict[str, object] = {
                "round": round_number,
                "model": response.model,
                "token_usage": response.token_usage,
                "tool_call_ids": [call.id for call in response.tool_calls],
            }
            rounds.append(round_trace)
            if not response.tool_calls:
                missing_tools = required_tools - called_tools
                if missing_tools:
                    missing_tool_retries += 1
                    if missing_tool_retries > 1 or round_number == self.settings.agent_max_rounds:
                        termination = TerminationReason.REPEATED_INVALID_TOOL_CALL
                        final = (
                            "I could not verify the request because the explicitly required "
                            "tool call was not made."
                        )
                        break
                    messages.append(Message(role="assistant", content=response.content))
                    messages.append(
                        Message(
                            role="system",
                            content=(
                                "The prior response did not execute the user's explicitly "
                                f"required tools: {sorted(missing_tools)}. Call those tools now "
                                "before producing a final answer. Do not claim verified state "
                                "without their results."
                            ),
                        )
                    )
                    continue
                termination = TerminationReason.FINAL_RESPONSE
                final = response.content or "I have no additional verified response."
                if any_tool_failure:
                    final = (
                        "The requested action was not completed because a tool failed. "
                        "No success is being claimed."
                    )
                break
            if (
                len(response.tool_calls) > 3
                or tool_count + len(response.tool_calls) > self.settings.agent_max_tool_calls
            ):
                termination = TerminationReason.TOOL_LIMIT
                final = "The tool safety limit was reached; processing stopped."
                break
            messages.append(
                Message(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            for call in response.tool_calls:
                tool_count += 1
                called_tools.add(call.name)
                context = ToolContext(
                    player_id=session.player_id,
                    npc_id=session.npc_id,
                    session_id=session.id,
                    agent_run_id=run.id,
                    message_id=user_message.id,
                )
                result = ToolExecutor(self.db, self.registry).execute(context, call)
                any_tool_failure = any_tool_failure or not result.ok
                if result.code == "INVALID_TOOL_ARGUMENTS":
                    invalid_count += 1
                    if invalid_count > 1:
                        termination = TerminationReason.REPEATED_INVALID_TOOL_CALL
                        final = "Repeated invalid tool arguments caused a safe stop."
                        break
                if result.code == "NPC_PERMISSION_DENIED":
                    termination = TerminationReason.SECURITY_REJECTION
                    final = "The requested action was rejected by authorization rules."
                    break
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        content=json.dumps(result.model_dump(mode="json")),
                    )
                )
            if termination in {
                TerminationReason.REPEATED_INVALID_TOOL_CALL,
                TerminationReason.SECURITY_REJECTION,
            }:
                break
        assistant = ConversationMessage(
            session_id=session.id,
            role=MessageRole.ASSISTANT,
            content=final,
            model_name=self.provider.name,
            token_usage=run.token_usage,
        )
        self.db.add(assistant)
        run.model_rounds = rounds
        run.termination_reason = termination
        run.status = (
            RunStatus.COMPLETED
            if termination == TerminationReason.FINAL_RESPONSE
            else RunStatus.FAILED
        )
        run.finished_at = datetime.now(UTC)
        self.db.commit()
        return final, run

    def _explicit_tool_requirements(self, user_content: str) -> set[str]:
        lowered = user_content.lower()
        if not any(marker in lowered for marker in ("use ", "call ", "invoke ")):
            return set()
        return {
            definition.name
            for definition in self._conversation_tools()
            if definition.name.lower() in lowered
        }

    def _conversation_tools(self) -> list[ToolDefinition]:
        return [
            definition
            for definition in self.registry.definitions()
            if definition.name not in {"create_task_plan", "replan_task"}
        ]

    def _build_context(self, session_id: UUID) -> tuple[list[Message], list[str]]:
        session = self.db.get(ConversationSession, session_id)
        assert session is not None
        npc = self.db.get(NPC, session.npc_id)
        assert npc is not None
        relationship = self.db.get(PlayerNPCRelationship, (session.player_id, session.npc_id))
        memories = self.db.scalars(
            select(Memory)
            .where(Memory.player_id == session.player_id, Memory.npc_id == session.npc_id)
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(5)
        ).all()
        recent = list(
            reversed(
                self.db.scalars(
                    select(ConversationMessage)
                    .where(ConversationMessage.session_id == session_id)
                    .order_by(ConversationMessage.created_at.desc())
                    .limit(12)
                ).all()
            )
        )
        system = (
            f"You are {npc.name}. Persona: {npc.persona}. "
            f"Relationship score: {relationship.score if relationship else 0}. "
            f"Session summary: {session.summary or 'none'}. "
            f"Important memories: {[memory.content for memory in memories]}. "
            "Use tools for state facts. Never claim a state-changing action succeeded "
            "unless its tool result has ok=true."
        )
        messages = [Message(role="system", content=system)]
        messages.extend(Message(role=item.role.value, content=item.content) for item in recent)
        ids = [str(item.id) for item in recent] + [str(item.id) for item in memories]
        return messages, ids
