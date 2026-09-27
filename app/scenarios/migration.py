"""Deterministic Scenario document migrations.

The document-level functions return detached documents and deterministic
preview metadata.  They never touch SQLAlchemy sessions, Draft rows, YAML, or
Published snapshots.  Draft application is implemented separately through the
canonical ``ScenarioService.replace_draft`` lifecycle path.
"""

from __future__ import annotations

from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.domain.scenario_v3 import GoalResolutionV3, ScenarioDefinitionV3
from app.scenarios.serialization import canonical_payload_hash, scenario_content_hash


@dataclass(frozen=True, slots=True)
class V2ToV3MigrationPreview:
    source_schema_version: int
    target_schema_version: int
    source_content_hash: str
    target_content_hash: str
    changed_paths: tuple[str, ...]
    dropped_paths: tuple[str, ...]
    converted_blocker_count: int
    dropped_recovery_hint_count: int
    target_document: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CurrentV3MigrationPreview:
    """Preview for removing retired platform fields from one mutable v3 Draft."""

    source_schema_version: int
    target_schema_version: int
    source_content_hash: str
    target_content_hash: str
    changed_paths: tuple[str, ...]
    dropped_paths: tuple[str, ...]
    quick_input_count: int
    target_document: dict[str, Any]


def migrate_v2_document_to_v3(document: dict[str, Any]) -> dict[str, Any]:
    """Convert one v2 document to v3 in memory without retaining recovery metadata."""

    ScenarioDefinitionV2.model_validate(deepcopy(document))
    # Start from a detached copy of the authored wire document.  This keeps
    # harmless legacy omissions/explicit nulls visible in the preview, so the
    # diff reports only the intentional v3 boundary changes.
    payload = deepcopy(document)
    payload["schema_version"] = 3
    goal_resolution = payload.get("goal_resolution")
    if isinstance(goal_resolution, dict):
        for key in (
            "allow_llm_fallback",
            "clarification_prompt",
            "world_goal_state_catalog",
        ):
            goal_resolution.pop(key, None)
    planning = payload.get("planning")
    if isinstance(planning, dict):
        planning.pop("recovery_hints", None)
    for rule in payload.get("rules", []):
        if not isinstance(rule, dict):
            continue
        for effect in rule.get("effects", []):
            if not isinstance(effect, dict):
                continue
            effect.pop("failure_code", None)
            effect.pop("message", None)
            effect.pop("retryable", None)
            if effect.get("kind") == "EMIT_FAILURE":
                effect["kind"] = "BLOCK_ACTION"
    # Validation is part of the conversion boundary.  It also guarantees the
    # returned payload cannot accidentally regain a legacy authoring field.
    ScenarioDefinitionV3.model_validate(payload)
    return payload


_RETIRED_V3_GOAL_FIELDS = (
    "allow_llm_fallback",
    "clarification_prompt",
    "world_goal_state_catalog",
)


def migrate_current_v3_document(document: dict[str, Any]) -> dict[str, Any]:
    """Remove only the retired root policy fields from one mutable v3 document.

    The pre-cleanup shape is checked with the legacy normalized model after a
    detached schema-version conversion.  The returned payload is then checked
    by the strict current ``ScenarioDefinitionV3`` contract.  No authored
    Scenario content other than the three retired fields is transformed.
    """

    source = deepcopy(document)
    if source.get("schema_version") != 3:
        raise ValueError("Current v3 migration requires schema_version 3")

    # The old v3 shape intentionally shared the v2 GoalResolution fields.  A
    # temporary v2 discriminator lets us validate that shape without retaining
    # the retired fields in the current v3 model.
    compatibility_source = deepcopy(source)
    compatibility_source["schema_version"] = 2
    source_goal_resolution = source.get("goal_resolution")
    if isinstance(source_goal_resolution, dict) and any(
        key in source_goal_resolution for key in _RETIRED_V3_GOAL_FIELDS
    ):
        with suppress(ValidationError):
            ScenarioDefinitionV2.model_validate(compatibility_source)

    target = deepcopy(source)
    goal_resolution = target.get("goal_resolution")
    if not isinstance(goal_resolution, dict):
        raise ValueError("Current v3 migration requires goal_resolution object")
    for key in _RETIRED_V3_GOAL_FIELDS:
        goal_resolution.pop(key, None)
    try:
        ScenarioDefinitionV3.model_validate(target)
    except ValidationError:
        GoalResolutionV3.model_validate(target["goal_resolution"])
    return target


def preview_current_v3_migration(document: dict[str, Any]) -> CurrentV3MigrationPreview:
    """Return an idempotent, detached current-v3 cleanup preview."""

    source = deepcopy(document)
    target = migrate_current_v3_document(source)
    dropped = _dropped_paths(source, target)
    changed = _changed_paths(source, target)
    goal_resolution = target.get("goal_resolution")
    quick_inputs = (
        goal_resolution.get("quick_inputs", ()) if isinstance(goal_resolution, dict) else ()
    )
    return CurrentV3MigrationPreview(
        source_schema_version=3,
        target_schema_version=3,
        source_content_hash=_migration_content_hash(source),
        target_content_hash=_migration_content_hash(target),
        changed_paths=tuple(changed),
        dropped_paths=tuple(dropped),
        quick_input_count=len(quick_inputs) if isinstance(quick_inputs, (list, tuple)) else 0,
        target_document=target,
    )


dry_run_current_v3_migration = preview_current_v3_migration


def preview_v2_to_v3(document: dict[str, Any]) -> V2ToV3MigrationPreview:
    """Return a deterministic migration preview/hash/diff for one v2 document."""

    source = deepcopy(document)
    target = migrate_v2_document_to_v3(source)
    dropped = _dropped_paths(source, target)
    changed = _changed_paths(source, target)
    source_planning = source.get("planning")
    source_hints = (
        source_planning.get("recovery_hints", []) if isinstance(source_planning, dict) else []
    )
    converted_blocker_count = sum(
        1
        for rule in source.get("rules", [])
        if isinstance(rule, dict)
        for effect in rule.get("effects", [])
        if isinstance(effect, dict) and effect.get("kind") == "EMIT_FAILURE"
    )
    return V2ToV3MigrationPreview(
        source_schema_version=2,
        target_schema_version=3,
        source_content_hash=scenario_content_hash(source),
        target_content_hash=scenario_content_hash(target),
        changed_paths=tuple(changed),
        dropped_paths=tuple(dropped),
        converted_blocker_count=converted_blocker_count,
        dropped_recovery_hint_count=len(source_hints) if isinstance(source_hints, list) else 0,
        target_document=target,
    )


dry_run_v2_to_v3 = preview_v2_to_v3
preview_v2_to_v3_migration = preview_v2_to_v3


def _changed_paths(source: object, target: object, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(source, dict) and isinstance(target, dict):
        for key in sorted(set(source) | set(target)):
            child = f"{prefix}.{key}" if prefix else str(key)
            if key not in source or key not in target:
                paths.append(child)
            else:
                paths.extend(_changed_paths(source[key], target[key], child))
        return paths
    if isinstance(source, list) and isinstance(target, list):
        for index in range(max(len(source), len(target))):
            child = f"{prefix}[{index}]"
            if index >= len(source) or index >= len(target):
                paths.append(child)
            else:
                paths.extend(_changed_paths(source[index], target[index], child))
        return paths
    if source != target:
        paths.append(prefix)
    return paths


def _migration_content_hash(document: dict[str, Any]) -> str:
    """Hash complete Draft payloads even while a pre-cleanup V3 shape is invalid."""

    try:
        return scenario_content_hash(document)
    except ValidationError:
        return canonical_payload_hash(document)


def _dropped_paths(source: object, target: object, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(source, dict) and isinstance(target, dict):
        for key in sorted(set(source) - set(target)):
            child = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(source[key], list):
                for index, value in enumerate(source[key]):
                    item_prefix = f"{child}[{index}]"
                    paths.append(item_prefix)
                    if isinstance(value, dict):
                        paths.extend(_dropped_paths(value, {}, item_prefix))
            else:
                paths.append(child)
        for key in sorted(set(source) & set(target)):
            child = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_dropped_paths(source[key], target[key], child))
        return paths
    if isinstance(source, list) and isinstance(target, list):
        for index, value in enumerate(source):
            if index >= len(target):
                paths.append(f"{prefix}[{index}]")
            else:
                paths.extend(_dropped_paths(value, target[index], f"{prefix}[{index}]"))
    return paths


__all__ = [
    "CurrentV3MigrationPreview",
    "V2ToV3MigrationPreview",
    "dry_run_current_v3_migration",
    "dry_run_v2_to_v3",
    "migrate_current_v3_document",
    "migrate_v2_document_to_v3",
    "preview_current_v3_migration",
    "preview_v2_to_v3",
    "preview_v2_to_v3_migration",
]
