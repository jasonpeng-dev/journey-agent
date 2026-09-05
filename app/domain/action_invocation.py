"""Canonical identity for one concrete Action invocation.

``ActionDefinitionV2`` describes what an Action *is*.  This module describes
one concrete invocation of that Action.  Keeping the two value models
separate lets proposal validation, persisted steps, idempotency checks, and
authoritative operations converge on one semantic representation without
making any of those layers authoritative for Scenario definitions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictStr, model_validator

from app.domain.scenario_v2 import (
    ActionBehavior,
    ActionDefinitionV2,
    ActionParameters,
    normalize_action_parameters,
    transport_resource_entries,
)


class ActionInvocationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActionInvocationBinding(ActionInvocationModel):
    """One Action-defined semantic binding, such as transport source region."""

    role: StrictStr = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_.:/-]{0,99}$",
    )
    value: JsonValue


class ActionInvocation(ActionInvocationModel):
    """Stable, typed identity for one concrete Action input."""

    action_key: StrictStr = Field(min_length=1, max_length=100)
    actor_key: StrictStr = Field(min_length=1, max_length=100)
    target_key: StrictStr = Field(min_length=1, max_length=160)
    bindings: tuple[ActionInvocationBinding, ...] = ()
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def canonicalize_bindings(self) -> ActionInvocation:
        roles = tuple(item.role for item in self.bindings)
        if len(set(roles)) != len(roles):
            raise ValueError("Action invocation binding roles must be unique")
        ordered = tuple(sorted(self.bindings, key=lambda item: item.role))
        if ordered != self.bindings:
            object.__setattr__(self, "bindings", ordered)
        return self

    def canonical_semantics(self) -> dict[str, object]:
        """Return identity fields only; display text never participates."""

        return {
            "action_key": self.action_key,
            "actor_key": self.actor_key,
            "target_key": self.target_key,
            "bindings": [item.model_dump(mode="json") for item in self.bindings],
            "parameters": _canonical_json_value(self.parameters),
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_semantics(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def signature(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


ActionInvocationBindingInput = ActionInvocationBinding | Mapping[str, object]


def canonical_action_parameters(
    action: ActionDefinitionV2,
    parameters: Mapping[str, object],
) -> ActionParameters:
    """Normalize one Action input for semantic comparison and persistence adapters.

    The public/runtime wire shape is unchanged.  Only the canonical identity
    sorts the structured transport cargo by resource key, because cargo item
    order is not part of transport semantics.
    """

    normalized = normalize_action_parameters(action, parameters)
    if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
        entries = sorted(transport_resource_entries(normalized), key=lambda item: item[0])
        return {
            "resources": [
                {"resource_key": resource_key, "amount": amount} for resource_key, amount in entries
            ]
        }
    return cast(ActionParameters, _canonical_json_value(normalized))


def canonical_action_invocation(
    action: ActionDefinitionV2 | None = None,
    *,
    action_key: str | None = None,
    actor_key: str,
    target_key: str,
    parameters: Mapping[str, object],
    bindings: Mapping[str, object] | Iterable[ActionInvocationBindingInput] | None = None,
) -> ActionInvocation:
    """Build the one canonical invocation used across application boundaries."""

    if action is not None:
        resolved_action_key = action.key
        normalized_parameters = canonical_action_parameters(action, parameters)
    else:
        if action_key is None:
            raise ValueError("Canonical Action invocation requires an Action key")
        resolved_action_key = action_key
        normalized_parameters = cast(ActionParameters, _canonical_json_value(parameters))
        if _looks_like_transport_parameters(normalized_parameters):
            entries = sorted(
                transport_resource_entries(normalized_parameters), key=lambda item: item[0]
            )
            normalized_parameters = {
                "resources": [
                    {"resource_key": resource_key, "amount": amount}
                    for resource_key, amount in entries
                ]
            }
    normalized_bindings = _canonical_bindings(bindings)
    return ActionInvocation(
        action_key=resolved_action_key,
        actor_key=actor_key,
        target_key=target_key,
        bindings=normalized_bindings,
        parameters=cast(dict[str, JsonValue], normalized_parameters),
    )


def action_invocation_from_tool_arguments(
    action: ActionDefinitionV2,
    *,
    actor_key: str,
    tool_arguments: Mapping[str, object],
) -> ActionInvocation:
    """Adapt a persisted AgentStep/tool payload into the canonical value."""

    raw_action_key = tool_arguments.get("action_key")
    target_key = tool_arguments.get("target_key")
    parameters = tool_arguments.get("parameters", {})
    if raw_action_key != action.key:
        raise ValueError("AgentStep Action key does not match the Scenario Action")
    if not isinstance(target_key, str) or not target_key:
        raise ValueError("AgentStep tool arguments need a target key")
    if not isinstance(parameters, Mapping):
        raise ValueError("AgentStep tool arguments need a parameter object")
    return canonical_action_invocation(
        action,
        actor_key=actor_key,
        target_key=target_key,
        parameters=parameters,
    )


def action_invocation_from_operation(
    action: ActionDefinitionV2,
    *,
    actor_key: str,
    target_key: str,
    parameters: Mapping[str, object],
    outcome: Mapping[str, object] | None = None,
    actor_start_region: str | None = None,
    bindings: Mapping[str, object] | Iterable[ActionInvocationBindingInput] | None = None,
) -> ActionInvocation:
    """Adapt an authoritative WorldOperation row into the canonical value."""

    return canonical_action_invocation(
        action,
        actor_key=actor_key,
        target_key=target_key,
        parameters=parameters,
        bindings=(
            bindings
            if bindings is not None
            else transport_bindings_from_outcome(
                action,
                outcome,
                actor_start_region=actor_start_region,
            )
        ),
    )


def transport_bindings_from_outcome(
    action: ActionDefinitionV2,
    outcome: Mapping[str, object] | None,
    *,
    actor_start_region: str | None = None,
) -> dict[str, str]:
    """Recover public transport binding roles from authoritative outcome data.

    Resource mutations use their region scope for transport.  Negative deltas
    prove the source region and positive deltas prove the destination region.
    An execution-start actor region, when available, is retained as the
    stronger source observation; the final Actor location is intentionally not
    used to infer the source.
    """

    if action.behavior != ActionBehavior.TRANSPORT_RESOURCE or not isinstance(outcome, Mapping):
        return {}
    raw_mutations = outcome.get("resource_mutations")
    if not isinstance(raw_mutations, (list, tuple)):
        raw_mutations = ()
    source_regions = {
        str(item["scope_node_key"])
        for item in raw_mutations
        if isinstance(item, Mapping)
        and isinstance(item.get("scope_node_key"), str)
        and isinstance(item.get("amount"), int)
        and not isinstance(item.get("amount"), bool)
        and item["amount"] < 0
    }
    destination_regions = {
        str(item["scope_node_key"])
        for item in raw_mutations
        if isinstance(item, Mapping)
        and isinstance(item.get("scope_node_key"), str)
        and isinstance(item.get("amount"), int)
        and not isinstance(item.get("amount"), bool)
        and item["amount"] > 0
    }
    bindings: dict[str, str] = {}
    if actor_start_region is not None:
        bindings["source_region"] = actor_start_region
    elif len(source_regions) == 1:
        bindings["source_region"] = next(iter(source_regions))
    if len(destination_regions) == 1:
        bindings["destination_region"] = next(iter(destination_regions))
    return bindings


def _canonical_bindings(
    bindings: Mapping[str, object] | Iterable[ActionInvocationBindingInput] | None,
) -> tuple[ActionInvocationBinding, ...]:
    if bindings is None:
        return ()
    if isinstance(bindings, Mapping):
        values: Iterable[ActionInvocationBindingInput] = tuple(
            {"role": role, "value": value} for role, value in bindings.items()
        )
    else:
        values = bindings
    return tuple(
        sorted(
            (
                item
                if isinstance(item, ActionInvocationBinding)
                else ActionInvocationBinding.model_validate(item)
                for item in values
            ),
            key=lambda item: item.role,
        )
    )


def _canonical_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Action invocation value is not JSON-compatible: {type(value).__name__}")


def _looks_like_transport_parameters(parameters: Mapping[str, object]) -> bool:
    raw_resources = parameters.get("resources")
    if not isinstance(raw_resources, (list, tuple)) or not raw_resources:
        return False
    return all(
        isinstance(item, Mapping) and set(item) == {"resource_key", "amount"}
        for item in raw_resources
    )


__all__ = [
    "ActionInvocation",
    "ActionInvocationBinding",
    "action_invocation_from_operation",
    "action_invocation_from_tool_arguments",
    "canonical_action_invocation",
    "canonical_action_parameters",
    "transport_bindings_from_outcome",
]
