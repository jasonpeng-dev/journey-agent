"""Bounded, Scenario-wide presentation policy contracts.

Presentation policy changes how already-safe player data is arranged.  It does
not contain Scenario truth, entity-local overrides, arbitrary layout, or
knowledge visibility rules.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class PresentationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PresentationTemplate(StrEnum):
    COMPACT = "compact"
    STANDARD = "standard"
    DETAILED = "detailed"


class PresentationDensity(StrEnum):
    COMPACT = "COMPACT"
    STANDARD = "STANDARD"
    DETAILED = "DETAILED"


class PresentationDefaultOpen(StrEnum):
    COLLAPSED = "COLLAPSED"
    COMPACT = "COMPACT"
    FULL = "FULL"


class PresentationSummarySlot(StrEnum):
    HEADER = "HEADER"
    BODY = "BODY"
    BOTH = "BOTH"


class PresentationEntityDetail(StrEnum):
    SUMMARY = "SUMMARY"
    DETAIL = "DETAIL"
    CAUSALITY = "CAUSALITY"


class PresentationKnowledgeLevel(StrEnum):
    A = "A"
    A_PLUS_B = "A+B"
    A_PLUS_B_PLUS_C = "A+B+C"


class PresentationPlanDetail(StrEnum):
    COLLAPSED = "COLLAPSED"
    COMPACT = "COMPACT"
    FULL = "FULL"


class PresentationTimelineDensity(StrEnum):
    COMPACT = "COMPACT"
    STANDARD = "STANDARD"
    DETAILED = "DETAILED"


class PresentationNodeFamily(StrEnum):
    GENERIC = "GENERIC"
    REGION = "REGION"
    FACILITY = "FACILITY"
    TRANSPORT = "TRANSPORT"


class PresentationSemanticSlot(StrEnum):
    NAME = "NAME"
    NODE_TYPE = "NODE_TYPE"
    VISIBILITY = "VISIBILITY"
    ACCESS = "ACCESS"
    FACTS = "FACTS"
    RELATIONS = "RELATIONS"
    RESOURCES = "RESOURCES"
    STATUS = "STATUS"


class PresentationResourceSlot(StrEnum):
    NAME = "NAME"
    AMOUNT = "AMOUNT"
    STATUS = "STATUS"
    UNIT = "UNIT"


class PresentationRelationSlot(StrEnum):
    TYPE = "TYPE"
    TARGET = "TARGET"
    VISIBILITY = "VISIBILITY"


class PresentationActorField(StrEnum):
    NAME = "NAME"
    ROLE = "ROLE"
    LOCATION = "LOCATION"
    STATUS = "STATUS"
    TASK = "TASK"
    CAPABILITIES = "CAPABILITIES"
    COMMAND_REACHABILITY = "COMMAND_REACHABILITY"


class GlobalPresentationOverrides(PresentationContract):
    density: PresentationDensity | None = None
    default_open: PresentationDefaultOpen | None = None
    summary_slot: PresentationSummarySlot | None = None
    semantic_order: tuple[PresentationSemanticSlot, ...] | None = None

    @model_validator(mode="after")
    def validate_order(self) -> GlobalPresentationOverrides:
        if self.semantic_order is not None and len(set(self.semantic_order)) != len(
            self.semantic_order
        ):
            raise ValueError("global semantic_order cannot contain duplicates")
        return self


class WorldPresentationOverrides(PresentationContract):
    entity_detail: PresentationEntityDetail | None = None
    knowledge_level: PresentationKnowledgeLevel | None = None
    resource_order: tuple[PresentationResourceSlot, ...] | None = None
    relation_order: tuple[PresentationRelationSlot, ...] | None = None

    @model_validator(mode="after")
    def validate_orders(self) -> WorldPresentationOverrides:
        if self.resource_order is not None and len(set(self.resource_order)) != len(
            self.resource_order
        ):
            raise ValueError("resource_order cannot contain duplicates")
        if self.relation_order is not None and len(set(self.relation_order)) != len(
            self.relation_order
        ):
            raise ValueError("relation_order cannot contain duplicates")
        return self


class ActorTeamPresentationOverrides(PresentationContract):
    visible_fields: tuple[PresentationActorField, ...] | None = None
    field_order: tuple[PresentationActorField, ...] | None = None

    @model_validator(mode="after")
    def validate_orders(self) -> ActorTeamPresentationOverrides:
        for name in ("visible_fields", "field_order"):
            values = getattr(self, name)
            if values is not None and len(set(values)) != len(values):
                raise ValueError(f"{name} cannot contain duplicates")
        return self


class GoalExecutionPresentationOverrides(PresentationContract):
    roadmap_detail: PresentationEntityDetail | None = None
    plan_default: PresentationPlanDetail | None = None
    timeline_density: PresentationTimelineDensity | None = None


class NodeFamilyPresentationOverride(PresentationContract):
    node_family: PresentationNodeFamily
    entity_detail: PresentationEntityDetail | None = None
    default_open: PresentationDefaultOpen | None = None
    semantic_order: tuple[PresentationSemanticSlot, ...] | None = None

    @model_validator(mode="after")
    def validate_order(self) -> NodeFamilyPresentationOverride:
        if self.semantic_order is not None and len(set(self.semantic_order)) != len(
            self.semantic_order
        ):
            raise ValueError("family semantic_order cannot contain duplicates")
        return self


class SemanticPresentationOverride(PresentationContract):
    semantic_key: str
    summary_slot: PresentationSummarySlot | None = None
    entity_detail: PresentationEntityDetail | None = None
    default_open: PresentationDefaultOpen | None = None

    @model_validator(mode="after")
    def validate_key(self) -> SemanticPresentationOverride:
        if not self.semantic_key or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789_.-"
            for char in self.semantic_key
        ):
            raise ValueError("semantic_key must be a stable safe semantic identity")
        return self


class PresentationProfileV1(PresentationContract):
    schema_version: Literal[1] = 1
    template: PresentationTemplate = PresentationTemplate.STANDARD
    global_display: GlobalPresentationOverrides | None = None
    world_entities: WorldPresentationOverrides | None = None
    actor_team: ActorTeamPresentationOverrides | None = None
    goal_execution: GoalExecutionPresentationOverrides | None = None
    family_overrides: tuple[NodeFamilyPresentationOverride, ...] = ()
    semantic_overrides: tuple[SemanticPresentationOverride, ...] = ()

    @model_validator(mode="after")
    def validate_override_identity(self) -> PresentationProfileV1:
        family_keys = [item.node_family for item in self.family_overrides]
        if len(set(family_keys)) != len(family_keys):
            raise ValueError("family_overrides cannot contain duplicate node families")
        semantic_keys = [item.semantic_key for item in self.semantic_overrides]
        if len(set(semantic_keys)) != len(semantic_keys):
            raise ValueError("semantic_overrides cannot contain duplicate semantic keys")
        return self


def default_presentation_profile_document() -> dict[str, object]:
    """Return the sparse product-default profile document."""

    return PresentationProfileV1().model_dump(mode="json", exclude_none=True)


def validate_presentation_profile(document: object) -> PresentationProfileV1:
    return PresentationProfileV1.model_validate(document)
