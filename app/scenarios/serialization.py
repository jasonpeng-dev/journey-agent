"""Canonical serialization and hashing for Scenario definition documents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.domain.scenario_v2 import LocalityContractV2, ScenarioDefinitionV2
from app.scenarios.documents import parse_scenario_document

_OPTIONAL_ACTION_FIELDS_WITH_LEGACY_OMISSION = (
    "target_node_type_keys",
    "target_actor_roles",
    "operation_bindings",
)

_OPTIONAL_ACTION_PLANNING_FIELDS_WITH_LEGACY_OMISSION = ("target_terminal_effects",)

_OPTIONAL_ACTION_DEFAULTS_WITH_LEGACY_OMISSION = (
    ("behavior", "RULE"),
    ("locality", "NONE"),
)

_OPTIONAL_INITIALIZATION_FIELDS_WITH_LEGACY_OMISSION = ("resource_initial_states",)

_DEFAULT_LOCALITY_PAYLOAD = LocalityContractV2().model_dump(mode="json")


def canonical_document(document: dict[str, Any]) -> ScenarioDefinitionV2:
    """Validate and normalize ordering without changing Scenario semantics."""

    return ScenarioDefinitionV2.model_validate(canonical_document_payload(document))


def canonical_document_payload(document: dict[str, Any]) -> dict[str, Any]:
    """Return canonical JSON while preserving safe legacy omissions.

    Several v2 Action fields were added with empty defaults. Versions persisted
    before those changes legitimately omit them. Their absence is equivalent to
    an empty tuple, so the serializer keeps the original presence/absence bit
    for persisted snapshot equality. The regular semantic hash remains stable
    across both shapes; the payload hash below is available for historical raw
    hashes.
    """

    return _canonical_v2_payload(parse_scenario_document(document), document)


def _canonical_v2_payload(
    parsed: ScenarioDefinitionV2,
    source_document: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = parsed.model_dump(mode="json")
    world = normalized["world"]
    world["node_types"].sort(key=lambda item: item["key"])
    world["nodes"].sort(key=lambda item: item["key"])
    for node in world["nodes"]:
        node["interaction_keys"].sort()
        node["facts"].sort(key=lambda item: item["key"])
        for fact in node["facts"]:
            if fact["value_type"] == "ENUM":
                fact["allowed_values"].sort(key=_scalar_sort_key)
            fact.get("goal_aliases", []).sort(key=lambda value: str(value).casefold())
            fact.get("goal_examples", []).sort(key=lambda value: str(value).casefold())
            fact.get("goal_target_values", []).sort(key=_scalar_sort_key)
    world["relations"].sort(
        key=lambda item: (
            item["source_node_key"],
            item["relation_type_key"],
            item["target_node_key"],
            item.get("key") or "",
        )
    )
    world["resources"].sort(key=lambda item: item["key"])

    normalized["interactions"].sort(key=lambda item: item["key"])
    actors = normalized["actors"]
    actors["roles"].sort(key=lambda item: item["key"])
    for role in actors["roles"]:
        role["capabilities"].sort()
    actors["actor_profiles"].sort(key=lambda item: item["key"])
    for actor in actors["actor_profiles"]:
        actor["doctrine"].sort(key=lambda item: item["key"])
        actor["allowed_action_keys"].sort()
        _sort_authority(actor["authority_policy"])

    normalized["actions"].sort(key=lambda item: item["key"])
    for action in normalized["actions"]:
        action["parameters"].sort(key=lambda item: item["key"])
        for parameter in action["parameters"]:
            if parameter["value_type"] == "ENUM":
                parameter["allowed_values"].sort(key=_scalar_sort_key)
        action["allowed_actor_capabilities"].sort()
        action["expected_outcomes"].sort(key=lambda item: item["code"])
        _sort_authority(action["authority_policy"])
        planning = action["planning"]
        planning["terminal_effects"].sort(key=lambda item: (item["node_key"], item["fact_key"]))
        planning["target_terminal_effects"].sort(key=lambda item: item["fact_key"])
        planning["supporting_effects"].sort(key=lambda item: (item["node_key"], item["fact_key"]))
        planning["success_outcome_codes"].sort()
        planning["wait_success_outcome_codes"].sort()

    source_actions = source_document.get("actions")
    if isinstance(source_actions, list):
        source_actions_by_key = {
            item.get("key"): item
            for item in source_actions
            if isinstance(item, Mapping) and isinstance(item.get("key"), str)
        }
        for action in normalized["actions"]:
            source_action = source_actions_by_key.get(action["key"])
            if source_action is None:
                continue
            for field in _OPTIONAL_ACTION_FIELDS_WITH_LEGACY_OMISSION:
                if field not in source_action and not action[field]:
                    action.pop(field)
            for field, default in _OPTIONAL_ACTION_DEFAULTS_WITH_LEGACY_OMISSION:
                if field not in source_action and action.get(field) == default:
                    action.pop(field, None)
            source_planning = source_action.get("planning")
            if isinstance(source_planning, Mapping):
                planning = action["planning"]
                for field in _OPTIONAL_ACTION_PLANNING_FIELDS_WITH_LEGACY_OMISSION:
                    if field not in source_planning and not planning[field]:
                        planning.pop(field)

    source_initialization = source_document.get("initialization")
    if isinstance(source_initialization, Mapping):
        initialization = normalized["initialization"]
        for field in _OPTIONAL_INITIALIZATION_FIELDS_WITH_LEGACY_OMISSION:
            if field not in source_initialization and not initialization[field]:
                initialization.pop(field)

    source_metadata = source_document.get("metadata")
    if isinstance(source_metadata, Mapping):
        metadata = normalized["metadata"]
        if (
            "locality" not in source_metadata
            and metadata.get("locality") == _DEFAULT_LOCALITY_PAYLOAD
        ):
            metadata.pop("locality", None)

    source_rules = source_document.get("rules")
    if isinstance(source_rules, list):
        for rule, source_rule in zip(normalized["rules"], source_rules, strict=True):
            if not isinstance(source_rule, Mapping):
                continue
            condition = rule.get("condition")
            source_condition = source_rule.get("condition")
            if isinstance(condition, dict) and isinstance(source_condition, Mapping):
                _preserve_legacy_condition_resource_scopes(condition, source_condition)
            for effect, source_effect in zip(
                rule["effects"], source_rule.get("effects", ()), strict=True
            ):
                if isinstance(source_effect, Mapping):
                    _preserve_legacy_resource_scope(effect, source_effect)

    normalized["rules"].sort(
        key=lambda item: (
            item["action_key"],
            item["phase"],
            -item["priority"],
            item["key"],
        )
    )
    normalized["objectives"].sort(key=lambda item: item["key"])
    for objective in normalized["objectives"]:
        objective["completion_requirements"].sort(key=lambda item: item["key"])
        for requirement in objective["completion_requirements"]:
            requirement.get("accepted_values", []).sort(key=_scalar_sort_key)
        objective["prerequisites"].sort(key=lambda item: item["key"])
        for prerequisite in objective["prerequisites"]:
            prerequisite["requirements"].sort(key=lambda item: item["key"])
            for requirement in prerequisite["requirements"]:
                requirement.get("accepted_values", []).sort(key=_scalar_sort_key)
        objective["subsumes"].sort()
        objective["goal_aliases"].sort(key=lambda value: value.casefold())
        objective["goal_examples"].sort(key=lambda value: value.casefold())
    derived_states = normalized.get("derived_states", [])
    if isinstance(derived_states, list):
        derived_states.sort(key=lambda item: item["key"])
        for state in derived_states:
            state.get("goal_aliases", []).sort(key=lambda value: str(value).casefold())
            state.get("goal_examples", []).sort(key=lambda value: str(value).casefold())
            for dependency in state["dependencies"]:
                dependency.get("accepted_values", []).sort(key=_scalar_sort_key)
                gate = dependency.get("knowledge_gate")
                if isinstance(gate, dict):
                    gate.get("accepted_values", []).sort(key=_scalar_sort_key)
            state["dependencies"].sort(key=_derived_dependency_sort_key)
    normalized["planning"]["recovery_hints"].sort(key=lambda item: item["failure_code"])
    public_knowledge = normalized.get("public_knowledge")
    if isinstance(public_knowledge, dict):
        hints = public_knowledge.get("resource_source_hints")
        if isinstance(hints, list):
            hints.sort(key=lambda item: item["resource_key"])
    public_references = normalized.get("public_references")
    if isinstance(public_references, list):
        public_references.sort(
            key=lambda item: (
                str(item["term"]).casefold(),
                item["ref_type"],
                item["ref_key"],
            )
        )
    # Validate without dumping the model again: another dump would reintroduce
    # fields intentionally omitted by legacy payloads.
    ScenarioDefinitionV2.model_validate(normalized)
    return normalized


def _sort_authority(policy: dict[str, Any]) -> None:
    policy["autonomous_limits"].sort(key=lambda item: item["parameter_key"])
    policy["approval_required_values"].sort(key=lambda item: item["parameter_key"])
    for approval in policy["approval_required_values"]:
        approval["values"].sort(key=_scalar_sort_key)


def _preserve_legacy_condition_resource_scopes(
    condition: dict[str, Any],
    source_condition: Mapping[str, Any],
) -> None:
    _preserve_legacy_resource_scope(condition, source_condition)
    for nested, source_nested in zip(
        condition.get("conditions", ()),
        source_condition.get("conditions", ()),
        strict=True,
    ):
        if isinstance(nested, dict) and isinstance(source_nested, Mapping):
            _preserve_legacy_condition_resource_scopes(nested, source_nested)
    nested = condition.get("condition")
    source_nested = source_condition.get("condition")
    if isinstance(nested, dict) and isinstance(source_nested, Mapping):
        _preserve_legacy_condition_resource_scopes(nested, source_nested)


def _preserve_legacy_resource_scope(
    node: dict[str, Any],
    source_node: Mapping[str, Any],
) -> None:
    if "resource_scope" not in source_node and node.get("resource_scope") is None:
        node.pop("resource_scope", None)


def _scalar_sort_key(value: object) -> tuple[str, str]:
    return type(value).__name__, str(value)


def _optional_scalar_sort_key(value: object) -> tuple[str, str]:
    return ("<none>", "") if value is None else _scalar_sort_key(value)


def _scalar_sequence_sort_key(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(sorted(_scalar_sort_key(item) for item in value))


def _knowledge_gate_sort_key(value: object) -> tuple[object, ...]:
    if not isinstance(value, dict):
        return (_optional_scalar_sort_key(None), _optional_scalar_sort_key(None), ())
    return (
        _optional_scalar_sort_key(value.get("node_key")),
        _optional_scalar_sort_key(value.get("fact_key")),
        _scalar_sequence_sort_key(value.get("accepted_values")),
    )


def _derived_dependency_sort_key(item: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(item.get("kind", "")),
        _optional_scalar_sort_key(item.get("node_key")),
        _optional_scalar_sort_key(item.get("fact_key")),
        _optional_scalar_sort_key(item.get("region_key")),
        _optional_scalar_sort_key(item.get("resource_key")),
        _optional_scalar_sort_key(item.get("minimum")),
        _optional_scalar_sort_key(item.get("derived_key")),
        _scalar_sequence_sort_key(item.get("accepted_values")),
        _knowledge_gate_sort_key(item.get("knowledge_gate")),
    )


def canonical_document_bytes(document: dict[str, Any]) -> bytes:
    normalized = canonical_document(document).model_dump(mode="json")
    return canonical_payload_bytes(normalized)


def canonical_payload_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()


def scenario_content_hash(document: dict[str, Any]) -> str:
    return canonical_payload_hash(canonical_document(document).model_dump(mode="json"))


__all__ = [
    "canonical_document",
    "canonical_document_bytes",
    "canonical_document_payload",
    "canonical_payload_bytes",
    "canonical_payload_hash",
    "scenario_content_hash",
]
