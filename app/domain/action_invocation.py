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
    ActionOperationBindingSource,
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


def canonical_action_invocation_contract(action: ActionDefinitionV2) -> dict[str, object]:
    """Compile the canonical typed invocation contract for one Action.

    Resolver stages consume smaller views of this one contract.  In
    particular, scalar storage types stay distinct from semantic identity
    types, and relation-source semantics stay attached to their backing slot.
    """

    if action.target_semantic_reference_type is not None:
        target_semantic_type = action.target_semantic_reference_type.value
    elif action.target_kind.value == "NODE" and len(action.target_node_type_keys) == 1:
        node_type = action.target_node_type_keys[0].casefold()
        target_semantic_type = (
            "REGION" if node_type == "region" else "FACILITY" if node_type == "facility" else "NODE"
        )
    else:
        target_semantic_type = action.target_kind.value
    relation_source_slot = action.relation_source_slot()
    actor = {
        "slot_key": "actor",
        "storage_channel": "actor",
        "logical_role": "actor",
        "scalar_value_type": None,
        "semantic_reference_type": "ACTOR",
        "expected_type": "ACTOR",
        "goal_required": False,
        "runtime_required": True,
        "cardinality": "ONE",
        **(
            {"required_role_key": action.required_actor_role_key}
            if action.required_actor_role_key is not None
            else {}
        ),
    }
    target = {
        # Compatibility aliases retained for existing public projections.
        "field": "target_key",
        "role": "target",
        "source": "ACTION_TARGET_KEY",
        "value_type": target_semantic_type,
        # Canonical slot semantics.
        "slot_key": "target",
        "storage_channel": "target",
        "logical_role": "target",
        "scalar_value_type": None,
        "semantic_reference_type": target_semantic_type,
        "expected_type": target_semantic_type,
        "goal_required": False,
        "runtime_required": True,
        "cardinality": "ONE",
        "node_type_keys": list(action.target_node_type_keys),
        "required_interaction_key": action.required_interaction_key,
    }
    bindings = []
    for binding in action.operation_bindings:
        identity = ("binding", binding.role)
        logical_role = (
            "source"
            if identity == relation_source_slot
            or binding.source == ActionOperationBindingSource.EXECUTION_START_ACTOR_REGION
            else "binding"
        )
        bindings.append(
            {
                # Compatibility aliases retained for existing consumers.
                "role": binding.role,
                "value_type": binding.value_type.value,
                "source": binding.source.value,
                "description": binding.description,
                # Canonical slot semantics.
                "slot_key": binding.role,
                "storage_channel": "binding",
                "logical_role": logical_role,
                "scalar_value_type": None,
                "semantic_reference_type": binding.value_type.value,
                "expected_type": binding.value_type.value,
                "goal_required": False,
                "runtime_required": False,
                "cardinality": "ONE",
            }
        )
    parameters = []
    for parameter in action.parameters:
        semantic_type = (
            parameter.semantic_reference_type.value
            if parameter.semantic_reference_type is not None
            else None
        )
        identity = ("parameter", parameter.key)
        parameters.append(
            {
                "slot_key": parameter.key,
                "name": parameter.name,
                "storage_channel": "parameter",
                "logical_role": "source" if identity == relation_source_slot else "parameter",
                "scalar_value_type": parameter.value_type.value,
                "semantic_reference_type": semantic_type,
                "expected_type": semantic_type or parameter.value_type.value,
                "goal_required": False,
                "runtime_required": parameter.required,
                "cardinality": "ONE",
                "minimum": parameter.minimum,
                "maximum": parameter.maximum,
                "allowed_values": list(parameter.allowed_values),
            }
        )
    slots = [actor, target, *bindings, *parameters]
    relation_semantics = None
    if action.source_relation_type_key is not None and relation_source_slot is not None:
        relation_semantics = {
            "source_relation_type_key": action.source_relation_type_key,
            "source_storage_channel": relation_source_slot[0],
            "source_slot_key": relation_source_slot[1],
            "target_slot_key": "target",
            "direction": "SOURCE_TO_TARGET",
        }
    return {
        "action_key": action.key,
        "actor": actor,
        "target": target,
        "bindings": bindings,
        "parameters": parameters,
        "slots": slots,
        "relation_semantics": relation_semantics,
        "target_actor_roles": [
            {
                "target_key": item.target_key,
                "required_actor_role_key": item.required_actor_role_key,
            }
            for item in action.target_actor_roles
        ],
    }


def action_operation_binding_contract(action: ActionDefinitionV2) -> dict[str, object]:
    """Return the compact legacy/public view of the canonical contract."""

    contract = canonical_action_invocation_contract(action)
    target = cast(dict[str, object], contract["target"])
    bindings = cast(list[dict[str, object]], contract["bindings"])
    return {
        "bindings": [
            {
                "role": item["slot_key"],
                "value_type": item["semantic_reference_type"],
                "source": item["source"],
                "description": item["description"],
            }
            for item in bindings
        ],
        "target": {
            "field": "target_key",
            "role": "target",
            "source": "ACTION_TARGET_KEY",
            "value_type": target["semantic_reference_type"],
            "node_type_keys": target["node_type_keys"],
        },
    }


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
    "action_operation_binding_contract",
    "canonical_action_invocation",
    "canonical_action_parameters",
    "transport_bindings_from_outcome",
]
