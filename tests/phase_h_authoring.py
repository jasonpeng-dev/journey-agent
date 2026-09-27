"""Deterministic, in-memory checks for the Phase H authoring contract.

The Phase H audit script writes optional reports under ``tmp/``. Formal tests
exercise the same schema ownership boundary without reading those reports, so
this module exposes only the reusable in-memory inventory builder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, get_args, get_origin

from pydantic import BaseModel

from app.domain.scenario_v3 import ScenarioDefinitionV3
from app.scenarios.initialization import FieldUiOwner, field_ui_owner


@dataclass(frozen=True, slots=True)
class AuthoringField:
    """One leaf in the current V3 schema and its canonical UI owner."""

    path: str
    owner: FieldUiOwner
    collection: bool
    classification: str
    section: str
    control: str
    coverage_status: str
    current: bool


def _unwrap(value: object) -> object:
    if get_origin(value) is Annotated:
        return _unwrap(get_args(value)[0])
    return value


def _nested_models(value: object) -> tuple[type[BaseModel], ...]:
    value = _unwrap(value)
    result: list[type[BaseModel]] = []
    if isinstance(value, type) and issubclass(value, BaseModel):
        result.append(value)
    for argument in get_args(value):
        result.extend(_nested_models(argument))
    return tuple(result)


def _owner_for_path(path: str) -> FieldUiOwner:
    normalized = path.replace("[]", "")
    if normalized == "schema_version" or normalized.startswith("schema_version."):
        return FieldUiOwner.SYSTEM
    if normalized == "engine_contract" or normalized.startswith("engine_contract."):
        return FieldUiOwner.SYSTEM
    if normalized == "world.key":
        return FieldUiOwner.DERIVED_READONLY
    if normalized == "world.name":
        return FieldUiOwner.LEGACY
    return field_ui_owner(path)


def _classification_for(path: str, owner: FieldUiOwner) -> str:
    normalized = path.replace("[]", "")
    if normalized.startswith(("schema_version", "engine_contract")):
        return "SYSTEM_MANAGED"
    if normalized == "world.key":
        return "DERIVED_READ_ONLY"
    if normalized == "world.name":
        return "LEGACY_COMPATIBILITY"
    return {
        FieldUiOwner.DESIGN_ONLY: "AUTHORABLE",
        FieldUiOwner.INITIALIZATION_ONLY: "INITIALIZATION_AUTHORABLE",
        FieldUiOwner.SYSTEM: "SYSTEM_MANAGED",
        FieldUiOwner.LEGACY: "LEGACY_COMPATIBILITY",
        FieldUiOwner.DERIVED_READONLY: "DERIVED_READ_ONLY",
        FieldUiOwner.SHARED_CONTEXT_READONLY: "DERIVED_READ_ONLY",
    }[owner]


def _section_for(path: str, classification: str) -> str:
    normalized = path.replace("[]", "")
    if classification == "SYSTEM_MANAGED":
        return "scenario envelope"
    if classification == "LEGACY_COMPATIBILITY":
        return "legacy compatibility inspector"
    if normalized == "world.key":
        return "world (readonly projection)"
    if classification == "DERIVED_READ_ONLY":
        return "derived-states"
    if normalized.startswith("metadata"):
        return "overview"
    if normalized.startswith("world.node_types"):
        return "node-types"
    if normalized.startswith("world.nodes"):
        return (
            "initialization"
            if normalized.endswith(("initial_access", "initial_visibility"))
            else "world-entities"
        )
    if normalized.startswith("world.relation_types"):
        return "relation-types"
    if normalized.startswith("world.relations"):
        return "initialization" if normalized.endswith("initial_visibility") else "relations"
    if normalized.startswith("world.resources"):
        return "resources"
    if normalized.startswith("actors.roles"):
        return "roles"
    if normalized.startswith("actors.actor_profiles"):
        return (
            "initialization"
            if normalized.endswith(("initial_node_key", "command_reachability"))
            else "actors"
        )
    if normalized.startswith("interactions"):
        return "interactions"
    if normalized.startswith("actions"):
        return "actions"
    if normalized.startswith("rules"):
        return "rules"
    if normalized.startswith("derived_states"):
        return "derived-states"
    if normalized.startswith("goal_resolution"):
        return "goal-resolution"
    if normalized.startswith("planning"):
        return "planning-instructions"
    if normalized.startswith("public_references"):
        return "terminology-references"
    if normalized.startswith("initialization"):
        return "initialization"
    return "overview"


def _is_reference(path: str) -> bool:
    leaf = path.replace("[]", "").rsplit(".", 1)[-1]
    return leaf.endswith("_key") or leaf in {"ref_key", "role", "target"}


def _has_identity(path: str) -> bool:
    normalized = path.replace("[]", "")
    leaf = normalized.rsplit(".", 1)[-1]
    return leaf in {"key", "code", "pool_key"} or (
        "dependencies" in normalized
        and leaf in {"kind", "node_key", "fact_key", "accepted_values", "derived_key"}
    )


def _control_for(path: str, classification: str, collection: bool) -> str:
    if classification in {
        "SYSTEM_MANAGED",
        "PLATFORM_POLICY",
        "LEGACY_COMPATIBILITY",
        "DERIVED_READ_ONLY",
    }:
        return "readonly projection"
    if _has_identity(path):
        return "creation dialog + static identity"
    if collection:
        return "typed collection editor"
    if _is_reference(path):
        return "typed reference picker"
    return "typed field"


def build_v3_authoring_field_inventory() -> tuple[AuthoringField, ...]:
    """Return every V3 schema leaf with its canonical authoring owner.

    Collection indexes are represented as ``[]`` so the result remains stable
    across concrete documents. The function performs no file or DB I/O.
    """

    result: list[AuthoringField] = []
    seen: set[tuple[type[BaseModel], str]] = set()

    def walk(
        model: type[BaseModel],
        prefix: str = "",
        stack: tuple[type[BaseModel], ...] = (),
    ) -> None:
        marker = (model, prefix)
        if marker in seen:
            return
        seen.add(marker)
        for name, field in model.model_fields.items():
            path = f"{prefix}.{name}" if prefix else name
            annotation = field.annotation
            base = _unwrap(annotation)
            origin = get_origin(base)
            collection = origin in (list, tuple, set, frozenset)
            owner = _owner_for_path(path)
            classification = _classification_for(path, owner)
            result.append(
                AuthoringField(
                    path=path,
                    owner=owner,
                    collection=collection,
                    classification=classification,
                    section=_section_for(path, classification),
                    control=_control_for(path, classification, collection),
                    coverage_status=(
                        "COVERED_TYPED"
                        if classification in {"AUTHORABLE", "INITIALIZATION_AUTHORABLE"}
                        else "EXPLICIT_EXEMPTION"
                    ),
                    current=classification in {"AUTHORABLE", "INITIALIZATION_AUTHORABLE"},
                )
            )
            for child in _nested_models(annotation):
                if child in stack:
                    continue
                walk(child, path + ("[]" if collection else ""), (*stack, model))

    walk(ScenarioDefinitionV3)
    return tuple(result)


__all__ = ["AuthoringField", "build_v3_authoring_field_inventory"]
