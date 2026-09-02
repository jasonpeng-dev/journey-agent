from __future__ import annotations

import json
from collections.abc import Iterator
from threading import Event
from time import sleep
from typing import Literal

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.agent.provider import (
    DynamicGoalCandidateReference,
    DynamicGoalEntityGroundingRequest,
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    GenericProviderError,
    GoalSelectionRequest,
    OpenAICompatibleGenericProvider,
    PlannerInput,
    PlanRequest,
    dynamic_goal_recovery_feedback,
    goal_provider_request_snapshot,
    goal_provider_response_snapshot,
    provider_call_history_metadata,
)
from app.core.config import Settings


def _settings(*, total_timeout: float = 1.0, observability: str = "NORMAL") -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite+pysqlite:///:memory:",
        model_provider="openai_compatible",
        model_name="fake-model",
        model_api_key=SecretStr("not-a-real-key"),
        model_timeout_seconds=1.0,
        model_total_timeout_seconds=total_timeout,
        goal_resolution_observability=observability,
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


def test_dynamic_goal_payloads_use_separate_public_grounding_and_interpretation_contracts() -> None:
    captured: list[dict[str, object]] = []

    def complete(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert isinstance(body, dict)
        captured.append(body)
        user_payload = json.loads(body["messages"][1]["content"])
        if "public_catalog" in user_payload:
            content = (
                '{"status":"RESOLVED","candidate_refs":[{"ref_type":"NODE","key":"public_node"}]}'
            )
        else:
            content = (
                '{"status":"RESOLVED","requirements":[{"kind":"FACT",'
                '"node_key":"public_node","fact_key":"passable",'
                '"accepted_values":[true]}]}'
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": "stop",
                    }
                ]
            },
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(_settings(), transport=httpx.MockTransport(complete))
    grounding = provider.ground_dynamic_goal_entities(
        DynamicGoalEntityGroundingRequest(
            goal="repair the public road",
            public_catalog={
                "entities": [{"key": "public_node", "name": "Public Road"}],
                "public_topology": {"relations": []},
            },
        )
    )
    interpretation = provider.interpret_dynamic_goal(
        DynamicGoalInterpretationRequest(
            goal="repair the public road",
            ontology={
                "world": {
                    "nodes": [{"key": "public_node", "name": "Public Road"}],
                    "facts": [
                        {
                            "node_key": "public_node",
                            "fact_key": "passable",
                            "value_type": "BOOLEAN",
                        }
                    ],
                },
                "grounding": {
                    "projection": {
                        "allowed_entity_keys": ["public_node"],
                        "allowed_region_keys": [],
                        "allowed_resource_keys": [],
                        "allowed_derived_state_keys": [],
                        "allowed_fact_keys": ["public_node.passable"],
                    }
                },
            },
            grounded_entity_keys=("public_node",),
            recovery_attempt=1,
        )
    )

    assert grounding.candidate_keys == ()
    assert grounding.candidate_refs == (
        DynamicGoalCandidateReference(ref_type="NODE", key="public_node"),
    )
    assert interpretation.requirements
    assert json.loads(captured[0]["messages"][1]["content"])["public_catalog"]
    assert "ontology" not in json.loads(captured[0]["messages"][1]["content"])
    interpretation_payload = json.loads(captured[1]["messages"][1]["content"])
    assert interpretation_payload["grounded_entity_keys"] == ["public_node"]
    assert interpretation_payload["recovery_attempt"] == 1
    assert isinstance(interpretation_payload["ontology"]["grounding"]["projection"], dict)
    assert interpretation_payload["ontology"]["grounding"]["projection"] == {
        "allowed_entity_keys": ["public_node"],
        "allowed_region_keys": [],
        "allowed_resource_keys": [],
        "allowed_derived_state_keys": [],
        "allowed_fact_keys": ["public_node.passable"],
    }
    assert "temperature" not in captured[0]
    assert "seed" not in captured[0]
    assert "temperature" not in captured[1]
    assert "seed" not in captured[1]


def test_dynamic_goal_requirement_schema_is_strict_and_discriminated() -> None:
    schema = DynamicGoalInterpretation.model_json_schema()
    definitions = schema["$defs"]
    union = definitions["AdHocGoalRequirementCandidateV1"]

    assert union["discriminator"] == {
        "mapping": {
            "DERIVED_STATE": "#/$defs/AdHocDerivedStateRequirementCandidateV1",
            "FACT": "#/$defs/AdHocFactRequirementCandidateV1",
            "RESOURCE_AT_LEAST": "#/$defs/AdHocResourceAtLeastRequirementCandidateV1",
        },
        "propertyName": "kind",
    }
    expected_required = {
        "AdHocFactRequirementCandidateV1": {"kind", "node_key", "fact_key", "accepted_values"},
        "AdHocResourceAtLeastRequirementCandidateV1": {
            "kind",
            "region_key",
            "resource_key",
            "minimum",
        },
        "AdHocDerivedStateRequirementCandidateV1": {
            "kind",
            "derived_key",
            "accepted_values",
        },
    }
    for definition_name, required in expected_required.items():
        definition = definitions[definition_name]
        assert definition["additionalProperties"] is False
        assert set(definition["required"]) == required
        assert "target_value" not in definition["properties"]

    assert (
        definitions["AdHocFactRequirementCandidateV1"]["properties"]["accepted_values"]["minItems"]
        == 1
    )
    assert (
        definitions["AdHocDerivedStateRequirementCandidateV1"]["properties"]["accepted_values"][
            "minItems"
        ]
        == 1
    )
    assert (
        definitions["AdHocResourceAtLeastRequirementCandidateV1"]["properties"]["minimum"][
            "minimum"
        ]
        == 0
    )


@pytest.mark.parametrize(
    "invalid_requirement",
    [
        {
            "kind": "DERIVED_STATE",
            "derived_key": "north_basic_engineering_support",
        },
        {
            "kind": "DERIVED_STATE",
            "derived_key": "north_basic_engineering_support",
            "accepted_values": None,
        },
        {
            "kind": "DERIVED_STATE",
            "derived_key": "north_basic_engineering_support",
            "accepted_values": [],
        },
        {
            "kind": "DERIVED_STATE",
            "derived_key": "north_basic_engineering_support",
            "accepted_values": ["AVAILABLE"],
            "target_value": "AVAILABLE",
        },
        {
            "kind": "FACT",
            "node_key": "public_node",
            "accepted_values": [True],
        },
        {
            "kind": "RESOURCE_AT_LEAST",
            "region_key": "east_residential_district",
            "resource_key": "emergency_relief_supplies",
        },
    ],
)
def test_dynamic_goal_requirement_union_rejects_invalid_shapes(
    invalid_requirement: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        DynamicGoalInterpretation.model_validate(
            {"status": "RESOLVED", "requirements": [invalid_requirement]}
        )


def test_dynamic_goal_recovery_feedback_contains_the_legal_derived_shape() -> None:
    ontology = {
        "world": {
            "derived_states": [
                {
                    "key": "north_basic_engineering_support",
                    "target_value": "AVAILABLE",
                }
            ]
        }
    }
    feedback = dynamic_goal_recovery_feedback(
        {
            "status": "RESOLVED",
            "requirements": [
                {
                    "kind": "DERIVED_STATE",
                    "derived_key": "north_basic_engineering_support",
                }
            ],
        },
        public_ontology=ontology,
    )

    assert [item.model_dump(mode="json") for item in feedback] == [
        {
            "requirement_index": 0,
            "kind": "DERIVED_STATE",
            "issue": "MISSING_REQUIRED_FIELD",
            "field": "accepted_values",
            "expected_shape": {
                "kind": "DERIVED_STATE",
                "derived_key": "north_basic_engineering_support",
                "accepted_values": ["AVAILABLE"],
            },
            "focused_target_value": "AVAILABLE",
        }
    ]

    request = DynamicGoalInterpretationRequest(
        goal="restore northern support",
        ontology=ontology,
        grounded_entity_keys=("north_basic_engineering_support",),
        recovery_attempt=1,
        recovery_feedback=feedback,
    )
    provider = OpenAICompatibleGenericProvider(
        _settings(), transport=httpx.MockTransport(lambda request: _goal_response(request))
    )
    body, _ = provider._build_request_body("dynamic_goal", request.model_dump(mode="json"))
    user_payload = json.loads(body["messages"][1]["content"])
    assert user_payload["recovery_feedback"] == [item.model_dump(mode="json") for item in feedback]
    assert "DERIVED_STATE" in body["messages"][0]["content"]
    assert '"accepted_values":["AVAILABLE"]' in body["messages"][0]["content"]
    assert "target_value" in body["messages"][0]["content"]


def test_dynamic_goal_calls_keep_independent_metadata_history() -> None:
    def complete(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user_payload = json.loads(body["messages"][1]["content"])
        is_grounding = "public_catalog" in user_payload
        content = (
            '{"status":"RESOLVED","candidate_refs":[{"ref_type":"NODE","key":"public_node"}]}'
            if is_grounding
            else (
                '{"status":"RESOLVED","requirements":[{"kind":"FACT",'
                '"node_key":"public_node","fact_key":"passable",'
                '"accepted_values":[true]}]}'
            )
        )
        prompt_tokens = 11 if is_grounding else 13
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "prompt_cache_hit_tokens": 3 if is_grounding else 4,
                    "prompt_cache_miss_tokens": 8 if is_grounding else 9,
                    "completion_tokens": 5 if is_grounding else 6,
                    "completion_tokens_details": {
                        "reasoning_tokens": 1 if is_grounding else 2,
                    },
                    "total_tokens": 16 if is_grounding else 19,
                },
            },
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(_settings(), transport=httpx.MockTransport(complete))
    provider.ground_dynamic_goal_entities(
        DynamicGoalEntityGroundingRequest(
            goal="repair the public road",
            public_catalog={"entities": [{"key": "public_node"}]},
        )
    )
    provider.interpret_dynamic_goal(
        DynamicGoalInterpretationRequest(
            goal="repair the public road",
            ontology={
                "world": {
                    "nodes": [{"key": "public_node"}],
                    "facts": [
                        {
                            "node_key": "public_node",
                            "fact_key": "passable",
                            "value_type": "BOOLEAN",
                        }
                    ],
                }
            },
            grounded_entity_keys=("public_node",),
        )
    )

    history = provider.call_metadata_history
    assert len(history) == 2
    assert [item.call_sequence for item in history] == [1, 2]
    assert [item.call_type for item in history] == [
        "DYNAMIC_GOAL_GROUNDING",
        "DYNAMIC_GOAL",
    ]
    assert [item.prompt_tokens for item in history] == [11, 13]
    assert [item.prompt_cache_hit_tokens for item in history] == [3, 4]
    assert [item.prompt_cache_miss_tokens for item in history] == [8, 9]
    assert [item.completion_tokens for item in history] == [5, 6]
    assert [item.reasoning_tokens for item in history] == [1, 2]
    assert all(item.latency_ms >= 0 for item in history)
    assert [item["call_sequence"] for item in provider_call_history_metadata(provider)] == [1, 2]
    assert all(item.debug_snapshot is None for item in history)


def test_debug_goal_calls_keep_safe_input_and_output_snapshots() -> None:
    def complete(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user_payload = json.loads(body["messages"][1]["content"])
        if "public_catalog" in user_payload:
            content = (
                '{"status":"RESOLVED","candidate_refs":[{"ref_type":"NODE","key":"public_node"}]}'
            )
        else:
            content = (
                '{"status":"RESOLVED","requirements":[{"kind":"FACT",'
                '"node_key":"public_node","fact_key":"passable",'
                '"accepted_values":[true]}]}'
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(
        _settings(observability="DEBUG"),
        transport=httpx.MockTransport(complete),
    )
    provider.ground_dynamic_goal_entities(
        DynamicGoalEntityGroundingRequest(
            goal="repair the public road",
            public_catalog={"references": [{"ref_type": "NODE", "key": "public_node"}]},
        )
    )
    provider.interpret_dynamic_goal(
        DynamicGoalInterpretationRequest(
            goal="repair the public road",
            ontology={"world": {"nodes": [{"key": "public_node"}]}},
            grounded_entity_keys=("public_node",),
            recovery_attempt=1,
        )
    )

    history = provider.call_metadata_history
    assert len(history) == 2
    assert history[0].debug_snapshot is not None
    assert history[0].debug_snapshot["input"]["goal"] == "repair the public road"
    assert history[0].debug_snapshot["input"]["public_catalog"]
    assert history[0].debug_snapshot["output"]["status"] == "RESOLVED"
    assert history[1].debug_snapshot is not None
    assert history[1].debug_snapshot["input"]["recovery_attempt"] == 1
    assert history[1].debug_snapshot["input"]["ontology"]
    assert history[1].debug_snapshot["output"]["requirements"]


def test_goal_snapshot_size_caps_record_truncation_without_raw_payload() -> None:
    request_snapshot = goal_provider_request_snapshot(
        "dynamic_goal_grounding",
        {"goal": "x", "public_catalog": {"references": ["x" * 4000] * 200}},
    )
    response_snapshot = goal_provider_response_snapshot(
        "dynamic_goal",
        {
            "status": "RESOLVED",
            "requirements": [{"kind": "FACT", "accepted_values": ["x" * 4000]}] * 200,
        },
    )

    assert request_snapshot["truncated"] is True
    assert request_snapshot["original_size"] > request_snapshot["stored_size"]
    assert response_snapshot["truncated"] is True
    assert response_snapshot["original_size"] > response_snapshot["stored_size"]
    assert "x" * 4000 not in str(request_snapshot)
    assert "x" * 4000 not in str(response_snapshot)


def test_dynamic_goal_calls_use_fast_semantic_profile_and_planning_keeps_reasoning_profile() -> (
    None
):
    captured: list[dict[str, object]] = []

    def complete(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert isinstance(body, dict)
        captured.append(body)
        user_payload = json.loads(body["messages"][1]["content"])
        if "public_catalog" in user_payload:
            content = (
                '{"status":"RESOLVED","candidate_refs":[{"ref_type":"NODE","key":"public_node"}]}'
            )
        elif "ontology" in user_payload:
            content = (
                '{"status":"RESOLVED","requirements":[{"kind":"FACT",'
                '"node_key":"public_node","fact_key":"passable",'
                '"accepted_values":[true]}]}'
            )
        else:
            content = _plan_response(request).json()["choices"][0]["message"]["content"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(
        _settings().model_copy(
            update={
                "model_name": "planning-model",
                "semantic_model": "semantic-model",
                "model_thinking_mode": "enabled",
                "model_reasoning_effort": "high",
                "model_max_output_tokens": 8192,
            }
        ),
        transport=httpx.MockTransport(complete),
    )
    provider.ground_dynamic_goal_entities(
        DynamicGoalEntityGroundingRequest(
            goal="repair the public road",
            public_catalog={"entities": [{"key": "public_node"}]},
        )
    )
    provider.interpret_dynamic_goal(
        DynamicGoalInterpretationRequest(
            goal="repair the public road",
            ontology={
                "world": {
                    "nodes": [{"key": "public_node"}],
                    "facts": [{"node_key": "public_node", "fact_key": "passable"}],
                }
            },
            grounded_entity_keys=("public_node",),
        )
    )
    provider.propose_plan(
        PlanRequest(
            call_type="INITIAL_PLAN",
            goal="known goal",
            planner_input=PlannerInput(objective={"objective_keys": ["known"]}),
        )
    )

    assert [body["thinking"] for body in captured] == [
        {"type": "disabled"},
        {"type": "disabled"},
        {"type": "enabled"},
    ]
    assert [body["model"] for body in captured] == [
        "semantic-model",
        "semantic-model",
        "planning-model",
    ]
    assert [body["reasoning_effort"] for body in captured] == ["low", "low", "high"]
    assert [body.get("max_tokens") for body in captured] == [2048, 2048, 8192]
    history = provider.call_metadata_history
    assert [item.profile for item in history] == [
        "FAST_SEMANTIC",
        "FAST_SEMANTIC",
        "PLANNING_REASONING",
    ]
    assert [item.thinking_mode for item in history] == ["disabled", "disabled", "enabled"]
    assert [item.model for item in history] == [
        "semantic-model",
        "semantic-model",
        "planning-model",
    ]
    assert [item.configured_output_token_limit for item in history] == [2048, 2048, 8192]


def test_dynamic_interpretation_schema_failure_records_safe_type_diagnostics() -> None:
    def invalid_response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"status":"RESOLVED","requirements":[{"kind":"FACT",'
                                '"node_key":"public_node","fact_key":"passable",'
                                '"accepted_values":"true"}]}'
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(
        _settings().model_copy(
            update={
                "model_thinking_mode": "enabled",
                "model_reasoning_effort": "high",
                "goal_resolution_observability": "DEBUG",
            }
        ),
        transport=httpx.MockTransport(invalid_response),
    )

    with pytest.raises(GenericProviderError) as error:
        provider.interpret_dynamic_goal(
            DynamicGoalInterpretationRequest(
                goal="repair the public road",
                ontology={"world": {"nodes": [{"key": "public_node"}]}},
                grounded_entity_keys=("public_node",),
            )
        )

    expected = {
        "validation_error_type": "tuple_type",
        "field_path": "requirements[0].FACT.accepted_values",
        "expected_type": "array",
        "actual_json_type": "string",
    }
    assert error.value.code == "MODEL_PROVIDER_RESPONSE_INVALID"
    assert error.value.validation_diagnostics == (expected,)
    metadata = provider.last_call_metadata
    assert metadata is not None
    assert metadata.profile == "FAST_SEMANTIC"
    assert metadata.validation_diagnostics == (expected,)
    assert "true" not in str(error.value.validation_diagnostics)
    assert metadata.debug_snapshot is not None
    output = metadata.debug_snapshot["output"]
    assert output["requirements"][0]["accepted_values"] == {
        "json_type": "string",
        "value_omitted": True,
    }


def test_retryable_transport_failure_retries_once_and_records_each_network_call() -> None:
    calls = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.RemoteProtocolError("peer closed the connection", request=request)
        return _goal_response(request)

    provider = OpenAICompatibleGenericProvider(_settings(), transport=httpx.MockTransport(flaky))

    result = provider.select_objectives(
        GoalSelectionRequest(goal="known", objective_candidates=({"key": "known"},))
    )

    assert result.objective_keys == ("known",)
    assert calls == 2
    metadata = provider.last_call_metadata
    assert metadata is not None
    assert metadata.outcome == "SUCCESS"
    assert len(metadata.network_calls) == 2
    assert metadata.network_calls[0]["call_index"] == 1
    assert metadata.network_calls[0]["outcome"] == "ERROR"
    assert metadata.network_calls[0]["error_category"] == "RemoteProtocolError"
    assert metadata.network_calls[0]["retryable"] is True
    assert metadata.network_calls[1]["call_index"] == 2
    assert metadata.network_calls[1]["outcome"] == "SUCCESS"
    assert metadata.network_calls[1]["response_headers_received_at"] is not None
    assert metadata.network_calls[1]["response_bytes_received"] is not None


def test_retryable_transport_failure_is_bounded_to_one_retry() -> None:
    calls = 0

    def broken(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadError("connection reset", request=request)

    provider = OpenAICompatibleGenericProvider(_settings(), transport=httpx.MockTransport(broken))

    with pytest.raises(GenericProviderError) as error:
        provider.select_objectives(
            GoalSelectionRequest(goal="known", objective_candidates=({"key": "known"},))
        )

    assert error.value.code == "MODEL_PROVIDER_HTTP_ERROR"
    assert calls == 2
    metadata = provider.last_call_metadata
    assert metadata is not None
    assert metadata.outcome == "ERROR"
    assert len(metadata.network_calls) == 2
    assert all(item["outcome"] == "ERROR" for item in metadata.network_calls)
    assert all(item["retryable"] is True for item in metadata.network_calls)


def test_completed_response_or_invalid_response_is_never_retried() -> None:
    status_calls = 0

    def status_error(request: httpx.Request) -> httpx.Response:
        nonlocal status_calls
        status_calls += 1
        return httpx.Response(503, request=request)

    provider = OpenAICompatibleGenericProvider(
        _settings(), transport=httpx.MockTransport(status_error)
    )
    with pytest.raises(GenericProviderError) as status_failure:
        provider.select_objectives(
            GoalSelectionRequest(goal="known", objective_candidates=({"key": "known"},))
        )
    assert status_failure.value.code == "MODEL_PROVIDER_HTTP_ERROR"
    assert status_calls == 1

    malformed_calls = 0

    def malformed(request: httpx.Request) -> httpx.Response:
        nonlocal malformed_calls
        malformed_calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not-json"}}]},
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(
        _settings(), transport=httpx.MockTransport(malformed)
    )
    with pytest.raises(GenericProviderError) as malformed_failure:
        provider.select_objectives(
            GoalSelectionRequest(goal="known", objective_candidates=({"key": "known"},))
        )
    assert malformed_failure.value.code == "MODEL_PROVIDER_RESPONSE_INVALID"
    assert malformed_calls == 1
    metadata = provider.last_call_metadata
    assert metadata is not None
    assert len(metadata.network_calls) == 1
    assert metadata.network_calls[0]["outcome"] == "SUCCESS"


def test_transport_retry_obeys_one_logical_total_deadline() -> None:
    calls = 0

    def slow_second_attempt(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.RemoteProtocolError("peer closed the connection", request=request)
        sleep(0.15)
        return _goal_response(request)

    provider = OpenAICompatibleGenericProvider(
        _settings(total_timeout=0.02),
        transport=httpx.MockTransport(slow_second_attempt),
    )

    with pytest.raises(GenericProviderError) as error:
        provider.select_objectives(
            GoalSelectionRequest(goal="known", objective_candidates=({"key": "known"},))
        )

    assert error.value.code == "MODEL_PROVIDER_TIMEOUT"
    assert calls == 2
    metadata = provider.last_call_metadata
    assert metadata is not None
    assert metadata.outcome == "TIMEOUT"
    assert metadata.error_category == "PROVIDER_TOTAL_DEADLINE"
    assert len(metadata.network_calls) == 2
    assert metadata.network_calls[0]["outcome"] == "ERROR"
    assert metadata.network_calls[1]["outcome"] == "TIMEOUT"
    assert metadata.network_calls[1]["timeout_category"] == "PROVIDER_TOTAL_DEADLINE"


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
            planner_input=PlannerInput(objective={"objective_keys": ["known"]}),
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
        "SEMANTIC_MODEL",
        "GOAL_RESOLUTION_OBSERVABILITY",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None)

    assert settings.model_thinking_mode == "disabled"
    assert settings.model_reasoning_effort == "low"
    assert settings.semantic_model is None
    assert settings.goal_resolution_observability == "NORMAL"
    assert settings.model_timeout_seconds == 20
    assert settings.model_total_timeout_seconds == 60
    assert settings.model_max_output_tokens == 8192


def test_provider_settings_parse_independent_semantic_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEMANTIC_MODEL", "semantic-model")

    settings = Settings(_env_file=None)

    assert settings.semantic_model == "semantic-model"


def test_provider_settings_parse_goal_resolution_debug_observability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOAL_RESOLUTION_OBSERVABILITY", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.goal_resolution_observability == "DEBUG"


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
            planner_input=PlannerInput(
                objective={"objective_keys": ["known"]},
                known_world={"facts": {}},
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
