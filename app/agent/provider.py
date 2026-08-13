"""Validated provider boundary for generic goal selection and planning."""

from __future__ import annotations

import json
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.domain.scenario_v2 import StrictScalar


class ProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoalSelectionRequest(ProviderModel):
    goal: str
    objective_candidates: tuple[dict[str, object], ...]


class GoalSelection(ProviderModel):
    objective_keys: tuple[str, ...] = Field(min_length=1)


class PlanStepProposal(ProviderModel):
    action_key: str
    target_key: str
    actor_key: str
    parameters: dict[str, StrictScalar] = Field(default_factory=dict)


class PlanRequest(ProviderModel):
    goal: str
    objective_keys: tuple[str, ...]
    replan_reason: str | None
    known_world: dict[str, object]
    actors: tuple[dict[str, object], ...]
    actions: tuple[dict[str, object], ...]


class PlanProposal(ProviderModel):
    steps: tuple[PlanStepProposal, ...]


class GenericModelProvider(Protocol):
    @property
    def model_name(self) -> str: ...
    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection: ...
    def propose_plan(self, request: PlanRequest) -> PlanProposal: ...


class OpenAICompatibleGenericProvider:
    def __init__(self, settings: Settings) -> None:
        if settings.model_api_key is None:
            raise ValueError("MODEL_API_KEY_REQUIRED")
        self._base_url = settings.model_base_url.rstrip("/")
        self._api_key = settings.model_api_key.get_secret_value()
        self._model_name = settings.model_name
        self._timeout = settings.model_timeout_seconds

    @property
    def model_name(self) -> str:
        return self._model_name

    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection:
        return GoalSelection.model_validate(self._invoke("goal_selection", request.model_dump()))

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        return PlanProposal.model_validate(self._invoke("plan", request.model_dump()))

    def _invoke(self, purpose: str, payload: dict[str, object]) -> object:
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model_name,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": f"Return only valid JSON for generic {purpose}."},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        return json.loads(response.json()["choices"][0]["message"]["content"])


def build_generic_provider(settings: Settings) -> GenericModelProvider | None:
    if settings.model_provider == "mock":
        return None
    return OpenAICompatibleGenericProvider(settings)
