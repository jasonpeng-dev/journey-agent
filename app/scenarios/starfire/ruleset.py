"""Pure deterministic decisions for the Starfire scenario."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from app.domain.world import AccessState, RelationType, WorldDefinition
from app.scenarios.starfire.definition import STARFIRE_WORLD

type FactRef = tuple[str, str]


@dataclass(frozen=True, slots=True)
class StarfireFactState:
    value: str


@dataclass(frozen=True, slots=True)
class StarfireResources:
    soldiers_available: int
    food: int
    gold: int
    morale: int


@dataclass(frozen=True, slots=True)
class StarfireRuleState:
    facts: Mapping[FactRef, StarfireFactState]
    resources: StarfireResources

    def fact(self, node_key: str, fact_key: str) -> StarfireFactState:
        state = self.facts.get((node_key, fact_key))
        if state is None:
            raise StarfireRuleViolation(
                "STARFIRE_STATE_INVALID",
                "A required Starfire fact is missing from the runtime state",
            )
        return state

    def fact_value(self, node_key: str, fact_key: str) -> str:
        """Expose the canonical value through the generic scenario-state contract."""

        return self.fact(node_key, fact_key).value

    @staticmethod
    def node_known(_node_key: str) -> bool:
        """Authoritative truth states are complete when used by internal policies."""

        return True

    def fact_known(self, node_key: str, fact_key: str) -> bool:
        return (node_key, fact_key) in self.facts


@dataclass(frozen=True, slots=True)
class StarfireKnowledgeState:
    """Player/agent projection containing no hidden node or fact truth."""

    facts: Mapping[FactRef, str]
    node_access: Mapping[str, AccessState]
    resources: StarfireResources

    def fact_value(self, node_key: str, fact_key: str) -> str:
        try:
            return self.facts[(node_key, fact_key)]
        except KeyError:
            raise StarfireRuleViolation(
                "STARFIRE_KNOWLEDGE_UNAVAILABLE",
                "The requested fact is not known to the player or their agents",
            ) from None

    def node_known(self, node_key: str) -> bool:
        return node_key in self.node_access

    def fact_known(self, node_key: str, fact_key: str) -> bool:
        return (node_key, fact_key) in self.facts

    def known_target_states(self) -> Mapping[str, AccessState]:
        return self.node_access


@dataclass(frozen=True, slots=True)
class FactUpdate:
    node_key: str
    fact_key: str
    value: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", MappingProxyType(dict(self.value)))


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    payload: Mapping[str, Any] = field(default_factory=dict)
    fact_updates: tuple[FactUpdate, ...] = ()
    unlock_node_keys: tuple[str, ...] = ()
    reveal_node_keys: tuple[str, ...] = ()
    reveal_fact_refs: tuple[FactRef, ...] = ()
    casualties: int = 0
    morale_delta: int = 0
    food_delta: int = 0
    gold_delta: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        object.__setattr__(self, "fact_updates", tuple(self.fact_updates))
        object.__setattr__(self, "unlock_node_keys", tuple(self.unlock_node_keys))
        object.__setattr__(self, "reveal_node_keys", tuple(self.reveal_node_keys))
        object.__setattr__(self, "reveal_fact_refs", tuple(self.reveal_fact_refs))


class StarfireRuleViolation(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class StarfireRuleset:
    """Interpret Starfire facts and relations without mutating runtime state."""

    def __init__(self, world: WorldDefinition = STARFIRE_WORLD) -> None:
        self.world = world

    def validate_reconnaissance(self, target_key: str, approach: str) -> None:
        self._require_interaction(target_key, "reconnaissance", "RECON_TARGET_INVALID")
        if approach not in {"CAUTIOUS", "STANDARD", "AGGRESSIVE"}:
            raise StarfireRuleViolation(
                "RECON_APPROACH_INVALID",
                "The reconnaissance approach is invalid",
            )

    def resolve_reconnaissance(self, target_key: str) -> RuleOutcome:
        self._require_interaction(target_key, "reconnaissance", "RECON_TARGET_INVALID")
        return RuleOutcome(
            payload={
                "result": "PARTIAL_SUCCESS",
                "facts_discovered": ["valley_intelligence"],
                "casualties": 0,
            },
            fact_updates=(FactUpdate(target_key, "valley_intelligence", {"status": "PARTIAL"}),),
            unlock_node_keys=(target_key,),
            reveal_fact_refs=((target_key, "ambush_status"),),
        )

    def validate_village_support(
        self,
        state: StarfireRuleState,
        food_offer: int,
        requested_support: str,
    ) -> str:
        if requested_support not in {"INTELLIGENCE", "GUIDE", "SUPPLIES"}:
            raise StarfireRuleViolation(
                "VILLAGE_SUPPORT_INVALID",
                "The requested village support is invalid",
            )
        if state.resources.food < food_offer:
            raise StarfireRuleViolation(
                "SUPPLY_INSUFFICIENT",
                "The domain does not have enough food for this offer",
                retryable=True,
            )
        return self._unique_interaction_node("negotiate_support")

    def negotiate_village_support(
        self,
        state: StarfireRuleState,
        food_offer: int,
        requested_support: str,
    ) -> RuleOutcome:
        village_key = self.validate_village_support(state, food_offer, requested_support)
        support = requested_support if food_offer >= 20 else "INTELLIGENCE"
        return RuleOutcome(
            payload={"village_support": support},
            fact_updates=(
                FactUpdate(
                    village_key,
                    "village_support",
                    {"status": support, "food_offer": food_offer},
                ),
            ),
            food_delta=-food_offer,
        )

    def validate_military_operation(
        self,
        target_key: str,
        mission_type: str,
        strategy: str,
        state: StarfireRuleState,
    ) -> None:
        self.validate_military_parameters(target_key, mission_type, strategy)
        if mission_type == "DISRUPT_SUPPLY":
            supply = state.fact(target_key, "supply_status")
            if supply.value != "ACTIVE":
                raise StarfireRuleViolation(
                    "ENEMY_SUPPLY_ROUTE_UNAVAILABLE",
                    "The enemy supply route is not active",
                    retryable=True,
                )

    def validate_military_parameters(
        self,
        target_key: str,
        mission_type: str,
        strategy: str,
    ) -> None:
        interactions = {
            "CLEAR_VALLEY": "clear_threat",
            "DISRUPT_SUPPLY": "disrupt_supply",
            "ESCORT": "clear_threat",
            "DEFEND": "clear_threat",
        }
        interaction = interactions.get(mission_type)
        if interaction is None:
            raise StarfireRuleViolation(
                "MILITARY_MISSION_INVALID",
                "The military mission is invalid",
            )
        self._require_interaction(target_key, interaction, "MILITARY_TARGET_INVALID")
        if strategy not in {"CAUTIOUS", "STANDARD", "AGGRESSIVE"}:
            raise StarfireRuleViolation(
                "MILITARY_STRATEGY_INVALID",
                "The strategy is invalid",
            )

    def resolve_military_operation(
        self,
        target_key: str,
        mission_type: str,
        state: StarfireRuleState,
    ) -> RuleOutcome:
        if mission_type == "DISRUPT_SUPPLY":
            return self.resolve_disrupt_supply(target_key, state)
        return self.resolve_clear_threat(target_key, mission_type, state)

    def resolve_disrupt_supply(
        self,
        supply_key: str,
        state: StarfireRuleState,
    ) -> RuleOutcome:
        self._require_interaction(supply_key, "disrupt_supply", "MILITARY_TARGET_INVALID")
        supported_node = self._single_target(supply_key, RelationType.SUPPORTS)
        village_key = self._single_source_with_fact(
            supported_node,
            RelationType.SUPPORTS,
            "village_support",
        )
        support = state.fact(village_key, "village_support").value
        casualties = 2 if support == "GUIDE" else 4
        return RuleOutcome(
            payload={
                "result": "VICTORY",
                "mission_type": "DISRUPT_SUPPLY",
                "casualties": casualties,
                "facts_changed": ["enemy_supply_route"],
            },
            fact_updates=(FactUpdate(supply_key, "supply_status", {"status": "DISRUPTED"}),),
            casualties=casualties,
            morale_delta=3,
        )

    def resolve_clear_threat(
        self,
        valley_key: str,
        mission_type: str,
        state: StarfireRuleState,
    ) -> RuleOutcome:
        self._require_interaction(valley_key, "clear_threat", "MILITARY_TARGET_INVALID")
        supply_key = self._single_source_with_fact(
            valley_key,
            RelationType.SUPPORTS,
            "supply_status",
        )
        village_key = self._single_source_with_fact(
            valley_key,
            RelationType.SUPPORTS,
            "village_support",
        )
        supply = state.fact(supply_key, "supply_status")
        ambush = state.fact(valley_key, "ambush_status")
        threat_supported = ambush.value == "ACTIVE" and supply.value != "DISRUPTED"
        if mission_type == "CLEAR_VALLEY" and threat_supported:
            reveal_targets = self._targets(valley_key, RelationType.REVEALS, required=True)
            return RuleOutcome(
                payload={
                    "result": "DEFEAT",
                    "mission_type": mission_type,
                    "failure_code": "ENCOUNTER_DEFEAT",
                    "casualties": 18,
                    "facts_discovered": ["enemy_supply_route"],
                },
                fact_updates=(
                    FactUpdate(supply_key, "supply_status", {"status": "ACTIVE"}),
                    FactUpdate(valley_key, "valley_intelligence", {"status": "COMPLETE"}),
                ),
                unlock_node_keys=reveal_targets,
                reveal_node_keys=reveal_targets,
                reveal_fact_refs=tuple((key, "supply_status") for key in reveal_targets),
                casualties=18,
                morale_delta=-10,
            )
        support = state.fact(village_key, "village_support").value
        casualties = 3 if support == "GUIDE" else 6
        return RuleOutcome(
            payload={
                "result": "VICTORY",
                "mission_type": mission_type,
                "casualties": casualties,
                "facts_changed": ["valley_security"],
            },
            fact_updates=(
                FactUpdate(valley_key, "valley_security", {"status": "SAFE"}),
                FactUpdate(valley_key, "ambush_status", {"status": "CLEARED"}),
            ),
            reveal_fact_refs=((valley_key, "ambush_status"),),
            unlock_node_keys=self._targets(valley_key, RelationType.UNLOCKS, required=True),
            casualties=casualties,
            morale_delta=5,
        )

    def validate_repair(
        self,
        target_key: str,
        repair_level: str,
        food_commitment: int,
        gold_commitment: int,
        state: StarfireRuleState,
    ) -> None:
        self.validate_repair_parameters(target_key, repair_level)
        valley_key = self._single_source_with_fact(
            target_key,
            RelationType.UNLOCKS,
            "valley_security",
        )
        if state.fact(valley_key, "valley_security").value != "SAFE":
            raise StarfireRuleViolation(
                "VALLEY_UNSAFE",
                "The outpost cannot be repaired until the valley is safe",
                retryable=True,
            )
        if state.resources.food < food_commitment or state.resources.gold < gold_commitment:
            raise StarfireRuleViolation(
                "RESOURCE_INSUFFICIENT",
                "The domain lacks the resources required for repair",
                retryable=True,
            )

    def validate_repair_parameters(self, target_key: str, repair_level: str) -> None:
        self._require_interaction(target_key, "repair", "REPAIR_TARGET_INVALID")
        if repair_level not in {"TEMPORARY", "FULL"}:
            raise StarfireRuleViolation("REPAIR_LEVEL_INVALID", "The repair level is invalid")

    def prepare_repair(
        self,
        target_key: str,
        repair_level: str,
        food_commitment: int,
        gold_commitment: int,
        state: StarfireRuleState,
    ) -> RuleOutcome:
        self.validate_repair(
            target_key,
            repair_level,
            food_commitment,
            gold_commitment,
            state,
        )
        return RuleOutcome(
            food_delta=-food_commitment,
            gold_delta=-gold_commitment,
        )

    def resolve_repair(
        self,
        target_key: str,
        repair_level: str,
        state: StarfireRuleState,
    ) -> RuleOutcome:
        self._require_interaction(target_key, "repair", "REPAIR_TARGET_INVALID")
        valley_key = self._single_source_with_fact(
            target_key,
            RelationType.UNLOCKS,
            "valley_security",
        )
        if state.fact(valley_key, "valley_security").value != "SAFE":
            return RuleOutcome(
                payload={
                    "result": "FAILED",
                    "failure_code": "VALLEY_UNSAFE",
                    "facts_changed": [],
                }
            )
        status = "RESTORED" if repair_level == "FULL" else "OPERATIONAL"
        return RuleOutcome(
            payload={
                "result": "COMPLETED",
                "outpost_status": status,
                "facts_changed": ["starfire_outpost_status"],
            },
            fact_updates=(FactUpdate(target_key, "outpost_status", {"status": status}),),
            unlock_node_keys=(
                target_key,
                *self._targets(target_key, RelationType.ENABLES, required=False),
            ),
        )

    def validate_trade_route(self, target_key: str, state: StarfireRuleState) -> None:
        self.validate_trade_route_target(target_key)
        valley_key, outpost_key, village_key = self._trade_dependency_nodes(target_key)
        if state.fact(outpost_key, "outpost_status").value not in {
            "OPERATIONAL",
            "RESTORED",
        }:
            raise StarfireRuleViolation(
                "STARFIRE_OUTPOST_OFFLINE",
                "The outpost must be operational before testing the trade route",
                retryable=True,
            )
        if state.fact(valley_key, "valley_security").value != "SAFE":
            raise StarfireRuleViolation(
                "VALLEY_UNSAFE",
                "The valley must be safe before testing trade",
            )
        if state.fact(village_key, "village_support").value not in {"GUIDE", "SUPPLIES"}:
            raise StarfireRuleViolation(
                "TRADE_SUPPORT_REQUIRED",
                "Village support or escort capacity is required for the trade test",
                retryable=True,
            )

    def validate_trade_route_target(self, target_key: str) -> None:
        self._require_interaction(target_key, "test_trade_route", "TRADE_ROUTE_INVALID")

    def resolve_trade_route_test(
        self,
        target_key: str,
        state: StarfireRuleState,
    ) -> RuleOutcome:
        self._require_interaction(target_key, "test_trade_route", "TRADE_ROUTE_INVALID")
        invalidated = self._trade_invalidated_prerequisites(target_key, state)
        if invalidated:
            return RuleOutcome(
                payload={
                    "result": "FAILED",
                    "failure_code": "WORLD_STATE_CHANGED",
                    "invalidated_prerequisites": invalidated,
                    "facts_changed": [],
                }
            )
        return RuleOutcome(
            payload={
                "result": "COMPLETED",
                "trade_route_status": "OPEN",
                "facts_changed": ["northern_trade_route_status"],
            },
            fact_updates=(FactUpdate(target_key, "trade_route_status", {"status": "OPEN"}),),
            unlock_node_keys=(target_key,),
        )

    def _trade_invalidated_prerequisites(
        self,
        target_key: str,
        state: StarfireRuleState,
    ) -> list[str]:
        valley_key, outpost_key, village_key = self._trade_dependency_nodes(target_key)
        invalidated = []
        if state.fact(valley_key, "valley_security").value != "SAFE":
            invalidated.append("valley_security")
        if state.fact(outpost_key, "outpost_status").value not in {
            "OPERATIONAL",
            "RESTORED",
        }:
            invalidated.append("starfire_outpost_status")
        if state.fact(village_key, "village_support").value not in {"GUIDE", "SUPPLIES"}:
            invalidated.append("village_support")
        return invalidated

    def _trade_dependency_nodes(self, target_key: str) -> tuple[str, str, str]:
        enablers = self._sources(target_key, RelationType.ENABLES, required=True)
        valley_key = self._node_with_fact(enablers, "valley_security")
        outpost_key = self._node_with_fact(enablers, "outpost_status")
        supporters = self._sources(target_key, RelationType.SUPPORTS, required=True)
        village_key = self._node_with_fact(supporters, "village_support")
        return valley_key, outpost_key, village_key

    def _require_interaction(
        self,
        target_key: str,
        interaction_key: str,
        error_code: str,
    ) -> None:
        node = self.world.node(target_key)
        if node is None or not node.supports(interaction_key):
            messages = {
                "RECON_TARGET_INVALID": "The target cannot be reconnoitered",
                "MILITARY_TARGET_INVALID": "The military target is invalid",
                "REPAIR_TARGET_INVALID": "The repair target is invalid",
                "TRADE_ROUTE_INVALID": "The trade route is unknown",
            }
            raise StarfireRuleViolation(
                error_code,
                messages.get(error_code, "The Starfire interaction target is invalid"),
            )

    def _unique_interaction_node(self, interaction_key: str) -> str:
        candidates = tuple(node.key for node in self.world.nodes if node.supports(interaction_key))
        if len(candidates) != 1:
            raise StarfireRuleViolation(
                "STARFIRE_RELATION_INVALID",
                "The Starfire definition does not identify one rule target",
            )
        return candidates[0]

    def _single_source_with_fact(
        self,
        target_key: str,
        relation_type: RelationType,
        fact_key: str,
    ) -> str:
        return self._node_with_fact(
            self._sources(target_key, relation_type, required=True),
            fact_key,
        )

    def _single_target(self, source_key: str, relation_type: RelationType) -> str:
        targets = self._targets(source_key, relation_type, required=True)
        if len(targets) != 1:
            raise StarfireRuleViolation(
                "STARFIRE_RELATION_INVALID",
                "The Starfire relation does not identify one target",
            )
        return targets[0]

    def _node_with_fact(self, node_keys: tuple[str, ...], fact_key: str) -> str:
        candidates = tuple(
            key
            for key in node_keys
            if (node := self.world.node(key)) is not None and node.fact(fact_key) is not None
        )
        if len(candidates) != 1:
            raise StarfireRuleViolation(
                "STARFIRE_RELATION_INVALID",
                "The Starfire relations do not identify one required fact owner",
            )
        return candidates[0]

    def _sources(
        self,
        target_key: str,
        relation_type: RelationType,
        *,
        required: bool,
    ) -> tuple[str, ...]:
        sources = tuple(
            relation.source_node_key
            for relation in self.world.relations
            if relation.target_node_key == target_key and relation.relation_type == relation_type
        )
        self._require_relations(sources, required)
        return sources

    def _targets(
        self,
        source_key: str,
        relation_type: RelationType,
        *,
        required: bool,
    ) -> tuple[str, ...]:
        targets = tuple(
            relation.target_node_key
            for relation in self.world.relations
            if relation.source_node_key == source_key and relation.relation_type == relation_type
        )
        self._require_relations(targets, required)
        return targets

    @staticmethod
    def _require_relations(node_keys: tuple[str, ...], required: bool) -> None:
        if required and not node_keys:
            raise StarfireRuleViolation(
                "STARFIRE_RELATION_INVALID",
                "A required Starfire relation is missing",
            )
