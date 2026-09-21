"""Schema-aware authoring operations over an incomplete V2 Draft document.

The Draft is intentionally allowed to be incomplete, so this module does not
parse it into ``ScenarioDefinitionV2``.  It owns only the editor reference
contract: stable-key identity, typed reference discovery, rename propagation,
delete guards, and validation-path locators.  Runtime and publish validation
remain authoritative elsewhere.
"""

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


# These are the actual top-level V2 aggregate collections.  Facts, resource
# pools, rule AST nodes, and target bindings remain nested in their owning
# objects; they are not promoted to invented CRUD collections.
_COLLECTIONS: dict[str, tuple[str, ...]] = {
    "node_type": ("world", "node_types"),
    "node": ("world", "nodes"),
    "relation_type": ("world", "relation_types"),
    "relation": ("world", "relations"),
    "resource": ("world", "resources"),
    "role": ("actors", "roles"),
    "actor": ("actors", "actor_profiles"),
    "interaction": ("interactions",),
    "action": ("actions",),
    "rule": ("rules",),
    "derived_state": ("derived_states",),
    "public_reference": ("public_references",),
}

_KEY_FIELDS = {
    "node_type": "key",
    "node": "key",
    "relation_type": "key",
    "relation": "key",
    "resource": "key",
    "role": "key",
    "actor": "key",
    "interaction": "key",
    "action": "key",
    "rule": "key",
    "derived_state": "key",
}

_ROOT_OBJECTS = {
    "metadata": "metadata",
    "initialization": "initialization",
    "goal_resolution": "goal_resolution",
    "planning": "planning",
    "public_knowledge": "public_knowledge",
    "engine_contract": "engine_contract",
}

_NODE_REFERENCE_FIELDS = {
    "start_node_key",
    "initial_node_key",
    "source_node_key",
    "target_node_key",
    "node_key",
    "anchor_node_key",
    "scope_node_key",
    "region_key",
    "facility_key",
}
_ACTOR_REFERENCE_FIELDS = {"primary_actor_key", "actor_key"}
_ROLE_REFERENCE_FIELDS = {"role_key", "required_actor_role_key"}
_INTERACTION_REFERENCE_FIELDS = {"required_interaction_key"}
_ACTION_REFERENCE_FIELDS = {"action_key"}
_RESOURCE_REFERENCE_FIELDS = {"resource_key"}
_DERIVED_REFERENCE_FIELDS = {"derived_key"}
_RELATION_REFERENCE_FIELDS = {"relation_key"}
_RELATION_TYPE_REFERENCE_FIELDS = {"relation_type_key"}
_NODE_TYPE_REFERENCE_FIELDS = {
    "node_type_key",
    "target_node_type_key",
    "region_node_type_key",
    "facility_node_type_key",
    "transport_node_type_key",
}
_LIST_REFERENCE_FIELDS = {
    "interaction_keys": "interaction",
    "allowed_action_keys": "action",
    "target_node_type_keys": "node_type",
    "candidate_region_keys": "node",
    "goal_required_slots": None,  # semantic slot identities, not catalog keys
}


def _path(path: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in path)


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


def _relation_key(value: dict[str, Any]) -> str | None:
    explicit = value.get("key")
    if isinstance(explicit, str) and explicit:
        return explicit
    source = value.get("source_node_key")
    relation = value.get("relation_type_key")
    target = value.get("target_node_key")
    if all(isinstance(item, str) and item for item in (source, relation, target)):
        return f"{source}__{relation}__{target}"
    return None


def _object_key(object_kind: str, value: dict[str, Any]) -> str | None:
    if object_kind == "relation":
        return _relation_key(value)
    if object_kind == "public_reference":
        ref_type = value.get("ref_type")
        ref_key = value.get("ref_key")
        term = value.get("term")
        if all(isinstance(item, str) and item for item in (ref_type, ref_key, term)):
            return f"{ref_type}:{ref_key}:{term}"
        return None
    field = _KEY_FIELDS.get(object_kind)
    key = value.get(field) if field is not None else None
    return key if isinstance(key, str) and key else None


def _object(document: dict[str, Any], object_kind: str, key: str) -> dict[str, Any] | None:
    collection = _collection(document, object_kind)
    if collection is None:
        return None
    return next(
        (
            item
            for item in collection
            if isinstance(item, dict) and _object_key(object_kind, item) == key
        ),
        None,
    )


def _kind_for_path(path: tuple[str | int, ...]) -> str | None:
    if not path or not isinstance(path[-1], int):
        return None
    collection_path = tuple(part for part in path[:-1] if isinstance(part, str))
    return next((kind for kind, value in _COLLECTIONS.items() if value == collection_path), None)


def _source_for_path(
    document: dict[str, Any],
    path: tuple[str | int, ...],
    inherited: ObjectLocator | None,
) -> tuple[ObjectLocator | None, tuple[str | int, ...] | None]:
    object_kind = _kind_for_path(path)
    if object_kind is not None and isinstance(path[-1], int):
        value: object = document
        for part in path:
            if not isinstance(value, (dict, list)):
                value = None
                break
            if isinstance(value, list) and isinstance(part, int) and part < len(value):
                value = value[part]
            elif isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
        if isinstance(value, dict):
            key = _object_key(object_kind, value)
            if key is not None:
                return ObjectLocator(object_kind, key), path
    if inherited is not None:
        return inherited, None
    if len(path) == 1 and isinstance(path[0], str) and path[0] in _ROOT_OBJECTS:
        return ObjectLocator(_ROOT_OBJECTS[path[0]], None), path
    return None, None


def _relative_field_path(
    path: tuple[str | int, ...],
    source_path: tuple[str | int, ...] | None,
) -> str:
    if source_path is not None and path[: len(source_path)] == source_path:
        path = path[len(source_path) :]
    return _path(path)


def _edge_source(source: ObjectLocator | None, field_path: str) -> ObjectLocator:
    if source is None:
        return ObjectLocator("document", None, field_path)
    return ObjectLocator(source.object_kind, source.object_key, field_path)


def _lookup_action(document: dict[str, Any], source: ObjectLocator | None) -> dict[str, Any] | None:
    if source is None or source.object_kind != "action" or source.object_key is None:
        return None
    return _object(document, "action", source.object_key)


def _explicit_node_key(value: object) -> str | None:
    if isinstance(value, dict):
        node_key = value.get("node_key")
        if isinstance(node_key, str) and node_key:
            return node_key
        selector = value.get("node")
        if isinstance(selector, dict) and selector.get("kind") == "EXPLICIT":
            node_key = selector.get("node_key")
            if isinstance(node_key, str) and node_key:
                return node_key
    return None


def _fact_target(
    value: dict[str, Any],
    fact_key: str,
    source: ObjectLocator | None,
    *,
    required_fact: bool = False,
) -> ObjectLocator | None:
    node_key = _explicit_node_key(value)
    if node_key is None and required_fact:
        selector = value.get("node")
        if isinstance(selector, dict):
            anchor_node_key = selector.get("anchor_node_key")
            node_key = anchor_node_key if isinstance(anchor_node_key, str) else None
    if node_key is None and source is not None and source.object_kind == "node":
        node_key = source.object_key
    if node_key is None:
        # CURRENT_TARGET and target-relative planning projections do not carry
        # a concrete Node key in the authored document.  Keep the Fact key in
        # the shared graph; reference_index expands this wildcard to each
        # matching authored Node Fact before delete guards consume it.
        return ObjectLocator("node", None, f"facts.{fact_key}")
    # Fact is a nested identity.  The owning Node remains the authoring
    # object; field_path identifies the nested Fact without inventing a CRUD
    # collection or a second stable-key namespace.
    return ObjectLocator("node", node_key, f"facts.{fact_key}")


def _pool_target(pool_key: str) -> ObjectLocator:
    # Resource pools are nested under Initialization, not top-level entities.
    return ObjectLocator("initialization", None, f"resource_pools.{pool_key}")


def _reference_target(
    document: dict[str, Any],
    container: dict[str, Any],
    field: str,
    value: object,
    source: ObjectLocator | None,
) -> ObjectLocator | None:
    if not isinstance(value, str) or not value:
        return None
    list_kind = _LIST_REFERENCE_FIELDS.get(field, "__missing__")
    if list_kind == "interaction":
        return ObjectLocator("interaction", value)
    if list_kind == "action":
        return ObjectLocator("action", value)
    if list_kind == "node_type":
        return ObjectLocator("node_type", value)
    if list_kind == "node":
        return ObjectLocator("node", value)
    if field in _NODE_REFERENCE_FIELDS:
        return ObjectLocator("node", value)
    if field in _NODE_TYPE_REFERENCE_FIELDS:
        return ObjectLocator("node_type", value)
    if field in _ACTOR_REFERENCE_FIELDS:
        return ObjectLocator("actor", value)
    if field in _ROLE_REFERENCE_FIELDS:
        return ObjectLocator("role", value)
    if field in _INTERACTION_REFERENCE_FIELDS:
        return ObjectLocator("interaction", value)
    if field in _ACTION_REFERENCE_FIELDS:
        return ObjectLocator("action", value)
    if field in _RESOURCE_REFERENCE_FIELDS:
        return ObjectLocator("resource", value)
    if field in _DERIVED_REFERENCE_FIELDS:
        return ObjectLocator("derived_state", value)
    if field in _RELATION_REFERENCE_FIELDS:
        return ObjectLocator("relation", value)
    if field in _RELATION_TYPE_REFERENCE_FIELDS:
        return ObjectLocator("relation_type", value)
    if field == "fact_key":
        return _fact_target(container, value, source)
    if field == "required_fact_key":
        return _fact_target(container, value, source, required_fact=True)
    if field == "pool_key":
        return _pool_target(value)
    if field == "target_key":
        action = _lookup_action(document, source)
        target_kind = action.get("target_kind") if action is not None else "NODE"
        return ObjectLocator("actor" if target_kind == "ACTOR" else "node", value)
    if field == "ref_key" and source is not None and source.object_kind == "public_reference":
        ref_type = container.get("ref_type")
        if ref_type == "REGION":
            return ObjectLocator("node", value)
        if ref_type == "NODE":
            return ObjectLocator("node", value)
        if ref_type == "RESOURCE":
            return ObjectLocator("resource", value)
        if ref_type == "DERIVED_STATE":
            return ObjectLocator("derived_state", value)
        if ref_type == "ACTION":
            return ObjectLocator("action", value)
        if ref_type == "ACTOR":
            return ObjectLocator("actor", value)
    return None


def _locality_passability_edges(document: dict[str, Any]) -> list[ReferenceEdge]:
    """Project the unscoped locality Fact-key contract onto scoped Node Facts.

    Locality stores only a Fact key, while runtime validation accepts that key
    when it exists on a Node.  Keep the delete guard conservative by exposing
    one edge for every matching authored Node Fact; this does not change the
    runtime contract or mutate the document.
    """

    metadata = document.get("metadata")
    locality = metadata.get("locality") if isinstance(metadata, dict) else None
    fact_key = locality.get("passability_fact_key") if isinstance(locality, dict) else None
    if not isinstance(fact_key, str) or not fact_key:
        return []
    edges: list[ReferenceEdge] = []
    for node in _collection(document, "node") or []:
        if not isinstance(node, dict):
            continue
        node_key = _object_key("node", node)
        facts = node.get("facts")
        if not isinstance(node_key, str) or not isinstance(facts, list):
            continue
        if any(isinstance(fact, dict) and fact.get("key") == fact_key for fact in facts):
            edges.append(
                ReferenceEdge(
                    ObjectLocator("metadata", None, "locality.passability_fact_key"),
                    ObjectLocator("node", node_key, f"facts.{fact_key}"),
                )
            )
    return edges


def _expand_dynamic_fact_edges(
    document: dict[str, Any],
    edges: list[ReferenceEdge],
) -> list[ReferenceEdge]:
    nodes = [
        node
        for node in (_collection(document, "node") or [])
        if isinstance(node, dict) and isinstance(_object_key("node", node), str)
    ]
    expanded: list[ReferenceEdge] = []
    for edge in edges:
        target = edge.target
        if target.object_kind != "node" or target.object_key is not None:
            expanded.append(edge)
            continue
        prefix, separator, fact_key = target.field_path.partition("facts.")
        if prefix or not separator or not fact_key:
            expanded.append(edge)
            continue
        for node in nodes:
            node_key = _object_key("node", node)
            facts = node.get("facts")
            if not isinstance(node_key, str) or not isinstance(facts, list):
                continue
            if any(isinstance(fact, dict) and fact.get("key") == fact_key for fact in facts):
                expanded.append(
                    ReferenceEdge(
                        edge.source,
                        ObjectLocator("node", node_key, target.field_path),
                    )
                )
    return expanded


def reference_index(document: dict[str, Any]) -> tuple[ReferenceEdge, ...]:
    edges: list[ReferenceEdge] = []
    _walk(document, document, (), None, None, edges)
    edges = _expand_dynamic_fact_edges(document, edges)
    edges.extend(_locality_passability_edges(document))
    return tuple(edges)


def _walk(
    document: dict[str, Any],
    value: object,
    path: tuple[str | int, ...],
    source: ObjectLocator | None,
    source_path: tuple[str | int, ...] | None,
    edges: list[ReferenceEdge],
) -> None:
    if isinstance(value, dict):
        current_source, current_source_path = _source_for_path(document, path, source)
        if current_source is None or current_source_path is None:
            current_source_path = source_path
        for key, child in value.items():
            child_path = (*path, key)
            field_path = _relative_field_path(child_path, current_source_path)
            if isinstance(child, list):
                for index, item in enumerate(child):
                    target = _reference_target(document, value, key, item, current_source)
                    if target is not None:
                        edges.append(
                            ReferenceEdge(
                                _edge_source(current_source, field_path),
                                target,
                            )
                        )
                    _walk(
                        document,
                        item,
                        (*child_path, index),
                        current_source,
                        current_source_path,
                        edges,
                    )
            else:
                target = _reference_target(document, value, key, child, current_source)
                if target is not None:
                    edges.append(
                        ReferenceEdge(
                            _edge_source(current_source, field_path),
                            target,
                        )
                    )
                _walk(document, child, child_path, current_source, current_source_path, edges)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk(document, child, (*path, index), source, source_path, edges)


def _rewrite_references(
    value: object,
    *,
    document: dict[str, Any],
    object_kind: str,
    old_key: str,
    new_key: str,
    path: tuple[str | int, ...] = (),
    source: ObjectLocator | None = None,
    source_path: tuple[str | int, ...] | None = None,
) -> None:
    if isinstance(value, dict):
        current_source, current_source_path = _source_for_path(document, path, source)
        if current_source is None or current_source_path is None:
            current_source_path = source_path
        for key, child in list(value.items()):
            if isinstance(child, list):
                for index, item in enumerate(child):
                    target = _reference_target(document, value, key, item, current_source)
                    if _target_matches(target, object_kind, old_key):
                        children = value[key]
                        assert isinstance(children, list)
                        children[index] = new_key
                    else:
                        _rewrite_references(
                            item,
                            document=document,
                            object_kind=object_kind,
                            old_key=old_key,
                            new_key=new_key,
                            path=(*path, key, index),
                            source=current_source,
                            source_path=current_source_path,
                        )
            else:
                target = _reference_target(document, value, key, child, current_source)
                if _target_matches(target, object_kind, old_key):
                    value[key] = new_key
                else:
                    _rewrite_references(
                        child,
                        document=document,
                        object_kind=object_kind,
                        old_key=old_key,
                        new_key=new_key,
                        path=(*path, key),
                        source=current_source,
                        source_path=current_source_path,
                    )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _rewrite_references(
                child,
                document=document,
                object_kind=object_kind,
                old_key=old_key,
                new_key=new_key,
                path=(*path, index),
                source=source,
                source_path=source_path,
            )


def _target_matches(target: ObjectLocator | None, object_kind: str, object_key: str) -> bool:
    return (
        target is not None and target.object_kind == object_kind and target.object_key == object_key
    )


def rename_key(
    document: dict[str, Any],
    *,
    object_kind: str,
    old_key: str,
    new_key: str,
    protected_document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key_field = _KEY_FIELDS.get(object_kind)
    if key_field is None:
        if object_kind == "relation" and _object(document, object_kind, old_key) is not None:
            target = _object(document, object_kind, old_key)
            if target is None or not isinstance(target.get("key"), str):
                raise DraftAuthoringError(
                    "SCENARIO_OBJECT_KIND_UNSUPPORTED",
                    "Only explicit Relation keys can be renamed",
                )
            key_field = "key"
        else:
            raise DraftAuthoringError(
                "SCENARIO_OBJECT_KIND_UNSUPPORTED",
                "Unsupported stable-key object kind",
            )
    target = _object(document, object_kind, old_key)
    if target is None:
        raise DraftAuthoringError("SCENARIO_OBJECT_NOT_FOUND", "The Draft object does not exist")
    if (
        protected_document is not None
        and _object(protected_document, object_kind, old_key) is not None
    ):
        raise DraftAuthoringError(
            "SCENARIO_PUBLISHED_STABLE_KEY_RENAME",
            "published stable key cannot be renamed as display-only change",
        )
    if _object(document, object_kind, new_key) is not None:
        raise DraftAuthoringError("SCENARIO_OBJECT_KEY_CONFLICT", "The new object key is in use")
    changed = deepcopy(document)
    changed_target = _object(changed, object_kind, old_key)
    assert changed_target is not None
    changed_target[key_field] = new_key
    _rewrite_references(
        changed,
        document=changed,
        object_kind=object_kind,
        old_key=old_key,
        new_key=new_key,
    )
    return changed


def delete_object(
    document: dict[str, Any],
    *,
    object_kind: str,
    object_key: str,
) -> dict[str, Any]:
    used_by = tuple(
        edge
        for edge in reference_index(document)
        if edge.target.object_kind == object_kind and edge.target.object_key == object_key
    )
    if used_by:
        raise DraftAuthoringError(
            "SCENARIO_OBJECT_REFERENCED",
            "The Draft object is referenced and cannot be deleted",
            references=used_by,
        )
    collection = _collection(document, object_kind)
    if collection is None:
        raise DraftAuthoringError("SCENARIO_OBJECT_KIND_UNSUPPORTED", "Unsupported object kind")
    retained = [
        item
        for item in collection
        if not isinstance(item, dict) or _object_key(object_kind, item) != object_key
    ]
    if len(retained) == len(collection):
        raise DraftAuthoringError("SCENARIO_OBJECT_NOT_FOUND", "The Draft object does not exist")
    changed = deepcopy(document)
    changed_collection = _collection(changed, object_kind)
    assert changed_collection is not None
    changed_collection[:] = retained
    return changed


def delete_fact(
    document: dict[str, Any],
    *,
    node_key: str,
    fact_key: str,
) -> dict[str, Any]:
    """Delete one Node-scoped Fact without cascading inbound references."""

    used_by = tuple(
        edge
        for edge in reference_index(document)
        if (
            edge.target.object_kind == "node"
            and edge.target.object_key == node_key
            and edge.target.field_path == f"facts.{fact_key}"
        )
    )
    if used_by:
        raise DraftAuthoringError(
            "SCENARIO_FACT_REFERENCED",
            "The Node Fact is referenced and cannot be deleted",
            references=used_by,
        )

    node = _object(document, "node", node_key)
    if node is None or not isinstance(node.get("facts"), list):
        raise DraftAuthoringError("SCENARIO_FACT_NOT_FOUND", "The Node Fact does not exist")
    facts = node["facts"]
    assert isinstance(facts, list)
    retained = [
        item
        for item in facts
        if not isinstance(item, dict) or item.get("key") != fact_key
    ]
    if len(retained) == len(facts):
        raise DraftAuthoringError("SCENARIO_FACT_NOT_FOUND", "The Node Fact does not exist")
    changed = deepcopy(document)
    changed_node = _object(changed, "node", node_key)
    assert changed_node is not None
    changed_node["facts"] = retained
    return changed


def locator_for_path(document: dict[str, Any], path: str) -> ObjectLocator | None:
    """Map a validator path to the nearest real editor object.

    The validator remains the only producer of semantic diagnostics.  This
    adapter only supplies navigation metadata and returns ``None`` when a path
    cannot identify a concrete object, allowing the UI to retain the raw path.
    """

    parts = tuple(part for part in path.split(".") if part != "")
    for object_kind, collection_path in _COLLECTIONS.items():
        prefix = collection_path
        if parts[: len(prefix)] != prefix:
            continue
        remainder = parts[len(prefix) :]
        if not remainder:
            return None
        index_or_key = remainder[0]
        collection = _collection(document, object_kind)
        if collection is None:
            return None
        item: object
        consumed = 1
        if index_or_key.isdigit():
            index = int(index_or_key)
            if index >= len(collection):
                return None
            item = collection[index]
        else:
            item = next(
                (
                    candidate
                    for candidate in collection
                    if isinstance(candidate, dict)
                    and _object_key(object_kind, candidate) == index_or_key
                ),
                None,
            )
        if not isinstance(item, dict):
            return None
        key = _object_key(object_kind, item)
        if key is None:
            return None
        field_path = _path(tuple(remainder[consumed:])) or None
        return ObjectLocator(object_kind, key, field_path)

    if parts and parts[0] in _ROOT_OBJECTS:
        root = parts[0]
        if len(parts) == 1:
            return ObjectLocator(_ROOT_OBJECTS[root], None, None)
        return ObjectLocator(_ROOT_OBJECTS[root], None, _path(parts[1:]))
    return None


__all__ = [
    "DraftAuthoringError",
    "ObjectLocator",
    "ReferenceEdge",
    "delete_fact",
    "delete_object",
    "locator_for_path",
    "reference_index",
    "rename_key",
]
