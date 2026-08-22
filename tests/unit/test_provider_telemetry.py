from __future__ import annotations

import json
from collections.abc import Iterator
from threading import Event
from time import sleep
from typing import Literal

import httpx
import pytest
from pydantic import SecretStr

from app.agent.provider import (
    GenericProviderError,
    GoalSelectionRequest,
    OpenAICompatibleGenericProvider,
    PlanningContext,
    PlanRequest,
)
from app.core.config import Settings


def _settings(*, total_timeout: float = 1.0) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite+pysqlite:///:memory:",
        model_provider="openai_compatible",
        model_name="fake-model",
        model_api_key=SecretStr("not-a-real-key"),
        model_timeout_seconds=1.0,
        model_total_timeout_seconds=total_timeout,
    )


def _goal_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": '{"status":"SELECTED","objective_keys":["known"]}',
                    },
                    "finish_reason": "stop",
                }
            ],
        },
        request=request,
    )


def _plan_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"plan_summary":"complete","steps":['
                            '{"purpose":"inspect","action_key":"inspect",'
                            '"actor_key":"actor_one","target_key":"node_one",'
                            '"parameters":{}}]}'
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
        },
        request=request,
    )


class _DelayedBody(httpx.SyncByteStream):
    def __init__(
        self,
        chunks: tuple[bytes, ...],
        delay: float,
        first_chunk_sent: Event | None = None,
        delay_before_first: bool = False,
    ):
        self._chunks = chunks
        self._delay = delay
        self._first_chunk_sent = first_chunk_sent
        self._delay_before_first = delay_before_first

    def __iter__(self) -> Iterator[bytes]:
        for index, chunk in enumerate(self._chunks):
            if index or self._delay_before_first:
                sleep(self._delay)
            yield chunk
            if index == 0 and self._first_chunk_sent is not None:
                self._first_chunk_sent.set()

    def close(self) -> None:
        return None


def test_fast_response_records_headers_first_byte_and_bytes() -> None:
    provider = OpenAICompatibleGenericProvider(
        _settings(), transport=httpx.MockTransport(_goal_response)
    )

    result = provider.select_objectives(
        GoalSelectionRequest(goal="known", objective_candidates=({"key": "known"},))
    )

    assert result.objective_keys == ("known",)
    metadata = provider.last_call_metadata
    assert metadata is not None
    assert metadata.outcome == "SUCCESS"
    assert metadata.request_started_at is not None
    assert metadata.request_send_completed_at is None
    assert metadata.response_headers_received_at is not None
    assert metadata.first_response_byte_at is not None
    assert metadata.response_bytes_received is not None
    assert metadata.response_bytes_received > 0
    assert metadata.request_cancelled_at is None
    assert metadata.timeout_subtype is None


@pytest.mark.parametrize("call_type", ["INITIAL_PLAN", "REPLAN", "REPAIR"])
def test_enabled_uncapped_provider_uses_nullable_production_settings(
    monkeypatch: pytest.MonkeyPatch,
    call_type: Literal["INITIAL_PLAN", "REPLAN", "REPAIR"],
) -> None:
    captured: dict[str, object] = {}

    def complete(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _plan_response(request)

    def unexpected_executor(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("No total deadline must not create a deadline executor")

    monkeypatch.setattr("app.agent.provider.ThreadPoolExecutor", unexpected_executor)
    provider = OpenAICompatibleGenericProvider(
        _settings().model_copy(
            update={
                "model_name": "deepseek-v4-flash",
                "model_thinking_mode": "enabled",
                "model_reasoning_effort": "low",
                "model_timeout_seconds": None,
                "model_total_timeout_seconds": None,
                "model_max_output_tokens": None,
            }
        ),
        transport=httpx.MockTransport(complete),
    )

    provider.propose_plan(
        PlanRequest(
            call_type=call_type,
            goal="known goal",
            planning_context=PlanningContext(goal={"objective_keys": ["known"]}),
        )
    )

    assert captured["model"] == "deepseek-v4-flash"
    assert captured["thinking"] == {"type": "enabled"}
    assert captured["reasoning_effort"] == "low"
    assert "max_tokens" not in captured
    assert "max_completion_tokens" not in captured
    metadata = provider.last_call_metadata
    assert metadata is not None
    assert metadata.thinking_mode == "enabled"
    assert metadata.reasoning_effort == "low"
    assert metadata.configured_output_token_limit is None
    assert metadata.http_timeout_seconds is None
    assert metadata.total_deadline_seconds is None


def test_provider_settings_defaults_remain_bounded_and_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "MODEL_THINKING_MODE",
        "MODEL_REASONING_EFFORT",
        "MODEL_TIMEOUT_SECONDS",
        "MODEL_TOTAL_TIMEOUT_SECONDS",
        "MODEL_MAX_OUTPUT_TOKENS",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None)

    assert settings.model_thinking_mode == "disabled"
    assert settings.model_reasoning_effort == "low"
    assert settings.model_timeout_seconds == 20
    assert settings.model_total_timeout_seconds == 60
    assert settings.model_max_output_tokens == 8192


def test_provider_settings_parse_explicit_null_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "null")
    monkeypatch.setenv("MODEL_TOTAL_TIMEOUT_SECONDS", "null")
    monkeypatch.setenv("MODEL_MAX_OUTPUT_TOKENS", "null")

    settings = Settings(_env_file=None)

    assert settings.model_timeout_seconds is None
    assert settings.model_total_timeout_seconds is None
    assert settings.model_max_output_tokens is None


def test_headers_received_but_slow_body_is_distinguished_from_no_response() -> None:
    provider = OpenAICompatibleGenericProvider(
        _settings(total_timeout=0.02),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                stream=_DelayedBody((b"{", b"}"), 0.15, delay_before_first=True),
                request=request,
            )
        ),
    )

    with pytest.raises(GenericProviderError) as error:
        provider.select_objectives(
            GoalSelectionRequest(goal="known", objective_candidates=({"key": "known"},))
        )

    assert error.value.code == "MODEL_PROVIDER_TIMEOUT"
    metadata = provider.last_call_metadata
    assert metadata is not None
    assert metadata.response_headers_received_at is not None
    assert metadata.first_response_byte_at is None
    assert metadata.response_bytes_received == 0
    assert metadata.timeout_subtype == "PROVIDER_TOTAL_DEADLINE"
    assert metadata.request_cancelled_at is not None


def test_total_deadline_without_response_keeps_response_phase_fields_null() -> None:
    def no_response(request: httpx.Request) -> httpx.Response:
        sleep(0.15)
        return _goal_response(request)

    provider = OpenAICompatibleGenericProvider(
        _settings(total_timeout=0.02), transport=httpx.MockTransport(no_response)
    )

    with pytest.raises(GenericProviderError):
        provider.select_objectives(
            GoalSelectionRequest(goal="known", objective_candidates=({"key": "known"},))
        )

    metadata = provider.last_call_metadata
    assert metadata is not None
    assert metadata.response_headers_received_at is None
    assert metadata.first_response_byte_at is None
    assert metadata.response_bytes_received is None
    assert metadata.timeout_subtype == "PROVIDER_TOTAL_DEADLINE"
    assert metadata.request_cancelled_at is not None


def test_partial_body_before_total_deadline_preserves_received_bytes() -> None:
    first_chunk_sent = Event()
    provider = OpenAICompatibleGenericProvider(
        _settings(total_timeout=0.02),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                stream=_DelayedBody((b"partial", b"rest"), 0.15, first_chunk_sent),
                request=request,
            )
        ),
    )

    with pytest.raises(GenericProviderError):
        provider.select_objectives(
            GoalSelectionRequest(goal="known", objective_candidates=({"key": "known"},))
        )

    assert first_chunk_sent.is_set()
    metadata = provider.last_call_metadata
    assert metadata is not None
    assert metadata.response_headers_received_at is not None
    assert metadata.first_response_byte_at is not None
    assert metadata.response_bytes_received == len(b"partial")
    assert metadata.timeout_subtype == "PROVIDER_TOTAL_DEADLINE"


def test_phase_telemetry_does_not_change_plan_parsing() -> None:
    provider = OpenAICompatibleGenericProvider(
        _settings(), transport=httpx.MockTransport(_plan_response)
    )

    proposal = provider.propose_plan(
        PlanRequest(
            call_type="INITIAL_PLAN",
            goal="known goal",
            planning_context=PlanningContext(
                goal={"objective_keys": ["known"]},
                current_knowledge={"facts": {}},
            ),
        )
    )

    assert len(proposal.steps) == 1
    assert proposal.steps[0].action_key == "inspect"
    metadata = provider.last_call_metadata
    assert metadata is not None
    assert metadata.outcome == "SUCCESS"
    assert metadata.response_headers_received_at is not None
    assert metadata.first_response_byte_at is not None
    assert metadata.response_bytes_received is not None
