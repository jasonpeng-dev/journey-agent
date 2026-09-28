"""Canonical, version-aware Scenario semantic comparison.

This module is the single Draft/Working Copy versus Published Version
comparison authority.  It intentionally compares canonical authoring payloads
and aligns semantic collections by their authored identity.  Presentation
consumers should use the structured result instead of recursively comparing
raw JSON snapshots.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from app.scenarios.documents import parse_scenario_document_versioned
from app.scenarios.serialization import canonical_document_payload


class SemanticDiffScope(StrEnum):
    DESIGN = "DESIGN"
    INITIALIZATION = "INITIALIZATION"


class SemanticChangeKind(StrEnum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"
    REORDERED = "REORDERED"


@dataclass(frozen=True, slots=True)
class SemanticDiffEntry:
    scope: SemanticDiffScope
    editor_section: str
    editor_subsection: str
    object_kind: str
    object_key: str | None
    object_display_name: str
    change_kind: SemanticChangeKind
    locator: dict[str, str | None]
    field_path: str | None = None
    before: object | None = None
    after: object | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _CollectionSpec:
    kind: str
    identity: Callable[[Mapping[str, Any]], str]


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _required(value: object, fallback: str) -> str:
    return _string(value) or fallback


def _relation_identity(value: Mapping[str, Any]) -> str:
    explicit = _string(value.get("key"))
    if explicit:
        return explicit
    return "__".join(
        _required(value.get(part), "")
        for part in ("source_node_key", "relation_type_key", "target_node_key")
    )


def _public_reference_identity(value: Mapping[str, Any]) -> str:
    return "|".join(_required(value.get(part), "") for part in ("term", "ref_type", "ref_key"))


def _legacy_resource_identity(value: Mapping[str, Any]) -> str:
    return "|".join(
        _required(value.get(part), "global") for part in ("resource_key", "scope_node_key")
    )


def _derived_dependency_identity(value: Mapping[str, Any]) -> str:
    fields = (
        "kind",
        "node_key",
        "fact_key",
        "region_key",
        "resource_key",
        "minimum",
        "derived_key",
    )
    return "|".join(_required(value.get(field), "") for field in fields)


_IDENTITY_COLLECTIONS: dict[tuple[str, ...], _CollectionSpec] = {
    ("world", "node_types"): _CollectionSpec(
        "node_type", lambda item: _required(item.get("key"), "")
    ),
    ("world", "nodes"): _CollectionSpec("node", lambda item: _required(item.get("key"), "")),
    ("world", "relation_types"): _CollectionSpec(
        "relation_type", lambda item: _required(item.get("key"), "")
    ),
    ("world", "relations"): _CollectionSpec("relation", _relation_identity),
    ("world", "resources"): _CollectionSpec(
        "resource", lambda item: _required(item.get("key"), "")
    ),
    ("actors", "roles"): _CollectionSpec("role", lambda item: _required(item.get("key"), "")),
    ("actors", "actor_profiles"): _CollectionSpec(
        "actor", lambda item: _required(item.get("key"), "")
    ),
    ("interactions",): _CollectionSpec("interaction", lambda item: _required(item.get("key"), "")),
    ("actions",): _CollectionSpec("action", lambda item: _required(item.get("key"), "")),
    ("rules",): _CollectionSpec("rule", lambda item: _required(item.get("key"), "")),
    ("objectives",): _CollectionSpec("objective", lambda item: _required(item.get("key"), "")),
    ("derived_states",): _CollectionSpec(
        "derived_state", lambda item: _required(item.get("key"), "")
    ),
    ("public_references",): _CollectionSpec("public_reference", _public_reference_identity),
    ("initialization", "resource_pools"): _CollectionSpec(
        "resource_pool", lambda item: _required(item.get("pool_key"), "")
    ),
    ("initialization", "region_resource_knowledge"): _CollectionSpec(
        "region_resource_knowledge", lambda item: _required(item.get("region_key"), "")
    ),
    ("initialization", "resource_initial_states"): _CollectionSpec(
        "legacy_resource_initial_state", _legacy_resource_identity
    ),
}

_NESTED_IDENTITY_FIELDS: dict[tuple[str, ...], _CollectionSpec] = {
    ("facts",): _CollectionSpec("fact", lambda item: _required(item.get("key"), "")),
    ("parameters",): _CollectionSpec(
        "action_parameter", lambda item: _required(item.get("key"), "")
    ),
    ("expected_outcomes",): _CollectionSpec(
        "action_outcome", lambda item: _required(item.get("code"), "")
    ),
    ("target_contracts",): _CollectionSpec(
        "action_target_contract", lambda item: _required(item.get("target_key"), "")
    ),
    ("operation_bindings",): _CollectionSpec(
        "action_binding", lambda item: _required(item.get("key"), "")
    ),
    ("doctrine",): _CollectionSpec("actor_doctrine", lambda item: _required(item.get("key"), "")),
    ("autonomous_limits",): _CollectionSpec(
        "authority_limit", lambda item: _required(item.get("parameter_key"), "")
    ),
    ("approval_required_values",): _CollectionSpec(
        "authority_approval", lambda item: _required(item.get("parameter_key"), "")
    ),
}

_ORDERED_COLLECTIONS = {
    ("planning", "instructions"),
    ("goal_resolution", "quick_inputs"),
}

_INITIALIZATION_FIELDS = {
    "initial_access",
    "initial_visibility",
    "initial_value",
    "initial_node_key",
    "command_reachability",
    "resource_initial_states",
    "resource_pools",
    "region_resource_knowledge",
}

_SECTION_LABELS = {
    "world": ("世界模型", "世界模型"),
    "actors": ("角色与行动", "角色"),
    "interactions": ("角色与行动", "交互"),
    "actions": ("角色与行动", "行动"),
    "rules": ("角色与行动", "规则"),
    "objectives": ("目标与规划", "目标"),
    "goal_resolution": ("目标与规划", "目标解析"),
    "planning": ("目标与规划", "规划指引"),
    "derived_states": ("目标与规划", "派生状态"),
    "initialization": ("初始化", "初始化"),
    "public_references": ("目标与规划", "术语与引用"),
    "metadata": ("世界模型", "场景信息"),
    "engine_contract": ("世界模型", "引擎契约"),
}


def _path_text(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _collection_spec(path: tuple[str, ...]) -> _CollectionSpec | None:
    direct = _IDENTITY_COLLECTIONS.get(path)
    if direct is not None:
        return direct
    if path and path[-1] in {
        "facts",
        "parameters",
        "expected_outcomes",
        "target_contracts",
        "operation_bindings",
        "doctrine",
        "autonomous_limits",
        "approval_required_values",
    }:
        return _NESTED_IDENTITY_FIELDS.get((path[-1],))
    return None


def _scope_for(path: tuple[str, ...]) -> SemanticDiffScope:
    if path and path[0] == "initialization":
        return SemanticDiffScope.INITIALIZATION
    if any(part in _INITIALIZATION_FIELDS for part in path):
        return SemanticDiffScope.INITIALIZATION
    if (
        path
        and path[0] == "world"
        and path[-1]
        in {
            "initial_access",
            "initial_visibility",
            "initial_value",
        }
    ):
        return SemanticDiffScope.INITIALIZATION
    return SemanticDiffScope.DESIGN


def _editor_labels(path: tuple[str, ...]) -> tuple[str, str]:
    if _scope_for(path) == SemanticDiffScope.INITIALIZATION:
        root = path[0] if path else "initialization"
        if root == "initialization" and len(path) >= 2:
            subsection = {
                "resource_pools": "资源池",
                "region_resource_knowledge": "区域库存情报",
                "resource_initial_states": "资源初始化",
            }.get(path[1], "初始化")
        elif root == "world" and len(path) >= 2:
            subsection = {
                "nodes": "节点",
                "relations": "关系",
                "resources": "资源",
            }.get(path[1], "初始化")
        elif root == "actors":
            subsection = "参与者"
        else:
            subsection = "初始化"
        return "初始化", subsection
    root = path[0] if path else "metadata"
    section, subsection = _SECTION_LABELS.get(root, ("世界模型", root))
    if len(path) >= 2 and root == "world":
        subsection = {
            "nodes": "世界实体",
            "node_types": "节点类型",
            "relations": "关系实例",
            "relation_types": "关系类型",
            "resources": "资源定义",
        }.get(path[1], subsection)
    if len(path) >= 2 and root == "initialization":
        subsection = {
            "resource_pools": "资源池",
            "region_resource_knowledge": "区域库存情报",
            "resource_initial_states": "资源初始化",
        }.get(path[1], subsection)
    return section, subsection


def _display_name(value: Mapping[str, Any] | None, key: str | None, fallback: str) -> str:
    if value:
        for field in ("name", "term", "code", "pool_key", "region_key", "key"):
            candidate = value.get(field)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return key or fallback


def _object_context(
    path: tuple[str, ...], context: tuple[str, str | None, Mapping[str, Any] | None]
) -> tuple[str, str | None, Mapping[str, Any] | None]:
    if context[0]:
        return context
    spec = _collection_spec(path[:-1]) if path else None
    if spec is not None:
        return spec.kind, None, None
    return context


def _entry(
    *,
    path: tuple[str, ...],
    kind: str,
    key: str | None,
    value: Mapping[str, Any] | None,
    change: SemanticChangeKind,
    before: object | None,
    after: object | None,
    field_path: tuple[str, ...] | None = None,
) -> SemanticDiffEntry:
    section, subsection = _editor_labels(path)
    scope = _scope_for(path)
    object_key = key
    field = _path_text(field_path or path) if field_path or path else None
    return SemanticDiffEntry(
        scope=scope,
        editor_section=section,
        editor_subsection=subsection,
        object_kind=kind or "scenario",
        object_key=object_key,
        object_display_name=_display_name(value, object_key, subsection),
        change_kind=change,
        locator={
            "object_kind": kind or "scenario",
            "object_key": object_key,
            "field_path": field,
        },
        field_path=field,
        before=before,
        after=after,
    )


def _is_mapping(value: object) -> bool:
    return isinstance(value, Mapping)


def _same_multiset(left: list[object], right: list[object]) -> bool:
    remaining = list(right)
    for item in left:
        for index, candidate in enumerate(remaining):
            if item == candidate:
                remaining.pop(index)
                break
        else:
            return False
    return not remaining


def _compare(
    before: object,
    after: object,
    path: tuple[str, ...],
    entries: list[SemanticDiffEntry],
    *,
    context: tuple[str, str | None, Mapping[str, Any] | None] = ("", None, None),
) -> None:
    if before == after:
        return
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        keys = sorted(set(before) | set(after))
        for name in keys:
            child_path = (*path, str(name))
            in_before = name in before
            in_after = name in after
            child_before = before.get(name)
            child_after = after.get(name)
            if not in_before:
                entries.append(
                    _entry(
                        path=child_path,
                        kind=context[0],
                        key=context[1],
                        value=context[2],
                        change=SemanticChangeKind.ADDED,
                        before=None,
                        after=child_after,
                        field_path=child_path,
                    )
                )
            elif not in_after:
                entries.append(
                    _entry(
                        path=child_path,
                        kind=context[0],
                        key=context[1],
                        value=context[2],
                        change=SemanticChangeKind.REMOVED,
                        before=child_before,
                        after=None,
                        field_path=child_path,
                    )
                )
            else:
                _compare(child_before, child_after, child_path, entries, context=context)
        return
    if isinstance(before, list) and isinstance(after, list):
        spec = _collection_spec(path)
        if spec is not None and all(isinstance(item, Mapping) for item in (*before, *after)):
            before_map = {spec.identity(item): item for item in before if isinstance(item, Mapping)}
            after_map = {spec.identity(item): item for item in after if isinstance(item, Mapping)}
            for identity in sorted(set(before_map) | set(after_map)):
                child_path = (*path, identity)
                if identity not in before_map:
                    entries.append(
                        _entry(
                            path=path,
                            kind=spec.kind,
                            key=identity,
                            value=after_map[identity],
                            change=SemanticChangeKind.ADDED,
                            before=None,
                            after=after_map[identity],
                            field_path=child_path,
                        )
                    )
                elif identity not in after_map:
                    entries.append(
                        _entry(
                            path=path,
                            kind=spec.kind,
                            key=identity,
                            value=before_map[identity],
                            change=SemanticChangeKind.REMOVED,
                            before=before_map[identity],
                            after=None,
                            field_path=child_path,
                        )
                    )
                else:
                    _compare(
                        before_map[identity],
                        after_map[identity],
                        child_path,
                        entries,
                        context=(spec.kind, identity, after_map[identity]),
                    )
            return
        if path in _ORDERED_COLLECTIONS:
            if _same_multiset(before, after):
                entries.append(
                    _entry(
                        path=path,
                        kind="ordered_item",
                        key=None,
                        value=None,
                        change=SemanticChangeKind.REORDERED,
                        before=before,
                        after=after,
                        field_path=path,
                    )
                )
                return
            max_len = max(len(before), len(after))
            for index in range(max_len):
                child_path = (*path, str(index))
                if index >= len(before):
                    entries.append(
                        _entry(
                            path=path,
                            kind="ordered_item",
                            key=str(index),
                            value=None,
                            change=SemanticChangeKind.ADDED,
                            before=None,
                            after=after[index],
                            field_path=child_path,
                        )
                    )
                elif index >= len(after):
                    entries.append(
                        _entry(
                            path=path,
                            kind="ordered_item",
                            key=str(index),
                            value=None,
                            change=SemanticChangeKind.REMOVED,
                            before=before[index],
                            after=None,
                            field_path=child_path,
                        )
                    )
                elif before[index] != after[index]:
                    entries.append(
                        _entry(
                            path=path,
                            kind="ordered_item",
                            key=str(index),
                            value=None,
                            change=SemanticChangeKind.MODIFIED,
                            before=before[index],
                            after=after[index],
                            field_path=child_path,
                        )
                    )
            return
        for index in range(max(len(before), len(after))):
            child_path = (*path, str(index))
            if index >= len(before):
                entries.append(
                    _entry(
                        path=path,
                        kind=context[0],
                        key=context[1],
                        value=context[2],
                        change=SemanticChangeKind.ADDED,
                        before=None,
                        after=after[index],
                        field_path=child_path,
                    )
                )
            elif index >= len(after):
                entries.append(
                    _entry(
                        path=path,
                        kind=context[0],
                        key=context[1],
                        value=context[2],
                        change=SemanticChangeKind.REMOVED,
                        before=before[index],
                        after=None,
                        field_path=child_path,
                    )
                )
            else:
                _compare(before[index], after[index], child_path, entries, context=context)
        return
    kind, key, value = context
    entries.append(
        _entry(
            path=path,
            kind=kind,
            key=key,
            value=value,
            change=SemanticChangeKind.MODIFIED,
            before=before,
            after=after,
            field_path=path,
        )
    )


def _summary(entries: list[SemanticDiffEntry]) -> list[dict[str, object]]:
    sections: dict[str, dict[str, object]] = {}
    for entry in entries:
        section = sections.setdefault(
            entry.editor_section,
            {"section": entry.editor_section, "count": 0, "subsections": {}},
        )
        section_count = section.get("count")
        section["count"] = (section_count if isinstance(section_count, int) else 0) + 1
        subsections = section["subsections"]
        assert isinstance(subsections, dict)
        item = subsections.setdefault(
            entry.editor_subsection,
            {"subsection": entry.editor_subsection, "count": 0},
        )
        item_count = item.get("count")
        item["count"] = (item_count if isinstance(item_count, int) else 0) + 1
    result: list[dict[str, object]] = []
    for section in sections.values():
        subsections = section["subsections"]
        assert isinstance(subsections, dict)
        result.append({**section, "subsections": list(subsections.values())})
    return result


def semantic_diff(
    working_document: Mapping[str, Any],
    published_document: Mapping[str, Any] | None,
    *,
    published_version_id: str | None = None,
    published_version_number: int | None = None,
    published_schema_version: int | None = None,
    include_entries: bool = True,
) -> dict[str, object]:
    """Return the canonical semantic diff for two versioned documents."""

    baseline = {
        "published_version_id": published_version_id,
        "published_version_number": published_version_number,
        "published_schema_version": published_schema_version,
    }
    if published_document is None:
        return {
            "published": False,
            "compared_version": None,
            "is_equal": None,
            "comparable": False,
            "total_changed_objects": 0,
            "total_changed_items": 0,
            "section_summaries": [],
            "entries": [],
            **baseline,
        }

    def canonical_semantics(document: Mapping[str, Any]) -> dict[str, Any]:
        # ``canonical_document_payload`` preserves a few legacy wire omission
        # bits for immutable snapshot compatibility.  Semantic comparison
        # intentionally removes those representation bits by parsing the
        # canonical payload once more and dumping the typed model.
        payload = canonical_document_payload(dict(document))
        parsed = parse_scenario_document_versioned(payload)
        return parsed.model_dump(mode="json")

    before = canonical_semantics(published_document)
    after = canonical_semantics(working_document)
    entries: list[SemanticDiffEntry] = []
    _compare(before, after, (), entries)
    # Canonical payload equality is the authoritative fast path.  It also
    # protects callers from representation-only list/order differences.
    if before == after:
        entries = []
    sections = _summary(entries)
    return {
        "published": True,
        "compared_version": {
            "id": published_version_id,
            "version_number": published_version_number,
            "schema_version": published_schema_version,
        },
        "is_equal": not entries,
        "comparable": True,
        "total_changed_objects": len(
            {(item.scope, item.object_kind, item.object_key) for item in entries}
        ),
        "total_changed_items": len(entries),
        "section_summaries": sections,
        "entries": [item.as_dict() for item in entries] if include_entries else [],
        **baseline,
    }


__all__ = [
    "SemanticChangeKind",
    "SemanticDiffEntry",
    "SemanticDiffScope",
    "semantic_diff",
]
