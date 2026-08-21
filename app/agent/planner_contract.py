"""Generic, compact Planner Action Contract projections.

The runtime and Validator remain authoritative.  This module only translates
their generic contracts into a small, knowledge-safe JSON projection for the
Planner.  It deliberately does not bind every Action to every Target.
"""

from __future__ import annotations

from typing import Any, cast

from app.domain.enums import CommandReachability
from app.domain.scenario_v2 import (
    ActionBehavior,
    ActionDefinitionV2,
    ActionLocality,
    ConditionKind,
    EffectKind,
    EffectV2,
    NodeSelectorKind,
    RulePhase,
    ScenarioDefinitionV2,
    StrictScalar,
)


def actor_execution_state(*, status: str, command_reachability: str) -> dict[str, object]:
    """Return current Actor executability without changing static permissions."""

    if status != "ACTIVE":
        return {
            "status": "KNOWN_BLOCKED",
            "known_blockers": [
                {
                    "type": "ACTOR_STATUS",
                    "current_value": status,
                    "required_value": "ACTIVE",
                }
            ],
        }
    if command_reachability == CommandReachability.ONLINE.value:
        return {"status": "EXECUTABLE", "known_blockers": []}
    if command_reachability == CommandReachability.DISCONNECTED.value:
        return {
            "status": "KNOWN_BLOCKED",
            "known_blockers": [
                {
                    "type": "COMMAND_REACHABILITY",
                    "current_value": CommandReachability.DISCONNECTED.value,
                    "required_value": CommandReachability.ONLINE.value,
                    "reason": (
                        "Actor is currently DISCONNECTED and cannot execute ordinary Actions."
                    ),
                }
            ],
        }
    return {
        "status": "UNKNOWN",
        "known_blockers": [
            {
                "type": "COMMAND_REACHABILITY",
                "current_value": command_reachability,
                "required_value": CommandReachability.ONLINE.value,
            }
        ],
    }


def action_planner_constraints(
    action: ActionDefinitionV2,
    *,
    known_preconditions: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    """Build the generic Action-level constraint contract.

    Existing ``hard_constraints``, ``target_requirements`` and ``locality``
    fields remain for compatibility.  This projection joins their meaning
    with the behavior-level semantics that were previously implicit.
    """

    executor: dict[str, object] = {
        "command_reachability": CommandReachability.ONLINE.value,
        "required_capabilities": [item.value for item in action.allowed_actor_capabilities],
    }
    if action.required_actor_role_key is not None:
        executor["required_role_key"] = action.required_actor_role_key

    target: dict[str, object] = {
        "kind": action.target_kind.value,
        "required_interaction_key": action.required_interaction_key,
    }
    if action.behavior == ActionBehavior.RELAY_MESSAGE:
        target["command_reachability"] = CommandReachability.DISCONNECTED.value

    contract: dict[str, object] = {
        "executor": executor,
        "target": target,
        "locality": _locality_contract(action),
    }

    knowledge: list[dict[str, object]] = []
    if action.behavior in {ActionBehavior.TRAVEL, ActionBehavior.TRANSPORT_RESOURCE}:
        knowledge.append(
            {
                "type": "KNOWN_BLOCKED_ROUTE",
                "known_false": "BLOCKS_EXECUTION",
                "unknown": "MAY_ATTEMPT",
            }
        )
    if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
        knowledge.append(
            {
                "type": "SOURCE_INVENTORY",
                "source": "PROJECTED_ACTOR_REGION",
                "required": "KNOWN_VISIBLE_AVAILABLE",
                "unknown": "CANNOT_INTENTIONALLY_TRANSPORT",
            }
        )
    if action.behavior == ActionBehavior.SURVEY_RESOURCES:
        knowledge.append(
            {
                "type": "RESOURCE_SURVEY_STATE",
                "required": "NOT_COMPLETED",
                "unknown_inventory": "SURVEY_CAN_REVEAL_KNOWLEDGE",
            }
        )
    if action.behavior == ActionBehavior.SUPPLY_POWER:
        knowledge.append(
            {
                "type": "DIRECT_RELATION",
                "relation_type_key": action.source_relation_type_key,
                "required": "KNOWN_VISIBLE",
            }
        )
        knowledge.append(
            {
                "type": "SOURCE_DECLARATIVE_REQUIREMENTS",
                "source": "source_key",
                "unknown": "DO_NOT_INFER_HIDDEN_POWER_STATE",
            }
        )
    if action.behavior == ActionBehavior.DEPLOY_HEAVY_ENGINEERING_SUPPORT:
        knowledge.append(
            {
                "type": "HEAVY_SUPPORT_AVAILABILITY",
                "required": "KNOWN_AVAILABLE",
            }
        )
    if knowledge:
        contract["knowledge"] = knowledge
    if known_preconditions:
        contract["known_preconditions"] = [dict(item) for item in known_preconditions]
    return contract


def planner_known_preconditions(
    definition: ScenarioDefinitionV2,
    action: ActionDefinitionV2,
    *,
    known_facts: dict[tuple[str, str], StrictScalar],
) -> tuple[dict[str, object], ...]:
    """Project compact, known declarative source/explicit requirements.

    Current-target requirements are represented by ``target_contracts``.  A
    source/explicit requirement stays symbolic so it does not become an
    Action-by-every-known-node Cartesian product.
    """

    result: list[dict[str, object]] = []
    for rule in definition.rules:
        if rule.action_key != action.key or rule.phase != RulePhase.PREFLIGHT:
            continue
        for condition in _condition_leaves(rule.condition):
            if condition.node is None or condition.fact_key is None:
                continue
            selector = condition.node.kind
            if selector == NodeSelectorKind.CURRENT_TARGET:
                continue
            if selector == NodeSelectorKind.EXPLICIT:
                node_key = condition.node.node_key
                if node_key is None or (node_key, condition.fact_key) not in known_facts:
                    continue
            elif selector == NodeSelectorKind.ACTION_SOURCE:
                if not any(fact_key == condition.fact_key for _, fact_key in known_facts):
                    continue
                node_key = None
            else:
                continue
            projection: dict[str, object] = {
                "selector": selector.value,
                "fact_key": condition.fact_key,
                "failure_condition": _condition_projection(condition),
            }
            if node_key is not None:
                projection["node_key"] = node_key
                projection["current_value"] = known_facts[(node_key, condition.fact_key)]
            if projection not in result:
                result.append(projection)
    return tuple(result)


def action_planner_effects(action: ActionDefinitionV2) -> list[dict[str, object]]:
    """Describe behavior-owned effects without exposing runtime Truth."""

    behavior = action.behavior
    effects: list[dict[str, object]] = []
    if behavior == ActionBehavior.TRAVEL:
        effects.extend(
            [
                {
                    "type": "ACTOR_LOCATION",
                    "actor": "executor",
                    "value": "target_key",
                },
                {
                    "type": "KNOWLEDGE_REVEAL_ON_FAILURE",
                    "subject": "transport_passability",
                    "failure_code": "TRAVEL_BLOCKED",
                },
            ]
        )
    elif behavior == ActionBehavior.RELAY_MESSAGE:
        effects.append(
            {
                "type": "ACTOR_COMMAND_REACHABILITY",
                "target": "target_actor",
                "value": CommandReachability.ONLINE.value,
            }
        )
    elif behavior == ActionBehavior.SURVEY_RESOURCES:
        effects.extend(
            [
                {
                    "type": "REGION_RESOURCE_KNOWLEDGE",
                    "target": "target_region",
                    "visibility": "VISIBLE",
                },
                {
                    "type": "RESOURCE_SURVEY_COMPLETED",
                    "target": "target_region",
                    "value": True,
                },
                {
                    "type": "RESOURCE_POOL_KNOWLEDGE",
                    "target": "target_region",
                    "transition": "DISCOVERABLE_HIDDEN_TO_VISIBLE",
                },
                {"type": "NO_TRUTH_CREATION", "subject": "resource_inventory"},
            ]
        )
    elif behavior == ActionBehavior.TRANSPORT_RESOURCE:
        effects.extend(
            [
                {
                    "type": "RESOURCE_CONSUMPTION",
                    "source": "PROJECTED_ACTOR_REGION",
                    "pool_filter": "VISIBLE_AVAILABLE",
                    "resource_key": "parameters.resource_key",
                    "amount": "parameters.amount",
                },
                {
                    "type": "RESOURCE_TRANSFER",
                    "destination": "target_key",
                    "pool": "default",
                    "resource_key": "parameters.resource_key",
                    "amount": "parameters.amount",
                },
                {
                    "type": "ACTOR_LOCATION",
                    "actor": "executor",
                    "value": "target_key",
                },
                {
                    "type": "KNOWLEDGE_REVEAL_ON_FAILURE",
                    "subject": "transport_passability",
                    "failure_code": "TRANSPORT_BLOCKED",
                },
            ]
        )
    elif behavior == ActionBehavior.INSPECT:
        effects.extend(
            [
                {"type": "KNOWLEDGE_REVEAL", "target": "inspect_target"},
                {"type": "NO_TRUTH_MUTATION", "subject": "inspected_target"},
            ]
        )
    elif behavior == ActionBehavior.SUPPLY_POWER:
        effects.extend(
            [
                {
                    "type": "FACT_MUTATION",
                    "target": "target_key",
                    "fact_key": "power_supply",
                    "value": "AVAILABLE",
                },
                {"type": "NO_IMPLIED_FACT_MUTATION", "fact_key": "operational"},
            ]
        )
    elif behavior == ActionBehavior.DEPLOY_HEAVY_ENGINEERING_SUPPORT:
        effects.append(
            {
                "type": "FACT_MUTATION",
                "target": "target_key",
                "fact_key": "heavy_engineering_support_ready",
                "value": True,
            }
        )

    # All repairable declarative Actions are intentionally separate from
    # power supply.  The target-conditioned Rule projection carries their
    # concrete effects and costs below.
    if action.required_interaction_key == "repairable":
        effects.append({"type": "NO_IMPLIED_FACT_MUTATION", "fact_key": "power_supply"})
    return _deduplicate(effects)


def planner_target_contracts(
    definition: ScenarioDefinitionV2,
    action: ActionDefinitionV2,
    *,
    known_node_keys: set[str],
    known_facts: dict[tuple[str, str], StrictScalar],
    known_relation_keys: set[str] | None = None,
    known_pool_keys: set[str] | None = None,
) -> dict[str, dict[str, object]]:
    """Return only known target-specific deterministic effect differences."""

    known_relation_keys = known_relation_keys or set()
    known_pool_keys = known_pool_keys or set()
    effects_by_target: dict[str, list[dict[str, object]]] = {}
    for rule in definition.rules:
        if rule.action_key != action.key or rule.phase != RulePhase.RESOLVE:
            continue
        if not _has_current_target_condition(rule.condition):
            continue
        for target_key in known_node_keys:
            if not _condition_matches_target(rule.condition, target_key, known_facts):
                continue
            effects = [
                projection
                for item in rule.effects
                if (projection := declarative_effect(item)) is not None
                and _effect_is_knowledge_safe(
                    item,
                    projection,
                    known_node_keys=known_node_keys,
                    known_relation_keys=known_relation_keys,
                    known_pool_keys=known_pool_keys,
                    known_facts=known_facts,
                    target_key=target_key,
                )
            ]
            if effects:
                effects_by_target.setdefault(target_key, []).extend(effects)

    contracts: dict[str, dict[str, object]] = {}
    for target_key in sorted(effects_by_target):
        contract: dict[str, object] = {}
        effects = _deduplicate(effects_by_target.get(target_key, []))
        if effects:
            contract["effects"] = effects
        if contract:
            contracts[target_key] = contract
    return contracts


def declarative_effect(effect: EffectV2) -> dict[str, object] | None:
    """Convert a safe, deterministic Rule effect to compact planner JSON."""

    kind = effect.kind
    if kind == EffectKind.SET_FACT:
        if effect.fact_key is None or effect.value is None:
            return None
        return {
            "type": "FACT_MUTATION",
            "target": _selector_name(effect.node),
            "fact_key": effect.fact_key,
            "value": _value_expression(effect.value),
        }
    if kind in {EffectKind.REVEAL_FACT, EffectKind.HIDE_FACT}:
        if effect.fact_key is None:
            return None
        return {
            "type": "KNOWLEDGE_REVEAL" if kind == EffectKind.REVEAL_FACT else "KNOWLEDGE_HIDE",
            "target": _selector_name(effect.node),
            "fact_key": effect.fact_key,
        }
    if kind in {
        EffectKind.ADJUST_RESOURCE,
        EffectKind.RESERVE_RESOURCE,
        EffectKind.RELEASE_RESOURCE,
    }:
        if effect.resource_key is None or effect.amount is None:
            return None
        amount = _integer_expression(effect.amount)
        effect_type = {
            EffectKind.ADJUST_RESOURCE: "RESOURCE_DELTA",
            EffectKind.RESERVE_RESOURCE: "RESOURCE_RESERVATION",
            EffectKind.RELEASE_RESOURCE: "RESOURCE_RELEASE",
        }[kind]
        return {
            "type": effect_type,
            "resource_key": effect.resource_key,
            "scope": (
                effect.resource_scope.kind.value if effect.resource_scope is not None else None
            ),
            "amount": amount,
        }
    if kind == EffectKind.SET_ACTOR_COMMAND_REACHABILITY:
        if effect.command_reachability is None:
            return None
        return {
            "type": "ACTOR_COMMAND_REACHABILITY",
            "target": effect.actor_key or "explicit_actor_key",
            "value": effect.command_reachability.value,
        }
    if kind == EffectKind.SET_RELATION_VISIBILITY:
        if effect.relation_key is None or effect.visibility is None:
            return None
        return {
            "type": "RELATION_KNOWLEDGE",
            "relation_key": effect.relation_key,
            "visibility": effect.visibility.value,
        }
    if kind == EffectKind.SET_REGION_RESOURCE_VISIBILITY:
        if effect.region_key is None or effect.visibility is None:
            return None
        return {
            "type": "REGION_RESOURCE_KNOWLEDGE",
            "region_key": effect.region_key,
            "visibility": effect.visibility.value,
        }
    if kind == EffectKind.SET_RESOURCE_POOL_VISIBILITY:
        if effect.pool_key is None or effect.visibility is None:
            return None
        return {
            "type": "RESOURCE_POOL_KNOWLEDGE",
            "pool_key": effect.pool_key,
            "visibility": effect.visibility.value,
        }
    if kind == EffectKind.SET_RESOURCE_POOL_AVAILABILITY:
        if effect.pool_key is None or effect.availability is None:
            return None
        return {
            "type": "RESOURCE_POOL_AVAILABILITY",
            "pool_key": effect.pool_key,
            "availability": effect.availability.value,
        }
    return None


def declarative_action_effects(
    definition: ScenarioDefinitionV2,
    action: ActionDefinitionV2,
    *,
    known_node_keys: set[str] | None = None,
    known_relation_keys: set[str] | None = None,
    known_pool_keys: set[str] | None = None,
    known_facts: dict[tuple[str, str], StrictScalar] | None = None,
) -> list[dict[str, object]]:
    """Return unconditional, Knowledge-safe deterministic effects for an Action.

    Explicit node/relation references are only useful to the Planner when the
    corresponding entity is already in Knowledge.  Filtering them here keeps
    the compact Action contract from reintroducing hidden-world leakage.
    """

    effects: list[dict[str, object]] = []
    known_node_keys = known_node_keys or set()
    known_relation_keys = known_relation_keys or set()
    known_pool_keys = known_pool_keys or set()
    known_facts = known_facts or {}
    for rule in definition.rules:
        if rule.action_key != action.key or rule.phase != RulePhase.RESOLVE:
            continue
        if _has_current_target_condition(rule.condition):
            continue
        for item in rule.effects:
            projection = declarative_effect(item)
            if projection is None or not _effect_is_knowledge_safe(
                item,
                projection,
                known_node_keys=known_node_keys,
                known_relation_keys=known_relation_keys,
                known_pool_keys=known_pool_keys,
                known_facts=known_facts,
            ):
                continue
            effects.append(projection)
    return _deduplicate(effects)


def _effect_is_knowledge_safe(
    effect: EffectV2,
    projection: dict[str, object],
    *,
    known_node_keys: set[str],
    known_relation_keys: set[str],
    known_pool_keys: set[str],
    known_facts: dict[tuple[str, str], StrictScalar],
    target_key: str | None = None,
) -> bool:
    if effect.node is not None and effect.node.kind == NodeSelectorKind.EXPLICIT:
        return effect.node.node_key in known_node_keys
    if projection.get("type") == "RELATION_KNOWLEDGE":
        relation_key = projection.get("relation_key")
        return isinstance(relation_key, str) and relation_key in known_relation_keys
    if projection.get("type") in {
        "RESOURCE_POOL_KNOWLEDGE",
        "RESOURCE_POOL_AVAILABILITY",
    }:
        pool_key = projection.get("pool_key")
        return isinstance(pool_key, str) and pool_key in known_pool_keys
    if projection.get("type") in {
        "FACT_MUTATION",
        "KNOWLEDGE_REVEAL",
        "KNOWLEDGE_HIDE",
    }:
        fact_key = projection.get("fact_key")
        if not isinstance(fact_key, str):
            return False
        if effect.node is not None and effect.node.kind == NodeSelectorKind.EXPLICIT:
            node_key = effect.node.node_key
            return node_key is not None and (node_key, fact_key) in known_facts
        if target_key is not None:
            return (target_key, fact_key) in known_facts
        return any(item_fact_key == fact_key for _, item_fact_key in known_facts)
    return True


def _locality_contract(action: ActionDefinitionV2) -> dict[str, object]:
    if action.behavior in {ActionBehavior.TRAVEL, ActionBehavior.TRANSPORT_RESOURCE}:
        contract: dict[str, object] = {
            "type": "ONE_HOP_TRANSPORT",
            "source": "PROJECTED_ACTOR_REGION",
            "destination": "TARGET_REGION",
        }
        if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
            contract["different_regions"] = True
        return contract
    if action.behavior == ActionBehavior.RELAY_MESSAGE:
        return {"type": "ACTOR_SAME_REGION", "target": "TARGET_ACTOR"}
    if action.behavior == ActionBehavior.SURVEY_RESOURCES:
        return {"type": "ACTOR_SAME_REGION", "target": "TARGET_REGION"}
    if action.behavior == ActionBehavior.DEPLOY_HEAVY_ENGINEERING_SUPPORT:
        return {"type": "LOCAL_TARGET_FACILITY_OR_TRANSPORT"}
    if action.locality == ActionLocality.LOCAL_TARGET:
        return {"type": ActionLocality.LOCAL_TARGET.value}
    return {"type": action.locality.value}


def _condition_leaves(condition: Any) -> tuple[Any, ...]:
    if condition is None:
        return ()
    if condition.kind in {ConditionKind.ALL, ConditionKind.ANY}:
        leaves: list[Any] = []
        for item in condition.conditions:
            leaves.extend(_condition_leaves(item))
        return tuple(leaves)
    if condition.kind == ConditionKind.NOT:
        return _condition_leaves(condition.condition)
    return (condition,)


def _condition_projection(condition: Any) -> dict[str, object]:
    projection: dict[str, object] = {"kind": condition.kind.value}
    if condition.value is not None:
        projection["value"] = condition.value
    if condition.values:
        projection["values"] = list(condition.values)
    if condition.operator is not None:
        projection["operator"] = condition.operator.value
    return projection


def _has_current_target_condition(condition: Any) -> bool:
    if condition is None:
        return False
    if condition.kind in {ConditionKind.ALL, ConditionKind.ANY}:
        return any(_has_current_target_condition(item) for item in condition.conditions)
    if condition.kind == ConditionKind.NOT:
        return _has_current_target_condition(condition.condition)
    return bool(
        condition.node is not None and condition.node.kind == NodeSelectorKind.CURRENT_TARGET
    )


def _condition_matches_target(
    condition: Any,
    target_key: str,
    known_facts: dict[tuple[str, str], StrictScalar],
) -> bool:
    if condition is None:
        return True
    if condition.kind == ConditionKind.ALL:
        return all(
            _condition_matches_target(item, target_key, known_facts)
            for item in condition.conditions
        )
    if condition.kind == ConditionKind.ANY:
        return any(
            _condition_matches_target(item, target_key, known_facts)
            for item in condition.conditions
        )
    if condition.kind == ConditionKind.NOT:
        return not _condition_matches_target(condition.condition, target_key, known_facts)
    if condition.node is None or condition.node.kind != NodeSelectorKind.CURRENT_TARGET:
        return True
    if condition.fact_key is None:
        return False
    current = known_facts.get((target_key, condition.fact_key))
    if current is None:
        return False
    if condition.kind == ConditionKind.FACT_EQUALS:
        return bool(current == condition.value)
    if condition.kind == ConditionKind.FACT_NOT_EQUALS:
        return bool(current != condition.value)
    if condition.kind == ConditionKind.FACT_IN:
        return bool(current in condition.values)
    return True


def _selector_name(selector: Any) -> str:
    if selector is None:
        return "unknown"
    if selector.kind == NodeSelectorKind.CURRENT_TARGET:
        return "target_key"
    if selector.kind == NodeSelectorKind.ACTION_SOURCE:
        return "source_key"
    if selector.kind == NodeSelectorKind.EXPLICIT:
        return str(selector.node_key)
    return "related_node"


def _value_expression(expression: Any) -> StrictScalar | dict[str, object]:
    if expression.source.value == "LITERAL":
        return cast(StrictScalar, expression.literal)
    return {"from_parameter": expression.parameter_key}


def _integer_expression(expression: Any) -> int | dict[str, object]:
    if expression.source.value == "LITERAL":
        return int(expression.literal) * int(expression.multiplier)
    return {
        "from_parameter": expression.parameter_key,
        "multiplier": expression.multiplier,
    }


def _deduplicate(values: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for value in values:
        marker = repr(sorted(value.items()))
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result
