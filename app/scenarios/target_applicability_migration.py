"""Deterministic migration of the legacy repair-profile target convention.

This module is the only place that knows the historical ``repair_profile``
Fact convention.  Runtime evaluation, authoring, and Knowledge projection use
the generic typed fields on ScenarioDefinition instead.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from app.domain.scenario_v2 import (
    ConditionKind,
    ConditionV2,
    NodeSelectorKind,
    ScenarioDefinitionV2,
)
from app.scenarios.serialization import scenario_content_hash

_LEGACY_FACT_KEY = "repair_profile"


@dataclass(frozen=True, slots=True)
class TargetApplicabilityMigrationReport:
    before_hash: str
    after_hash: str
    applied: bool
    blocked: bool
    facts_removed: int
    selectors_migrated: int
    rules_changed: int
    bindings_created: int
    visibility_migrated: int
    unsafe_conditions: tuple[str, ...] = ()
    residual_references: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    semantic_parity: bool = False
    document: dict[str, Any] | None = None


def migrate_target_applicability_document(
    document: dict[str, Any],
    *,
    apply_to_document: bool = False,
) -> TargetApplicabilityMigrationReport:
    """Plan or apply the legacy target migration on an in-memory document.

    The input is never mutated.  ``apply_to_document=False`` is the dry-run
    mode used by audit tooling; the proposed document is only returned when
    ``apply_to_document=True``.
    """

    source = deepcopy(document)
    before_hash = scenario_content_hash(source)
    proposed = deepcopy(source)
    world = proposed.get("world")
    nodes = world.get("nodes") if isinstance(world, dict) else None
    actions = proposed.get("actions")
    rules = proposed.get("rules")
    if not isinstance(nodes, list) or not isinstance(actions, list) or not isinstance(rules, list):
        return _blocked(
            before_hash,
            "Scenario document is missing world.nodes, actions, or rules",
            apply_to_document=apply_to_document,
        )

    profile_by_value: dict[str, str] = {}
    visibility_by_node: dict[str, str] = {}
    profile_nodes: set[str] = set()
    profile_fact_nodes: set[str] = set()
    errors: list[str] = []
    facts_removed = 0
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("key"), str):
            continue
        node_key = node["key"]
        facts = node.get("facts")
        if not isinstance(facts, list):
            continue
        retained: list[object] = []
        for fact in facts:
            if not isinstance(fact, dict) or fact.get("key") != _LEGACY_FACT_KEY:
                retained.append(fact)
                continue
            facts_removed += 1
            if node_key in profile_fact_nodes:
                errors.append(f"Node {node_key} contains duplicate repair_profile Facts")
            profile_fact_nodes.add(node_key)
            profile_nodes.add(node_key)
            value = fact.get("initial_value")
            if not isinstance(value, str):
                errors.append(f"Node {node_key} repair_profile value is not a string")
                continue
            previous = profile_by_value.get(value)
            if previous is not None and previous != node_key:
                errors.append(
                    f"repair_profile value {value!r} maps to both {previous} and {node_key}"
                )
            profile_by_value[value] = node_key
            visibility = fact.get("initial_visibility", "KNOWN")
            visibility_by_node[node_key] = str(visibility)
        node["facts"] = retained

    action_by_key = {
        item.get("key"): item
        for item in actions
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    action_keys_with_selectors: set[str] = set()
    legacy_rule_expectations: dict[str, tuple[frozenset[str], dict[str, Any] | None]] = {}
    selectors_migrated = 0
    rules_changed = 0
    unsafe_conditions: list[str] = []

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        action_key = rule.get("action_key")
        condition = rule.get("condition")
        selector_values = _selector_values(condition)
        if not selector_values:
            continue
        if not isinstance(action_key, str) or action_key not in action_by_key:
            errors.append(f"Rule {rule.get('key', '<unknown>')} selector has no Action")
            continue
        action = action_by_key[action_key]
        if action.get("target_kind", "NODE") != "NODE":
            errors.append(f"Rule {rule.get('key', '<unknown>')} selector targets a non-NODE Action")
            continue
        mapped_targets: set[str] = set()
        unknown_values: list[str] = []
        for value in selector_values:
            target_key = profile_by_value.get(value)
            if target_key is None:
                unknown_values.append(value)
            else:
                mapped_targets.add(target_key)
        if unknown_values:
            errors.append(
                f"Rule {rule.get('key', '<unknown>')} selector values have no target: "
                + ", ".join(sorted(unknown_values))
            )
            continue
        stripped, unsafe = _strip_positive_selectors(condition)
        if unsafe:
            unsafe_conditions.append(str(rule.get("key", "<unknown>")))
            continue
        if stripped is None:
            rule.pop("condition", None)
        else:
            rule["condition"] = stripped
        existing_targets = {
            value for value in rule.get("applicable_target_keys", []) if isinstance(value, str)
        }
        if existing_targets and existing_targets != mapped_targets:
            errors.append(
                f"Rule {rule.get('key', '<unknown>')} has conflicting applicable targets"
            )
            continue
        rule["applicable_target_keys"] = sorted(mapped_targets)
        action_keys_with_selectors.add(action_key)
        rule_key = str(rule.get("key", "<unknown>"))
        legacy_rule_expectations[rule_key] = (
            frozenset(mapped_targets),
            deepcopy(stripped) if stripped is not None else None,
        )
        selectors_migrated += len(selector_values)
        rules_changed += 1

    if errors or unsafe_conditions:
        return _blocked(
            before_hash,
            *errors,
            unsafe_conditions=unsafe_conditions,
            facts_removed=facts_removed,
            selectors_migrated=selectors_migrated,
            rules_changed=rules_changed,
            apply_to_document=apply_to_document,
        )

    bindings_created = 0
    visibility_migrated = 0
    for action_key in sorted(action_keys_with_selectors):
        action = action_by_key[action_key]
        raw_contracts = action.get("target_contracts", [])
        if raw_contracts is None:
            raw_contracts = []
        if not isinstance(raw_contracts, list):
            errors.append(f"Action {action_key} target_contracts is not a list")
            continue
        existing_contracts = {
            item.get("target_key"): item
            for item in raw_contracts
            if isinstance(item, dict) and isinstance(item.get("target_key"), str)
        }
        contracts: list[dict[str, Any]] = [
            item
            for item in raw_contracts
            if isinstance(item, dict)
        ]
        for target_key in sorted(profile_nodes):
            visibility = visibility_by_node.get(target_key, "KNOWN")
            existing = existing_contracts.get(target_key)
            if existing is not None:
                if str(existing.get("initial_visibility", "KNOWN")) != visibility:
                    errors.append(
                        f"Action {action_key} target {target_key} has conflicting "
                        "initial visibility"
                    )
                existing["reveal_on_inspect"] = True
                continue
            contracts.append(
                {
                    "target_key": target_key,
                    "initial_visibility": visibility,
                    "reveal_on_inspect": True,
                }
            )
            bindings_created += 1
            visibility_migrated += 1
        action["target_contracts"] = sorted(contracts, key=lambda item: item["target_key"])

    residual = tuple(_find_exact_strings(proposed, _LEGACY_FACT_KEY))
    if residual:
        errors.append("Residual repair_profile references remain: " + ", ".join(residual))
    if errors:
        return _blocked(
            before_hash,
            *errors,
            facts_removed=facts_removed,
            selectors_migrated=selectors_migrated,
            rules_changed=rules_changed,
            bindings_created=bindings_created,
            visibility_migrated=visibility_migrated,
            residual_references=residual,
            apply_to_document=apply_to_document,
        )

    try:
        parsed = ScenarioDefinitionV2.model_validate(proposed)
        proposed = parsed.model_dump(mode="json")
    except Exception as exc:  # pragma: no cover - exact Pydantic wording varies
        return _blocked(
            before_hash,
            f"Migrated ScenarioDefinition is invalid: {exc}",
            facts_removed=facts_removed,
            selectors_migrated=selectors_migrated,
            rules_changed=rules_changed,
            bindings_created=bindings_created,
            visibility_migrated=visibility_migrated,
            apply_to_document=apply_to_document,
        )

    after_hash = scenario_content_hash(proposed)
    semantic_parity = _semantic_parity(
        proposed,
        profile_by_value=profile_by_value,
        visibility_by_node=visibility_by_node,
        legacy_rule_expectations=legacy_rule_expectations,
        action_keys_with_selectors=action_keys_with_selectors,
    )
    return TargetApplicabilityMigrationReport(
        before_hash=before_hash,
        after_hash=after_hash,
        applied=apply_to_document,
        blocked=False,
        facts_removed=facts_removed,
        selectors_migrated=selectors_migrated,
        rules_changed=rules_changed,
        bindings_created=bindings_created,
        visibility_migrated=visibility_migrated,
        semantic_parity=semantic_parity,
        document=proposed if apply_to_document else None,
    )


def _selector_values(condition: object) -> tuple[str, ...]:
    if not isinstance(condition, dict):
        return ()
    kind = condition.get("kind")
    node = condition.get("node")
    if (
        kind in {ConditionKind.FACT_EQUALS.value, ConditionKind.FACT_IN.value}
        and isinstance(node, dict)
        and node.get("kind") == NodeSelectorKind.CURRENT_TARGET.value
        and condition.get("fact_key") == _LEGACY_FACT_KEY
    ):
        values = (
            [condition.get("value")]
            if kind == ConditionKind.FACT_EQUALS.value
            else condition.get("values", [])
        )
        return tuple(item for item in values if isinstance(item, str))
    if kind in {ConditionKind.ALL.value, ConditionKind.ANY.value}:
        result: list[str] = []
        for child in condition.get("conditions", []):
            result.extend(_selector_values(child))
        return tuple(result)
    if kind == ConditionKind.NOT.value:
        return _selector_values(condition.get("condition"))
    return ()


def _contains_selector(condition: object) -> bool:
    return bool(_selector_values(condition))


def _strip_positive_selectors(condition: object) -> tuple[dict[str, Any] | None, bool]:
    if not isinstance(condition, dict):
        return condition if isinstance(condition, dict) else None, False
    kind = condition.get("kind")
    if (
        kind in {ConditionKind.FACT_EQUALS.value, ConditionKind.FACT_IN.value}
        and isinstance(condition.get("node"), dict)
        and condition["node"].get("kind") == NodeSelectorKind.CURRENT_TARGET.value
        and condition.get("fact_key") == _LEGACY_FACT_KEY
    ):
        return None, False
    if kind == ConditionKind.ALL.value:
        raw_children = condition.get("conditions", [])
        if not isinstance(raw_children, list):
            return condition, _contains_selector(condition)
        children: list[dict[str, Any]] = []
        for child in raw_children:
            if not isinstance(child, dict):
                return condition, True
            if _contains_selector(child) and child.get("kind") in {
                ConditionKind.ANY.value,
                ConditionKind.NOT.value,
            }:
                return condition, True
            stripped, unsafe = _strip_positive_selectors(child)
            if unsafe:
                return condition, True
            if stripped is not None:
                children.append(stripped)
        if not children:
            return None, False
        if len(children) == 1:
            return children[0], False
        result = dict(condition)
        result["conditions"] = children
        return result, False
    if kind in {ConditionKind.ANY.value, ConditionKind.NOT.value}:
        return condition, _contains_selector(condition)
    return condition, False


def _find_exact_strings(value: object, needle: str, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(_find_exact_strings(child, needle, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_exact_strings(child, needle, f"{path}[{index}]"))
    elif value == needle:
        found.append(path)
    return found


def _semantic_parity(
    migrated: dict[str, Any],
    *,
    profile_by_value: dict[str, str],
    visibility_by_node: dict[str, str],
    legacy_rule_expectations: dict[str, tuple[frozenset[str], dict[str, Any] | None]],
    action_keys_with_selectors: set[str],
) -> bool:
    """Check the migration's preserved selector/contract semantics structurally.

    This intentionally avoids running the game engine during a dry-run.  It
    proves that each legacy selector became the same target set, that the
    remaining condition AST is exactly the selector-stripped AST, and that
    every legacy target retained its Knowledge visibility and inspect policy.
    """

    if _find_exact_strings(migrated, _LEGACY_FACT_KEY):
        return False
    rules = {
        item.get("key"): item
        for item in migrated.get("rules", [])
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    for rule_key, (expected_targets, expected_condition) in legacy_rule_expectations.items():
        rule = rules.get(rule_key)
        if rule is None:
            return False
        actual_targets = frozenset(
            item for item in rule.get("applicable_target_keys", []) if isinstance(item, str)
        )
        if actual_targets != expected_targets:
            return False
        if _stable_json(rule.get("condition")) != _stable_json(
            _normalized_condition(expected_condition)
        ):
            return False

    actions = {
        item.get("key"): item
        for item in migrated.get("actions", [])
        if isinstance(item, dict) and isinstance(item.get("key"), str)
    }
    for action_key in action_keys_with_selectors:
        action = actions.get(action_key)
        if action is None:
            return False
        contracts = {
            item.get("target_key"): item
            for item in action.get("target_contracts", [])
            if isinstance(item, dict) and isinstance(item.get("target_key"), str)
        }
        for target_key in set(profile_by_value.values()):
            contract = contracts.get(target_key)
            if contract is None:
                return False
            if str(contract.get("initial_visibility", "KNOWN")) != visibility_by_node.get(
                target_key, "KNOWN"
            ):
                return False
            if contract.get("reveal_on_inspect") is not True:
                return False
    return True


def _stable_json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalized_condition(value: object) -> object:
    """Normalize a selector-stripped raw AST before structural parity checks."""

    if value is None:
        return None
    return ConditionV2.model_validate(value).model_dump(mode="json")


def _blocked(
    before_hash: str,
    *errors: str,
    unsafe_conditions: list[str] | tuple[str, ...] = (),
    facts_removed: int = 0,
    selectors_migrated: int = 0,
    rules_changed: int = 0,
    bindings_created: int = 0,
    visibility_migrated: int = 0,
    residual_references: tuple[str, ...] = (),
    apply_to_document: bool,
) -> TargetApplicabilityMigrationReport:
    return TargetApplicabilityMigrationReport(
        before_hash=before_hash,
        after_hash=before_hash,
        applied=False,
        blocked=True,
        facts_removed=facts_removed,
        selectors_migrated=selectors_migrated,
        rules_changed=rules_changed,
        bindings_created=bindings_created,
        visibility_migrated=visibility_migrated,
        unsafe_conditions=tuple(unsafe_conditions),
        residual_references=tuple(residual_references),
        errors=tuple(errors),
        semantic_parity=False,
        document=None,
    )


__all__ = [
    "TargetApplicabilityMigrationReport",
    "migrate_target_applicability_document",
]
