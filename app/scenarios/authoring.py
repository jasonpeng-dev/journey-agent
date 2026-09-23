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

# Root collections are deliberately kept as nested ScenarioDefinition data.
# Their identities are still explicit authoring identities, so mutations must
# use this table rather than array indexes or display labels.
_ROOT_COLLECTIONS: dict[str, tuple[str, str, str]] = {
    "resource_source_hints": ("public_knowledge", "resource_source_hints", "resource_key"),
    "recovery_hints": ("planning", "recovery_hints", "failure_code"),
    "resource_initial_states": ("initialization", "resource_initial_states", "resource_key"),
    "resource_pools": ("initialization", "resource_pools", "pool_key"),
    "region_resource_knowledge": (
        "initialization",
        "region_resource_knowledge",
        "region_key",
    ),
}

_NESTED_COLLECTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("action", "parameters"): ("action_parameter", "key"),
    ("action", "expected_outcomes"): ("action_outcome", "code"),
    ("action", "operation_bindings"): ("action_binding", "role"),
    ("action", "target_actor_roles"): ("action_target_role", "composite"),
    ("action", "authority_policy.autonomous_limits"): (
        "action_authority_limit",
        "parameter_key",
    ),
    ("action", "authority_policy.approval_required_values"): (
        "action_authority_approval",
        "parameter_key",
    ),
    ("actor", "doctrine"): ("actor_doctrine", "key"),
    ("actor", "authority_policy.autonomous_limits"): (
        "actor_authority_limit",
        "parameter_key",
    ),
    ("actor", "authority_policy.approval_required_values"): (
        "actor_authority_approval",
        "parameter_key",
    ),
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


def _root_collection(document: dict[str, Any], collection_name: str) -> list[Any] | None:
    spec = _ROOT_COLLECTIONS.get(collection_name)
    if spec is None:
        return None
    root_name, field_name, _identity_field = spec
    root = document.get(root_name)
    if not isinstance(root, dict):
        return None
    value = root.get(field_name)
    return value if isinstance(value, list) else None


def _root_collection_identity(collection_name: str, value: dict[str, Any]) -> str | None:
    spec = _ROOT_COLLECTIONS.get(collection_name)
    if spec is None:
        return None
    _root_name, _field_name, identity_field = spec
    identity = value.get(identity_field)
    if collection_name == "resource_initial_states":
        scope = value.get("scope_node_key")
        if scope is not None and not isinstance(scope, str):
            return None
        if isinstance(identity, str) and identity:
            return f"{identity}:{scope or ''}"
        return None
    return identity if isinstance(identity, str) and identity else None


def _root_collection_item(
    document: dict[str, Any], collection_name: str, identity: str
) -> tuple[list[Any], int, dict[str, Any]] | None:
    collection = _root_collection(document, collection_name)
    if collection is None:
        return None
    for index, item in enumerate(collection):
        if isinstance(item, dict) and _root_collection_identity(collection_name, item) == identity:
            return collection, index, item
    return None


def _nested_collection_values(
    parent: dict[str, Any], collection_path: str
) -> list[Any] | None:
    current: object = parent
    for part in collection_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current if isinstance(current, list) else None


def _nested_identity(kind: str, item: dict[str, Any]) -> str | None:
    for (_parent_kind, _collection_path), (
        nested_kind,
        identity_field,
    ) in _NESTED_COLLECTIONS.items():
        if nested_kind != kind:
            continue
        if identity_field == "composite":
            target = item.get("target_key")
            role = item.get("required_actor_role_key")
            if isinstance(target, str) and target and isinstance(role, str) and role:
                return f"{target}:{role}"
            return None
        identity = item.get(identity_field)
        return identity if isinstance(identity, str) and identity else None
    return None


def _nested_locator(
    document: dict[str, Any],
    parent_kind: str,
    parent_key: str,
    collection_path: str,
    nested_key: str,
) -> ObjectLocator | None:
    parent = _object(document, parent_kind, parent_key)
    if parent is None:
        return None
    spec = _NESTED_COLLECTIONS.get((parent_kind, collection_path))
    if spec is None:
        return None
    nested_kind, _identity_field = spec
    items = _nested_collection_values(parent, collection_path)
    if items is None:
        return None
    if any(
        isinstance(item, dict) and _nested_identity(nested_kind, item) == nested_key
        for item in items
    ):
        return ObjectLocator(nested_kind, f"{parent_key}:{nested_key}", collection_path)
    return None


def _safe_reference_index(document: dict[str, Any]) -> tuple[ReferenceEdge, ...]:
    """Keep authoring preflight fail-closed if raw shape analysis itself fails."""

    try:
        return reference_index(document)
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise DraftAuthoringError(
            "SCENARIO_REFERENCE_ANALYSIS_FAILED",
            "The working copy could not be analyzed safely for references",
        ) from exc


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


def _action_key_for_source(document: dict[str, Any], source: ObjectLocator | None) -> str | None:
    if source is None:
        return None
    if source.object_kind == "action":
        return source.object_key
    if source.object_kind != "rule" or source.object_key is None:
        return None
    rule = _object(document, "rule", source.object_key)
    action_key = rule.get("action_key") if isinstance(rule, dict) else None
    return action_key if isinstance(action_key, str) and action_key else None


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


def _node_has_fact(node: dict[str, Any], fact_key: str) -> bool:
    facts = node.get("facts")
    return isinstance(facts, list) and any(
        isinstance(fact, dict) and fact.get("key") == fact_key for fact in facts
    )


def _fact_initial_value(node: dict[str, Any], fact_key: str) -> object:
    facts = node.get("facts")
    if not isinstance(facts, list):
        return None
    for fact in facts:
        if isinstance(fact, dict) and fact.get("key") == fact_key:
            return fact.get("initial_value")
    return None


def _fact_can_change(value: object, fact_key: str) -> bool:
    if isinstance(value, dict):
        if value.get("kind") == "SET_FACT" and value.get("fact_key") == fact_key:
            return True
        return any(_fact_can_change(child, fact_key) for child in value.values())
    if isinstance(value, list):
        return any(_fact_can_change(child, fact_key) for child in value)
    return False


def _action_target_node_keys(
    document: dict[str, Any], source: ObjectLocator | None
) -> set[str] | None:
    action_key = _action_key_for_source(document, source)
    action = _object(document, "action", action_key) if action_key is not None else None
    if not isinstance(action, dict) or action.get("target_kind", "NODE") != "NODE":
        return None

    roles = action.get("target_actor_roles")
    explicit: set[str] = set()
    if isinstance(roles, list):
        explicit = {
            target_key
            for role in roles
            if isinstance(role, dict)
            and isinstance((target_key := role.get("target_key")), str)
            and target_key
        }
    if explicit:
        return explicit

    node_type_keys = action.get("target_node_type_keys")
    if not isinstance(node_type_keys, list) or not node_type_keys:
        return None
    allowed_types = {key for key in node_type_keys if isinstance(key, str) and key}
    return {
        node_key
        for node in (_collection(document, "node") or [])
        if isinstance(node, dict)
        and isinstance((node_key := _object_key("node", node)), str)
        and node.get("node_type_key") in allowed_types
    }


def _fact_targets(
    document: dict[str, Any],
    container: dict[str, Any],
    fact_key: str,
    source: ObjectLocator | None,
    *,
    required_fact: bool = False,
) -> tuple[ObjectLocator, ...]:
    direct = _fact_target(container, fact_key, source, required_fact=required_fact)
    if direct is None or direct.object_key is not None:
        return (direct,) if direct is not None else ()

    selector = container.get("node")
    selector_kind = selector.get("kind") if isinstance(selector, dict) else None
    target_relative = selector_kind == "CURRENT_TARGET" or (
        selector is None and _action_key_for_source(document, source) is not None
    )
    if not target_relative:
        return (direct,)

    candidate_keys = _action_target_node_keys(document, source)
    if candidate_keys is None:
        # A genuinely dynamic selector remains conservative.  The wildcard is
        # expanded by reference_index to every authored Node carrying the Fact.
        return (direct,)

    candidates = [
        node
        for node in (_collection(document, "node") or [])
        if isinstance(node, dict)
        and _object_key("node", node) in candidate_keys
        and _node_has_fact(node, fact_key)
    ]

    # An immutable authored discriminator can prove that a target-relative
    # condition does not consume a sibling Node's same-key Fact.  If any rule
    # can mutate that Fact, retain all action candidates instead.
    if not _fact_can_change(document.get("rules"), fact_key):
        kind = container.get("kind")
        if kind == "FACT_EQUALS" and "value" in container:
            expected = container.get("value")
            candidates = [
                node for node in candidates if _fact_initial_value(node, fact_key) == expected
            ]
        elif kind == "FACT_IN" and isinstance(container.get("values"), list):
            expected_values = container["values"]
            candidates = [
                node
                for node in candidates
                if _fact_initial_value(node, fact_key) in expected_values
            ]

    return tuple(
        ObjectLocator("node", node_key, f"facts.{fact_key}")
        for node in candidates
        if isinstance((node_key := _object_key("node", node)), str)
    )


def _reference_targets(
    document: dict[str, Any],
    container: dict[str, Any],
    field: str,
    value: object,
    source: ObjectLocator | None,
) -> tuple[ObjectLocator, ...]:
    if not isinstance(value, str) or not value:
        return ()
    if field == "fact_key":
        return _fact_targets(document, container, value, source)
    if field == "required_fact_key":
        return _fact_targets(document, container, value, source, required_fact=True)
    target = _reference_target(document, container, field, value, source)
    return (target,) if target is not None else ()


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
    if field in {"parameter_key", "outcome_code"}:
        action_key = _action_key_for_source(document, source)
        if action_key is None:
            return None
        nested_kind = "action_parameter" if field == "parameter_key" else "action_outcome"
        nested_key = f"{action_key}:{value}"
        return ObjectLocator(nested_kind, nested_key, field)
    if field == "failure_code" and source is not None and source.object_kind != "planning":
        return ObjectLocator("planning", None, f"recovery_hints.{value}")
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
                    for target in _reference_targets(document, value, key, item, current_source):
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
                for target in _reference_targets(document, value, key, child, current_source):
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
        for edge in _safe_reference_index(document)
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
        for edge in _safe_reference_index(document)
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


def delete_root_collection_item(
    document: dict[str, Any],
    *,
    collection: str,
    identity: str,
) -> dict[str, Any]:
    """Delete one explicitly identified root collection item.

    Root collections remain nested in the canonical document.  This helper is
    the only authoring delete path for identity-bearing pool/recovery/knowledge
    rows, so the UI cannot accidentally splice by an array index when the
    reference preflight is unavailable.
    """

    item = _root_collection_item(document, collection, identity)
    if item is None:
        raise DraftAuthoringError(
            "SCENARIO_ROOT_COLLECTION_ITEM_NOT_FOUND",
            "The requested root collection item does not exist",
        )
    used_by = tuple(
        edge
        for edge in _safe_reference_index(document)
        if (
            (collection == "resource_pools"
             and edge.target.object_kind == "initialization"
             and edge.target.field_path == f"resource_pools.{identity}")
            or (collection == "recovery_hints"
                and edge.target.object_kind == "planning"
                and edge.target.field_path == f"recovery_hints.{identity}")
        )
        and not (
            edge.source.object_kind
            == ("initialization" if collection == "resource_pools" else "planning")
            and edge.source.field_path in {
                f"{collection}.{index}.{_ROOT_COLLECTIONS[collection][2]}"
                for index in range(len(_root_collection(document, collection) or ()))
            }
        )
    )
    if used_by:
        raise DraftAuthoringError(
            "SCENARIO_ROOT_COLLECTION_ITEM_REFERENCED",
            "The root collection item is referenced and cannot be deleted",
            references=used_by,
        )
    _collection_values, _index, _value = item
    changed = deepcopy(document)
    changed_item = _root_collection_item(changed, collection, identity)
    if changed_item is None:
        raise DraftAuthoringError(
            "SCENARIO_ROOT_COLLECTION_ITEM_NOT_FOUND",
            "The requested root collection item does not exist",
        )
    changed_item[0].pop(changed_item[1])
    return changed


def delete_nested_object(
    document: dict[str, Any],
    *,
    parent_kind: str,
    parent_key: str,
    collection: str,
    nested_key: str,
) -> dict[str, Any]:
    """Delete a supported externally identifiable nested row by scoped identity."""

    spec = _NESTED_COLLECTIONS.get((parent_kind, collection))
    if spec is None:
        raise DraftAuthoringError(
            "SCENARIO_NESTED_IDENTITY_UNSUPPORTED",
            "This nested row has no safe scoped identity operation",
        )
    nested_kind, _identity_field = spec
    locator = _nested_locator(document, parent_kind, parent_key, collection, nested_key)
    if locator is None:
        raise DraftAuthoringError(
            "SCENARIO_NESTED_OBJECT_NOT_FOUND",
            "The requested nested row does not exist",
        )
    used_by = tuple(
        edge
        for edge in _safe_reference_index(document)
        if edge.target.object_kind == nested_kind
        and edge.target.object_key == locator.object_key
    )
    if used_by:
        raise DraftAuthoringError(
            "SCENARIO_NESTED_OBJECT_REFERENCED",
            "The nested row is referenced and cannot be deleted",
            references=used_by,
        )
    changed = deepcopy(document)
    parent = _object(changed, parent_kind, parent_key)
    if parent is None:
        raise DraftAuthoringError(
            "SCENARIO_OBJECT_NOT_FOUND",
            "The owning object does not exist",
        )
    values = _nested_collection_values(parent, collection)
    if values is None:
        raise DraftAuthoringError(
            "SCENARIO_NESTED_OBJECT_NOT_FOUND",
            "The requested nested row does not exist",
        )
    retained = [
        item
        for item in values
        if not isinstance(item, dict) or _nested_identity(nested_kind, item) != nested_key
    ]
    if len(retained) == len(values):
        raise DraftAuthoringError(
            "SCENARIO_NESTED_OBJECT_NOT_FOUND",
            "The requested nested row does not exist",
        )
    if collection.startswith("authority_policy."):
        policy = parent.get("authority_policy")
        if not isinstance(policy, dict):
            raise DraftAuthoringError(
                "SCENARIO_NESTED_OBJECT_NOT_FOUND",
                "The owning authority policy does not exist",
            )
        policy[collection.split(".", 1)[1]] = retained
    else:
        parent[collection] = retained
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
    "delete_nested_object",
    "delete_object",
    "delete_root_collection_item",
    "locator_for_path",
    "reference_index",
    "rename_key",
]
