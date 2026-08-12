from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from app.domain.runtime_scope import RuntimeScope


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class Message(BaseModel):
    role: str
    content: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ModelResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    token_usage: int = 0
    model: str


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class ModelProvider(Protocol):
    name: str

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition]
    ) -> ModelResponse: ...


@dataclass(frozen=True)
class ToolContext:
    player_id: UUID
    npc_id: UUID
    session_id: UUID
    agent_run_id: UUID
    message_id: UUID
    scenario_key: str
    runtime_scope: RuntimeScope | None = None
    task_id: UUID | None = None
    plan_id: UUID | None = None
    step_id: UUID | None = None
    planned_arguments: dict[str, Any] | None = None
    plan_source: str | None = None
    planner_model: str | None = None
    plan_validation_status: str | None = None
    plan_validation_errors: list[dict[str, Any]] | None = None


class ToolResult(BaseModel):
    ok: bool
    code: str
    message: str
    retryable: bool = False
    data: dict[str, Any] | list[Any] | None = None


@dataclass
class MockStep:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    token_usage: int = 0
