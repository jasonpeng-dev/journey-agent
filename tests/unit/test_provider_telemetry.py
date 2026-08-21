from __future__ import annotations

from collections.abc import Iterator
from threading import Event
from time import sleep

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
