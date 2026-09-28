"""Resolve bounded PresentationProfile policy over already player-safe data.

This module deliberately accepts authored semantic metadata and safe projection
values only.  It never loads a Scenario snapshot to discover additional facts,
relations, resources, or runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.domain.presentation import (
    PresentationActorField,
    PresentationDefaultOpen,
    PresentationDensity,
    PresentationEntityDetail,
    PresentationKnowledgeLevel,
    PresentationPlanDetail,
    PresentationProfileV1,
    PresentationRelationSlot,
    PresentationResourceSlot,
    PresentationSemanticSlot,
    PresentationSummarySlot,
    PresentationTemplate,
    PresentationTimelineDensity,
    validate_presentation_profile,
)
from app.domain.scenario_v2 import FactDefinitionV2, ResourceDefinitionV2, ScenarioDefinitionV2


@dataclass(frozen=True, slots=True)
class ResolvedEntityPresentation:
    """One safe entity surface policy after precedence has been applied."""

    summary_slot: PresentationSummarySlot
    detail_level: PresentationEntityDetail
    default_open: PresentationDefaultOpen
    knowledge_level: PresentationKnowledgeLevel
    semantic_order: tuple[PresentationSemanticSlot, ...]


@dataclass(frozen=True, slots=True)
class ResolvedPresentation:
    """The resolved product contract exposed to a Player renderer."""

    profile: PresentationProfileV1
    template: PresentationTemplate
    density: PresentationDensity
    default_open: PresentationDefaultOpen
    summary_slot: PresentationSummarySlot
    entity_detail: PresentationEntityDetail
    knowledge_level: PresentationKnowledgeLevel
    semantic_order: tuple[PresentationSemanticSlot, ...]
    resource_order: tuple[PresentationResourceSlot, ...]
    relation_order: tuple[PresentationRelationSlot, ...]
    actor_fields: tuple[PresentationActorField, ...]
    roadmap_detail: PresentationEntityDetail
    plan_default: PresentationPlanDetail
    timeline_density: PresentationTimelineDensity

    def entity(
        self,
        family: str,
        *,
        semantic_key: str | None = None,
    ) -> ResolvedEntityPresentation:
        """Resolve family then safe semantic overrides for one entity.

        Unknown historical semantic keys simply have no effect.  This is
        intentional: deleting an authored key must never create a placeholder
        or expose a different entity.
        """

        normalized_family = family.upper()
        family_override = next(
            (
                item
                for item in self.profile.family_overrides
                if item.node_family.value == normalized_family
            ),
            None,
        )
        detail = (
            family_override.entity_detail
            if family_override and family_override.entity_detail
            else self.entity_detail
        )
        default_open = (
            family_override.default_open
            if family_override and family_override.default_open
            else self.default_open
        )
        semantic_order = (
            family_override.semantic_order
            if family_override and family_override.semantic_order
            else self.semantic_order
        )
        summary_slot = self.summary_slot
        semantic_keys = tuple(item for item in (semantic_key, normalized_family.casefold()) if item)
        semantic_override = next(
            (
                item
                for item in self.profile.semantic_overrides
                if item.semantic_key in semantic_keys
            ),
            None,
        )
        if semantic_override is not None:
            summary_slot = semantic_override.summary_slot or summary_slot
            detail = semantic_override.entity_detail or detail
            default_open = semantic_override.default_open or default_open
        return ResolvedEntityPresentation(
            summary_slot=summary_slot,
            detail_level=detail,
            default_open=default_open,
            knowledge_level=self.knowledge_level,
            semantic_order=tuple(semantic_order),
        )

    def public_document(self, *, revision: int = 1) -> dict[str, Any]:
        """Return only resolved, safe controls for the Player surface."""

        return {
            "revision": revision,
            "template": self.template.value,
            "density": self.density.value,
            "default_open": self.default_open.value,
            "summary_slot": self.summary_slot.value,
            "entity_detail": self.entity_detail.value,
            "knowledge_level": self.knowledge_level.value,
            "semantic_order": [item.value for item in self.semantic_order],
            "resource_order": [item.value for item in self.resource_order],
            "relation_order": [item.value for item in self.relation_order],
            "actor_fields": [item.value for item in self.actor_fields],
            "roadmap_detail": self.roadmap_detail.value,
            "plan_default": self.plan_default.value,
            "timeline_density": self.timeline_density.value,
        }

    @staticmethod
    def fact_value_label(fact: FactDefinitionV2, value: object) -> str | None:
        """Resolve a Scenario-authored label without changing the scalar value."""

        for item in fact.value_labels:
            if type(item.value) is type(value) and item.value == value:
                return item.label
        return None

    @staticmethod
    def fact_summary_value_label(fact: FactDefinitionV2, value: object) -> str | None:
        for item in fact.value_labels:
            if type(item.value) is type(value) and item.value == value:
                return item.summary_label or item.label
        return None

    @staticmethod
    def fact_detail_value_label(fact: FactDefinitionV2, value: object) -> str | None:
        for item in fact.value_labels:
            if type(item.value) is type(value) and item.value == value:
                return item.detail_label or item.label
        return None

    @staticmethod
    def resource_unit(resource: ResourceDefinitionV2) -> str | None:
        return resource.display_unit or resource.unit


@dataclass(frozen=True, slots=True)
class _TemplateDefaults:
    density: PresentationDensity
    default_open: PresentationDefaultOpen
    summary_slot: PresentationSummarySlot
    entity_detail: PresentationEntityDetail
    knowledge_level: PresentationKnowledgeLevel
    semantic_order: tuple[PresentationSemanticSlot, ...]
    resource_order: tuple[PresentationResourceSlot, ...]
    relation_order: tuple[PresentationRelationSlot, ...]
    actor_fields: tuple[PresentationActorField, ...]
    roadmap_detail: PresentationEntityDetail
    plan_default: PresentationPlanDetail
    timeline_density: PresentationTimelineDensity


_STANDARD_DEFAULTS = _TemplateDefaults(
    density=PresentationDensity.STANDARD,
    default_open=PresentationDefaultOpen.COMPACT,
    summary_slot=PresentationSummarySlot.HEADER,
    entity_detail=PresentationEntityDetail.DETAIL,
    knowledge_level=PresentationKnowledgeLevel.A_PLUS_B_PLUS_C,
    semantic_order=(
        PresentationSemanticSlot.NAME,
        PresentationSemanticSlot.STATUS,
        PresentationSemanticSlot.FACTS,
        PresentationSemanticSlot.RESOURCES,
        PresentationSemanticSlot.RELATIONS,
    ),
    resource_order=(
        PresentationResourceSlot.NAME,
        PresentationResourceSlot.AMOUNT,
        PresentationResourceSlot.STATUS,
        PresentationResourceSlot.UNIT,
    ),
    relation_order=(
        PresentationRelationSlot.TYPE,
        PresentationRelationSlot.TARGET,
        PresentationRelationSlot.VISIBILITY,
    ),
    actor_fields=(
        PresentationActorField.NAME,
        PresentationActorField.ROLE,
        PresentationActorField.LOCATION,
        PresentationActorField.COMMAND_REACHABILITY,
    ),
    roadmap_detail=PresentationEntityDetail.DETAIL,
    plan_default=PresentationPlanDetail.COMPACT,
    timeline_density=PresentationTimelineDensity.STANDARD,
)

_TEMPLATE_DEFAULTS: dict[PresentationTemplate, _TemplateDefaults] = {
    PresentationTemplate.STANDARD: _STANDARD_DEFAULTS,
    PresentationTemplate.COMPACT: replace(
        _STANDARD_DEFAULTS,
        density=PresentationDensity.COMPACT,
        default_open=PresentationDefaultOpen.COLLAPSED,
        entity_detail=PresentationEntityDetail.SUMMARY,
        knowledge_level=PresentationKnowledgeLevel.A,
        semantic_order=(PresentationSemanticSlot.NAME, PresentationSemanticSlot.STATUS),
        resource_order=(PresentationResourceSlot.NAME, PresentationResourceSlot.AMOUNT),
        relation_order=(PresentationRelationSlot.TYPE, PresentationRelationSlot.TARGET),
        actor_fields=(PresentationActorField.NAME, PresentationActorField.ROLE),
        roadmap_detail=PresentationEntityDetail.SUMMARY,
        plan_default=PresentationPlanDetail.COLLAPSED,
        timeline_density=PresentationTimelineDensity.COMPACT,
    ),
    PresentationTemplate.DETAILED: replace(
        _STANDARD_DEFAULTS,
        density=PresentationDensity.DETAILED,
        default_open=PresentationDefaultOpen.FULL,
        summary_slot=PresentationSummarySlot.BOTH,
        entity_detail=PresentationEntityDetail.CAUSALITY,
        knowledge_level=PresentationKnowledgeLevel.A_PLUS_B_PLUS_C,
        semantic_order=(
            PresentationSemanticSlot.NAME,
            PresentationSemanticSlot.NODE_TYPE,
            PresentationSemanticSlot.VISIBILITY,
            PresentationSemanticSlot.ACCESS,
            PresentationSemanticSlot.STATUS,
            PresentationSemanticSlot.FACTS,
            PresentationSemanticSlot.RESOURCES,
            PresentationSemanticSlot.RELATIONS,
        ),
        actor_fields=(
            PresentationActorField.NAME,
            PresentationActorField.ROLE,
            PresentationActorField.LOCATION,
            PresentationActorField.STATUS,
            PresentationActorField.TASK,
            PresentationActorField.COMMAND_REACHABILITY,
        ),
        roadmap_detail=PresentationEntityDetail.CAUSALITY,
        plan_default=PresentationPlanDetail.FULL,
        timeline_density=PresentationTimelineDensity.DETAILED,
    ),
}

_SAFE_ACTOR_FIELDS = frozenset(
    {
        PresentationActorField.NAME,
        PresentationActorField.ROLE,
        PresentationActorField.LOCATION,
        PresentationActorField.STATUS,
        PresentationActorField.TASK,
        PresentationActorField.COMMAND_REACHABILITY,
    }
)


def resolve_presentation_profile(document: object | None) -> ResolvedPresentation:
    """Validate a stored profile or safely fall back to the product default."""

    try:
        profile = validate_presentation_profile(document or {})
    except (TypeError, ValueError):
        profile = validate_presentation_profile({})
    defaults = _TEMPLATE_DEFAULTS[profile.template]
    global_display = profile.global_display
    world = profile.world_entities
    actors = profile.actor_team
    goal = profile.goal_execution
    actor_fields = (
        actors.field_order
        if actors and actors.field_order
        else actors.visible_fields
        if actors and actors.visible_fields
        else defaults.actor_fields
    )
    actor_fields = tuple(item for item in actor_fields if item in _SAFE_ACTOR_FIELDS)
    if actors and actors.visible_fields:
        visible_actor_fields = set(actors.visible_fields)
        actor_fields = tuple(
            item
            for item in actor_fields
            if item in visible_actor_fields and item in _SAFE_ACTOR_FIELDS
        )
    return ResolvedPresentation(
        profile=profile,
        template=profile.template,
        density=(
            global_display.density
            if global_display and global_display.density
            else defaults.density
        ),
        default_open=(
            global_display.default_open
            if global_display and global_display.default_open
            else defaults.default_open
        ),
        summary_slot=(
            global_display.summary_slot
            if global_display and global_display.summary_slot
            else defaults.summary_slot
        ),
        entity_detail=(
            world.entity_detail if world and world.entity_detail else defaults.entity_detail
        ),
        knowledge_level=(
            world.knowledge_level if world and world.knowledge_level else defaults.knowledge_level
        ),
        semantic_order=(
            global_display.semantic_order
            if global_display and global_display.semantic_order
            else defaults.semantic_order
        ),
        resource_order=(
            world.resource_order if world and world.resource_order else defaults.resource_order
        ),
        relation_order=(
            world.relation_order if world and world.relation_order else defaults.relation_order
        ),
        actor_fields=actor_fields,
        roadmap_detail=(
            goal.roadmap_detail if goal and goal.roadmap_detail else defaults.roadmap_detail
        ),
        plan_default=(goal.plan_default if goal and goal.plan_default else defaults.plan_default),
        timeline_density=(
            goal.timeline_density if goal and goal.timeline_density else defaults.timeline_density
        ),
    )


def resolved_presentation_for_scenario(
    definition: ScenarioDefinitionV2,
    document: object | None,
) -> ResolvedPresentation:
    """Resolve policy while keeping the Scenario as semantic metadata only."""

    # This explicit boundary makes it difficult for a future caller to pass a
    # raw Runtime/Truth object into the resolver by accident.  The definition
    # is used only for safe authored labels and family metadata by callers.
    _ = definition
    return resolve_presentation_profile(document)


__all__ = [
    "ResolvedEntityPresentation",
    "ResolvedPresentation",
    "resolve_presentation_profile",
    "resolved_presentation_for_scenario",
]
