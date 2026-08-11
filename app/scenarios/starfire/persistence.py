"""Compatibility mapping from canonical Starfire definitions to the legacy ORM."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import NodeStatus, NodeType
from app.domain.world import AccessState, NodeDefinition, WorldNodeType
from app.scenarios.starfire.definition import STARFIRE_WORLD

_PERSISTED_NODE_TYPES = {
    WorldNodeType.HEADQUARTERS: NodeType.START,
    WorldNodeType.LOCATION: NodeType.EVENT,
    WorldNodeType.SETTLEMENT: NodeType.NPC,
    WorldNodeType.FACILITY: NodeType.EVENT,
    WorldNodeType.ROUTE: NodeType.EVENT,
}

_PERSISTED_ACCESS_STATES = {
    AccessState.LOCKED: NodeStatus.LOCKED,
    AccessState.AVAILABLE: NodeStatus.AVAILABLE,
}


@dataclass(frozen=True, slots=True)
class PersistedNodeSpec:
    key: str
    name: str
    description: str
    node_type: NodeType
    default_status: NodeStatus


def persisted_node_specs() -> tuple[PersistedNodeSpec, ...]:
    return tuple(_persisted_node(node) for node in STARFIRE_WORLD.nodes)


def _persisted_node(node: NodeDefinition) -> PersistedNodeSpec:
    return PersistedNodeSpec(
        key=node.key,
        name=node.name,
        description=node.description,
        node_type=_PERSISTED_NODE_TYPES[node.node_type],
        default_status=_PERSISTED_ACCESS_STATES[node.initial_access],
    )
