"""Validated provider boundary for generic goal selection and planning."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.config import Settings
from app.domain.scenario_v2 import StrictScalar


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
    parameter_domain: tuple[dict[str, object], ...] = ()
    public_effects: tuple[dict[str, object], ...] = ()
    objective_relevance: tuple[dict[str, object], ...] = ()
    currently_executable: bool
    known_blockers: tuple[dict[str, object], ...] = ()
    public_prerequisites: tuple[dict[str, object], ...] = ()
    authority: dict[str, object] = Field(default_factory=dict)


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
    repair_attempt: int = 0
    repair_diagnostics: tuple[dict[str, object], ...] = ()

    def provider_payload(self) -> dict[str, object]:
        """Return only the canonical V1 provider input.

        ``planning_action_catalog`` and the other legacy projections stay on
        the in-process request object for compatibility, but are deliberately
        omitted from this payload whenever a PlanningContext is available.
        """

        if self.planning_context is not None:
            return {
                "call_type": self.call_type,
                "goal": self.goal,
                "replan_reason": self.replan_reason,
                "planning_context": self.planning_context.model_dump(mode="json"),
                "repair_attempt": self.repair_attempt,
                "repair_diagnostics": list(self.repair_diagnostics),
            }
        return self.model_dump(mode="json")


class PlanProposal(ProviderModel):
    plan_summary: str = ""
    steps: tuple[PlanStepProposal, ...]


class ProviderCallMetadata(ProviderModel):
    call_type: str
    latency_ms: int
    context_bytes: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


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


class OpenAICompatibleGenericProvider:
    def __init__(self, settings: Settings) -> None:
        if settings.model_api_key is None or not settings.model_api_key.get_secret_value().strip():
            raise GenericProviderError(
                "MODEL_PROVIDER_CONFIGURATION_INVALID",
                "MODEL_API_KEY is required for the configured model provider",
            )
        self._base_url = settings.model_base_url.rstrip("/")
        self._api_key = settings.model_api_key.get_secret_value()
        self._model_name = settings.model_name
        self._timeout = settings.model_timeout_seconds
        self._last_call_metadata: ProviderCallMetadata | None = None

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

    def _invoke(self, purpose: str, payload: dict[str, object]) -> object:
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
        started = perf_counter()
        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model_name,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                f"Return only valid JSON for generic {purpose}. "
                                f"Use exactly this response shape: {response_contract}. "
                                "Select only keys supplied in the user payload; never invent keys. "
                                "For planning, use only entities supplied in planning_context. "
                                "Produce one coherent, ordered, complete multi-step plan toward "
                                "the frozen ObjectiveScope. You must choose action_key, actor_key, "
                                "target_key, parameters, and order yourself. Future steps may be "
                                "currently locked or unavailable when earlier steps are expected "
                                "to establish their prerequisites. Keep purpose and actor reason "
                                "short, omit chain-of-thought, never infer hidden state, and "
                                "respect repair_diagnostics."
                            ),
                        },
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GenericProviderError(
                "MODEL_PROVIDER_TIMEOUT", "The model provider request timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise GenericProviderError(
                "MODEL_PROVIDER_HTTP_ERROR", "The model provider request failed"
            ) from exc
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GenericProviderError(
                "MODEL_PROVIDER_RESPONSE_INVALID",
                "The model provider returned malformed JSON",
            ) from exc
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        self._last_call_metadata = ProviderCallMetadata(
            call_type=purpose.upper(),
            latency_ms=round((perf_counter() - started) * 1000),
            context_bytes=(
                len(
                    json.dumps(
                        payload.get("planning_context", {}),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                if "planning_context" in payload
                else None
            ),
            prompt_tokens=_optional_int(usage, "prompt_tokens"),
            completion_tokens=_optional_int(usage, "completion_tokens"),
            total_tokens=_optional_int(usage, "total_tokens"),
        )
        return parsed


def build_generic_provider(settings: Settings) -> GenericModelProvider | None:
    if settings.model_provider == "mock":
        return None
    return OpenAICompatibleGenericProvider(settings)


def provider_call_metadata(provider: GenericModelProvider) -> dict[str, object]:
    metadata = getattr(provider, "last_call_metadata", None)
    return metadata.model_dump(mode="json") if isinstance(metadata, ProviderCallMetadata) else {}


def _optional_int(value: object, key: str) -> int | None:
    if not isinstance(value, dict):
        return None
    item = value.get(key)
    return item if isinstance(item, int) and not isinstance(item, bool) else None


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
    "PlanningActionCandidate",
    "PlanningContext",
    "PlanningContextV1",
    "ProviderCallMetadata",
    "build_generic_provider",
    "provider_call_metadata",
]
