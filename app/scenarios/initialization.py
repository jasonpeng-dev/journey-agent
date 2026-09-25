"""Authoring ownership and read-only bootstrap projection for ScenarioDefinitionV2.

The module deliberately owns no persisted state.  It explains and projects the
canonical Scenario document consumed by :mod:`app.services.runtime_initialization`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from app.domain.enums import (
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.resources import resource_pool_initial_states
from app.domain.scenario_v2 import (
    DerivedDependencyKind,
    ScenarioDefinitionV2,
    StrictScalar,
    relation_identity,
)


class FieldUiOwner(StrEnum):
    DESIGN_ONLY = "DESIGN_ONLY"
    INITIALIZATION_ONLY = "INITIALIZATION_ONLY"
    SHARED_CONTEXT_READONLY = "SHARED_CONTEXT_READONLY"
    SYSTEM = "SYSTEM"
    LEGACY = "LEGACY"
    DERIVED_READONLY = "DERIVED_READONLY"


class BootstrapValueSource(StrEnum):
    EXPLICIT = "EXPLICIT"
    DEFAULT = "DEFAULT"
    LEGACY_FALLBACK = "LEGACY_FALLBACK"
    ENGINE = "ENGINE"
    DERIVED = "DERIVED"
    MISSING = "MISSING"
    INVALID = "INVALID"


class BootstrapSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


@dataclass(frozen=True, slots=True)
class BootstrapFinding:
    identity: str
    label: str
    canonical_path: str
    owner: FieldUiOwner
    source: BootstrapValueSource
    severity: BootstrapSeverity
    value: object
    locator: dict[str, str | None]
    message: str = ""


_INITIALIZATION_SUFFIXES = {
    "initial_access",
    "initial_visibility",
    "initial_value",
    "initial_node_key",
    "command_reachability",
}


def field_ui_owner(path: str) -> FieldUiOwner:
    """Return the single editable UI owner for a canonical schema path.

    Collection indexes may be expressed as ``[]`` or concrete numeric indexes.
    Unknown future fields intentionally fall into Design: a coverage test can
    then require an editor/advanced owner instead of silently treating them as
    bootstrap values based only on their spelling.
    """

    normalized = path.replace("[", ".").replace("]", "").replace("..", ".")
    parts = tuple(part for part in normalized.split(".") if part and not part.isdigit())
    if not parts:
        return FieldUiOwner.SYSTEM
    if parts[0] in {"schema_version", "engine_contract"}:
        return FieldUiOwner.SYSTEM
    if parts[0] == "objectives":
        return FieldUiOwner.LEGACY
    if parts[:2] == ("initialization", "resource_initial_states"):
        return FieldUiOwner.LEGACY
    if parts[:2] == ("world", "resources") and parts[-1] == "initial_value":
        return FieldUiOwner.LEGACY
    if parts[0] == "initialization":
        return FieldUiOwner.INITIALIZATION_ONLY
    if parts[:2] == ("world", "nodes") and parts[-1] in _INITIALIZATION_SUFFIXES:
        return FieldUiOwner.INITIALIZATION_ONLY
    if parts[:2] == ("world", "relations") and parts[-1] == "initial_visibility":
        return FieldUiOwner.INITIALIZATION_ONLY
    if parts[:2] == ("actors", "actor_profiles") and parts[-1] in {
        "initial_node_key",
        "command_reachability",
    }:
        return FieldUiOwner.INITIALIZATION_ONLY
    return FieldUiOwner.DESIGN_ONLY


def schema_field_ownership() -> dict[str, FieldUiOwner]:
    """Enumerate the closed v2 schema and force every leaf through the contract."""

    schema = ScenarioDefinitionV2.model_json_schema(mode="validation")
    definitions = schema.get("$defs", {})
    result: dict[str, FieldUiOwner] = {}

    def walk(node: object, path: str, ref_stack: tuple[str, ...] = ()) -> None:
        if not isinstance(node, dict):
            return
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            name = reference.rsplit("/", 1)[-1]
            if name not in ref_stack:
                walk(definitions.get(name), path, (*ref_stack, name))
            return
        properties = node.get("properties")
        if isinstance(properties, dict):
            for name, child in properties.items():
                child_path = f"{path}.{name}" if path else name
                walk(child, child_path, ref_stack)
            return
        items = node.get("items")
        if isinstance(items, dict):
            walk(items, f"{path}[]", ref_stack)
            return
        variants = node.get("anyOf") or node.get("oneOf")
        if isinstance(variants, list):
            for variant in variants:
                walk(variant, path, ref_stack)
            if path and not any(
                isinstance(item, dict) and ("$ref" in item or "properties" in item)
                for item in variants
            ):
                result[path] = field_ui_owner(path)
            return
        if path:
            result[path] = field_ui_owner(path)

    walk(schema, "")
    return result


def _locator(
    *, section: str, kind: str | None = None, key: str | None = None, field: str | None = None
) -> dict[str, str | None]:
    return {"section": section, "object_kind": kind, "object_key": key, "field_path": field}


def _has(raw: dict[str, Any], *path: str) -> bool:
    current: object = raw
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _finding(
    identity: str,
    label: str,
    path: str,
    source: BootstrapValueSource,
    value: object,
    locator: dict[str, str | None],
    *,
    owner: FieldUiOwner = FieldUiOwner.INITIALIZATION_ONLY,
    message: str = "",
) -> BootstrapFinding:
    severity = (
        BootstrapSeverity.BLOCKING
        if source in {BootstrapValueSource.MISSING, BootstrapValueSource.INVALID}
        else BootstrapSeverity.WARNING
        if source == BootstrapValueSource.LEGACY_FALLBACK
        else BootstrapSeverity.INFO
    )
    return BootstrapFinding(identity, label, path, owner, source, severity, value, locator, message)


def analyze_bootstrap(
    definition: ScenarioDefinitionV2, raw_document: dict[str, Any] | None = None
) -> tuple[BootstrapFinding, ...]:
    """Explain every runtime bootstrap source without mutating the definition."""

    raw = raw_document or definition.model_dump(mode="json", exclude_none=True)
    findings: list[BootstrapFinding] = [
        _finding(
            "entry:start-node",
            "Start node",
            "initialization.start_node_key",
            BootstrapValueSource.EXPLICIT,
            definition.initialization.start_node_key,
            _locator(section="initialization", field="initialization.start_node_key"),
        ),
        _finding(
            "entry:primary-actor",
            "Primary actor",
            "initialization.primary_actor_key",
            BootstrapValueSource.EXPLICIT,
            definition.initialization.primary_actor_key,
            _locator(section="initialization", field="initialization.primary_actor_key"),
        ),
    ]
    raw_nodes = raw.get("world", {}).get("nodes", []) if isinstance(raw.get("world"), dict) else []
    raw_node_by_key = {
        item.get("key"): item for item in raw_nodes if isinstance(item, dict) and item.get("key")
    }
    for node in definition.world.nodes:
        node_raw = raw_node_by_key.get(node.key, {})
        locator = _locator(section="world-entities", kind="node", key=node.key)
        for field_name, node_value in (
            ("initial_access", node.initial_access.value),
            ("initial_visibility", node.initial_visibility.value),
        ):
            findings.append(
                _finding(
                    f"node:{node.key}:{field_name}",
                    f"{node.name} · {field_name}",
                    f"world.nodes[].{field_name}",
                    BootstrapValueSource.EXPLICIT
                    if isinstance(node_raw, dict) and field_name in node_raw
                    else BootstrapValueSource.MISSING,
                    node_value,
                    {**locator, "field_path": field_name},
                )
            )
        raw_facts = node_raw.get("facts", []) if isinstance(node_raw, dict) else []
        raw_fact_by_key = {
            item.get("key"): item
            for item in raw_facts
            if isinstance(item, dict) and item.get("key")
        }
        for fact in node.facts:
            fact_raw = raw_fact_by_key.get(fact.key, {})
            for field_name, fact_value in (
                ("initial_value", fact.initial_value),
                ("initial_visibility", fact.initial_visibility.value),
            ):
                findings.append(
                    _finding(
                        f"fact:{node.key}:{fact.key}:{field_name}",
                        f"{node.name} / {fact.name} · {field_name}",
                        f"world.nodes[].facts[].{field_name}",
                        BootstrapValueSource.EXPLICIT
                        if isinstance(fact_raw, dict) and field_name in fact_raw
                        else BootstrapValueSource.MISSING,
                        fact_value,
                        {**locator, "field_path": f"facts.{fact.key}.{field_name}"},
                    )
                )
    raw_relations = (
        raw.get("world", {}).get("relations", []) if isinstance(raw.get("world"), dict) else []
    )
    for index, relation in enumerate(definition.world.relations):
        relation_raw = raw_relations[index] if index < len(raw_relations) else {}
        key = relation_identity(relation)
        findings.append(
            _finding(
                f"relation:{key}:initial_visibility",
                f"Relation {key} · initial visibility",
                "world.relations[].initial_visibility",
                BootstrapValueSource.EXPLICIT
                if isinstance(relation_raw, dict) and "initial_visibility" in relation_raw
                else BootstrapValueSource.DEFAULT,
                relation.initial_visibility.value,
                _locator(
                    section="relations",
                    kind="relation",
                    key=key,
                    field="initial_visibility",
                ),
            )
        )
    raw_actors = (
        raw.get("actors", {}).get("actor_profiles", [])
        if isinstance(raw.get("actors"), dict)
        else []
    )
    raw_actor_by_key = {
        item.get("key"): item for item in raw_actors if isinstance(item, dict) and item.get("key")
    }
    for actor in definition.actors.actor_profiles:
        actor_raw = raw_actor_by_key.get(actor.key, {})
        locator = _locator(section="actors", kind="actor", key=actor.key)
        findings.extend(
            (
                _finding(
                    f"actor:{actor.key}:initial_node_key",
                    f"{actor.name} · initial location",
                    "actors.actor_profiles[].initial_node_key",
                    BootstrapValueSource.EXPLICIT,
                    actor.initial_node_key,
                    {**locator, "field_path": "initial_node_key"},
                ),
                _finding(
                    f"actor:{actor.key}:command_reachability",
                    f"{actor.name} · command reachability",
                    "actors.actor_profiles[].command_reachability",
                    BootstrapValueSource.EXPLICIT
                    if isinstance(actor_raw, dict) and "command_reachability" in actor_raw
                    else BootstrapValueSource.DEFAULT,
                    actor.command_reachability.value,
                    {**locator, "field_path": "command_reachability"},
                ),
                _finding(
                    f"actor:{actor.key}:runtime_status",
                    f"{actor.name} · runtime status",
                    "runtime.actor.status",
                    BootstrapValueSource.ENGINE,
                    "ACTIVE",
                    locator,
                    owner=FieldUiOwner.SYSTEM,
                ),
            )
        )
    raw_initialization = raw.get("initialization", {})
    raw_pools = (
        raw_initialization.get("resource_pools", []) if isinstance(raw_initialization, dict) else []
    )
    raw_legacy = (
        raw_initialization.get("resource_initial_states", [])
        if isinstance(raw_initialization, dict)
        else []
    )
    explicit_pool_keys = {item.pool_key for item in definition.initialization.resource_pools}
    explicit_pools_by_key = {
        item.pool_key: item for item in definition.initialization.resource_pools
    }
    legacy_resources = {
        item.resource_key for item in definition.initialization.resource_initial_states
    }
    for pool in resource_pool_initial_states(definition):
        source = (
            BootstrapValueSource.EXPLICIT
            if pool.pool_key in explicit_pool_keys
            else BootstrapValueSource.LEGACY_FALLBACK
        )
        authored_pool = explicit_pools_by_key.get(pool.pool_key)
        findings.append(
            _finding(
                f"pool:{pool.pool_key}:{pool.resource_key}:{pool.region_key or 'global'}",
                f"Resource pool · {pool.pool_key}",
                "initialization.resource_pools[]"
                if source == BootstrapValueSource.EXPLICIT
                else "initialization.resource_initial_states[]"
                if pool.resource_key in legacy_resources and raw_legacy
                else "world.resources[].initial_value",
                source,
                {
                    "pool_key": pool.pool_key,
                    "resource_key": pool.resource_key,
                    "region_key": authored_pool.region_key if authored_pool else pool.region_key,
                    "facility_key": authored_pool.facility_key if authored_pool else None,
                    "quantity": pool.quantity,
                    "reserved": pool.reserved_value,
                    "availability": pool.availability.value,
                    "visibility": pool.visibility.value,
                    "survey_discoverable": authored_pool.survey_discoverable
                    if authored_pool
                    else False,
                    "availability_requirement": (
                        authored_pool.availability_requirement.model_dump(
                            mode="json", exclude_none=True
                        )
                        if authored_pool and authored_pool.availability_requirement
                        else None
                    ),
                },
                _locator(
                    section="initialization",
                    kind="resource_pool",
                    key=pool.pool_key,
                    field="initialization.resource_pools",
                ),
                message="Compatibility source; current authoring should use resource_pools."
                if source == BootstrapValueSource.LEGACY_FALLBACK
                else "",
            )
        )
    configured_regions = {
        item.region_key: item for item in definition.initialization.region_resource_knowledge
    }
    region_type = definition.metadata.locality.region_node_type_key
    regions = [node for node in definition.world.nodes if node.node_type_key == region_type]
    for region in regions:
        region_config = configured_regions.get(region.key)
        findings.append(
            _finding(
                f"region-knowledge:{region.key}",
                f"{region.name} · resource knowledge",
                "initialization.region_resource_knowledge[]",
                BootstrapValueSource.EXPLICIT
                if region_config is not None
                else BootstrapValueSource.DEFAULT,
                {
                    "visibility": (
                        region_config.resource_inventory_visibility.value
                        if region_config
                        else "VISIBLE"
                    ),
                    "survey_completed": region_config.resource_survey_completed
                    if region_config
                    else True,
                },
                _locator(
                    section="initialization",
                    kind="region_resource_knowledge",
                    key=region.key,
                    field="initialization.region_resource_knowledge",
                ),
            )
        )
    for derived_state in definition.derived_states:
        state = derived_state
        findings.append(
            _finding(
                f"derived:{derived_state.key}",
                f"{state.name} · initial preview",
                f"derived_states.{derived_state.key}",
                BootstrapValueSource.DERIVED,
                None,
                _locator(section="derived-states", kind="derived_state", key=derived_state.key),
                owner=FieldUiOwner.DERIVED_READONLY,
            )
        )
    _ = raw_pools  # documents why presence is intentionally inspected above
    return tuple(findings)


def evaluate_initial_derived_states(
    definition: ScenarioDefinitionV2,
) -> dict[str, dict[str, object]]:
    """Evaluate Derived Truth/Knowledge from the exact authored initial snapshot."""

    truth_facts: dict[tuple[str, str], StrictScalar] = {}
    known_facts: dict[tuple[str, str], StrictScalar] = {}
    for node in definition.world.nodes:
        for fact in node.facts:
            identity = (node.key, fact.key)
            truth_facts[identity] = fact.initial_value
            if fact.initial_visibility.value == "KNOWN":
                known_facts[identity] = fact.initial_value

    truth_resources: dict[tuple[str, str], int] = {}
    known_resources: dict[tuple[str, str], int] = {}
    region_knowledge = {
        item.region_key: item for item in definition.initialization.region_resource_knowledge
    }
    for pool in resource_pool_initial_states(definition):
        if pool.region_key is None or pool.availability != ResourcePoolAvailability.AVAILABLE:
            continue
        identity = (pool.region_key, pool.resource_key)
        available = max(0, pool.quantity - pool.reserved_value)
        truth_resources[identity] = truth_resources.get(identity, 0) + available
        region_knowledge_state = region_knowledge.get(pool.region_key)
        region_known = region_knowledge_state is None or (
            region_knowledge_state.resource_inventory_visibility
            == ResourceInventoryVisibility.VISIBLE
            and region_knowledge_state.resource_survey_completed
        )
        if region_known and pool.visibility == ResourcePoolVisibility.VISIBLE:
            known_resources[identity] = known_resources.get(identity, 0) + available

    states = definition.derived_state_definitions
    truth_cache: dict[str, StrictScalar] = {}
    knowledge_cache: dict[str, StrictScalar | None] = {}

    def gate_known(dependency: Any) -> bool:
        gate = dependency.knowledge_gate
        return (
            gate is None or known_facts.get((gate.node_key, gate.fact_key)) in gate.accepted_values
        )

    def truth(key: str) -> StrictScalar:
        state = states[key]
        statuses: list[bool] = []
        for dependency in state.dependencies:
            if dependency.kind == DerivedDependencyKind.FACT:
                statuses.append(
                    truth_facts.get((dependency.node_key, dependency.fact_key))
                    in dependency.accepted_values
                )
            elif dependency.kind == DerivedDependencyKind.RESOURCE_AT_LEAST:
                statuses.append(
                    truth_resources.get((dependency.region_key, dependency.resource_key), 0)
                    >= (dependency.minimum or 0)
                )
            else:
                assert dependency.derived_key is not None
                statuses.append(truth(dependency.derived_key) in dependency.accepted_values)
        truth_cache[key] = state.available_value if all(statuses) else state.unavailable_value
        return truth_cache[key]

    def knowledge(key: str) -> StrictScalar | None:
        state = states[key]
        statuses: list[bool | None] = []
        for dependency in state.dependencies:
            if not gate_known(dependency):
                statuses.append(None)
            elif dependency.kind == DerivedDependencyKind.FACT:
                value = known_facts.get((dependency.node_key, dependency.fact_key))
                statuses.append(None if value is None else value in dependency.accepted_values)
            elif dependency.kind == DerivedDependencyKind.RESOURCE_AT_LEAST:
                identity = (dependency.region_key, dependency.resource_key)
                amount = known_resources.get(identity)
                statuses.append(None if amount is None else amount >= (dependency.minimum or 0))
            else:
                assert dependency.derived_key is not None
                value = knowledge(dependency.derived_key)
                statuses.append(None if value is None else value in dependency.accepted_values)
        result: bool | None = (
            False if False in statuses else True if all(x is True for x in statuses) else None
        )
        knowledge_cache[key] = (
            state.available_value
            if result is True
            else state.unavailable_value
            if result is False
            else None
        )
        return knowledge_cache[key]

    return {
        key: {
            "truth": truth(key),
            "knowledge": knowledge(key),
            "knowledge_status": "UNKNOWN" if knowledge_cache[key] is None else "KNOWN",
        }
        for key in sorted(states)
    }


def initialization_projection(
    definition: ScenarioDefinitionV2,
    raw_document: dict[str, Any] | None = None,
) -> dict[str, object]:
    """Build the variable-depth Finder projection consumed by the editor."""

    findings = analyze_bootstrap(definition, raw_document)
    node_types = {item.key: item for item in definition.world.node_types}
    roles = {item.key: item for item in definition.actors.roles}
    relation_types = {item.key: item for item in definition.world.relation_types}
    nodes = {item.key: item for item in definition.world.nodes}

    def entity(
        identity: str,
        label: str,
        locator: dict[str, str | None],
        fields: list[str],
        *,
        readonly: bool = False,
        context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "id": identity,
            "label": label,
            "locator": locator,
            "field_ids": fields,
            "readonly": readonly,
            "context": context or {},
        }

    node_groups: list[dict[str, object]] = []
    for type_key in sorted({node.node_type_key for node in definition.world.nodes}):
        node_type = node_types.get(type_key)
        items = []
        for node in definition.world.nodes:
            if node.node_type_key != type_key:
                continue
            field_ids = [
                f"node:{node.key}:initial_access",
                f"node:{node.key}:initial_visibility",
                *[
                    f"fact:{node.key}:{fact.key}:{field}"
                    for fact in node.facts
                    for field in ("initial_value", "initial_visibility")
                ],
            ]
            items.append(
                entity(
                    f"node:{node.key}",
                    node.name,
                    _locator(section="world-entities", kind="node", key=node.key),
                    field_ids,
                    context={"key": node.key, "type": node_type.name if node_type else type_key},
                )
            )
        node_groups.append(
            {
                "id": f"node-type:{type_key}",
                "label": node_type.name if node_type else type_key,
                "items": items,
            }
        )

    actor_groups = []
    for role_key in sorted({actor.role_key for actor in definition.actors.actor_profiles}):
        role = roles.get(role_key)
        items = [
            entity(
                f"actor:{actor.key}",
                actor.name,
                _locator(section="actors", kind="actor", key=actor.key),
                [
                    f"actor:{actor.key}:initial_node_key",
                    f"actor:{actor.key}:command_reachability",
                    f"actor:{actor.key}:runtime_status",
                ],
                context={"key": actor.key, "role": role.name if role else role_key},
            )
            for actor in definition.actors.actor_profiles
            if actor.role_key == role_key
        ]
        actor_groups.append(
            {"id": f"role:{role_key}", "label": role.name if role else role_key, "items": items}
        )

    pools = [item for item in findings if item.identity.startswith("pool:")]
    region_items = [item for item in findings if item.identity.startswith("region-knowledge:")]
    pool_by_identity = {
        f"pool:{pool.pool_key}:{pool.resource_key}:{pool.region_key or 'global'}": pool
        for pool in resource_pool_initial_states(definition)
    }

    def pool_context(item: BootstrapFinding) -> dict[str, object]:
        pool = pool_by_identity.get(item.identity)
        if pool is None:
            return {}
        return {
            "pool_key": pool.pool_key,
            "resource_key": pool.resource_key,
            "region_key": pool.region_key,
            "facility_key": pool.facility_key,
        }

    resource_groups = [
        {
            "id": "resource-pools",
            "label": "Resource pools",
            "items": [
                entity(
                    item.identity,
                    item.label,
                    item.locator,
                    [item.identity],
                    context=pool_context(item),
                )
                for item in pools
                if item.source == BootstrapValueSource.EXPLICIT
            ],
        },
        {
            "id": "region-resource-knowledge",
            "label": "Region resource knowledge",
            "items": [
                entity(
                    item.identity,
                    item.label,
                    item.locator,
                    [item.identity],
                    context={"region_key": item.locator.get("object_key")},
                )
                for item in region_items
            ],
        },
        {
            "id": "compatibility-resources",
            "label": "Compatibility sources",
            "items": [
                entity(
                    item.identity,
                    item.label,
                    item.locator,
                    [item.identity],
                    readonly=True,
                    context=pool_context(item),
                )
                for item in pools
                if item.source == BootstrapValueSource.LEGACY_FALLBACK
            ],
        },
    ]

    relation_groups = []
    for type_key in sorted({item.relation_type_key for item in definition.world.relations}):
        relation_type = relation_types.get(type_key)
        items = []
        for relation in definition.world.relations:
            if relation.relation_type_key != type_key:
                continue
            key = relation_identity(relation)
            items.append(
                entity(
                    f"relation:{key}",
                    (
                        f"{nodes[relation.source_node_key].name}"
                        f" → {nodes[relation.target_node_key].name}"
                    ),
                    _locator(section="relations", kind="relation", key=key),
                    [f"relation:{key}:initial_visibility"],
                    context={
                        "source": relation.source_node_key,
                        "type": relation_type.name if relation_type else type_key,
                        "target": relation.target_node_key,
                    },
                )
            )
        relation_groups.append(
            {
                "id": f"relation-type:{type_key}",
                "label": relation_type.name if relation_type else type_key,
                "items": items,
            }
        )

    derived_values = evaluate_initial_derived_states(definition)
    derived_items = [
        entity(
            f"derived:{state.key}",
            state.name,
            _locator(section="derived-states", kind="derived_state", key=state.key),
            [f"derived:{state.key}"],
            readonly=True,
            context={**derived_values[state.key], "dependencies": len(state.dependencies)},
        )
        for state in definition.derived_states
    ]
    domains = [
        {
            "id": "basic",
            "label": "Basic configuration",
            "groups": [
                {
                    "id": "entry",
                    "label": "Bootstrap entry",
                    "items": [
                        entity(
                            "bootstrap-entry",
                            "Start node & primary actor",
                            _locator(section="initialization"),
                            ["entry:start-node", "entry:primary-actor"],
                        )
                    ],
                }
            ],
        },
        {"id": "nodes", "label": "Nodes", "groups": node_groups},
        {"id": "actors", "label": "Actors", "groups": actor_groups},
        {"id": "resources", "label": "Resources", "groups": resource_groups},
        {"id": "relations", "label": "Relations", "groups": relation_groups},
        {
            "id": "derived",
            "label": "Derived state preview",
            "groups": [{"id": "derived-states", "label": "Derived states", "items": derived_items}],
        },
    ]
    return {
        "domains": domains,
        "findings": [asdict(item) for item in findings],
        "summary": {
            "nodes": len(definition.world.nodes),
            "actors": len(definition.actors.actor_profiles),
            "resource_pools": len(resource_pool_initial_states(definition)),
            "relations": len(definition.world.relations),
            "derived_states": len(definition.derived_states),
            "warnings": sum(item.severity != BootstrapSeverity.INFO for item in findings),
        },
    }


def bootstrap_parity(
    draft: ScenarioDefinitionV2,
    published: ScenarioDefinitionV2 | None,
) -> dict[str, object]:
    """Return semantic Design/Initialization changes, never a raw JSON diff."""

    if published is None:
        return {"published": False, "initialization_changes": [], "design_changes": []}

    def bootstrap_map(definition: ScenarioDefinitionV2) -> dict[str, object]:
        return {item.identity: item.value for item in analyze_bootstrap(definition)}

    before_bootstrap = bootstrap_map(published)
    after_bootstrap = bootstrap_map(draft)
    initialization_changes = sorted(
        key
        for key in before_bootstrap.keys() | after_bootstrap.keys()
        if before_bootstrap.get(key) != after_bootstrap.get(key)
    )

    def design_document(definition: ScenarioDefinitionV2) -> dict[str, object]:
        document = definition.model_dump(mode="json", exclude_none=True)
        document.pop("initialization", None)
        world = document.get("world")
        if isinstance(world, dict):
            for node in world.get("nodes", []):
                if not isinstance(node, dict):
                    continue
                node.pop("initial_access", None)
                node.pop("initial_visibility", None)
                for fact in node.get("facts", []):
                    if isinstance(fact, dict):
                        fact.pop("initial_value", None)
                        fact.pop("initial_visibility", None)
            for relation in world.get("relations", []):
                if isinstance(relation, dict):
                    relation.pop("initial_visibility", None)
            for resource in world.get("resources", []):
                if isinstance(resource, dict):
                    resource.pop("initial_value", None)
        actors = document.get("actors")
        if isinstance(actors, dict):
            for actor in actors.get("actor_profiles", []):
                if isinstance(actor, dict):
                    actor.pop("initial_node_key", None)
                    actor.pop("command_reachability", None)
        return document

    before = design_document(published)
    after = design_document(draft)
    design_changes = []
    for root in (
        "metadata",
        "world",
        "actors",
        "interactions",
        "actions",
        "rules",
        "objectives",
        "derived_states",
        "goal_resolution",
        "planning",
        "public_references",
    ):
        if before.get(root) != after.get(root):
            design_changes.append(root)
    return {
        "published": True,
        "initialization_changes": initialization_changes,
        "design_changes": design_changes,
    }


__all__ = [
    "BootstrapFinding",
    "BootstrapSeverity",
    "BootstrapValueSource",
    "FieldUiOwner",
    "analyze_bootstrap",
    "bootstrap_parity",
    "evaluate_initial_derived_states",
    "field_ui_owner",
    "initialization_projection",
    "schema_field_ownership",
]
