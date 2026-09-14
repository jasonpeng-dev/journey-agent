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
    DynamicGoalActionRoutingRequest,
    DynamicGoalCandidateReference,
    DynamicGoalEntityGrounding,
    DynamicGoalEntityGroundingRequest,
    DynamicGoalFamilyRoutingRequest,
    DynamicGoalGroundedOperation,
    DynamicGoalIntentDraft,
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    DynamicGoalMentionSlot,
    DynamicGoalOperationGrounding,
    DynamicGoalOperationGroundingRequest,
    DynamicGoalScalarMentionSlot,
    DynamicGoalSemanticRouting,
    DynamicGoalSemanticRoutingRequest,
    GenericProviderError,
    GoalSelectionRequest,
    OpenAICompatibleGenericProvider,
    PlannerInput,
    PlanRequest,
    _normalize_dynamic_goal_operation_grounding,
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


def test_semantic_routing_request_is_small_closed_and_observable() -> None:
    captured: list[dict[str, object]] = []

    def complete(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "family": "OPERATION",
                                    "action_match": "MATCHED",
                                    "action_key": "move_cargo",
                                    "candidate_keys": [],
                                    "clarification_prompt": None,
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    provider = OpenAICompatibleGenericProvider(
        _settings(observability="DEBUG"), transport=httpx.MockTransport(complete)
    )
    result = provider.route_dynamic_goal(
        DynamicGoalSemanticRoutingRequest(
            goal="move cargo",
            action_catalog=(
                {
                    "key": "move_cargo",
                    "name": "Move cargo",
                    "description": "Move a resource between regions",
                    "target": {"expected_type": "REGION"},
                    "bindings": [{"slot_key": "source", "expected_type": "REGION"}],
                    "parameters": [{"slot_key": "resource", "expected_type": "RESOURCE"}],
                },
            ),
        )
    )

    assert result.action_key == "move_cargo"
    user_payload = json.loads(captured[0]["messages"][1]["content"])
    assert set(user_payload) == {
        "goal",
        "action_catalog",
        "state_catalog",
        "recovery_attempt",
        "recovery_feedback",
    }
    assert "public_topology" not in str(user_payload)
    assert "temperature" not in captured[0]
    metadata = provider.call_metadata_history[-1]
    assert metadata.request_hash
    assert metadata.response_validation == "ACCEPTED"
    assert metadata.debug_snapshot is not None
    assert metadata.debug_snapshot["output"]["action_key"] == "move_cargo"
    assert metadata.prompt_template_version == "dynamic-goal-routing-v3"
    system_prompt = captured[0]["messages"][0]["content"]
    assert '"action_match":"MATCHED"' in system_prompt
    assert '"action_key":"exact_public_action_key","candidate_keys":[]' in system_prompt
    assert "Judge the player's semantic intent, not keyword matching." in system_prompt
    assert (
        "even when it produces a public terminal State, preserve OPERATION and never rewrite "
        "it as STATE"
    ) in system_prompt
    assert (
        "Once the intent is OPERATION, if exactly one Action in action_catalog semantically "
        "matches the requested invocation, return MATCHED"
    ) in system_prompt
    assert (
        "If multiple Actions genuinely compete for an OPERATION intent, return OPERATION with "
        "action_match AMBIGUOUS and every candidate_key"
    ) in system_prompt
    assert (
        "if no Action expresses the requested invocation, return OPERATION with action_match "
        "NO_MATCH"
    ) in system_prompt
    assert "how to reach it remains HOW" in system_prompt
    assert "把30个燃料运到南部 => OPERATION" in system_prompt
    assert "南部至少有30个燃料 => STATE" in system_prompt
    assert "修复中央河底隧道 => OPERATION" in system_prompt
    assert "让中央河底隧道处于可通行状态 => STATE" in system_prompt
    assert "keyword X means OPERATION" not in system_prompt


def test_family_routing_request_excludes_action_and_state_catalogs() -> None:
    captured: list[dict[str, object]] = []

    def complete(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"family":"STATE"}'}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 2},
            },
        )

    provider = OpenAICompatibleGenericProvider(
        _settings(observability="DEBUG"), transport=httpx.MockTransport(complete)
    )
    result = provider.decide_dynamic_goal_family(
        DynamicGoalFamilyRoutingRequest(
            goal="make the facility operational",
            deterministic_candidate_refs=(
                DynamicGoalCandidateReference(
                    ref_type="NODE", key="facility", provenance="EXACT_USER_MENTION"
                ),
            ),
        )
    )

    assert result.family == "STATE"
    payload = json.loads(captured[0]["messages"][1]["content"])
    assert set(payload) == {
        "goal",
        "deterministic_candidate_refs",
        "deterministic_ambiguous_refs",
        "semantic_candidate_refs",
        "explicit_role_evidence",
        "semantic_family_evidence",
        "recovery_attempt",
        "recovery_feedback",
    }
    assert "action_catalog" not in payload
    assert "state_catalog" not in payload
    prompt = captured[0]["messages"][0]["content"]
    assert "otherwise return STATE or OPERATION and freeze that choice" in prompt
    assert "Do not select, match, rank, or infer any Action or State requirement" in prompt
    assert "Candidate availability must not decide the family" in prompt
    assert provider.call_metadata_history[-1].call_type == "DYNAMIC_GOAL_FAMILY_ROUTING"


def test_action_routing_request_is_operation_only_and_excludes_state_candidates() -> None:
    captured: list[dict[str, object]] = []

    def complete(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"action_match":"MATCHED","action_key":"repair",'
                                '"candidate_keys":[],"clarification_prompt":null}'
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 4},
            },
        )

    provider = OpenAICompatibleGenericProvider(
        _settings(observability="DEBUG"), transport=httpx.MockTransport(complete)
    )
    result = provider.route_dynamic_goal_action(
        DynamicGoalActionRoutingRequest(
            goal="repair the facility",
            action_catalog=(
                {"key": "repair", "name": "Repair", "description": "Repair a facility"},
            ),
            relevant_public_entities=(
                {
                    "ref_type": "NODE",
                    "key": "corridor",
                    "provenance": "TOPOLOGY_ENRICHED",
                    "name": "Service corridor",
                    "description": "A public road corridor.",
                    "node_type_key": "transport",
                },
            ),
            public_topology={
                "relations": [],
                "transport_endpoint_pairs": [
                    {
                        "entity_key": "corridor",
                        "endpoint_region_keys": ["north", "central"],
                        "derived_target": {
                            "ref_type": "NODE",
                            "key": "corridor",
                            "name": "Service corridor",
                            "node_type_key": "transport",
                            "description": "A public road corridor.",
                            "provenance": "TOPOLOGY_ENRICHED",
                        },
                    }
                ],
            },
        )
    )

    assert result.action_key == "repair"
    payload = json.loads(captured[0]["messages"][1]["content"])
    assert payload["frozen_family"] == "OPERATION"
    assert payload["relevant_public_entities"][0]["key"] == "corridor"
    assert payload["public_topology"]["transport_endpoint_pairs"][0]["derived_target"] == {
        "ref_type": "NODE",
        "key": "corridor",
        "name": "Service corridor",
        "node_type_key": "transport",
        "description": "A public road corridor.",
        "provenance": "TOPOLOGY_ENRICHED",
    }
    assert "state_catalog" not in payload
    assert "family" not in result.model_dump(mode="json")
    prompt = captured[0]["messages"][0]["content"]
    assert "family is already immutably OPERATION" in prompt
    assert "never reconsider, restate, or change the family" in prompt
    assert "structural applicability, not semantic equivalence" in prompt
    assert "This stage selects only the Action identity" in prompt
    assert "does not ground the final target or actor" in prompt
    assert "complete bindings or parameters" in prompt
    assert "prove Action preconditions" in prompt
    assert "an execution precondition is not yet established" in prompt
    assert "conditions alone are not reasons for NO_MATCH" in prompt
    assert "Positive semantic evidence is sufficient for MATCHED" in prompt
    assert "a repair, restore, or remediation request is not equally matched" in prompt
    assert "NO_SEMANTIC_ACTION" in prompt
    assert "SEMANTIC_CONFLICT" in prompt
    assert provider.call_metadata_history[-1].call_type == "DYNAMIC_GOAL_ACTION_ROUTING"


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (
            {
                "action_match": "NO_MATCH",
                "action_key": None,
                "candidate_keys": [],
                "clarification_prompt": None,
            },
            "MISSING_NO_MATCH_REASON",
        ),
        (
            {
                "action_match": "NO_MATCH",
                "action_key": None,
                "candidate_keys": [],
                "clarification_prompt": None,
                "no_match_reason": "UNCERTAIN",
            },
            "INVALID_NO_MATCH_REASON",
        ),
        (
            {
                "action_match": "MATCHED",
                "action_key": "repair",
                "candidate_keys": [],
                "clarification_prompt": None,
                "no_match_reason": "SEMANTIC_CONFLICT",
            },
            "MATCHED_HAS_NO_MATCH_REASON",
        ),
    ],
)
def test_action_routing_rejects_missing_or_malformed_typed_no_match_reason(
    response: dict[str, object], expected_code: str
) -> None:
    def complete(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(response)},
                        "finish_reason": "stop",
                    }
                ]
            },
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(_settings(), transport=httpx.MockTransport(complete))

    with pytest.raises(GenericProviderError) as captured:
        provider.route_dynamic_goal_action(
            DynamicGoalActionRoutingRequest(
                goal="repair the facility",
                action_catalog=({"key": "repair", "name": "Repair", "description": "Repair"},),
            )
        )

    assert captured.value.validation_diagnostics[0]["code"] == expected_code


def test_action_routing_accepts_valid_typed_no_match_and_ambiguous_boundary() -> None:
    responses = iter(
        [
            {
                "action_match": "NO_MATCH",
                "action_key": None,
                "candidate_keys": [],
                "clarification_prompt": None,
                "no_match_reason": "NO_SEMANTIC_ACTION",
            },
            {
                "action_match": "AMBIGUOUS",
                "action_key": None,
                "candidate_keys": ["repair", "inspect"],
                "clarification_prompt": None,
                "no_match_reason": None,
            },
        ]
    )

    def complete(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(next(responses))},
                        "finish_reason": "stop",
                    }
                ]
            },
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(_settings(), transport=httpx.MockTransport(complete))
    request = DynamicGoalActionRoutingRequest(
        goal="repair the facility",
        action_catalog=(
            {"key": "repair", "name": "Repair", "description": "Repair"},
            {"key": "inspect", "name": "Inspect", "description": "Inspect"},
        ),
    )

    no_match = provider.route_dynamic_goal_action(request)
    assert no_match.no_match_reason == "NO_SEMANTIC_ACTION"
    ambiguous = provider.route_dynamic_goal_action(request)
    assert ambiguous.action_match == "AMBIGUOUS"
    assert ambiguous.no_match_reason is None


def test_semantic_routing_does_not_force_temperature_zero() -> None:
    provider = OpenAICompatibleGenericProvider(_settings())

    routing_body, _ = provider._build_request_body(
        "dynamic_goal_routing",
        DynamicGoalSemanticRoutingRequest(goal="move cargo", action_catalog=()).model_dump(
            mode="json"
        ),
    )
    operation_body, _ = provider._build_request_body("dynamic_goal_operation", {})
    family_body, _ = provider._build_request_body("dynamic_goal_family_routing", {})
    action_body, _ = provider._build_request_body("dynamic_goal_action_routing", {})
    state_body, _ = provider._build_request_body("dynamic_goal", {})

    assert "temperature" not in routing_body
    assert "temperature" not in operation_body
    assert "temperature" not in family_body
    assert "temperature" not in action_body
    assert "temperature" not in state_body


def test_semantic_routing_prompt_treats_topology_as_derived_action_evidence() -> None:
    provider = OpenAICompatibleGenericProvider(_settings())

    body, _ = provider._build_request_body("dynamic_goal_routing", {})
    prompt = body["messages"][0]["content"]

    assert "exact Region endpoint pair plus exactly one TOPOLOGY_ENRICHED Node" in prompt
    assert "one derived target evidence unit" in prompt
    assert (
        "do not treat the two Regions and that Node as three competing target identities" in prompt
    )
    assert "slot-shape compatibility establishes only structural applicability" in prompt
    assert "not a semantic Action match" in prompt
    assert "merely accepting a Node target or fitting Regions into slots is insufficient" in prompt
    assert "does not override the Family decision" in prompt
    assert "a genuinely terminal-state-only reading may remain STATE" in prompt
    assert "repair means clear_transport" not in prompt
    assert "road means transport Node" not in prompt


def test_operation_prompt_commits_unique_topology_target_without_guessing() -> None:
    provider = OpenAICompatibleGenericProvider(_settings())

    body, _ = provider._build_request_body("dynamic_goal_operation", {})
    prompt = body["messages"][0]["content"]

    assert "exact endpoint Regions" in prompt
    assert "exactly one contract-compatible mapping to target Node T" in prompt
    assert "explicit indirect constraint on T" in prompt
    assert "return target GROUNDED with T's canonical key" in prompt
    assert "leave target UNRESOLVED for deterministic backend composition" in prompt
    assert "Do not ask the player to repeat the endpoints or name T" in prompt
    assert (
        "Other same-type Nodes in public_references are ontology context and do not make "
        "that unique mapping ambiguous"
    ) in prompt
    assert "With zero or multiple compatible mappings, do not guess" in prompt
    assert "do not replace or auto-correct X" in prompt
    assert "backend typed validation owns that conflict" in prompt
    assert "north_service_corridor" not in prompt
    assert "central_east_transit_link" not in prompt


def _operation_request(
    *,
    recovery_attempt: int = 0,
    recovery_feedback: tuple[dict[str, object], ...] = (),
) -> DynamicGoalOperationGroundingRequest:
    return DynamicGoalOperationGroundingRequest(
        goal="move cargo to Region A",
        action_key="move_cargo",
        action_contract={
            "action_key": "move_cargo",
            "target": {"slot_key": "target", "expected_type": "REGION"},
            "actor": {"slot_key": "actor", "expected_type": "ACTOR"},
            "bindings": [],
            "parameters": [],
        },
        public_references=(
            {"ref_type": "REGION", "key": "region_a", "name": "Region A"},
            {"ref_type": "REGION", "key": "region_b", "name": "Region B"},
        ),
        recovery_attempt=recovery_attempt,
        recovery_feedback=recovery_feedback,
    )


def _operation_payload(*, target_key: str = "region_a") -> dict[str, object]:
    return {
        "frozen_family": "OPERATION",
        "status": "RESOLVED",
        "intent": {
            "frozen_family": "OPERATION",
            "action_key": "move_cargo",
            "actor": {
                "slot_key": "actor",
                "expected_type": "ACTOR",
                "status": "NOT_SPECIFIED",
            },
            "target": {
                "slot_key": "target",
                "expected_type": "REGION",
                "status": "GROUNDED",
                "ref_type": "REGION",
                "key": target_key,
            },
            "bindings": [],
            "parameters": [],
        },
        "supplementary_candidate_refs": [],
        "clarification_prompt": None,
    }


def _operation_transport(payload: dict[str, object]) -> httpx.MockTransport:
    def complete(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(payload)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10},
            },
            request=request,
        )

    return httpx.MockTransport(complete)


def test_operation_reference_key_with_redundant_name_is_safely_normalized() -> None:
    payload = _operation_payload()
    payload["intent"]["target"]["value"] = "Region A"  # type: ignore[index]
    provider = OpenAICompatibleGenericProvider(
        _settings(),
        transport=_operation_transport(payload),
    )

    result = provider.ground_dynamic_goal_operation(_operation_request())

    assert result.intent is not None
    assert result.intent.target.key == "region_a"
    assert result.intent.target.value is None


def test_operation_reference_key_with_conflicting_identity_is_rejected() -> None:
    payload = _operation_payload()
    payload["intent"]["target"]["value"] = "Region B"  # type: ignore[index]
    provider = OpenAICompatibleGenericProvider(
        _settings(),
        transport=_operation_transport(payload),
    )

    with pytest.raises(GenericProviderError) as captured:
        provider.ground_dynamic_goal_operation(_operation_request())

    assert captured.value.validation_diagnostics[0]["code"] == ("REFERENCE_KEY_AND_VALUE_BOTH_SET")


def test_operation_slot_contract_metadata_is_safely_removed() -> None:
    payload = _operation_payload()
    target = payload["intent"]["target"]  # type: ignore[index]
    target.update(  # type: ignore[union-attr]
        {"name": "Target", "node_type_keys": ["region"], "description": "metadata"}
    )
    provider = OpenAICompatibleGenericProvider(
        _settings(),
        transport=_operation_transport(payload),
    )

    result = provider.ground_dynamic_goal_operation(_operation_request())

    assert result.intent is not None and result.intent.target.key == "region_a"


def test_operation_normalization_does_not_repair_unknown_canonical_key() -> None:
    payload = _operation_payload(target_key="unknown_region")

    normalized = _normalize_dynamic_goal_operation_grounding(
        payload,
        _operation_request(),
    )

    assert normalized["intent"]["target"]["key"] == "unknown_region"  # type: ignore[index]


def test_operation_normalization_does_not_ground_unresolved_slot() -> None:
    payload = _operation_payload()
    payload["intent"]["target"] = {  # type: ignore[index]
        "slot_key": "target",
        "expected_type": "REGION",
        "status": "UNRESOLVED",
    }

    normalized = _normalize_dynamic_goal_operation_grounding(
        payload,
        _operation_request(),
    )
    result = DynamicGoalOperationGrounding.model_validate(normalized)

    assert result.intent is not None
    assert result.intent.target.status == "UNRESOLVED"
    assert result.intent.target.key is None


def test_operation_typed_recovery_rejects_changed_frozen_action() -> None:
    payload = _operation_payload()
    payload["intent"]["action_key"] = "other_action"  # type: ignore[index]
    feedback = (
        {
            "code": "REFERENCE_KEY_AND_VALUE_BOTH_SET",
            "preserve": {
                "frozen_family": "OPERATION",
                "intent.frozen_family": "OPERATION",
                "intent.action_key": "move_cargo",
                "intent.target": {
                    "slot_key": "target",
                    "expected_type": "REGION",
                    "status": "GROUNDED",
                    "ref_type": "REGION",
                    "key": "region_a",
                    "value": None,
                    "surface": None,
                },
            },
            "fix_only": ["intent.actor"],
        },
    )
    provider = OpenAICompatibleGenericProvider(
        _settings(),
        transport=_operation_transport(payload),
    )

    with pytest.raises(GenericProviderError) as captured:
        provider.ground_dynamic_goal_operation(
            _operation_request(recovery_attempt=1, recovery_feedback=feedback)
        )

    assert captured.value.validation_diagnostics[0]["code"] == ("RECOVERY_CHANGED_PRESERVED_FIELD")


def test_operation_invalid_top_level_status_gets_typed_recovery_feedback() -> None:
    payload = _operation_payload()
    payload["status"] = "UNRESOLVED"
    provider = OpenAICompatibleGenericProvider(
        _settings(),
        transport=_operation_transport(payload),
    )

    with pytest.raises(GenericProviderError) as captured:
        provider.ground_dynamic_goal_operation(_operation_request())

    diagnostic = captured.value.validation_diagnostics[0]
    assert diagnostic["code"] == "INVALID_OPERATION_STATUS"
    assert diagnostic["allowed"] == [
        "RESOLVED",
        "NEEDS_CLARIFICATION",
        "UNSUPPORTED",
    ]
    assert diagnostic["preserve"] == {"frozen_family": "OPERATION"}


def test_semantic_routing_normalizes_only_redundant_matched_candidate_key() -> None:
    def complete(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"family":"OPERATION","action_match":"MATCHED",'
                                '"action_key":"move_cargo","candidate_keys":["move_cargo"],'
                                '"clarification_prompt":null}'
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(_settings(), transport=httpx.MockTransport(complete))

    result = provider.route_dynamic_goal(
        DynamicGoalSemanticRoutingRequest(goal="move cargo", action_catalog=())
    )

    assert result == DynamicGoalSemanticRouting(
        family="OPERATION",
        action_match="MATCHED",
        action_key="move_cargo",
        candidate_keys=(),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "family": "OPERATION",
            "action_match": "MATCHED",
            "action_key": "move_cargo",
            "candidate_keys": ["move_cargo", "carry_cargo"],
        },
        {
            "family": "OPERATION",
            "action_match": "AMBIGUOUS",
            "action_key": "move_cargo",
            "candidate_keys": ["move_cargo", "carry_cargo"],
        },
    ],
)
def test_semantic_routing_does_not_normalize_non_equivalent_shapes(
    payload: dict[str, object],
) -> None:
    def complete(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}]
            },
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(_settings(), transport=httpx.MockTransport(complete))

    with pytest.raises(GenericProviderError) as captured:
        provider.route_dynamic_goal(
            DynamicGoalSemanticRoutingRequest(goal="move cargo", action_catalog=())
        )

    if payload["action_match"] == "MATCHED":
        assert captured.value.validation_diagnostics[0]["code"] == ("MATCHED_HAS_CANDIDATE_KEYS")
        assert captured.value.validation_diagnostics[0]["preserve"] == {
            "family": "OPERATION",
            "action_match": "MATCHED",
            "action_key": "move_cargo",
        }


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
    union = definitions["AdHocGoalRequirementCandidateV2"]

    assert union["discriminator"] == {
        "mapping": {
            "DERIVED_STATE": "#/$defs/AdHocDerivedStateRequirementCandidateV1",
            "FACT": "#/$defs/AdHocFactRequirementCandidateV1",
            "RESOURCE_AT_LEAST": "#/$defs/AdHocResourceAtLeastRequirementCandidateV1",
            "ACTION_COMPLETED": "#/$defs/AdHocActionCompletedRequirementCandidateV1",
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
        "AdHocActionCompletedRequirementCandidateV1": {"kind", "action_key"},
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


def test_dynamic_goal_prompt_explains_action_defined_derived_bindings() -> None:
    ontology = {
        "world": {
            "actions": [
                {
                    "key": "move_cargo",
                    "operation_binding_contract": {
                        "bindings": [
                            {
                                "role": "source_region",
                                "source": "EXECUTION_START_ACTOR_REGION",
                                "value_type": "REGION",
                            }
                        ],
                        "target": {
                            "field": "target_key",
                            "role": "destination_region",
                            "source": "ACTION_TARGET_KEY",
                            "value_type": "REGION",
                        },
                    },
                }
            ]
        }
    }
    request = DynamicGoalInterpretationRequest(
        goal="move 30 cargo from source to destination",
        ontology=ontology,
    )
    provider = OpenAICompatibleGenericProvider(_settings())

    body, _size = provider._build_request_body("dynamic_goal", request.model_dump(mode="json"))
    payload = json.loads(body["messages"][1]["content"])
    prompt = body["messages"][0]["content"]

    assert payload["ontology"] == ontology
    for term in (
        "operation_binding_contract",
        "binding role is a canonical invocation role",
        "derived from execution context rather than a literal Action parameter",
        "EXECUTION_START_ACTOR_REGION",
        "ACTION_TARGET_KEY",
        "explicit source Region",
        "explicit destination in target_key",
        "Do not require a literal source or destination parameter",
    ):
        assert term in prompt


def test_dynamic_grounding_prompt_exposes_typed_roles_and_advisory_refs() -> None:
    deterministic_refs = (
        DynamicGoalCandidateReference(ref_type="REGION", key="region_a"),
        DynamicGoalCandidateReference(ref_type="REGION", key="region_b"),
    )
    request = DynamicGoalEntityGroundingRequest(
        goal="transport 30 cargo from region a to region b",
        public_catalog={
            "references": [
                {
                    "ref_type": "REGION",
                    "key": "region_a",
                    "name": "Region A",
                },
                {
                    "ref_type": "REGION",
                    "key": "region_b",
                    "name": "Region B",
                },
                {
                    "ref_type": "RESOURCE",
                    "key": "cargo_alpha",
                    "name": "Cargo Alpha",
                },
                {
                    "ref_type": "ACTION",
                    "key": "transport_resource",
                    "name": "Transport Resource",
                    "parameters": [
                        {"key": "resource_key", "value_type": "STRING"},
                        {"key": "amount", "value_type": "INTEGER"},
                    ],
                    "operation_binding_contract": {
                        "bindings": [
                            {
                                "role": "source_region",
                                "source": "EXECUTION_START_ACTOR_REGION",
                                "value_type": "REGION",
                            }
                        ],
                        "target": {
                            "field": "target_key",
                            "role": "destination_region",
                            "source": "ACTION_TARGET_KEY",
                            "value_type": "REGION",
                        },
                    },
                },
            ]
        },
        deterministic_candidate_refs=deterministic_refs,
    )
    provider = OpenAICompatibleGenericProvider(_settings())

    body, _size = provider._build_request_body(
        "dynamic_goal_grounding",
        request.model_dump(mode="json"),
    )
    payload = json.loads(body["messages"][1]["content"])
    prompt = body["messages"][0]["content"]

    assert payload["deterministic_candidate_refs"] == [
        item.model_dump(mode="json") for item in deterministic_refs
    ]
    for term in (
        "evidence-only Semantic Grounding",
        "public Action name, description, target_kind",
        "do not rely on a fixed",
        "advisory retrieval candidates",
        "GROUNDED, UNRESOLVED, or NOT_SPECIFIED",
        "source or target role may be a binding declared by the Action contract",
        "multiple compatible candidates remain equally plausible",
    ):
        assert term in prompt


def test_dynamic_goal_prompt_exposes_explicit_slot_provenance() -> None:
    intent = DynamicGoalIntentDraft(
        intent_kind="OPERATION",
        action=DynamicGoalMentionSlot(status="GROUNDED", ref_type="ACTION", key="move_cargo"),
        source=DynamicGoalMentionSlot(status="GROUNDED", ref_type="REGION", key="region_a"),
        target=DynamicGoalMentionSlot(status="GROUNDED", ref_type="REGION", key="region_b"),
        resource=DynamicGoalMentionSlot(
            status="UNRESOLVED",
            ref_type="RESOURCE",
            surface="cargo shorthand",
        ),
        amount=DynamicGoalScalarMentionSlot(status="GROUNDED", value=30),
        actor=DynamicGoalMentionSlot(status="NOT_SPECIFIED"),
    )
    request = DynamicGoalEntityGroundingRequest(
        goal="move 30 cargo from region a to region b",
        public_catalog={"references": []},
        intent=intent,
    )
    provider = OpenAICompatibleGenericProvider(_settings())

    body, _size = provider._build_request_body(
        "dynamic_goal_grounding",
        request.model_dump(mode="json"),
    )
    payload = json.loads(body["messages"][1]["content"])
    prompt = body["messages"][0]["content"]

    assert payload["intent"] == intent.model_dump(mode="json")
    for term in (
        "evidence-only Semantic Grounding",
        "GROUNDED, UNRESOLVED, or NOT_SPECIFIED",
        "NOT_SPECIFIED means the player did not constrain it",
        "selected by semantic role evidence",
        "binding declared by the Action contract",
        "topology is context, not a substitute",
    ):
        assert term in prompt


def test_dynamic_goal_grounding_scalar_wire_schema_matches_runtime_contract() -> None:
    schema = DynamicGoalEntityGrounding.model_json_schema()
    definitions = schema["$defs"]
    intent_schema = definitions["DynamicGoalIntentDraft"]
    amount_schema = intent_schema["properties"]["amount"]
    assert amount_schema == {"$ref": "#/$defs/DynamicGoalScalarMentionSlot"}

    scalar_slot = definitions["DynamicGoalScalarMentionSlot"]
    assert scalar_slot["additionalProperties"] is False
    value_variants = scalar_slot["properties"]["value"]["anyOf"]
    assert value_variants[0] == {"$ref": "#/$defs/StrictScalar"}
    assert value_variants[1] == {"type": "null"}
    scalar_variants = definitions["StrictScalar"]["anyOf"]
    assert {variant["type"] for variant in scalar_variants} == {
        "string",
        "integer",
        "boolean",
    }
    assert {} not in scalar_variants

    accepted = DynamicGoalScalarMentionSlot(status="GROUNDED", value=30)
    assert accepted.value == 30
    for invalid_value in ({"value": 30}, [30]):
        with pytest.raises(ValidationError):
            DynamicGoalScalarMentionSlot.model_validate(
                {"status": "GROUNDED", "value": invalid_value}
            )


def test_dynamic_goal_grounding_reference_and_scalar_slot_modes_are_closed() -> None:
    reference = DynamicGoalMentionSlot(
        status="GROUNDED",
        ref_type="REGION",
        key="region_a",
    )
    assert reference.key == "region_a"
    assert DynamicGoalMentionSlot(status="UNRESOLVED", ref_type="RESOURCE").key is None
    assert DynamicGoalMentionSlot(status="NOT_SPECIFIED").ref_type is None
    assert DynamicGoalScalarMentionSlot(status="UNRESOLVED").value is None
    assert DynamicGoalScalarMentionSlot(status="NOT_SPECIFIED").value is None

    with pytest.raises(ValidationError):
        DynamicGoalMentionSlot.model_validate(
            {
                "status": "GROUNDED",
                "ref_type": "REGION",
                "key": "region_a",
                "value": 30,
            }
        )
    with pytest.raises(ValidationError):
        DynamicGoalScalarMentionSlot.model_validate(
            {
                "status": "GROUNDED",
                "ref_type": "REGION",
                "key": "region_a",
                "value": 30,
            }
        )


def test_dynamic_goal_grounding_request_uses_json_object_with_explicit_scalar_contract() -> None:
    provider = OpenAICompatibleGenericProvider(_settings())
    body, _size = provider._build_request_body("dynamic_goal_grounding", {})
    prompt = body["messages"][0]["content"]

    assert body["response_format"] == {"type": "json_object"}
    assert "json_schema" not in body["response_format"]
    assert '"value":30' in prompt
    assert "native JSON scalar" in prompt
    assert "never an object or array" in prompt
    assert '"typed_value|null"' not in prompt


def test_dynamic_grounding_wire_normalization_strips_only_provider_owned_fields() -> None:
    def complete(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "status": "RESOLVED",
                                    "candidate_refs": [
                                        {
                                            "ref_type": "NODE",
                                            "key": "central_hospital",
                                            "provenance": "EXACT_USER_MENTION",
                                            "match_semantics": "EXACT_OR_AUTHORED",
                                        }
                                    ],
                                    "intent": {
                                        "intent_kind": "OPERATION",
                                        "target": {
                                            "status": "GROUNDED",
                                            "ref_type": "NODE",
                                            "key": "central_hospital",
                                            "value": None,
                                            "surface": "central hospital",
                                            "provenance": "SEMANTIC_ROLE_EVIDENCE",
                                        },
                                        "amount": {
                                            "status": "GROUNDED",
                                            "value": 30,
                                            "surface": "30",
                                            "provenance": "EXPLICIT_USER_MENTION",
                                            "match_semantics": "EXACT_OR_AUTHORED",
                                            "ref_type": None,
                                            "key": None,
                                        },
                                    },
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    provider = OpenAICompatibleGenericProvider(_settings(), transport=httpx.MockTransport(complete))
    result = provider.ground_dynamic_goal_entities(
        DynamicGoalEntityGroundingRequest(
            goal="move 30 to central hospital",
            public_catalog={"references": [{"ref_type": "NODE", "key": "central_hospital"}]},
        )
    )

    assert result.candidate_refs[0].provenance == "LLM_SUPPLEMENTED"
    assert result.intent is not None
    assert result.intent.target.provenance is None
    assert result.intent.amount.provenance is None
    assert result.intent.amount.value == 30


def test_dynamic_grounding_wire_unknown_extra_still_fails_closed() -> None:
    def complete(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"RESOLVED","candidate_refs":['
                            '{"ref_type":"NODE","key":"central_hospital",'
                            '"unexpected_guess":"central_hospital"}]}'
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    provider = OpenAICompatibleGenericProvider(_settings(), transport=httpx.MockTransport(complete))
    with pytest.raises(GenericProviderError) as caught:
        provider.ground_dynamic_goal_entities(
            DynamicGoalEntityGroundingRequest(
                goal="central hospital",
                public_catalog={"references": [{"ref_type": "NODE", "key": "central_hospital"}]},
            )
        )
    assert caught.value.code == "MODEL_PROVIDER_RESPONSE_INVALID"
    assert any(
        item.get("field_path") == "candidate_refs[0].unexpected_guess"
        for item in caught.value.validation_diagnostics
    )


def test_dynamic_grounding_prompt_does_not_publish_provenance_wire_fields() -> None:
    provider = OpenAICompatibleGenericProvider(_settings())
    body, _size = provider._build_request_body("dynamic_goal_grounding", {})
    prompt = body["messages"][0]["content"]
    assert '"candidate_refs":[{"ref_type"' in prompt
    assert '"provenance":' not in prompt
    assert '"value":30,"surface":null' in prompt
    assert "provenance is backend-owned" in prompt.casefold()


def test_historical_grounding_snapshot_keeps_invalid_amount_shape_redacted() -> None:
    snapshot = goal_provider_response_snapshot(
        "dynamic_goal_grounding",
        {
            "status": "RESOLVED",
            "candidate_refs": [{"ref_type": "RESOURCE", "key": "emergency_fuel"}],
            "intent": {
                "intent_kind": "OPERATION",
                "amount": {"status": "GROUNDED", "value": {"amount": 30}},
            },
        },
        public_catalog={
            "references": [{"ref_type": "RESOURCE", "key": "emergency_fuel"}],
        },
    )

    assert snapshot["intent"]["amount"] == {
        "status": "GROUNDED",
        "value": {"json_type": "object", "value_omitted": True},
    }


def test_dynamic_grounding_snapshot_retains_safe_typed_intent_provenance() -> None:
    grounding = DynamicGoalEntityGrounding(
        candidate_refs=(
            DynamicGoalCandidateReference(ref_type="ACTION", key="move_cargo"),
            DynamicGoalCandidateReference(ref_type="REGION", key="region_a"),
            DynamicGoalCandidateReference(ref_type="REGION", key="region_b"),
            DynamicGoalCandidateReference(ref_type="RESOURCE", key="cargo"),
        ),
        intent=DynamicGoalIntentDraft(
            intent_kind="OPERATION",
            action=DynamicGoalMentionSlot(status="GROUNDED", ref_type="ACTION", key="move_cargo"),
            source=DynamicGoalMentionSlot(status="GROUNDED", ref_type="REGION", key="region_a"),
            target=DynamicGoalMentionSlot(status="GROUNDED", ref_type="REGION", key="region_b"),
            resource=DynamicGoalMentionSlot(status="GROUNDED", ref_type="RESOURCE", key="cargo"),
            amount=DynamicGoalScalarMentionSlot(status="GROUNDED", value=30),
            actor=DynamicGoalMentionSlot(status="NOT_SPECIFIED"),
        ),
    )

    snapshot = goal_provider_response_snapshot(
        "dynamic_goal_grounding",
        grounding,
        public_catalog={
            "references": [
                {"ref_type": "ACTION", "key": "move_cargo"},
                {"ref_type": "REGION", "key": "region_a"},
                {"ref_type": "REGION", "key": "region_b"},
                {"ref_type": "RESOURCE", "key": "cargo"},
            ]
        },
    )

    intent = snapshot["intent"]
    assert isinstance(intent, dict)
    assert intent["intent_kind"] == "OPERATION"
    assert intent["source"] == {
        "status": "GROUNDED",
        "ref_type": "REGION",
        "key": "region_a",
    }
    assert intent["amount"] == {"status": "GROUNDED", "value": 30}


def test_dynamic_goal_prompt_locks_stage_one_explicit_operation() -> None:
    request = DynamicGoalInterpretationRequest(
        goal="move 30 cargo from source to destination",
        ontology={"world": {"actions": [{"key": "move_cargo"}]}},
        grounded_operation=DynamicGoalGroundedOperation(
            action_key="move_cargo",
            target_key="destination",
            binding_constraints=({"role": "source_region", "value": "source"},),
            parameter_constraints={"resource_key": "cargo", "amount": 30},
        ),
    )
    provider = OpenAICompatibleGenericProvider(_settings())

    body, _size = provider._build_request_body("dynamic_goal", request.model_dump(mode="json"))
    payload = json.loads(body["messages"][1]["content"])
    prompt = body["messages"][0]["content"]

    assert payload["grounded_operation"]["action_key"] == "move_cargo"
    assert payload["grounded_operation"]["target_key"] == "destination"
    assert "universal locked, lossless public Stage 1 result" in prompt
    assert "Do not return NEEDS_CLARIFICATION, UNSUPPORTED" in prompt
    assert "actor_key and target_key null are intentional unconstrained fields" in prompt
    assert "no additional requirement" in prompt
    assert "never expand the semantics" in prompt


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
    assert [item.prompt_template_version for item in history] == [
        "dynamic-goal-grounding-v8",
        "dynamic-goal-interpretation-v2",
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
