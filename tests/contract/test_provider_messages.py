import json

from app.agent.providers import _message_payload
from app.agent.types import Message, ToolCall


def test_assistant_tool_call_uses_provider_wire_format() -> None:
    payload = _message_payload(
        Message(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name="get_player_state",
                    arguments={"include": "summary"},
                )
            ],
        )
    )
    assert payload["role"] == "assistant"
    assert payload["tool_calls"][0]["type"] == "function"
    function = payload["tool_calls"][0]["function"]
    assert function["name"] == "get_player_state"
    assert json.loads(function["arguments"]) == {"include": "summary"}
