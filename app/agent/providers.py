from __future__ import annotations

import json
from collections import deque
from typing import Any
from uuid import UUID

import httpx

from app.agent.types import (
    Message,
    MockStep,
    ModelResponse,
    ToolCall,
    ToolDefinition,
)
from app.core.config import Settings
from app.scenarios.contracts import ObjectiveScope
from app.scenarios.registry import scenario_binding


class ProviderFailure(Exception):
    pass


class ProviderOutputFailure(ProviderFailure):
    pass


class MockModelProvider:
    name = "mock-model"

    def __init__(self, steps: list[MockStep] | None = None):
        self.steps = deque(steps or [])

    async def complete(self, messages: list[Message], tools: list[ToolDefinition]) -> ModelResponse:
        if self.steps:
            step = self.steps.popleft()
            return ModelResponse(
                content=step.content,
                tool_calls=step.tool_calls,
                token_usage=step.token_usage,
                model=self.name,
            )
        tool_names = {tool.name for tool in tools}
        for message in reversed(messages):
            if not message.content or "PLANNER_REQUEST_JSON:" not in message.content:
                continue
            raw = message.content.split("PLANNER_REQUEST_JSON:", 1)[1].strip()
            request = json.loads(raw)
            task_id = UUID(str(request["task_id"]))
            scenario = scenario_binding(str(request["scenario_key"]))
            if scenario is None:
                continue
            raw_scope = request.get("objective_scope")
            if not isinstance(raw_scope, dict):
                continue
            scope = ObjectiveScope(
                scenario_key=str(raw_scope["scenario_key"]),
                catalog_version=str(raw_scope["catalog_version"]),
                objective_keys=tuple(str(key) for key in raw_scope["objective_keys"]),
            )
            if request["kind"] == "REPLAN":
                arguments = scenario.fallback_plans.recovery(
                    task_id,
                    int(request["next_plan_version"]),
                    str(request["failure_code"]),
                    scope,
                )
                tool_name = "replan_task"
            else:
                arguments = scenario.fallback_plans.initial(task_id, scope)
                tool_name = "create_task_plan"
            if tool_name not in tool_names:
                continue
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        id=f"mock-planner-{request['kind'].lower()}-{task_id}",
                        name=tool_name,
                        arguments=arguments,
                    )
                ],
                token_usage=64,
                model=self.name,
            )
        for message in reversed(messages):
            if not message.content or "TASK_CONTROL_JSON:" not in message.content:
                continue
            raw = message.content.split("TASK_CONTROL_JSON:", 1)[1].strip()
            control = json.loads(raw)
            tool_name = str(control["tool_name"])
            if tool_name not in tool_names:
                continue
            return ModelResponse(
                content=control.get("content"),
                tool_calls=[
                    ToolCall(
                        id=str(control["tool_call_id"]),
                        name=tool_name,
                        arguments=dict(control["arguments"]),
                    )
                ],
                token_usage=18,
                model=self.name,
            )
        return ModelResponse(
            content="No strategic action is pending.",
            token_usage=10,
            model=self.name,
        )


class OpenAICompatibleProvider:
    """Native tool-calling adapter for OpenAI-compatible Chat Completions APIs."""

    def __init__(self, settings: Settings):
        api_key = settings.model_api_key
        if api_key is None:
            raise ValueError("MODEL_API_KEY is required for openai_compatible provider")
        self.settings = settings
        self.api_key = api_key
        self.name = settings.model_name

    async def complete(self, messages: list[Message], tools: list[ToolDefinition]) -> ModelResponse:
        payload = {
            "model": self.settings.model_name,
            "messages": [_message_payload(message) for message in messages],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ],
            "thinking": {"type": "disabled"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.model_base_url,
                timeout=self.settings.model_timeout_seconds,
            ) as client:
                response = await client.post("/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                body: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            detail = _provider_error_detail(exc.response)
            raise ProviderFailure(
                f"Model provider returned HTTP {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderFailure(
                f"Model provider request failed ({exc.__class__.__name__})"
            ) from exc
        except ValueError as exc:
            raise ProviderFailure("Model provider returned invalid JSON") from exc
        choice = body["choices"][0]["message"]
        calls = [
            ToolCall(
                id=item["id"],
                name=item["function"]["name"],
                arguments=_json_arguments(item["function"].get("arguments", "{}")),
            )
            for item in choice.get("tool_calls", [])
        ]
        return ModelResponse(
            content=choice.get("content"),
            tool_calls=calls,
            token_usage=int(body.get("usage", {}).get("total_tokens", 0)),
            model=body.get("model", self.name),
        )


def _json_arguments(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return _normalize_planner_nested_objects(value)
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProviderOutputFailure("Tool arguments were not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ProviderOutputFailure("Tool arguments must be an object")
    return _normalize_planner_nested_objects(parsed)


def _normalize_planner_nested_objects(arguments: dict[str, Any]) -> dict[str, Any]:
    steps = arguments.get("steps")
    if not isinstance(steps, list):
        return arguments
    for step in steps:
        if not isinstance(step, dict):
            continue
        for field in ("constraints", "tool_arguments", "expected_outcome", "resume_condition"):
            value = step.get(field)
            if not isinstance(value, str):
                continue
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict) or (field == "resume_condition" and decoded is None):
                step[field] = decoded
    return arguments


def _provider_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return "response body was not JSON"
    if not isinstance(body, dict):
        return "provider returned a non-object error body"
    error = body.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        if code and message:
            return f"{code}: {message}"
        if message:
            return str(message)
        if code:
            return str(code)
    message = body.get("message")
    return str(message) if message else "provider rejected the request"


def _message_payload(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role}
    if message.content is not None:
        payload["content"] = message.content
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
            }
            for call in message.tool_calls
        ]
    return payload


def build_provider(settings: Settings):  # type: ignore[no-untyped-def]
    if settings.model_provider == "openai_compatible":
        return OpenAICompatibleProvider(settings)
    return MockModelProvider()
