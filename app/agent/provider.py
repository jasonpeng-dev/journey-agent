"""Validated provider boundary for generic goal selection and planning."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime
from threading import Lock
from time import perf_counter
from typing import Literal, Protocol

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.config import Settings
from app.domain.scenario_v2 import StrictScalar

log = structlog.get_logger(__name__)


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoalSelectionRequest(ProviderModel):
    goal: str
    objective_candidates: tuple[dict[str, object], ...]


class GoalSelection(ProviderModel):
    status: Literal["SELECTED", "NEEDS_CLARIFICATION", "UNSUPPORTED"] = "SELECTED"
    objective_keys: tuple[str, ...] = ()
    clarification_prompt: str | None = None


class PlanningContext(ProviderModel):
    """The entity-once, knowledge-safe Planner input for Checkpoint B.

    The fields intentionally use JSON-shaped mappings.  Scenario authors own
    the vocabulary in the immutable Version, while the provider boundary owns
    only the shape and the hard/soft distinction.  Keeping the values generic
    also lets the same contract serve Starfire, Medical, and future V2
    scenarios without a scenario-specific DTO.
    """

    goal: dict[str, object] = Field(default_factory=dict)
    current_knowledge: dict[str, object] = Field(default_factory=dict)
    relevant_actions: tuple[dict[str, object], ...] = ()
    relevant_actors: tuple[dict[str, object], ...] = ()
    relevant_targets: tuple[dict[str, object], ...] = ()
    previous_execution_context: dict[str, object] = Field(default_factory=dict)
    scenario_planning_hints: dict[str, object] = Field(default_factory=dict)

    def compact_dump(self) -> dict[str, object]:
        """Return the lossless provider projection of this context."""

        payload = self.model_dump(mode="json")
        if not payload.get("previous_execution_context"):
            payload.pop("previous_execution_context", None)
        return payload


class PlannerActorState(ProviderModel):
    """Canonical current Knowledge about one Actor."""

    actor_key: str
    role_key: str
    availability: str
    current_region: str | None
    command_reachability: str
    execution_state: dict[str, object] = Field(default_factory=dict)


class PlannerActionContract(ProviderModel):
    """One authoritative Planner-facing contract for an Action."""

    action_key: str
    executor_requirements: dict[str, object] = Field(default_factory=dict)
    target_contract: dict[str, object] = Field(default_factory=dict)
    locality: dict[str, object] = Field(default_factory=dict)
    parameters: tuple[dict[str, object], ...] = ()
    known_preconditions: tuple[dict[str, object], ...] = ()
    deterministic_effects: tuple[dict[str, object], ...] = ()
    knowledge_semantics: tuple[dict[str, object], ...] = ()


class PlannerTargetBinding(ProviderModel):
    """Sparse target-specific contract differences for one Action/Target."""

    action_key: str
    target_key: str
    requirements: tuple[dict[str, object], ...] = ()
    deterministic_effects: tuple[dict[str, object], ...] = ()


class PlannerKnownWorldSlice(ProviderModel):
    """Knowledge-safe world entities selected for the current dependency closure."""

    nodes: tuple[dict[str, object], ...] = ()
    facts: dict[str, object] = Field(default_factory=dict)
    relations: tuple[dict[str, object], ...] = ()
    resources: dict[str, object] = Field(default_factory=dict)
    resource_knowledge: tuple[dict[str, object], ...] = ()
    unknown_dependencies: tuple[dict[str, object], ...] = ()


class PlannerInput(ProviderModel):
    """Canonical schema shared by INITIAL, REPAIR, and REPLAN."""

    schema_version: Literal[2] = 2
    objective: dict[str, object] = Field(default_factory=dict)
    actors: tuple[PlannerActorState, ...] = ()
    action_contracts: tuple[PlannerActionContract, ...] = ()
    target_bindings: tuple[PlannerTargetBinding, ...] = ()
    known_world: PlannerKnownWorldSlice = Field(default_factory=PlannerKnownWorldSlice)
    execution_context: dict[str, object] = Field(default_factory=dict)


class PlanningActionCandidate(ProviderModel):
    """Deprecated compatibility view of an Actor x Action x Target binding.

    It remains available while old FakeProvider tests and persisted diagnostics
    migrate.  The canonical OpenAI-compatible payload is ``PlanningContext``;
    no provider call is instructed to choose candidate IDs.
    """

    candidate_id: str
    action_key: str
    action_name: str
    actor_key: str
    actor_name: str
    target_key: str
    target_name: str
    target_kind: str = "NODE"
    parameter_domain: tuple[dict[str, object], ...] = ()
    public_effects: tuple[dict[str, object], ...] = ()
    objective_relevance: tuple[dict[str, object], ...] = ()
    currently_executable: bool
    known_blockers: tuple[dict[str, object], ...] = ()
    public_prerequisites: tuple[dict[str, object], ...] = ()
    authority: dict[str, object] = Field(default_factory=dict)
    action_behavior: str = "RULE"
    action_locality: str = "NONE"


class PlanStepProposal(ProviderModel):
    purpose: str = ""
    action_key: str | None = None
    actor_key: str | None = None
    target_key: str | None = None
    parameters: dict[str, StrictScalar] = Field(default_factory=dict)
    short_actor_reason: str | None = None
    candidate_id: str | None = None

    @model_validator(mode="after")
    def validate_binding_shape(self) -> PlanStepProposal:
        """Accept direct V1 bindings or the temporary legacy candidate shape."""

        direct = all(
            value is not None for value in (self.action_key, self.actor_key, self.target_key)
        )
        if self.candidate_id is None and not direct:
            raise ValueError("A Plan step needs Action/Actor/Target keys")
        return self


class PlanRequest(ProviderModel):
    call_type: Literal["INITIAL_PLAN", "REPLAN", "REPAIR"]
    goal: str = ""
    objective_keys: tuple[str, ...] = ()
    objective_scope: tuple[dict[str, object], ...] = ()
    replan_reason: str | None = None
    known_world: dict[str, object] = Field(default_factory=dict)
    actors: tuple[dict[str, object], ...] = ()
    planning_metadata: dict[str, object] = Field(default_factory=dict)
    planning_action_catalog: tuple[PlanningActionCandidate, ...] = ()
    planning_context: PlanningContext | None = None
    planner_input: PlannerInput | None = None
    repair_attempt: int = 0
    repair_diagnostics: tuple[dict[str, object], ...] = ()

    def provider_payload(self) -> dict[str, object]:
        """Return only the canonical V1 provider input.

        ``planning_action_catalog`` and the other legacy projections stay on
        the in-process request object for compatibility, but are deliberately
        omitted from this payload whenever a PlanningContext is available.
        """

        if self.planner_input is not None:
            payload: dict[str, object] = {
                "call_type": self.call_type,
                "planner_input": self.planner_input.model_dump(mode="json"),
            }
            if self.replan_reason:
                payload["replan_reason"] = self.replan_reason
            if self.call_type == "REPAIR" or self.repair_attempt != 0:
                payload["repair_attempt"] = self.repair_attempt
            if self.repair_diagnostics:
                payload["repair_diagnostics"] = list(self.repair_diagnostics)
            return payload
        if self.planning_context is not None:
            payload = {
                "call_type": self.call_type,
                "goal": self.goal,
                "planning_context": self.planning_context.compact_dump(),
            }
            if self.replan_reason:
                payload["replan_reason"] = self.replan_reason
            if self.call_type == "REPAIR" or self.repair_attempt != 0:
                payload["repair_attempt"] = self.repair_attempt
            if self.repair_diagnostics:
                payload["repair_diagnostics"] = list(self.repair_diagnostics)
            return payload
        return self.model_dump(mode="json")


class PlanProposal(ProviderModel):
    plan_summary: str = ""
    steps: tuple[PlanStepProposal, ...]


class ProviderCallMetadata(ProviderModel):
    call_type: str
    latency_ms: int
    provider: str | None = None
    model: str | None = None
    thinking_mode: str | None = None
    reasoning_effort: str | None = None
    configured_output_token_limit: int | None = None
    http_timeout_seconds: float | None = None
    total_deadline_seconds: float | None = None
    started_at: str | None = None
    finished_at: str | None = None
    request_started_at: str | None = None
    request_send_completed_at: str | None = None
    response_headers_received_at: str | None = None
    first_response_byte_at: str | None = None
    response_bytes_received: int | None = None
    request_cancelled_at: str | None = None
    timeout_subtype: str | None = None
    wall_clock_latency_ms: int | None = None
    outcome: str | None = None
    error_category: str | None = None
    context_bytes: int | None = None
    request_size_bytes: int | None = None
    prompt_tokens: int | None = None
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    final_content_bytes: int | None = None
    finish_reason: str | None = None


class GenericModelProvider(Protocol):
    @property
    def model_name(self) -> str: ...
    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection: ...
    def propose_plan(self, request: PlanRequest) -> PlanProposal: ...


class GenericProviderError(ValueError):
    """Secret-safe provider failure surfaced at the application boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ProviderTotalTimeout(TimeoutError):
    """The provider call exceeded the configured end-to-end deadline."""


class _ProviderPhaseTelemetry:
    """Thread-safe phase timestamps for one synchronous HTTP request.

    HTTPX exposes response hooks after transport headers are available, and
    the response stream yields body chunks before the non-streaming client
    call returns.  It does not expose a reliable transport-level
    "request bytes sent" callback, so that field intentionally remains null.
    """

    def __init__(self, *, request_started_at: str) -> None:
        self._lock = Lock()
        self._request_started_at = request_started_at
        self._request_send_completed_at: str | None = None
        self._response_headers_received_at: str | None = None
        self._first_response_byte_at: str | None = None
        self._response_bytes_received: int | None = None
        self._request_cancelled_at: str | None = None
        self._timeout_subtype: str | None = None

    def mark_response_headers_received(self) -> None:
        with self._lock:
            self._response_headers_received_at = datetime.now(UTC).isoformat()
            self._response_bytes_received = 0

    def mark_response_chunk(self, byte_count: int) -> None:
        if byte_count <= 0:
            return
        with self._lock:
            if self._first_response_byte_at is None:
                self._first_response_byte_at = datetime.now(UTC).isoformat()
            self._response_bytes_received = (self._response_bytes_received or 0) + byte_count

    def mark_timeout(self, timeout_subtype: str) -> None:
        with self._lock:
            self._request_cancelled_at = datetime.now(UTC).isoformat()
            self._timeout_subtype = timeout_subtype

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "request_started_at": self._request_started_at,
                "request_send_completed_at": self._request_send_completed_at,
                "response_headers_received_at": self._response_headers_received_at,
                "first_response_byte_at": self._first_response_byte_at,
                "response_bytes_received": self._response_bytes_received,
                "request_cancelled_at": self._request_cancelled_at,
                "timeout_subtype": self._timeout_subtype,
            }


class _TelemetryResponseStream(httpx.SyncByteStream):
    """Count body bytes delivered by HTTPX without changing the stream."""

    def __init__(self, stream: httpx.SyncByteStream, telemetry: _ProviderPhaseTelemetry) -> None:
        self._stream = stream
        self._telemetry = telemetry

    def __iter__(self) -> Iterator[bytes]:
        try:
            for chunk in self._stream:
                self._telemetry.mark_response_chunk(len(chunk))
                yield chunk
        except httpx.TimeoutException as exc:
            self._telemetry.mark_timeout(type(exc).__name__)
            raise

    def close(self) -> None:
        self._stream.close()


class OpenAICompatibleGenericProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if settings.model_api_key is None or not settings.model_api_key.get_secret_value().strip():
            raise GenericProviderError(
                "MODEL_PROVIDER_CONFIGURATION_INVALID",
                "MODEL_API_KEY is required for the configured model provider",
            )
        self._base_url = settings.model_base_url.rstrip("/")
        self._api_key = settings.model_api_key.get_secret_value()
        self._model_name = settings.model_name
        self._timeout = settings.model_timeout_seconds
        self._total_timeout = settings.model_total_timeout_seconds
        self._max_output_tokens = settings.model_max_output_tokens
        self._thinking_mode = "enabled"
        self._reasoning_effort = "low"
        self._transport = transport
        self._last_call_metadata: ProviderCallMetadata | None = None

    @property
    def provider_name(self) -> str:
        return "openai_compatible"

    @property
    def thinking_mode(self) -> str:
        return self._thinking_mode

    @property
    def reasoning_effort(self) -> str:
        return self._reasoning_effort

    @property
    def configured_output_token_limit(self) -> int:
        return self._max_output_tokens

    @property
    def http_timeout_seconds(self) -> float:
        return self._timeout

    @property
    def total_deadline_seconds(self) -> float:
        return self._total_timeout

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def last_call_metadata(self) -> ProviderCallMetadata | None:
        return self._last_call_metadata

    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection:
        try:
            return GoalSelection.model_validate(
                self._invoke("goal_selection", request.model_dump())
            )
        except ValidationError as exc:
            raise GenericProviderError(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "The model provider returned an invalid Goal selection",
            ) from exc

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        try:
            return PlanProposal.model_validate(
                self._invoke(request.call_type.lower(), request.provider_payload())
            )
        except ValidationError as exc:
            raise GenericProviderError(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "The model provider returned an invalid Plan proposal",
            ) from exc

    def _build_request_body(
        self, purpose: str, payload: dict[str, object]
    ) -> tuple[dict[str, object], int]:
        response_contract = (
            '{"status":"SELECTED|NEEDS_CLARIFICATION|UNSUPPORTED",'
            '"objective_keys":["zero_or_more_candidate_keys"],'
            '"clarification_prompt":null}'
            if purpose == "goal_selection"
            else (
                '{"plan_summary":"short summary",'
                '"steps":[{"purpose":"goal-directed step",'
                '"action_key":"existing_action_key",'
                '"actor_key":"existing_actor_key",'
                '"target_key":"existing_target_key",'
                '"parameters":{},"short_actor_reason":"short reason"}]}'
            )
        )
        if purpose == "repair":
            planning_prompt = (
                "You are repairing a rejected plan for the same frozen ObjectiveScope. "
                "Return one complete corrected plan for that exact scope. The steps array "
                "MUST contain at least one step; never return an empty steps array. Use the "
                "validator diagnostics in the user payload to fix the rejected parts, and "
                "keep valid parts whenever possible. Every step must directly advance the "
                "current Objective, satisfy a public prerequisite, obtain Knowledge needed "
                "for that Objective, or be an explicitly necessary supporting action. Do not "
                "add speculative or preventive corrective actions solely to guard against "
                "unobserved or uninferred problems. Every step needs a concrete purpose "
                "supported by the current Knowledge and task state. Repair, clearing, "
                "recovery, and remediation actions must address a currently known failure, "
                "blockage, unmet prerequisite, or other concrete problem. Do not "
                "expand the ObjectiveScope, add downstream or sibling Objectives, or continue "
                "with broader work after the current Objective can be completed. Future steps "
                "may be currently locked or unavailable when earlier steps establish their "
                "prerequisites. Choose the Action, Actor, Target, parameters, and ordering "
                "yourself, and return the full corrected plan."
            )
        elif purpose in {"initial_plan", "replan"}:
            planning_prompt = (
                "Produce one complete plan for exactly the frozen ObjectiveScope in the "
                "user payload. The steps array MUST contain at least one step; never "
                "return steps=[]. Every step must directly advance the current Objective, "
                "satisfy a public prerequisite, obtain Knowledge needed for completing "
                "the Objective, or be an explicitly necessary supporting action. Do not "
                "add speculative or preventive corrective actions solely to guard against "
                "unobserved or uninferred problems. Every step needs a concrete purpose "
                "supported by the current Knowledge and task state. Repair, clearing, "
                "recovery, and remediation actions must address a currently known failure, "
                "blockage, unmet prerequisite, or other concrete problem. Do not "
                "expand the ObjectiveScope or include downstream, sibling, broader, or "
                "unrelated verification work. Once the current Objective can be completed, "
                "stop planning instead of adding more work. Future steps may be currently "
                "locked or unavailable when earlier steps are expected to establish their "
                "prerequisites. Choose the Action, Actor, Target, parameters, and ordering "
                "yourself."
            )
        else:
            planning_prompt = (
                "Produce one coherent, ordered, complete multi-step plan toward "
                "the frozen ObjectiveScope. You must choose action_key, actor_key, "
                "target_key, parameters, and order yourself. Future steps may be "
                "currently locked or unavailable when earlier steps are expected "
                "to establish their prerequisites."
            )
        generic_guidance = (
            "Static allowed_action_keys describe capability/role permission, "
            "not current executability. Treat planner_constraints and "
            "planner_effects as the generic Action contract. Resolve "
            "KNOWN_BLOCKED conditions before execution; UNKNOWN is not false, "
            "zero, or unavailable. Do not consume or transport resources whose "
            "availability is unknown. "
            if purpose in {"initial_plan", "replan", "repair"}
            else ""
        )
        request_body: dict[str, object] = {
            "model": self._model_name,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "low",
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Return only valid JSON for generic {purpose}. "
                        f"Use exactly this response shape: {response_contract}. "
                        "Select only keys supplied in the user payload; never invent keys. "
                        "For planning, use only entities supplied in planner_input. "
                        f"{planning_prompt} {generic_guidance}"
                        "Keep purpose and actor reason short, omit chain-of-thought, "
                        "never infer hidden state, and respect repair_diagnostics."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if purpose in {"initial_plan", "replan", "repair"}:
            request_body["max_tokens"] = self._max_output_tokens
        request_size_bytes = len(
            json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        return request_body, request_size_bytes

    def estimate_request_size_bytes(self, purpose: str, payload: dict[str, object]) -> int:
        """Return the exact compact JSON size of the request body, without I/O."""

        _request_body, request_size_bytes = self._build_request_body(purpose, payload)
        return request_size_bytes

    def _post_with_total_deadline(
        self,
        *,
        url: str,
        headers: dict[str, str],
        request_body: dict[str, object],
        telemetry: _ProviderPhaseTelemetry,
    ) -> httpx.Response:
        """Bound the complete synchronous HTTP call independently of HTTPX phases."""

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="journey-provider")
        future = executor.submit(
            self._post,
            url=url,
            headers=headers,
            request_body=request_body,
            telemetry=telemetry,
        )
        try:
            return future.result(timeout=self._total_timeout)
        except FutureTimeout as exc:
            future.cancel()
            telemetry.mark_timeout("PROVIDER_TOTAL_DEADLINE")
            raise ProviderTotalTimeout from exc
        finally:
            # A late upstream result must never keep the planning lifecycle
            # waiting or get a chance to mutate persistence.  The worker is
            # deliberately detached after the caller has crossed the total
            # deadline; its result is ignored.
            executor.shutdown(wait=False, cancel_futures=True)

    def _post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        request_body: dict[str, object],
        telemetry: _ProviderPhaseTelemetry,
    ) -> httpx.Response:
        def response_hook(response: httpx.Response) -> None:
            telemetry.mark_response_headers_received()
            if response.is_stream_consumed:
                telemetry.mark_response_chunk(len(response.content))
                return
            if not isinstance(response.stream, httpx.SyncByteStream):
                return
            telemetry_stream = _TelemetryResponseStream(response.stream, telemetry)
            response.stream = telemetry_stream

        try:
            with httpx.Client(
                timeout=httpx.Timeout(self._timeout),
                transport=self._transport,
                event_hooks={"response": [response_hook]},
            ) as client:
                return client.post(url, headers=headers, json=request_body)
        except httpx.TimeoutException as exc:
            telemetry.mark_timeout(type(exc).__name__)
            raise

    def _invoke(self, purpose: str, payload: dict[str, object]) -> object:
        request_body, request_size_bytes = self._build_request_body(purpose, payload)
        started_at = datetime.now(UTC)
        started = perf_counter()
        telemetry = _ProviderPhaseTelemetry(request_started_at=started_at.isoformat())
        try:
            response = self._post_with_total_deadline(
                url=f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                request_body=request_body,
                telemetry=telemetry,
            )
            response.raise_for_status()
        except ProviderTotalTimeout as exc:
            latency_ms = round((perf_counter() - started) * 1000)
            self._set_failure_metadata(
                purpose=purpose,
                started_at=started_at,
                latency_ms=latency_ms,
                context_bytes=_planning_context_bytes(payload),
                request_size_bytes=request_size_bytes,
                outcome="TIMEOUT",
                error_category="PROVIDER_TOTAL_DEADLINE",
                telemetry=telemetry,
            )
            _log_provider_failure(
                purpose=purpose,
                model=self._model_name,
                request_size_bytes=request_size_bytes,
                error=exc,
                response=None,
                latency_ms=latency_ms,
                http_timeout_seconds=self._timeout,
                total_deadline_seconds=self._total_timeout,
            )
            raise GenericProviderError(
                "MODEL_PROVIDER_TIMEOUT", "The model provider request timed out"
            ) from exc
        except httpx.TimeoutException as exc:
            latency_ms = round((perf_counter() - started) * 1000)
            self._set_failure_metadata(
                purpose=purpose,
                started_at=started_at,
                latency_ms=latency_ms,
                context_bytes=_planning_context_bytes(payload),
                request_size_bytes=request_size_bytes,
                outcome="TIMEOUT",
                error_category=type(exc).__name__,
                telemetry=telemetry,
            )
            _log_provider_failure(
                purpose=purpose,
                model=self._model_name,
                request_size_bytes=request_size_bytes,
                error=exc,
                response=None,
                latency_ms=latency_ms,
                http_timeout_seconds=self._timeout,
                total_deadline_seconds=self._total_timeout,
            )
            raise GenericProviderError(
                "MODEL_PROVIDER_TIMEOUT", "The model provider request timed out"
            ) from exc
        except httpx.HTTPError as exc:
            latency_ms = round((perf_counter() - started) * 1000)
            self._set_failure_metadata(
                purpose=purpose,
                started_at=started_at,
                latency_ms=latency_ms,
                context_bytes=_planning_context_bytes(payload),
                request_size_bytes=request_size_bytes,
                outcome="ERROR",
                error_category=type(exc).__name__,
                telemetry=telemetry,
            )
            _log_provider_failure(
                purpose=purpose,
                model=self._model_name,
                request_size_bytes=request_size_bytes,
                error=exc,
                response=getattr(exc, "response", None),
                latency_ms=latency_ms,
                http_timeout_seconds=self._timeout,
                total_deadline_seconds=self._total_timeout,
            )
            raise GenericProviderError(
                "MODEL_PROVIDER_HTTP_ERROR", "The model provider request failed"
            ) from exc
        try:
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            latency_ms = round((perf_counter() - started) * 1000)
            self._set_failure_metadata(
                purpose=purpose,
                started_at=started_at,
                latency_ms=latency_ms,
                context_bytes=_planning_context_bytes(payload),
                request_size_bytes=request_size_bytes,
                outcome="ERROR",
                error_category=type(exc).__name__,
                telemetry=telemetry,
            )
            raise GenericProviderError(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "The model provider returned malformed JSON",
            ) from exc
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        finish_reason = (
            choice.get("finish_reason")
            if isinstance(choice, dict) and isinstance(choice.get("finish_reason"), str)
            else None
        )
        self._last_call_metadata = ProviderCallMetadata(
            call_type=purpose.upper(),
            latency_ms=round((perf_counter() - started) * 1000),
            provider=self.provider_name,
            model=self._model_name,
            thinking_mode=self._thinking_mode,
            reasoning_effort=self._reasoning_effort,
            configured_output_token_limit=(
                self._max_output_tokens if purpose in {"initial_plan", "replan", "repair"} else None
            ),
            http_timeout_seconds=self._timeout,
            total_deadline_seconds=self._total_timeout,
            started_at=started_at.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            **telemetry.snapshot(),
            wall_clock_latency_ms=round((perf_counter() - started) * 1000),
            outcome="SUCCESS",
            context_bytes=_planning_context_bytes(payload),
            request_size_bytes=request_size_bytes,
            prompt_tokens=_optional_int(usage, "prompt_tokens"),
            prompt_cache_hit_tokens=_optional_int(usage, "prompt_cache_hit_tokens"),
            prompt_cache_miss_tokens=_optional_int(usage, "prompt_cache_miss_tokens"),
            completion_tokens=_optional_int(usage, "completion_tokens"),
            reasoning_tokens=_optional_nested_int(
                usage, "completion_tokens_details", "reasoning_tokens"
            ),
            total_tokens=_optional_int(usage, "total_tokens"),
            final_content_bytes=(
                len(content.encode("utf-8")) if isinstance(content, str) else None
            ),
            finish_reason=finish_reason,
        )
        return parsed

    def _set_failure_metadata(
        self,
        *,
        purpose: str,
        started_at: datetime,
        latency_ms: int,
        context_bytes: int | None,
        request_size_bytes: int,
        outcome: str,
        error_category: str,
        telemetry: _ProviderPhaseTelemetry,
    ) -> None:
        self._last_call_metadata = ProviderCallMetadata(
            call_type=purpose.upper(),
            latency_ms=latency_ms,
            provider=self.provider_name,
            model=self._model_name,
            thinking_mode=self._thinking_mode,
            reasoning_effort=self._reasoning_effort,
            configured_output_token_limit=(
                self._max_output_tokens if purpose in {"initial_plan", "replan", "repair"} else None
            ),
            http_timeout_seconds=self._timeout,
            total_deadline_seconds=self._total_timeout,
            started_at=started_at.isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            **telemetry.snapshot(),
            wall_clock_latency_ms=latency_ms,
            outcome=outcome,
            error_category=error_category,
            context_bytes=context_bytes,
            request_size_bytes=request_size_bytes,
        )


def build_generic_provider(settings: Settings) -> GenericModelProvider | None:
    if settings.model_provider == "mock":
        return None
    return OpenAICompatibleGenericProvider(settings)


def provider_call_metadata(provider: GenericModelProvider) -> dict[str, object]:
    metadata = getattr(provider, "last_call_metadata", None)
    return metadata.model_dump(mode="json") if isinstance(metadata, ProviderCallMetadata) else {}


def provider_call_start_metadata(
    provider: GenericModelProvider,
    request: PlanRequest,
) -> dict[str, object]:
    """Build safe, pre-request metadata for the persistent call audit."""

    payload = request.provider_payload()
    estimator = getattr(provider, "estimate_request_size_bytes", None)
    if callable(estimator):
        request_size_bytes = estimator(request.call_type.lower(), payload)
    else:
        request_size_bytes = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    return {
        "provider": getattr(provider, "provider_name", type(provider).__name__),
        "model": getattr(provider, "model_name", None),
        "call_type": request.call_type,
        "context_bytes": _planning_context_bytes(payload),
        "request_size_bytes": request_size_bytes,
        "thinking_mode": getattr(provider, "thinking_mode", None),
        "reasoning_effort": getattr(provider, "reasoning_effort", None),
        "configured_output_token_limit": getattr(provider, "configured_output_token_limit", None),
        "http_timeout_seconds": getattr(provider, "http_timeout_seconds", None),
        "total_deadline_seconds": getattr(provider, "total_deadline_seconds", None),
    }


def _planning_context_bytes(payload: dict[str, object]) -> int | None:
    context_key = "planner_input" if "planner_input" in payload else "planning_context"
    if context_key not in payload:
        return None
    return len(
        json.dumps(
            payload.get(context_key, {}), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    )


def _optional_int(value: object, key: str) -> int | None:
    if not isinstance(value, dict):
        return None
    item = value.get(key)
    return item if isinstance(item, int) and not isinstance(item, bool) else None


def _optional_nested_int(value: object, outer_key: str, inner_key: str) -> int | None:
    if not isinstance(value, dict):
        return None
    return _optional_int(value.get(outer_key), inner_key)


def _log_provider_failure(
    *,
    purpose: str,
    model: str,
    request_size_bytes: int,
    error: Exception,
    response: httpx.Response | None,
    latency_ms: int,
    http_timeout_seconds: float,
    total_deadline_seconds: float,
) -> None:
    """Record bounded, credential-safe upstream diagnostics for Developer logs."""

    log.error(
        "model_provider_upstream_error",
        purpose=purpose,
        model=model,
        error_type=type(error).__name__,
        upstream_status_code=response.status_code if response is not None else None,
        request_size_bytes=request_size_bytes,
        latency_ms=latency_ms,
        http_timeout_seconds=http_timeout_seconds,
        total_deadline_seconds=total_deadline_seconds,
        provider_request_id=_provider_request_id(response),
        response_body_summary=_response_body_summary(response),
    )


def _provider_request_id(response: httpx.Response | None) -> str | None:
    if response is None:
        return None
    for header_name in ("x-request-id", "x-deepseek-request-id", "request-id"):
        value = response.headers.get(header_name)
        if value:
            return _safe_text(value, limit=160)
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        for key in ("request_id", "requestId", "id"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return _safe_text(value, limit=160)
        error = body.get("error")
        if isinstance(error, dict):
            for key in ("request_id", "requestId", "id"):
                value = error.get(key)
                if isinstance(value, str) and value:
                    return _safe_text(value, limit=160)
    return None


def _response_body_summary(response: httpx.Response | None) -> dict[str, object]:
    if response is None:
        return {"available": False}

    raw_body = response.content
    summary: dict[str, object] = {
        "available": True,
        "bytes": len(raw_body),
        "sha256": hashlib.sha256(raw_body).hexdigest(),
    }
    try:
        body = response.json()
    except ValueError:
        summary["format"] = "text"
        summary["content_type"] = response.headers.get("content-type")
        return summary

    summary["format"] = "json"
    if isinstance(body, dict):
        summary["top_level_keys"] = sorted(str(key) for key in body)[:20]
        error = body.get("error")
        if isinstance(error, dict):
            summary["error"] = {
                key: _safe_text(value, limit=240)
                for key, value in error.items()
                if key in {"type", "code", "message"} and isinstance(value, (str, int, float, bool))
            }
        else:
            summary["fields"] = {
                key: _safe_text(value, limit=240)
                for key, value in body.items()
                if key in {"type", "code", "message", "detail"}
                and isinstance(value, (str, int, float, bool))
            }
    else:
        summary["value_type"] = type(body).__name__
    return summary


_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]+\b"),
)


def _safe_text(value: object, *, limit: int) -> str:
    text = str(value)
    for pattern in _SENSITIVE_TEXT_PATTERNS:
        text = pattern.sub("<redacted>", text)
    return text[:limit]


PlanningContextV1 = PlanningContext


__all__ = [
    "GenericModelProvider",
    "GenericProviderError",
    "GoalSelection",
    "GoalSelectionRequest",
    "OpenAICompatibleGenericProvider",
    "PlanProposal",
    "PlanRequest",
    "PlanStepProposal",
    "PlannerActionContract",
    "PlannerActorState",
    "PlannerInput",
    "PlannerKnownWorldSlice",
    "PlannerTargetBinding",
    "PlanningActionCandidate",
    "PlanningContext",
    "PlanningContextV1",
    "ProviderCallMetadata",
    "ProviderTotalTimeout",
    "build_generic_provider",
    "provider_call_metadata",
    "provider_call_start_metadata",
]
