"""Schema-aware authoring operations over an incomplete v2 Draft document."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ObjectLocator:
    object_kind: str
    object_key: str | None
    field_path: str | None = None


@dataclass(frozen=True, slots=True)
class ReferenceEdge:
    source: ObjectLocator
    target: ObjectLocator


class DraftAuthoringError(ValueError):
    def __init__(self, code: str, message: str, *, references: tuple[ReferenceEdge, ...] = ()):
        super().__init__(message)
        self.code = code
        self.message = message
        self.references = references


_COLLECTIONS: dict[str, tuple[str, ...]] = {
    "node_type": ("world", "node_types"),
    "node": ("world", "nodes"),
    "resource": ("world", "resources"),
    "role": ("actors", "roles"),
    "actor": ("actors", "actor_profiles"),
    "interaction": ("interactions",),
    "action": ("actions",),
    "rule": ("rules",),
    "objective": ("objectives",),
}

_REFERENCE_FIELDS: dict[str, str] = {
    "start_node_key": "node",
    "initial_node_key": "node",
    "source_node_key": "node",
    "target_node_key": "node",
    "node_key": "node",
    "anchor_node_key": "node",
    "primary_actor_key": "actor",
    "role_key": "role",
    "required_interaction_key": "interaction",
    "action_key": "action",
    "resource_key": "resource",
    "scope_node_key": "node",
}

_REFERENCE_LIST_FIELDS: dict[str, str] = {
    "interaction_keys": "interaction",
    "allowed_action_keys": "action",
    "subsumes": "objective",
}


def reference_index(document: dict[str, Any]) -> tuple[ReferenceEdge, ...]:
    edges: list[ReferenceEdge] = []
    _walk(document, (), None, edges)
    return tuple(edges)


def rename_key(
    document: dict[str, Any],
    *,
    object_kind: str,
    old_key: str,
    new_key: str,
) -> dict[str, Any]:
    target = _object(document, object_kind, old_key)
    if target is None:
        raise DraftAuthoringError("SCENARIO_OBJECT_NOT_FOUND", "The Draft object does not exist")
    if _object(document, object_kind, new_key) is not None:
        raise DraftAuthoringError("SCENARIO_OBJECT_KEY_CONFLICT", "The new object key is in use")
    changed = deepcopy(document)
    changed_target = _object(changed, object_kind, old_key)
    assert changed_target is not None
    changed_target["key"] = new_key
    _rewrite_references(changed, object_kind=object_kind, old_key=old_key, new_key=new_key)
    return changed


def delete_object(
    document: dict[str, Any],
    *,
    object_kind: str,
    object_key: str,
) -> dict[str, Any]:
    target = ObjectLocator(object_kind, object_key)
    used_by = tuple(edge for edge in reference_index(document) if edge.target == target)
    if used_by:
        raise DraftAuthoringError(
            "SCENARIO_OBJECT_REFERENCED",
            "The Draft object is referenced and cannot be deleted",
            references=used_by,
        )
    changed = deepcopy(document)
    collection = _collection(changed, object_kind)
    if collection is None:
        raise DraftAuthoringError("SCENARIO_OBJECT_KIND_UNSUPPORTED", "Unsupported object kind")
    retained = [
        item for item in collection if not isinstance(item, dict) or item.get("key") != object_key
    ]
    if len(retained) == len(collection):
        raise DraftAuthoringError("SCENARIO_OBJECT_NOT_FOUND", "The Draft object does not exist")
    collection[:] = retained
    return changed


def _walk(
    value: object,
    path: tuple[str | int, ...],
    source: ObjectLocator | None,
    edges: list[ReferenceEdge],
) -> None:
    if isinstance(value, dict):
        current_source = source
        collection_kind = _kind_for_path(path)
        if collection_kind is not None and isinstance(value.get("key"), str):
            current_source = ObjectLocator(collection_kind, value["key"])
        for key, child in value.items():
            field_path = _path((*path, key))
            if key in _REFERENCE_FIELDS and isinstance(child, str) and child:
                edges.append(
                    ReferenceEdge(
                        current_source or ObjectLocator("document", None, field_path),
                        ObjectLocator(_REFERENCE_FIELDS[key], child),
                    )
                )
            elif key in _REFERENCE_LIST_FIELDS and isinstance(child, list):
                edges.extend(
                    ReferenceEdge(
                        current_source or ObjectLocator("document", None, field_path),
                        ObjectLocator(_REFERENCE_LIST_FIELDS[key], item),
                    )
                    for item in child
                    if isinstance(item, str) and item
                )
            _walk(child, (*path, key), current_source, edges)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk(child, (*path, index), source, edges)


def _rewrite_references(value: object, *, object_kind: str, old_key: str, new_key: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _REFERENCE_FIELDS.get(key) == object_kind and child == old_key:
                value[key] = new_key
            elif _REFERENCE_LIST_FIELDS.get(key) == object_kind and isinstance(child, list):
                value[key] = [new_key if item == old_key else item for item in child]
            else:
                _rewrite_references(
                    child, object_kind=object_kind, old_key=old_key, new_key=new_key
                )
    elif isinstance(value, list):
        for child in value:
            _rewrite_references(child, object_kind=object_kind, old_key=old_key, new_key=new_key)


def _object(document: dict[str, Any], object_kind: str, key: str) -> dict[str, Any] | None:
    collection = _collection(document, object_kind)
    if collection is None:
        return None
    return next(
        (item for item in collection if isinstance(item, dict) and item.get("key") == key),
        None,
    )


def _collection(document: dict[str, Any], object_kind: str) -> list[Any] | None:
    path = _COLLECTIONS.get(object_kind)
    if path is None:
        return None
    value: object = document
    for part in path:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value if isinstance(value, list) else None


def _kind_for_path(path: tuple[str | int, ...]) -> str | None:
    if not path or not isinstance(path[-1], int):
        return None
    collection_path = tuple(part for part in path[:-1] if isinstance(part, str))
    return next((kind for kind, value in _COLLECTIONS.items() if value == collection_path), None)


def _path(path: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in path)


__all__ = [
    "DraftAuthoringError",
    "ObjectLocator",
    "ReferenceEdge",
    "delete_object",
    "reference_index",
    "rename_key",
]
