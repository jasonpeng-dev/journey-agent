"""Current authoring ScenarioDefinition v3 contract.

The v3 document is deliberately a sibling contract to the immutable v2
wire shape.  It reuses the v2 domain vocabulary for world, Action and
initialization objects, while replacing the two author-controlled recovery
surfaces:

* ``planning.recovery_hints`` is absent;
* Rule Effects use ``BLOCK_ACTION`` instead of authored failure code,
  message and retryability.

Runtime code can consume :meth:`ScenarioDefinitionV3.to_v2` as one normalized
semantic model.  That conversion is in-memory only and never rewrites an old
v2 snapshot.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.domain.enums import (
    CommandReachability,
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
)
from app.domain.scenario_v2 import (
    ConditionV2,
    EffectV2,
    FrozenDefinitionModel,
    IntegerExpressionV2,
    NodeSelectorV2,
    ResourceScopeV2,
    RulePhase,
    RuleTrigger,
    ScenarioDefinitionV2,
    StableKey,
    SymbolicCode,
    ValueExpressionV2,
    _require_unique,
    normalize_resource_source_hint_document,
)
from app.domain.world import AccessState


class EffectKindV3(StrEnum):
    """Authoring effect vocabulary without the legacy failure variant."""

    SET_FACT = "SET_FACT"
    REVEAL_FACT = "REVEAL_FACT"
    HIDE_FACT = "HIDE_FACT"
    REVEAL_NODE = "REVEAL_NODE"
    HIDE_NODE = "HIDE_NODE"
    SET_NODE_ACCESS = "SET_NODE_ACCESS"
    ADJUST_RESOURCE = "ADJUST_RESOURCE"
    RESERVE_RESOURCE = "RESERVE_RESOURCE"
    RELEASE_RESOURCE = "RELEASE_RESOURCE"
    EMIT_OUTCOME = "EMIT_OUTCOME"
    BLOCK_ACTION = "BLOCK_ACTION"
    WRITE_MEMORY_EVENT = "WRITE_MEMORY_EVENT"
    SET_ACTOR_COMMAND_REACHABILITY = "SET_ACTOR_COMMAND_REACHABILITY"
    SET_RELATION_VISIBILITY = "SET_RELATION_VISIBILITY"
    SET_REGION_RESOURCE_VISIBILITY = "SET_REGION_RESOURCE_VISIBILITY"
    SET_RESOURCE_POOL_VISIBILITY = "SET_RESOURCE_POOL_VISIBILITY"
    SET_RESOURCE_POOL_AVAILABILITY = "SET_RESOURCE_POOL_AVAILABILITY"
    REVEAL_TARGET_REGION_FACILITY_FACTS = "REVEAL_TARGET_REGION_FACILITY_FACTS"


class EffectDefinitionV3(FrozenDefinitionModel):
    """One v3 Rule effect without author-controlled failure metadata."""

    kind: EffectKindV3
    node: NodeSelectorV2 | None = None
    fact_key: StableKey | None = None
    value: ValueExpressionV2 | None = None
    access: AccessState | None = None
    resource_key: StableKey | None = None
    resource_scope: ResourceScopeV2 | None = None
    amount: IntegerExpressionV2 | None = None
    outcome_code: SymbolicCode | None = None
    memory_key: StableKey | None = None
    memory_content: str | None = Field(default=None, max_length=4000)
    actor_key: StableKey | None = None
    relation_key: str | None = None
    command_reachability: CommandReachability | None = None
    region_key: StableKey | None = None
    pool_key: StableKey | None = None
    visibility: ResourceInventoryVisibility | None = None
    availability: ResourcePoolAvailability | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> EffectDefinitionV3:
        # Reuse the complete v2 effect shape validator as a semantic check.
        # The v3 model itself has no failure_code/message/retryable fields, so
        # those fields cannot enter the authoring document.
        EffectV2.model_validate(self.model_dump(mode="json", exclude_none=True))
        if self.kind == EffectKindV3.BLOCK_ACTION and any(
            value is not None
            for value in (
                self.node,
                self.fact_key,
                self.value,
                self.access,
                self.resource_key,
                self.resource_scope,
                self.amount,
                self.outcome_code,
                self.memory_key,
                self.memory_content,
                self.actor_key,
                self.relation_key,
                self.command_reachability,
                self.region_key,
                self.pool_key,
                self.visibility,
                self.availability,
            )
        ):
            raise ValueError("BLOCK_ACTION cannot carry mutations or outcome data")
        return self


class RuleDefinitionV3(FrozenDefinitionModel):
    """Rule contract whose blocker effect is semantic and metadata-free."""

    key: StableKey
    phase: RulePhase
    action_key: StableKey | None = Field(default=None, exclude_if=lambda value: value is None)
    trigger: RuleTrigger = Field(
        default=RuleTrigger.ACTION,
        exclude_if=lambda value: value == RuleTrigger.ACTION,
    )
    applicable_target_keys: tuple[StableKey, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    priority: int
    condition: ConditionV2 | None = None
    effects: tuple[EffectDefinitionV3, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_phase_effects(self) -> RuleDefinitionV3:
        _require_unique(self.applicable_target_keys, "Rule applicable target keys")
        terminals = [
            effect
            for effect in self.effects
            if effect.kind in {EffectKindV3.EMIT_OUTCOME, EffectKindV3.BLOCK_ACTION}
        ]
        if self.trigger == RuleTrigger.ACTION and self.action_key is None:
            raise ValueError("ACTION Rules require action_key")
        if self.trigger == RuleTrigger.STATE and self.action_key is not None:
            raise ValueError("STATE Rules must not declare action_key")
        if self.trigger == RuleTrigger.STATE and self.applicable_target_keys:
            raise ValueError("STATE Rules must not declare applicable target keys")
        if self.trigger == RuleTrigger.STATE and self.phase != RulePhase.RESOLVE:
            raise ValueError("STATE Rules must use the RESOLVE phase")
        if self.trigger == RuleTrigger.STATE and any(
            effect.kind == EffectKindV3.BLOCK_ACTION for effect in self.effects
        ):
            raise ValueError("STATE Rules may not block an Action")
        if self.phase == RulePhase.PREFLIGHT:
            if any(effect.kind != EffectKindV3.BLOCK_ACTION for effect in self.effects):
                raise ValueError("PREFLIGHT v3 Rules may only emit BLOCK_ACTION")
        elif self.trigger == RuleTrigger.ACTION and len(terminals) != 1:
            raise ValueError("A v3 RESOLVE Rule requires exactly one outcome or blocker")
        return self


class PlanningDefinitionV3(FrozenDefinitionModel):
    """Author planning guidance only; recovery hints are not authoring data."""

    instructions: tuple[str, ...] = ()


INTERNAL_GOAL_CLARIFICATION_FALLBACK = "Please clarify the intended objective."


class GoalResolutionV3(FrozenDefinitionModel):
    """Scenario-owned Goal Resolution content for the current v3 contract.

    Provider availability and the World Goal State route are platform policy;
    they are injected only when the authored v3 document is normalized to the
    legacy v2 runtime vocabulary.  The portable v3 document therefore owns
    only the ordered quick-input strings.
    """

    quick_inputs: tuple[str, ...] = Field(default=(), exclude_if=lambda value: not value)

    @field_validator("quick_inputs", mode="before")
    @classmethod
    def normalize_quick_inputs(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[object] = [
            item.strip() if isinstance(item, str) else item for item in value
        ]
        if any(item == "" for item in normalized):
            raise ValueError("Goal Resolution quick inputs cannot be blank")
        folded = [item.casefold() for item in normalized if isinstance(item, str)]
        _require_unique(folded, "Goal Resolution quick inputs")
        return tuple(normalized)


class ScenarioDefinitionV3(ScenarioDefinitionV2):
    """Current authoring schema version.

    Inheriting the v2 root keeps the normalized runtime API and all stable
    domain methods compatible while the overridden Rule/Planning fields close
    the authoring contract around the new semantics.
    """

    schema_version: Literal[3] = 3  # type: ignore[assignment]
    goal_resolution: GoalResolutionV3  # type: ignore[assignment]
    rules: tuple[RuleDefinitionV3, ...]  # type: ignore[assignment]
    planning: PlanningDefinitionV3 = Field(  # type: ignore[assignment]
        default_factory=PlanningDefinitionV3
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_resource_source_hints(cls, value: object) -> object:
        return normalize_resource_source_hint_document(value)

    def to_v2(self) -> ScenarioDefinitionV2:
        """Return the normalized internal semantic model without persistence."""

        payload = self.model_dump(mode="json")
        payload["schema_version"] = 2
        payload["goal_resolution"] = {
            "allow_llm_fallback": True,
            "clarification_prompt": INTERNAL_GOAL_CLARIFICATION_FALLBACK,
            "quick_inputs": list(self.goal_resolution.quick_inputs),
            "world_goal_state_catalog": True,
        }
        return ScenarioDefinitionV2.model_validate(payload)

    @classmethod
    def from_v2(cls, definition: ScenarioDefinitionV2) -> ScenarioDefinitionV3:
        """Build a v3 authoring document from a v2 document in memory.

        This helper intentionally rejects old authored recovery metadata in the
        resulting model; callers that need a migration preview should use the
        explicit migration module, which records the fields it drops.
        """

        from app.scenarios.migration import migrate_v2_document_to_v3

        return cls.model_validate(migrate_v2_document_to_v3(definition.model_dump(mode="json")))

    @property
    def objective_catalog_version(self) -> str:
        return f"scenario-v3:{self.metadata.key}"


__all__ = [
    "INTERNAL_GOAL_CLARIFICATION_FALLBACK",
    "EffectDefinitionV3",
    "EffectKindV3",
    "GoalResolutionV3",
    "PlanningDefinitionV3",
    "RuleDefinitionV3",
    "ScenarioDefinitionV3",
]
