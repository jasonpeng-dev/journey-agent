"""Stable persisted document schema for Scenario definitions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from app.domain.scenario import BehaviorBundleRef, ScenarioDefinition
from app.domain.world import (
    AccessState,
    FactDefinition,
    FactValueType,
    InteractionDefinition,
    NodeDefinition,
    RelationDefinition,
    RelationType,
    ResourceDefinition,
    Visibility,
    WorldDefinition,
    WorldNodeType,
)
from app.scenarios.contracts import (
    ObjectiveDefinition,
    ObjectivePrerequisite,
    ObjectiveVerificationRequirement,
)

SCENARIO_DOCUMENT_SCHEMA_VERSION = 1
type StrictFactValue = StrictStr | StrictInt | StrictBool


class ScenarioDocumentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BehaviorBundleDocument(ScenarioDocumentModel):
    key: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)


class InteractionDocument(ScenarioDocumentModel):
    key: str
    name: str
    description: str = ""


class FactDocument(ScenarioDocumentModel):
    key: str
    name: str
    value_type: FactValueType
    initial_value: StrictFactValue
    initial_visibility: Visibility
    allowed_values: tuple[StrictFactValue, ...] = ()


class NodeDocument(ScenarioDocumentModel):
    key: str
    name: str
    description: str
    node_type: WorldNodeType
    initial_access: AccessState
    initial_visibility: Visibility
    interaction_keys: tuple[str, ...] = ()
    facts: tuple[FactDocument, ...] = ()


class RelationDocument(ScenarioDocumentModel):
    source_node_key: str
    relation_type: RelationType
    target_node_key: str


class ResourceDocument(ScenarioDocumentModel):
    key: str
    name: str
    initial_value: int
    minimum: int = 0
    maximum: int | None = None


class WorldDocument(ScenarioDocumentModel):
    key: str
    name: str
    interactions: tuple[InteractionDocument, ...]
    nodes: tuple[NodeDocument, ...]
    relations: tuple[RelationDocument, ...]
    resources: tuple[ResourceDocument, ...]


class ObjectiveRequirementDocument(ScenarioDocumentModel):
    key: str
    node_key: str
    fact_key: str
    accepted_values: tuple[str, ...]
    description: str


class ObjectivePrerequisiteDocument(ScenarioDocumentModel):
    key: str
    description: str
    requirements: tuple[ObjectiveRequirementDocument, ...]


class ObjectiveDocument(ScenarioDocumentModel):
    key: str
    name: str
    description: str
    completion_requirements: tuple[ObjectiveRequirementDocument, ...]
    prerequisites: tuple[ObjectivePrerequisiteDocument, ...] = ()
    subsumes: tuple[str, ...] = ()


class ObjectiveCatalogDocument(ScenarioDocumentModel):
    catalog_version: str = Field(min_length=1, max_length=100)
    definitions: tuple[ObjectiveDocument, ...]


class ScenarioDefinitionDocument(ScenarioDocumentModel):
    """Versioned storage schema independent from ORM and mutable Draft metadata."""

    schema_version: Literal[1] = 1
    world: WorldDocument
    objective_catalog: ObjectiveCatalogDocument
    behavior_bundle: BehaviorBundleDocument

    @classmethod
    def from_domain(cls, definition: ScenarioDefinition) -> ScenarioDefinitionDocument:
        return cls(
            world=_world_document(definition.world),
            objective_catalog=ObjectiveCatalogDocument(
                catalog_version=definition.objective_catalog_version,
                definitions=tuple(
                    _objective_document(objective) for objective in definition.objectives
                ),
            ),
            behavior_bundle=BehaviorBundleDocument(
                key=definition.behavior_bundle.key,
                version=definition.behavior_bundle.version,
            ),
        )

    def to_domain(self) -> ScenarioDefinition:
        interactions = {
            item.key: InteractionDefinition(
                key=item.key,
                name=item.name,
                description=item.description,
            )
            for item in self.world.interactions
        }
        world = WorldDefinition(
            key=self.world.key,
            name=self.world.name,
            interactions=tuple(interactions.values()),
            nodes=tuple(_node_definition(node, interactions) for node in self.world.nodes),
            relations=tuple(
                RelationDefinition(
                    source_node_key=relation.source_node_key,
                    relation_type=relation.relation_type,
                    target_node_key=relation.target_node_key,
                )
                for relation in self.world.relations
            ),
            resources=tuple(
                ResourceDefinition(
                    key=resource.key,
                    name=resource.name,
                    initial_value=resource.initial_value,
                    minimum=resource.minimum,
                    maximum=resource.maximum,
                )
                for resource in self.world.resources
            ),
        )
        return ScenarioDefinition(
            world=world,
            objective_catalog_version=self.objective_catalog.catalog_version,
            objectives=tuple(
                _objective_definition(objective) for objective in self.objective_catalog.definitions
            ),
            behavior_bundle=BehaviorBundleRef(
                key=self.behavior_bundle.key,
                version=self.behavior_bundle.version,
            ),
        )


def _world_document(world: WorldDefinition) -> WorldDocument:
    return WorldDocument(
        key=world.key,
        name=world.name,
        interactions=tuple(
            InteractionDocument(
                key=interaction.key,
                name=interaction.name,
                description=interaction.description,
            )
            for interaction in world.interactions
        ),
        nodes=tuple(
            NodeDocument(
                key=node.key,
                name=node.name,
                description=node.description,
                node_type=node.node_type,
                initial_access=node.initial_access,
                initial_visibility=node.initial_visibility,
                interaction_keys=tuple(interaction.key for interaction in node.interactions),
                facts=tuple(
                    FactDocument(
                        key=fact.key,
                        name=fact.name,
                        value_type=fact.value_type,
                        initial_value=fact.initial_value,
                        initial_visibility=fact.initial_visibility,
                        allowed_values=fact.allowed_values,
                    )
                    for fact in node.facts
                ),
            )
            for node in world.nodes
        ),
        relations=tuple(
            RelationDocument(
                source_node_key=relation.source_node_key,
                relation_type=relation.relation_type,
                target_node_key=relation.target_node_key,
            )
            for relation in world.relations
        ),
        resources=tuple(
            ResourceDocument(
                key=resource.key,
                name=resource.name,
                initial_value=resource.initial_value,
                minimum=resource.minimum,
                maximum=resource.maximum,
            )
            for resource in world.resources
        ),
    )


def _node_definition(
    node: NodeDocument,
    interactions: dict[str, InteractionDefinition],
) -> NodeDefinition:
    try:
        node_interactions = tuple(interactions[key] for key in node.interaction_keys)
    except KeyError as exc:
        raise ValueError(f"node {node.key} references unknown interaction {exc.args[0]}") from exc
    return NodeDefinition(
        key=node.key,
        name=node.name,
        description=node.description,
        node_type=node.node_type,
        initial_access=node.initial_access,
        initial_visibility=node.initial_visibility,
        interactions=node_interactions,
        facts=tuple(
            FactDefinition(
                key=fact.key,
                name=fact.name,
                value_type=fact.value_type,
                initial_value=fact.initial_value,
                initial_visibility=fact.initial_visibility,
                allowed_values=fact.allowed_values,
            )
            for fact in node.facts
        ),
    )


def _requirement_document(
    requirement: ObjectiveVerificationRequirement,
) -> ObjectiveRequirementDocument:
    return ObjectiveRequirementDocument(
        key=requirement.key,
        node_key=requirement.node_key,
        fact_key=requirement.fact_key,
        accepted_values=tuple(sorted(requirement.accepted_values)),
        description=requirement.description,
    )


def _objective_document(objective: ObjectiveDefinition) -> ObjectiveDocument:
    return ObjectiveDocument(
        key=objective.key,
        name=objective.name,
        description=objective.description,
        completion_requirements=tuple(
            _requirement_document(requirement) for requirement in objective.completion_requirements
        ),
        prerequisites=tuple(
            ObjectivePrerequisiteDocument(
                key=prerequisite.key,
                description=prerequisite.description,
                requirements=tuple(
                    _requirement_document(requirement) for requirement in prerequisite.requirements
                ),
            )
            for prerequisite in objective.prerequisites
        ),
        subsumes=tuple(sorted(objective.subsumes)),
    )


def _requirement_definition(
    requirement: ObjectiveRequirementDocument,
) -> ObjectiveVerificationRequirement:
    return ObjectiveVerificationRequirement(
        key=requirement.key,
        node_key=requirement.node_key,
        fact_key=requirement.fact_key,
        accepted_values=frozenset(requirement.accepted_values),
        description=requirement.description,
    )


def _objective_definition(objective: ObjectiveDocument) -> ObjectiveDefinition:
    return ObjectiveDefinition(
        key=objective.key,
        name=objective.name,
        description=objective.description,
        completion_requirements=tuple(
            _requirement_definition(requirement)
            for requirement in objective.completion_requirements
        ),
        prerequisites=tuple(
            ObjectivePrerequisite(
                key=prerequisite.key,
                description=prerequisite.description,
                requirements=tuple(
                    _requirement_definition(requirement)
                    for requirement in prerequisite.requirements
                ),
            )
            for prerequisite in objective.prerequisites
        ),
        subsumes=frozenset(objective.subsumes),
    )


__all__ = [
    "SCENARIO_DOCUMENT_SCHEMA_VERSION",
    "ScenarioDefinitionDocument",
]
