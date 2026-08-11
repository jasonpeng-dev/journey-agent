"""Immutable value objects that describe a scenario world."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.world.types import (
    AccessState,
    FactValue,
    FactValueType,
    RelationType,
    Visibility,
    WorldNodeType,
)

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


def _validate_key(key: str, *, field: str = "key") -> None:
    if not _KEY_PATTERN.fullmatch(key):
        raise ValueError(
            f"{field} must start with a lowercase letter and contain only "
            "lowercase letters, numbers, or underscores"
        )


def _validate_name(name: str) -> None:
    if not name.strip():
        raise ValueError("name must not be blank")


@dataclass(frozen=True, slots=True)
class InteractionDefinition:
    """An action capability that a scenario may attach to one or more nodes."""

    key: str
    name: str
    description: str = ""

    def __post_init__(self) -> None:
        _validate_key(self.key)
        _validate_name(self.name)


@dataclass(frozen=True, slots=True)
class FactDefinition:
    """A typed initial world truth with an independent visibility setting."""

    key: str
    name: str
    value_type: FactValueType
    initial_value: FactValue
    initial_visibility: Visibility = Visibility.KNOWN
    allowed_values: tuple[FactValue, ...] = ()

    def __post_init__(self) -> None:
        _validate_key(self.key)
        _validate_name(self.name)
        object.__setattr__(self, "allowed_values", tuple(self.allowed_values))
        self._validate_value_shape()

    def _validate_value_shape(self) -> None:
        if self.value_type == FactValueType.STRING:
            valid = isinstance(self.initial_value, str)
        elif self.value_type == FactValueType.INTEGER:
            valid = isinstance(self.initial_value, int) and not isinstance(self.initial_value, bool)
        elif self.value_type == FactValueType.BOOLEAN:
            valid = isinstance(self.initial_value, bool)
        else:
            valid = bool(self.allowed_values) and self.initial_value in self.allowed_values

        if not valid:
            raise ValueError("initial_value does not match the declared value_type")
        if self.value_type != FactValueType.ENUM and self.allowed_values:
            raise ValueError("allowed_values are only valid for ENUM facts")
        if len(set(self.allowed_values)) != len(self.allowed_values):
            raise ValueError("allowed_values must not contain duplicates")
        if self.value_type == FactValueType.ENUM and any(
            type(value) is not type(self.initial_value) for value in self.allowed_values
        ):
            raise ValueError("ENUM allowed_values must use one scalar type")


@dataclass(frozen=True, slots=True)
class NodeDefinition:
    """An independently addressable object and its initial scenario configuration."""

    key: str
    name: str
    description: str
    node_type: WorldNodeType
    initial_access: AccessState
    initial_visibility: Visibility
    interactions: tuple[InteractionDefinition, ...] = ()
    facts: tuple[FactDefinition, ...] = ()

    def __post_init__(self) -> None:
        _validate_key(self.key)
        _validate_name(self.name)
        object.__setattr__(self, "interactions", tuple(self.interactions))
        object.__setattr__(self, "facts", tuple(self.facts))
        interaction_keys = [interaction.key for interaction in self.interactions]
        fact_keys = [fact.key for fact in self.facts]
        if len(set(interaction_keys)) != len(interaction_keys):
            raise ValueError("node interactions must use unique keys")
        if len(set(fact_keys)) != len(fact_keys):
            raise ValueError("node facts must use unique keys")

    def supports(self, interaction_key: str) -> bool:
        """Return whether this node advertises an interaction capability."""

        return any(interaction.key == interaction_key for interaction in self.interactions)

    def fact(self, fact_key: str) -> FactDefinition | None:
        """Look up a fact owned by this node without exposing persistence details."""

        return next((fact for fact in self.facts if fact.key == fact_key), None)


@dataclass(frozen=True, slots=True)
class RelationDefinition:
    """A semantic node-to-node link with no embedded rule or effect DSL."""

    source_node_key: str
    relation_type: RelationType
    target_node_key: str

    def __post_init__(self) -> None:
        _validate_key(self.source_node_key, field="source_node_key")
        _validate_key(self.target_node_key, field="target_node_key")


@dataclass(frozen=True, slots=True)
class ResourceDefinition:
    """A bounded numeric resource initialized when a scenario runtime is created."""

    key: str
    name: str
    initial_value: int
    minimum: int = 0
    maximum: int | None = None

    def __post_init__(self) -> None:
        _validate_key(self.key)
        _validate_name(self.name)
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("maximum must be greater than or equal to minimum")
        if self.initial_value < self.minimum:
            raise ValueError("initial_value must be greater than or equal to minimum")
        if self.maximum is not None and self.initial_value > self.maximum:
            raise ValueError("initial_value must be less than or equal to maximum")


@dataclass(frozen=True, slots=True)
class WorldDefinition:
    """A complete, immutable description of one scenario world."""

    key: str
    name: str
    interactions: tuple[InteractionDefinition, ...]
    nodes: tuple[NodeDefinition, ...]
    relations: tuple[RelationDefinition, ...]
    resources: tuple[ResourceDefinition, ...]

    def __post_init__(self) -> None:
        _validate_key(self.key)
        _validate_name(self.name)
        object.__setattr__(self, "interactions", tuple(self.interactions))
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "relations", tuple(self.relations))
        object.__setattr__(self, "resources", tuple(self.resources))
        self._validate_unique_keys()
        self._validate_references()

    def _validate_unique_keys(self) -> None:
        collections = {
            "interactions": [item.key for item in self.interactions],
            "nodes": [item.key for item in self.nodes],
            "resources": [item.key for item in self.resources],
        }
        for label, keys in collections.items():
            if len(set(keys)) != len(keys):
                raise ValueError(f"world {label} must use unique keys")

    def _validate_references(self) -> None:
        interactions = {item.key: item for item in self.interactions}
        node_keys = {item.key for item in self.nodes}
        for node in self.nodes:
            for interaction in node.interactions:
                if interactions.get(interaction.key) != interaction:
                    raise ValueError(
                        f"node {node.key} references an interaction outside the world catalog"
                    )
        for relation in self.relations:
            if relation.source_node_key not in node_keys:
                raise ValueError(f"relation source {relation.source_node_key} is not a world node")
            if relation.target_node_key not in node_keys:
                raise ValueError(f"relation target {relation.target_node_key} is not a world node")

    def node(self, key: str) -> NodeDefinition | None:
        return next((node for node in self.nodes if node.key == key), None)

    def interaction(self, key: str) -> InteractionDefinition | None:
        return next(
            (interaction for interaction in self.interactions if interaction.key == key), None
        )

    def resource(self, key: str) -> ResourceDefinition | None:
        return next((resource for resource in self.resources if resource.key == key), None)
