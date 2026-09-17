"""Shared Knowledge-safe projection for Player and Planner consumers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.agent.planner_contract import (
    json_scope_identity,
    planner_resource_requirement_from_condition,
    planner_resource_requirements,
    planner_source_requirement_predicates,
    planner_target_contracts,
)
from app.domain.enums import (
    RelationVisibility,
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.resources import is_runtime_known_inflow_pool, resource_pool_initial_states
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import (
    ActionBehavior,
    ActionDefinitionV2,
    ConditionKind,
    ConditionV2,
    NodeSelectorKind,
    ScenarioDefinitionV2,
    relation_identity,
)
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceRelationKnowledge,
    GameInstanceResourceState,
)


def resource_knowledge_status(
    *,
    inventory_visibility: ResourceInventoryVisibility,
    survey_completed: bool,
    has_visible_pool: bool,
) -> str:
    """Classify the public Knowledge state of one Region/resource pair.

    This helper intentionally depends only on public Knowledge.  Persisted
    hidden Pool rows are Truth and must never change the Planner/Validator
    classification.  A visible Pool is known; an absent visible Pool is a
    known zero only after a visible, completed survey.
    """

    if inventory_visibility == ResourceInventoryVisibility.VISIBLE and survey_completed:
        return "KNOWN" if has_visible_pool else "KNOWN_ZERO"
    return "KNOWN" if has_visible_pool else "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RegionResourceKnowledgeView:
    region_key: str
    resource_inventory_visibility: ResourceInventoryVisibility
    resource_survey_completed: bool


@dataclass(frozen=True, slots=True)
class KnownResourcePoolView:
    pool_key: str
    resource_key: str
    region_key: str | None
    facility_key: str | None
    quantity: int
    available_quantity: int
    availability: ResourcePoolAvailability
    availability_requirement: dict[str, Any] | None
    availability_requirement_status: str | None


class SharedKnowledgeProjection:
    """Project only gameplay Knowledge, never hidden runtime Truth."""

    def __init__(self, db: Session, scope: RuntimeScope, definition: ScenarioDefinitionV2) -> None:
        self.db = db
        self.scope = scope
        self.definition = definition
        self._region_states: dict[str, RegionResourceKnowledgeView] | None = None
        self._visible_pools: tuple[KnownResourcePoolView, ...] | None = None
        self._target_knowledge_contracts_cache: tuple[dict[str, Any], ...] | None = None
        self._target_owned_resource_requirement_keys: set[tuple[str, str]] = set()
        self._global_resource_requirements_cache: dict[
            str, tuple[dict[str, Any], ...]
        ] | None = None
        self._static_pool_requirements: dict[tuple[str, str | None, str], dict[str, Any]] = {}
        for pool in resource_pool_initial_states(definition):
            requirement = pool.availability_requirement
            if requirement is not None:
                self._static_pool_requirements[
                    (pool.resource_key, pool.region_key, pool.pool_key)
                ] = requirement.model_dump(mode="json")

    @property
    def region_keys(self) -> tuple[str, ...]:
        locality = self.definition.metadata.locality
        if not locality.enabled or locality.region_node_type_key is None:
            return ()
        return tuple(
            sorted(
                node.key
                for node in self.definition.world.nodes
                if node.node_type_key == locality.region_node_type_key
            )
        )

    def known_node_rows(self) -> tuple[GameInstanceNodeState, ...]:
        return tuple(
            self.db.scalars(
                select(GameInstanceNodeState).where(
                    GameInstanceNodeState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceNodeState.visibility == Visibility.KNOWN,
                )
            )
        )

    def known_fact_rows(self) -> tuple[GameInstanceFactState, ...]:
        known_nodes = {row.node_key for row in self.known_node_rows()}
        return tuple(
            row
            for row in self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceFactState.visibility == Visibility.KNOWN,
                )
            )
            if row.node_key in known_nodes
        )

    def known_relations(self) -> tuple[dict[str, Any], ...]:
        known_nodes = {row.node_key for row in self.known_node_rows()}
        try:
            relation_rows = tuple(
                self.db.scalars(
                    select(GameInstanceRelationKnowledge).where(
                        GameInstanceRelationKnowledge.game_instance_id
                        == self.scope.game_instance_id
                    )
                )
            )
        except SQLAlchemyError:
            relation_rows = ()
        visibility_by_key = {row.relation_key: row.visibility for row in relation_rows}
        known: list[dict[str, Any]] = []
        for item in self.definition.world.relations:
            if (
                visibility_by_key.get(relation_identity(item), item.initial_visibility)
                != RelationVisibility.VISIBLE
                or item.source_node_key not in known_nodes
                or item.target_node_key not in known_nodes
            ):
                continue
            projection = item.model_dump(mode="json")
            projection["relation_key"] = relation_identity(item)
            known.append(projection)
        return tuple(known)

    def public_resource_source_hints(self) -> tuple[dict[str, Any], ...]:
        """Project authored, quantity-free Resource source background.

        These rows are immutable ScenarioVersion metadata, not runtime Pool
        Truth.  Keeping the projection separate prevents hidden inventory,
        facility state, and storage identities from crossing the Knowledge
        boundary.
        """

        return tuple(
            {
                "resource_key": hint.resource_key,
                **(
                    {"primary_region_key": hint.primary_region_key}
                    if hint.primary_region_key is not None
                    else {}
                ),
                **(
                    {"candidate_region_keys": list(hint.candidate_region_keys)}
                    if hint.candidate_region_keys
                    else {}
                ),
            }
            for hint in sorted(
                self.definition.public_knowledge.resource_source_hints,
                key=lambda item: item.resource_key,
            )
        )

    def known_action_requirements(
        self,
        *,
        target_contracts: tuple[dict[str, Any], ...] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Expose action requirements that are already supported by Knowledge.

        Static role/relation contracts are public Scenario metadata.  Dynamic
        Fact requirements are included only for Facts whose current visibility
        is KNOWN; hidden Facts and their values are omitted entirely.  The same
        projection is consumed by both PlanningContext and Player API.  Target
        roles are copied only from the shared target contract projection; the
        authored ``target_actor_roles`` mapping is never a second visibility
        authority here.
        """

        known_nodes = {row.node_key for row in self.known_node_rows()}
        known_facts = {
            (row.node_key, row.fact_key): row.truth_value for row in self.known_fact_rows()
        }
        role_names = {role.key: role.name for role in self.definition.actors.roles}
        target_contracts = (
            self.target_knowledge_contracts()
            if target_contracts is None
            else target_contracts
        )
        global_resource_requirements = self.global_action_resource_requirements(
            target_contracts=target_contracts,
        )
        known_relations = self.known_relations()
        target_roles_by_action: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for contract in target_contracts:
            role_key = contract.get("required_actor_role_key")
            target_key = contract.get("target_key")
            action_key = contract.get("action_key")
            if not (
                isinstance(action_key, str)
                and isinstance(target_key, str)
                and isinstance(role_key, str)
            ):
                continue
            target_roles_by_action[action_key].append(
                {
                    "target_key": target_key,
                    "required_actor_role_key": role_key,
                    "required_actor_role_name": contract.get(
                        "required_actor_role_name",
                        role_names.get(role_key, role_key),
                    ),
                }
            )
        for action_roles in target_roles_by_action.values():
            action_roles.sort(key=lambda item: str(item["target_key"]))
        result: list[dict[str, Any]] = []
        for action in sorted(self.definition.actions, key=lambda item: item.key):
            resource_requirements = global_resource_requirements.get(action.key, ())
            if not (
                action.required_actor_role_key is not None
                or target_roles_by_action.get(action.key)
                or action.source_relation_type_key is not None
                or action.behavior
                in {
                    ActionBehavior.SUPPLY_POWER,
                    ActionBehavior.DEPLOY_HEAVY_ENGINEERING_SUPPORT,
                }
                or resource_requirements
            ):
                continue
            entry: dict[str, Any] = {
                "action_key": action.key,
                "action_name": action.name,
            }
            if action.required_actor_role_key is not None:
                entry["required_actor_role_key"] = action.required_actor_role_key
                entry["required_actor_role_name"] = role_names.get(
                    action.required_actor_role_key,
                    action.required_actor_role_key,
                )
            if target_roles_by_action.get(action.key):
                entry["target_actor_roles"] = target_roles_by_action[action.key]
            if action.source_relation_type_key is not None:
                entry["source_relation_type_key"] = action.source_relation_type_key
                source_requirements = self._known_source_requirement_sets(
                    action,
                    known_nodes=known_nodes,
                    known_facts=known_facts,
                    known_relations=known_relations,
                )
                if source_requirements:
                    entry["source_requirements"] = source_requirements
            known_preconditions: list[dict[str, Any]] = []
            for rule in self.definition.rules:
                if rule.action_key != action.key or rule.phase.value != "PREFLIGHT":
                    continue
                for condition in self._condition_leaves(rule.condition):
                    if condition.kind not in {
                        ConditionKind.FACT_EQUALS,
                        ConditionKind.FACT_NOT_EQUALS,
                        ConditionKind.FACT_IN,
                        ConditionKind.FACT_COMPARE,
                    }:
                        continue
                    if condition.node is None or condition.fact_key is None:
                        continue
                    for node_key in self._known_condition_nodes(
                        condition.node.kind,
                        condition.node.node_key,
                        known_nodes,
                    ):
                        current_value = known_facts.get((node_key, condition.fact_key))
                        if current_value is None:
                            continue
                        projection = {
                            "node_key": node_key,
                            "fact_key": condition.fact_key,
                            "selector": condition.node.kind.value,
                            "current_value": current_value,
                            "failure_condition": self._condition_summary(condition),
                        }
                        if projection not in known_preconditions:
                            known_preconditions.append(projection)
            if known_preconditions:
                entry["known_preconditions"] = known_preconditions
            if resource_requirements:
                entry["resource_requirements"] = [dict(item) for item in resource_requirements]
            result.append(entry)
        return tuple(result)

    def _known_source_requirement_sets(
        self,
        action: ActionDefinitionV2,
        *,
        known_nodes: set[str],
        known_facts: dict[tuple[str, str], Any],
        known_relations: tuple[dict[str, Any], ...],
    ) -> list[dict[str, Any]]:
        predicates = planner_source_requirement_predicates(self.definition, action)
        if not predicates:
            return []
        source_node_keys = sorted(
            {
                str(relation["source_node_key"])
                for relation in known_relations
                if relation.get("relation_type_key") == action.source_relation_type_key
                and isinstance(relation.get("source_node_key"), str)
                and str(relation["source_node_key"]) in known_nodes
            }
        )
        requirements: list[dict[str, Any]] = []
        for source_node_key in source_node_keys:
            current_values = {
                str(predicate["fact_key"]): known_facts.get(
                    (source_node_key, str(predicate["fact_key"]))
                )
                for predicate in predicates
                if isinstance(predicate.get("fact_key"), str)
            }
            if (
                len(current_values) != len(predicates)
                or any(value is None for value in current_values.values())
            ):
                # An incomplete current source contract is UNKNOWN. Omit the
                # entire group so Player cannot mistake it for not-ready or
                # receive hidden prerequisite identity/value data.
                continue
            conditions = [dict(predicate) for predicate in predicates]
            satisfied = all(
                _source_predicate_is_satisfied(
                    current_values[str(predicate["fact_key"])],
                    predicate,
                )
                for predicate in predicates
            )
            requirements.append(
                {
                    "source_node_key": source_node_key,
                    "kind": (
                        "POWER_SOURCE_READINESS"
                        if action.behavior == ActionBehavior.SUPPLY_POWER
                        else "SOURCE_REQUIREMENTS"
                    ),
                    "status": "SATISFIED" if satisfied else "UNSATISFIED",
                    "conditions": conditions,
                }
            )
        return requirements

    def global_action_resource_requirements(
        self,
        *,
        target_contracts: tuple[dict[str, Any], ...] | None = None,
    ) -> dict[str, tuple[dict[str, Any], ...]]:
        """Return Action-level resource requirements not owned by one target.

        A requirement is moved to a target contract only when the shared
        projection can prove both a unique public target and a matching
        explicit target Region. Ambiguous or actor-scoped requirements remain
        Action-global.
        """

        if target_contracts is None and self._global_resource_requirements_cache is not None:
            return self._global_resource_requirements_cache
        resource_projection = self.planner_resources()
        known_resources = resource_projection.get("resources", {})
        known_resource_knowledge = resource_projection.get("regions", {})
        owned_keys = self._target_owned_resource_requirement_keys
        result: dict[str, tuple[dict[str, Any], ...]] = {}
        for action in sorted(self.definition.actions, key=lambda item: item.key):
            requirements = planner_resource_requirements(
                self.definition,
                action,
                known_resources=known_resources if isinstance(known_resources, dict) else None,
                known_resource_knowledge=(
                    known_resource_knowledge
                    if isinstance(known_resource_knowledge, dict)
                    else None
                ),
            )
            remaining = tuple(
                item
                for item in requirements
                if (
                    str(item.get("resource_key", "")),
                    json_scope_identity(item.get("scope")),
                )
                not in owned_keys
            )
            if remaining:
                result[action.key] = remaining
        if target_contracts is None:
            self._global_resource_requirements_cache = result
        return result

    def target_knowledge_contracts(self) -> tuple[dict[str, Any], ...]:
        """Return the single Knowledge-safe target contract projection.

        Target-specific roles, costs, special prerequisites, applicability and
        deterministic target effects all originate here.  A target-qualified
        PREFLIGHT selector is a visibility gate: authored identity is not a
        substitute for a KNOWN current Fact.  Actions without such a selector
        use only public node/action eligibility (known node, interaction and
        node type).  Resource-pool survey knowledge stays on its independent
        resource channel and is consumed by the same target contracts through
        ``planner_resources``.

        The returned shape is intentionally close to
        ``PublicTargetActionContractResponse`` so Player and Planner adapters
        can use different DTOs without re-deciding visibility.
        """

        if self._target_knowledge_contracts_cache is not None:
            return self._target_knowledge_contracts_cache

        known_nodes = {row.node_key for row in self.known_node_rows()}
        known_facts = {
            (row.node_key, row.fact_key): row.truth_value for row in self.known_fact_rows()
        }
        resource_projection = self.planner_resources()
        known_resources = resource_projection.get("resources", {})
        known_resource_knowledge = resource_projection.get("regions", {})
        known_relation_keys = {
            str(item["relation_key"])
            for item in self.known_relations()
            if item.get("relation_key") is not None
        }
        known_pool_keys = {item.pool_key for item in self.visible_resource_pools()}
        role_names = {role.key: role.name for role in self.definition.actors.roles}
        self._target_owned_resource_requirement_keys.clear()
        selector_rules_by_action: dict[
            str,
            list[tuple[tuple[tuple[ConditionV2, bool], ...], tuple[ConditionV2, ...]]],
        ] = defaultdict(list)
        for rule in self.definition.rules:
            if rule.phase.value != "PREFLIGHT":
                continue
            leaves = self._condition_leaves_with_polarity(rule.condition)
            selector_conditions = tuple(
                condition
                for condition, positive in leaves
                if positive
                and condition.node is not None
                and condition.node.kind == NodeSelectorKind.CURRENT_TARGET
                and condition.kind in {ConditionKind.FACT_EQUALS, ConditionKind.FACT_IN}
            )
            if selector_conditions:
                if rule.action_key is None:
                    continue
                selector_rules_by_action[rule.action_key].append(
                    (leaves, selector_conditions)
                )

        result: list[dict[str, Any]] = []
        for action in sorted(self.definition.actions, key=lambda item: item.key):
            action_resource_requirements = planner_resource_requirements(
                self.definition,
                action,
                known_resources=known_resources if isinstance(known_resources, dict) else None,
                known_resource_knowledge=(
                    known_resource_knowledge
                    if isinstance(known_resource_knowledge, dict)
                    else None
                ),
            )
            target_role_by_key = {
                item.target_key: item.required_actor_role_key
                for item in action.target_actor_roles
            }
            eligible_targets = tuple(
                sorted(
                    node.key
                    for node in self.definition.world.nodes
                    if node.key in known_nodes
                    and action.required_interaction_key in node.interaction_keys
                    and (
                        not action.target_node_type_keys
                        or node.node_type_key in action.target_node_type_keys
                    )
                )
            )
            selector_rules = selector_rules_by_action.get(action.key, [])
            unique_target_key = eligible_targets[0] if len(eligible_targets) == 1 else None
            for target_key in eligible_targets:
                matched_rules = [
                    (leaves, selector_conditions)
                    for leaves, selector_conditions in selector_rules
                    if all(
                        self._target_selector_matches(
                            condition,
                            target_key,
                            known_facts,
                            allow_authored_identity=False,
                        )
                        for condition in selector_conditions
                    )
                ]
                if selector_rules and not matched_rules:
                    # A target-specific authored identity is not enough to
                    # make a hidden target contract player/planner-visible.
                    continue

                requirement: dict[str, Any] = {"action_key": action.key}
                # A target contract carries only a target-specific role.  A
                # global action role is already represented once on the
                # action contract and is not repeated for every target.
                required_actor_role = target_role_by_key.get(target_key)
                if required_actor_role is not None:
                    requirement["required_actor_role_key"] = required_actor_role

                for leaves, selector_conditions in matched_rules:
                    selector_ids = {id(condition) for condition in selector_conditions}
                    for condition, positive in leaves:
                        if id(condition) in selector_ids:
                            continue
                        if condition.kind == ConditionKind.RESOURCE_COMPARE and positive:
                            cost = self._planner_resource_cost(condition)
                            if cost is not None:
                                costs = requirement.setdefault("cost", {})
                                assert isinstance(costs, dict)
                                resource_key, amount = cost
                                previous = costs.get(resource_key)
                                costs[resource_key] = max(previous or 0, amount)
                            resource_requirement = planner_resource_requirement_from_condition(
                                condition,
                                target_key=target_key,
                                known_resources=(
                                    known_resources if isinstance(known_resources, dict) else None
                                ),
                                known_resource_knowledge=(
                                    known_resource_knowledge
                                    if isinstance(known_resource_knowledge, dict)
                                    else None
                                ),
                            )
                            if resource_requirement is not None:
                                requirements = requirement.setdefault(
                                    "resource_requirements", []
                                )
                                assert isinstance(requirements, list)
                                if resource_requirement not in requirements:
                                    requirements.append(resource_requirement)
                            continue
                        special = self._planner_fact_requirement(
                            condition,
                            positive=positive,
                            target_key=target_key,
                            known_facts=known_facts,
                            include_unknown=False,
                        )
                        if special is not None:
                            special_requirements = requirement.setdefault(
                                "special_requirements", []
                            )
                            assert isinstance(special_requirements, list)
                            if special not in special_requirements:
                                special_requirements.append(special)

                if target_key == unique_target_key:
                    for resource_requirement in action_resource_requirements:
                        if not self._resource_requirement_is_target_owned(
                            resource_requirement,
                            target_key=target_key,
                        ):
                            continue
                        resource_requirements = requirement.setdefault(
                            "resource_requirements", []
                        )
                        assert isinstance(resource_requirements, list)
                        if resource_requirement not in resource_requirements:
                            resource_requirements.append(dict(resource_requirement))
                        cost = requirement.setdefault("cost", {})
                        assert isinstance(cost, dict)
                        resource_key = str(resource_requirement["resource_key"])
                        minimum = resource_requirement.get("minimum")
                        if isinstance(minimum, int) and minimum > int(cost.get(resource_key, 0)):
                            cost[resource_key] = minimum
                        self._target_owned_resource_requirement_keys.add(
                            (
                                resource_key,
                                json_scope_identity(resource_requirement.get("scope")),
                            )
                        )

                # The target has passed the shared Knowledge gate above.  Only
                # now may the generic planner helper join deterministic target
                # effect metadata for this one authorized target.
                effect_contract = planner_target_contracts(
                    self.definition,
                    action,
                    known_node_keys=known_nodes,
                    known_facts=known_facts,
                    known_relation_keys=known_relation_keys,
                    known_pool_keys=known_pool_keys,
                    allowed_target_keys={target_key},
                    include_authored_hidden_target_effects=True,
                ).get(target_key, {})
                effects = effect_contract.get("effects")
                if isinstance(effects, list) and effects:
                    requirement["effects"] = [dict(item) for item in effects]

                # A bare target applicability marker is useful to the Planner
                # only when it carries a target binding/effect.  Do not send
                # empty inspect/travel markers to the Player DTO.
                if not any(
                    requirement.get(key)
                    for key in (
                        "required_actor_role_key",
                        "cost",
                        "resource_requirements",
                        "special_requirements",
                        "effects",
                    )
                ):
                    continue

                entry: dict[str, Any] = {
                    "target_key": target_key,
                    "action_key": action.key,
                    "action_name": action.name,
                }
                if required_actor_role is not None:
                    entry["required_actor_role_key"] = required_actor_role
                    entry["required_actor_role_name"] = role_names.get(
                        required_actor_role,
                        required_actor_role,
                    )
                if action.source_relation_type_key is not None:
                    entry["source_relation_type_key"] = action.source_relation_type_key
                for key in (
                    "cost",
                    "resource_requirements",
                    "special_requirements",
                    "effects",
                ):
                    value = requirement.get(key)
                    if value:
                        entry[key] = value
                result.append(entry)

        self._target_knowledge_contracts_cache = tuple(result)
        return self._target_knowledge_contracts_cache

    def _resource_requirement_is_target_owned(
        self,
        requirement: dict[str, Any],
        *,
        target_key: str,
    ) -> bool:
        scope = requirement.get("scope")
        if not isinstance(scope, dict) or scope.get("kind") != "EXPLICIT":
            return False
        target_region = self._static_region_for_node(target_key)
        return target_region is not None and scope.get("node_key") == target_region

    def _static_region_for_node(self, node_key: str) -> str | None:
        locality = self.definition.metadata.locality
        if locality.region_node_type_key is None:
            return None
        node = self.definition.world.node(node_key)
        if node is None:
            return None
        if node.node_type_key == locality.region_node_type_key:
            return node_key
        relation_type = locality.located_in_relation_type_key
        if relation_type is None:
            return None
        regions = tuple(
            relation.target_node_key
            for relation in self.definition.world.relations
            if (
                relation.source_node_key == node_key
                and relation.relation_type_key == relation_type
            )
        )
        return regions[0] if len(regions) == 1 else None

    def planner_action_requirements(self) -> tuple[dict[str, Any], ...]:
        """Return target requirements derived from the shared target projection.

        This is a Planner-shaped adapter only.  It contains no independent
        visibility logic and never enriches a target from authored hidden
        Scenario rules.
        """

        by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for contract in self.target_knowledge_contracts():
            target_key = contract.get("target_key")
            action_key = contract.get("action_key")
            if not isinstance(target_key, str) or not isinstance(action_key, str):
                continue
            requirement: dict[str, Any] = {"action_key": action_key}
            for key in (
                "required_actor_role_key",
                "cost",
                "resource_requirements",
                "special_requirements",
                "effects",
            ):
                value = contract.get(key)
                if value:
                    requirement[key] = value
            by_target[target_key].append(requirement)
        return tuple(
            {
                "target_key": target_key,
                "requirements": sorted(
                    requirements,
                    key=lambda item: str(item.get("action_key", "")),
                ),
            }
            for target_key, requirements in sorted(by_target.items())
            if requirements
        )

    def known_target_action_contracts(
        self,
        *,
        target_contracts: tuple[dict[str, Any], ...] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Expose the shared target projection in a Player-safe DTO shape.

        Planner adapters continue to consume ``target_knowledge_contracts``
        directly because they may need authored effect metadata.  The legacy
        Player contract remains additive/compatible, but Fact identities are
        filtered against the same current Knowledge projection so hidden
        target effects cannot cross the API boundary.
        """

        source = self.target_knowledge_contracts() if target_contracts is None else target_contracts
        known_facts = {
            (row.node_key, row.fact_key)
            for row in self.known_fact_rows()
        }
        result: list[dict[str, Any]] = []
        for item in source:
            projected = dict(item)
            target_key = projected.get("target_key")
            safe_effects: list[dict[str, Any]] = []
            if isinstance(target_key, str):
                for effect in projected.get("effects", []):
                    if not isinstance(effect, dict):
                        continue
                    if effect.get("type") != "FACT_MUTATION":
                        continue
                    effect_target = effect.get("target")
                    resolved_target = (
                        target_key
                        if (
                            effect_target == "target_key"
                            or effect_target == "CURRENT_TARGET"
                            or effect_target == target_key
                        )
                        else effect_target
                    )
                    fact_key = effect.get("fact_key")
                    if (
                        isinstance(resolved_target, str)
                        and isinstance(fact_key, str)
                        and (resolved_target, fact_key) in known_facts
                    ):
                        safe_effects.append(dict(effect))
            if safe_effects:
                projected["effects"] = safe_effects
            else:
                projected.pop("effects", None)
            result.append(projected)
        return tuple(result)

    def known_producer_bindings(
        self,
        *,
        target_contracts: tuple[dict[str, Any], ...] | None = None,
        action_requirements: tuple[dict[str, Any], ...] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Project player-safe producer bindings for Facility Presentation.

        A binding is the shared semantic owner of one Action/target output and
        all currently safe requirements needed by that Action.  Output Fact
        identities are emitted only when the corresponding Fact is already
        known; authored hidden effects are never copied into this Player DTO.
        The frontend therefore consumes statuses and typed requirements rather
        than evaluating Scenario predicates itself.
        """

        contracts = (
            self.target_knowledge_contracts()
            if target_contracts is None
            else target_contracts
        )
        action_rows = (
            self.known_action_requirements(target_contracts=contracts)
            if action_requirements is None
            else action_requirements
        )
        action_by_key = {
            str(item["action_key"]): item
            for item in action_rows
            if isinstance(item, dict) and isinstance(item.get("action_key"), str)
        }
        known_nodes = {row.node_key for row in self.known_node_rows()}
        known_facts = {
            (row.node_key, row.fact_key): row.truth_value
            for row in self.known_fact_rows()
        }
        bindings: list[dict[str, Any]] = []
        for contract in sorted(
            contracts,
            key=lambda item: (str(item.get("target_key", "")), str(item.get("action_key", ""))),
        ):
            target_key = contract.get("target_key")
            action_key = contract.get("action_key")
            action_name = contract.get("action_name")
            if not (
                isinstance(target_key, str)
                and isinstance(action_key, str)
                and isinstance(action_name, str)
            ):
                continue
            outputs: list[dict[str, Any]] = []
            binding_source_node_key, source_binding_key = (
                self._producer_binding_source_identity(contract)
            )
            for effect in contract.get("effects", []):
                if not isinstance(effect, dict):
                    continue
                if effect.get("type") != "FACT_MUTATION":
                    continue
                effect_target = effect.get("target")
                if not (
                    effect_target == "target_key"
                    or effect_target == "CURRENT_TARGET"
                    or effect_target == target_key
                ):
                    continue
                fact_key = effect.get("fact_key")
                desired_value = effect.get("value")
                if not isinstance(fact_key, str) or type(desired_value) not in {str, int, bool}:
                    continue
                identity = (target_key, fact_key)
                if identity not in known_facts:
                    # The effect identity itself may be authored and useful to
                    # the Planner, but it is not player-safe presentation data.
                    continue
                output = {
                    "semantic_key": f"{target_key}.{fact_key}",
                    "target_key": target_key,
                    "fact_key": fact_key,
                    "desired_value": desired_value,
                    "status": (
                        "SATISFIED"
                        if known_facts[identity] == desired_value
                        else "UNSATISFIED"
                    ),
                }
                if output not in outputs:
                    outputs.append(output)
            if not outputs:
                continue

            action_row = action_by_key.get(action_key, {})
            requirements: list[dict[str, Any]] = []
            requirement_keys: set[str] = set()

            def add_requirement(
                key: str,
                value: dict[str, Any],
                *,
                _requirement_keys: set[str] = requirement_keys,
                _requirements: list[dict[str, Any]] = requirements,
            ) -> None:
                if key in _requirement_keys:
                    return
                _requirement_keys.add(key)
                _requirements.append({"key": key, **value})

            resource_requirements: list[dict[str, Any]] = []
            for source in (
                contract.get("resource_requirements", []),
                action_row.get("resource_requirements", []),
            ):
                if isinstance(source, list):
                    for item in source:
                        if not isinstance(item, dict):
                            continue
                        owner_target_key = item.get("owner_target_key")
                        scoped_target_key = (
                            item.get("scope", {}).get("target_key")
                            if isinstance(item.get("scope"), dict)
                            else None
                        )
                        if (
                            isinstance(owner_target_key, str)
                            and owner_target_key != target_key
                        ) or (
                            isinstance(scoped_target_key, str)
                            and scoped_target_key != target_key
                        ):
                            continue
                        owner_source_key = item.get("owner_source_key")
                        if isinstance(owner_source_key, str) and (
                            binding_source_node_key is None
                            or owner_source_key != binding_source_node_key
                        ):
                            continue
                        if isinstance(item.get("source_node_key"), str) and (
                            binding_source_node_key is None
                            or item["source_node_key"] != binding_source_node_key
                        ):
                            continue
                        resource_requirements.append(item)
            for resource in resource_requirements:
                resource_key = resource.get("resource_key")
                minimum = resource.get("minimum")
                if not isinstance(resource_key, str) or not isinstance(minimum, int):
                    continue
                scope = resource.get("scope")
                scope = scope if isinstance(scope, dict) else None
                known_status = resource.get("known_status")
                known_available = resource.get("known_available")
                if known_status == "UNKNOWN":
                    status = "UNKNOWN"
                elif isinstance(known_available, int) and not isinstance(known_available, bool):
                    status = "SATISFIED" if known_available >= minimum else "UNSATISFIED"
                else:
                    status = "UNKNOWN"
                scope_identity = json_scope_identity(scope)
                add_requirement(
                    f"{action_key}:{target_key}:resource:{resource_key}:{scope_identity}:{minimum}",
                    {
                        "kind": "RESOURCE",
                        "status": status,
                        "resource_key": resource_key,
                        "minimum": minimum,
                        "scope": scope,
                        **(
                            {"known_status": known_status}
                            if known_status in {"KNOWN", "KNOWN_ZERO", "UNKNOWN"}
                            else {}
                        ),
                        **(
                            {"known_available": known_available}
                            if isinstance(known_available, int)
                            and not isinstance(known_available, bool)
                            else {}
                        ),
                    },
                )
            cost = contract.get("cost")
            if isinstance(cost, dict):
                for resource_key, amount in cost.items():
                    if not isinstance(resource_key, str) or not isinstance(amount, int):
                        continue
                    # A raw authored cost is not by itself a Player-safe
                    # resource identity.  Keep it only when the shared
                    # projection already exposed the same resource through a
                    # typed requirement (for example after resource survey).
                    if not any(
                        item.get("resource_key") == resource_key
                        for item in resource_requirements
                    ):
                        continue
                    if any(
                        item.get("resource_key") == resource_key
                        and item.get("minimum") == amount
                        for item in resource_requirements
                    ):
                        continue
                    add_requirement(
                        f"{action_key}:{target_key}:resource:{resource_key}::{amount}",
                        {
                            "kind": "RESOURCE",
                            "status": "UNKNOWN",
                            "resource_key": resource_key,
                            "minimum": amount,
                        },
                    )

            role_values = [
                (
                    contract.get("required_actor_role_key"),
                    contract.get("required_actor_role_name"),
                ),
                (
                    action_row.get("required_actor_role_key"),
                    action_row.get("required_actor_role_name"),
                ),
            ]
            for role_key, role_name in role_values:
                if not isinstance(role_key, str) and not isinstance(role_name, str):
                    continue
                role_identity = role_key if isinstance(role_key, str) else str(role_name)
                add_requirement(
                    f"{action_key}:{target_key}:role:{role_identity}",
                    {
                        "kind": "ROLE",
                        **({"role_key": role_key} if isinstance(role_key, str) else {}),
                        **({"display_name": role_name} if isinstance(role_name, str) else {}),
                    },
                )

            for special in contract.get("special_requirements", []):
                if not isinstance(special, dict):
                    continue
                self._add_known_fact_requirement(
                    add_requirement,
                    action_key=action_key,
                    target_key=target_key,
                    requirement=special,
                    known_facts=known_facts,
                )

            for precondition in self._known_global_action_preconditions(
                action_key,
                known_nodes=known_nodes,
                known_facts=known_facts,
            ):
                if not isinstance(precondition, dict):
                    continue
                node_key = precondition.get("node_key")
                fact_key = precondition.get("fact_key")
                failure = precondition.get("failure_condition")
                if not isinstance(node_key, str) or not isinstance(fact_key, str):
                    continue
                if (node_key, fact_key) not in known_facts or not isinstance(failure, dict):
                    continue
                required = self._required_predicate_from_failure(failure)
                if required is None:
                    continue
                requirement = {"node_key": node_key, "fact_key": fact_key, **required}
                self._add_known_fact_requirement(
                    add_requirement,
                    action_key=action_key,
                    target_key=target_key,
                    requirement=requirement,
                    known_facts=known_facts,
                )

            for source in action_row.get("source_requirements", []):
                if not isinstance(source, dict):
                    continue
                # The action-level source list describes candidate sources,
                # not the source of this target binding.  It is only safe to
                # join one row when the canonical binding selected an exact
                # source identity.
                if binding_source_node_key is None:
                    continue
                candidate_source_node_key = source.get("source_node_key")
                source_kind = source.get("kind")
                candidate_source_binding_key = source.get("source_binding_key")
                if not isinstance(candidate_source_binding_key, str):
                    candidate_source_binding_key = source.get("binding_key")
                if (
                    not isinstance(candidate_source_node_key, str)
                    or not isinstance(source_kind, str)
                    or candidate_source_node_key != binding_source_node_key
                    or (
                        source_binding_key is not None
                        and isinstance(candidate_source_binding_key, str)
                        and candidate_source_binding_key != source_binding_key
                    )
                ):
                    continue
                add_requirement(
                    f"{action_key}:{target_key}:source:{candidate_source_node_key}:{source_kind}",
                    {
                        "kind": "SOURCE",
                        "status": source.get("status")
                        if source.get("status") in {"SATISFIED", "UNSATISFIED"}
                        else "UNKNOWN",
                        "source_node_key": candidate_source_node_key,
                        "source_kind": source_kind,
                        "conditions": [
                            dict(item)
                            for item in source.get("conditions", [])
                            if isinstance(item, dict)
                        ],
                    },
                )

            bindings.append(
                {
                    "binding_key": (
                        f"{action_key}:{target_key}"
                        + (
                            f":binding:{source_binding_key}"
                            if source_binding_key is not None
                            else f":source:{binding_source_node_key}"
                            if binding_source_node_key is not None
                            else ""
                        )
                    ),
                    "action_key": action_key,
                    "action_name": action_name,
                    "target_key": target_key,
                    "producer_kind": "ACTION_PRODUCED_STATE",
                    "outputs": outputs,
                    "requirements": requirements,
                    **(
                        {"source_node_key": binding_source_node_key}
                        if binding_source_node_key is not None
                        else {}
                    ),
                    **(
                        {"source_binding_key": source_binding_key}
                        if source_binding_key is not None
                        else {}
                    ),
                }
            )

        resource_names = {item.key: item.name for item in self.definition.world.resources}
        pools_by_resource: dict[tuple[str, str], list[KnownResourcePoolView]] = defaultdict(list)
        for pool in self.visible_resource_pools():
            if pool.facility_key is None or pool.facility_key not in known_nodes:
                continue
            pools_by_resource[(pool.facility_key, pool.resource_key)].append(pool)
        for (target_key, resource_key), pools in sorted(pools_by_resource.items()):
            available = all(
                pool.availability == ResourcePoolAvailability.AVAILABLE
                for pool in pools
            )
            requirements: list[dict[str, Any]] = []
            for pool in pools:
                requirement = pool.availability_requirement
                if not isinstance(requirement, dict):
                    continue
                node_key = requirement.get("node_key")
                fact_key = requirement.get("fact_key")
                expected = requirement.get("value")
                if (
                    not isinstance(node_key, str)
                    or not isinstance(fact_key, str)
                    or (node_key, fact_key) not in known_facts
                    or type(expected) not in {str, int, bool}
                ):
                    continue
                requirements.append(
                    {
                        "key": f"resource_availability:{target_key}:{resource_key}"
                        f":fact:{node_key}:{fact_key}",
                        "kind": "FACT",
                        "status": (
                            "SATISFIED"
                            if known_facts[(node_key, fact_key)] == expected
                            else "UNSATISFIED"
                        ),
                        "node_key": node_key,
                        "fact_key": fact_key,
                        "operator": "EQ",
                        "value": expected,
                    }
                )
                break
            binding_key = f"resource_availability:{target_key}:{resource_key}"
            bindings.append(
                {
                    "binding_key": binding_key,
                    "action_key": f"resource_availability:{resource_key}",
                    "action_name": resource_names.get(resource_key, resource_key),
                    "target_key": target_key,
                    "producer_kind": "RESOURCE_AVAILABILITY",
                    "outputs": [{
                        "semantic_key": f"{target_key}.{resource_key}.availability",
                        "target_key": target_key,
                        "resource_key": resource_key,
                        "desired_value": "AVAILABLE",
                        "status": "SATISFIED" if available else "UNSATISFIED",
                    }],
                    "requirements": requirements,
                }
            )
        return tuple(bindings)

    def _known_global_action_preconditions(
        self,
        action_key: str,
        *,
        known_nodes: set[str],
        known_facts: dict[tuple[str, str], Any],
    ) -> tuple[dict[str, Any], ...]:
        """Return only preconditions owned by the Action as a whole.

        ``known_action_requirements`` intentionally keeps a compatibility
        aggregate for the action-oriented Player/Planner DTO.  A producer
        binding cannot consume that aggregate: a condition that mentions a
        current target, source, or related node describes selector
        applicability and belongs to a narrower binding scope.  Only rules
        made entirely of explicit-node predicates are action-global here.
        Target-scoped predicates are already projected by the canonical
        target contract's ``special_requirements``.
        """

        result: list[dict[str, Any]] = []
        for rule in self.definition.rules:
            if rule.action_key != action_key or rule.phase.value != "PREFLIGHT":
                continue
            leaves = self._condition_leaves(rule.condition)
            if any(
                condition.node is not None
                and condition.node.kind != NodeSelectorKind.EXPLICIT
                for condition in leaves
            ):
                # CURRENT_TARGET, ACTION_SOURCE, and RELATED predicates are
                # selector/binding information, never global C requirements.
                continue
            for condition in leaves:
                if condition.kind not in {
                    ConditionKind.FACT_EQUALS,
                    ConditionKind.FACT_NOT_EQUALS,
                    ConditionKind.FACT_IN,
                    ConditionKind.FACT_COMPARE,
                }:
                    continue
                if condition.node is None or condition.fact_key is None:
                    continue
                node_keys = self._known_condition_nodes(
                    condition.node.kind,
                    condition.node.node_key,
                    known_nodes,
                )
                for node_key in node_keys:
                    if (node_key, condition.fact_key) not in known_facts:
                        continue
                    required = self._required_predicate_from_failure(
                        self._condition_summary(condition)
                    )
                    if required is None:
                        continue
                    projection = {
                        "node_key": node_key,
                        "fact_key": condition.fact_key,
                        "failure_condition": self._condition_summary(condition),
                        **required,
                    }
                    if projection not in result:
                        result.append(projection)
        return tuple(result)

    @staticmethod
    def _producer_binding_source_identity(
        contract: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        """Read an optional exact source identity from a canonical binding.

        Legacy target contracts are target-only, so they return
        ``(None, None)``.  The additive fields let a future/source-parameter
        binding carry one exact source without making the Player projection
        aggregate all candidate source requirements.
        """

        source_node_key = contract.get("source_node_key")
        if not isinstance(source_node_key, str):
            source_node_key = contract.get("selected_source_node_key")
        if not isinstance(source_node_key, str):
            source_node_key = None
        source_binding_key = contract.get("source_binding_key")
        if not isinstance(source_binding_key, str):
            source_binding_key = None
        source_binding = contract.get("source_binding")
        if isinstance(source_binding, dict):
            if source_node_key is None:
                candidate = source_binding.get("source_node_key")
                if not isinstance(candidate, str):
                    candidate = source_binding.get("node_key")
                if isinstance(candidate, str):
                    source_node_key = candidate
            if source_binding_key is None:
                candidate = source_binding.get("binding_key")
                if isinstance(candidate, str):
                    source_binding_key = candidate
        return source_node_key, source_binding_key

    @staticmethod
    def _required_predicate_from_failure(
        failure: dict[str, Any],
    ) -> dict[str, Any] | None:
        kind = failure.get("kind")
        if kind == "FACT_NOT_EQUALS":
            return {"operator": "EQ", "value": failure.get("value")}
        if kind == "FACT_EQUALS":
            return {"operator": "NE", "value": failure.get("value")}
        if kind == "FACT_IN":
            return {"operator": "NOT_IN", "values": list(failure.get("values", []))}
        if kind == "FACT_COMPARE" and isinstance(failure.get("operator"), str):
            return {
                "operator": f"NOT_{failure['operator']}",
                "value": failure.get("value"),
            }
        return None

    @staticmethod
    def _add_known_fact_requirement(
        add_requirement: Any,
        *,
        action_key: str,
        target_key: str,
        requirement: dict[str, Any],
        known_facts: dict[tuple[str, str], Any],
    ) -> None:
        node_key = requirement.get("node_key")
        fact_key = requirement.get("fact_key")
        if not isinstance(node_key, str) or not isinstance(fact_key, str):
            return
        identity = (node_key, fact_key)
        if identity not in known_facts:
            return
        operator = requirement.get("operator")
        if not isinstance(operator, str):
            return
        values = requirement.get("values")
        expected_values = values if isinstance(values, list) else []
        expected = requirement.get("value")
        predicate = {
            "operator": operator,
            "value": expected,
            "values": expected_values,
        }
        status = _public_predicate_status(known_facts[identity], predicate)
        requirement_key = (
            f"{action_key}:{target_key}:fact:{node_key}:{fact_key}:"
            f"{operator}:{expected!r}:{expected_values!r}"
        )
        add_requirement(
            requirement_key,
            {
                "kind": "FACT",
                "status": status,
                "node_key": node_key,
                "fact_key": fact_key,
                "operator": operator,
                **({"value": expected} if expected is not None else {}),
                **({"values": expected_values} if expected_values else {}),
            },
        )

    @staticmethod
    def _condition_leaves(condition: ConditionV2 | None) -> tuple[ConditionV2, ...]:
        if condition is None:
            return ()
        if condition.kind in {ConditionKind.ALL, ConditionKind.ANY}:
            leaves: list[ConditionV2] = []
            for child in condition.conditions:
                leaves.extend(SharedKnowledgeProjection._condition_leaves(child))
            return tuple(leaves)
        if condition.kind == ConditionKind.NOT:
            return SharedKnowledgeProjection._condition_leaves(condition.condition)
        return (condition,)

    @staticmethod
    def _condition_leaves_with_polarity(
        condition: ConditionV2 | None,
        *,
        positive: bool = True,
    ) -> tuple[tuple[ConditionV2, bool], ...]:
        if condition is None:
            return ()
        if condition.kind in {ConditionKind.ALL, ConditionKind.ANY}:
            leaves: list[tuple[ConditionV2, bool]] = []
            for child in condition.conditions:
                leaves.extend(
                    SharedKnowledgeProjection._condition_leaves_with_polarity(
                        child,
                        positive=positive,
                    )
                )
            return tuple(leaves)
        if condition.kind == ConditionKind.NOT:
            return SharedKnowledgeProjection._condition_leaves_with_polarity(
                condition.condition,
                positive=not positive,
            )
        return ((condition, positive),)

    @staticmethod
    def _target_selector_matches(
        condition: ConditionV2,
        target_key: str,
        known_facts: dict[tuple[str, str], Any],
        *,
        allow_authored_identity: bool = False,
    ) -> bool:
        if condition.fact_key is None:
            return False
        current_value = known_facts.get((target_key, condition.fact_key))
        if current_value is None and allow_authored_identity:
            # A target-qualified Rule commonly uses an authored profile/role
            # value equal to the immutable target key (for example
            # a typed Fact-value predicate).  This is a contract identity,
            # not the current runtime Truth.  Keep the
            # target binding discoverable while never projecting the hidden
            # current value.
            if condition.kind == ConditionKind.FACT_EQUALS:
                return condition.value == target_key
            if condition.kind == ConditionKind.FACT_IN:
                return target_key in condition.values
            return False
        if condition.kind == ConditionKind.FACT_EQUALS:
            return current_value is not None and current_value == condition.value
        if condition.kind == ConditionKind.FACT_IN:
            return current_value is not None and current_value in condition.values
        return False

    @staticmethod
    def _planner_resource_cost(condition: ConditionV2) -> tuple[str, int] | None:
        if (
            condition.resource_key is None
            or condition.operator is None
            or type(condition.value) is not int
        ):
            return None
        if condition.operator.value == "LT":
            return condition.resource_key, condition.value
        if condition.operator.value == "LTE":
            return condition.resource_key, condition.value + 1
        return None

    @staticmethod
    def _planner_fact_requirement(
        condition: ConditionV2,
        *,
        positive: bool,
        target_key: str,
        known_facts: dict[tuple[str, str], Any],
        include_unknown: bool = False,
    ) -> dict[str, Any] | None:
        if condition.node is None or condition.fact_key is None:
            return None
        if condition.node.kind == NodeSelectorKind.EXPLICIT:
            node_key = condition.node.node_key
        elif condition.node.kind == NodeSelectorKind.CURRENT_TARGET:
            node_key = target_key
        else:
            return None
        if node_key is None:
            return None
        fact_is_known = (node_key, condition.fact_key) in known_facts
        if not fact_is_known and not include_unknown:
            return None
        if condition.kind == ConditionKind.FACT_EQUALS:
            operator = "NE" if positive else "EQ"
            value: Any = condition.value
        elif condition.kind == ConditionKind.FACT_NOT_EQUALS:
            operator = "EQ" if positive else "NE"
            value = condition.value
        elif condition.kind == ConditionKind.FACT_IN:
            operator = "NOT_IN" if positive else "IN"
            value = list(condition.values)
        elif condition.kind == ConditionKind.FACT_COMPARE:
            if condition.operator is None:
                return None
            operator = f"NOT_{condition.operator.value}" if positive else condition.operator.value
            value = condition.value
        else:
            return None
        projection = {
            "node_key": node_key,
            "fact_key": condition.fact_key,
            "operator": operator,
            "value": value,
        }
        if not fact_is_known:
            projection["knowledge_status"] = "UNKNOWN"
        return projection

    @staticmethod
    def _known_condition_nodes(
        selector_kind: NodeSelectorKind,
        explicit_node_key: str | None,
        known_nodes: set[str],
    ) -> tuple[str, ...]:
        if selector_kind == NodeSelectorKind.EXPLICIT:
            return (explicit_node_key,) if explicit_node_key in known_nodes else ()
        if selector_kind in {NodeSelectorKind.CURRENT_TARGET, NodeSelectorKind.ACTION_SOURCE}:
            return tuple(sorted(known_nodes))
        return ()

    @staticmethod
    def _condition_summary(condition: ConditionV2) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": condition.kind.value}
        if condition.value is not None:
            result["value"] = condition.value
        if condition.values:
            result["values"] = list(condition.values)
        if condition.operator is not None:
            result["operator"] = condition.operator.value
        return result

    def actor_rows(self) -> tuple[GameInstanceActor, ...]:
        """Return the shared active-Actor identity/location projection source."""

        return tuple(
            self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == self.scope.game_instance_id,
                    GameInstanceActor.status == "ACTIVE",
                )
            )
        )

    def region_states(self) -> dict[str, RegionResourceKnowledgeView]:
        if self._region_states is not None:
            return self._region_states
        rows: tuple[GameInstanceRegionResourceKnowledge, ...]
        try:
            rows = tuple(
                self.db.scalars(
                    select(GameInstanceRegionResourceKnowledge).where(
                        GameInstanceRegionResourceKnowledge.game_instance_id
                        == self.scope.game_instance_id
                    )
                )
            )
        except SQLAlchemyError:
            rows = ()
        by_key = {row.region_key: row for row in rows}
        self._region_states = {
            region_key: RegionResourceKnowledgeView(
                region_key=region_key,
                resource_inventory_visibility=_enum_value(
                    by_key.get(region_key),
                    "resource_inventory_visibility",
                    ResourceInventoryVisibility.VISIBLE,
                ),
                resource_survey_completed=bool(
                    getattr(by_key.get(region_key), "resource_survey_completed", True)
                ),
            )
            for region_key in self.region_keys
        }
        return self._region_states

    def visible_resource_pools(self) -> tuple[KnownResourcePoolView, ...]:
        if self._visible_pools is not None:
            return self._visible_pools
        try:
            rows = tuple(
                self.db.scalars(
                    select(GameInstanceResourceState).where(
                        GameInstanceResourceState.game_instance_id == self.scope.game_instance_id
                    )
                )
            )
        except SQLAlchemyError:
            rows = ()
        region_states = self.region_states()
        visible: list[KnownResourcePoolView] = []
        for row in rows:
            visibility = _enum_value(row, "visibility", ResourcePoolVisibility.VISIBLE)
            if visibility != ResourcePoolVisibility.VISIBLE:
                continue
            region_key = row.scope_node_key
            if region_key is not None and not is_runtime_known_inflow_pool(row.pool_key):
                region_state = region_states.get(region_key)
                if (
                    region_state is None
                    or region_state.resource_inventory_visibility
                    != ResourceInventoryVisibility.VISIBLE
                    or not region_state.resource_survey_completed
                ):
                    continue
            raw_availability_requirement = self.raw_availability_requirement_for_pool(row)
            availability_requirement = self.known_requirement(raw_availability_requirement)
            visible.append(
                KnownResourcePoolView(
                    pool_key=row.pool_key,
                    resource_key=row.resource_key,
                    region_key=region_key,
                    facility_key=row.facility_key,
                    quantity=row.value,
                    available_quantity=max(0, row.value - row.reserved_value),
                    availability=_enum_value(
                        row,
                        "availability",
                        ResourcePoolAvailability.AVAILABLE,
                    ),
                    availability_requirement=availability_requirement,
                    availability_requirement_status=self.requirement_status(
                        raw_availability_requirement
                    ),
                )
            )
        self._visible_pools = tuple(
            sorted(
                visible, key=lambda item: (item.region_key or "", item.resource_key, item.pool_key)
            )
        )
        return self._visible_pools

    def resource_intelligence(self) -> dict[str, Any]:
        """Return region/resource aggregates plus visible Pool detail."""

        resource_definitions = {item.key: item for item in self.definition.world.resources}
        grouped: dict[tuple[str | None, str], list[KnownResourcePoolView]] = defaultdict(list)
        for pool in self.visible_resource_pools():
            grouped[(pool.region_key, pool.resource_key)].append(pool)
        regions: dict[str, dict[str, Any]] = {}
        for region_key, state in self.region_states().items():
            region_node = self.definition.world.node(region_key)
            resources: dict[str, Any] = {}
            for (pool_region, resource_key), pool_rows in grouped.items():
                if pool_region != region_key:
                    continue
                summary = self._resource_summary(
                    pool_rows,
                    resource_definitions[resource_key].name,
                )
                resource = resource_definitions[resource_key]
                if resource.unit is not None:
                    summary["unit"] = resource.unit
                if resource.display_unit is not None:
                    summary["display_unit"] = resource.display_unit
                resources[resource_key] = summary
            regions[region_key] = {
                "region_name": region_node.name if region_node is not None else region_key,
                "resource_inventory_visibility": state.resource_inventory_visibility.value,
                "resource_survey_completed": state.resource_survey_completed,
                "resources": resources,
            }
        global_resources: dict[str, Any] = {}
        for (global_region_key, resource_key), pool_rows in grouped.items():
            if global_region_key is None:
                summary = self._resource_summary(
                    pool_rows, resource_definitions[resource_key].name
                )
                resource = resource_definitions[resource_key]
                if resource.unit is not None:
                    summary["unit"] = resource.unit
                if resource.display_unit is not None:
                    summary["display_unit"] = resource.display_unit
                global_resources[resource_key] = summary
        return {
            "total_regions": len(self.region_keys),
            "visible_region_count": sum(
                state.resource_inventory_visibility == ResourceInventoryVisibility.VISIBLE
                for state in self.region_states().values()
            ),
            "regions": regions,
            "global_resources": global_resources,
        }

    def planner_resources(self) -> dict[str, Any]:
        """Return a compact, knowledge-safe Planner resource projection."""

        intelligence = self.resource_intelligence()
        by_resource: dict[str, dict[str, Any]] = {}
        planner_regions: dict[str, dict[str, Any]] = {}
        for region_key, region in intelligence["regions"].items():
            planner_region = {key: value for key, value in region.items() if key != "resources"}
            planner_region["resources"] = {
                resource_key: self._planner_resource_summary(summary)
                for resource_key, summary in region["resources"].items()
            }
            planner_regions[region_key] = planner_region
            for resource_key, summary in region["resources"].items():
                planner_summary = self._planner_resource_summary(summary)
                resource = by_resource.setdefault(
                    resource_key,
                    {"regions": {}, "known_total": 0, "known_available": 0},
                )
                resource["regions"][region_key] = {
                    "known_total": planner_summary["known_total"],
                    "known_available": planner_summary["known_available"],
                    "pools": planner_summary["pools"],
                }
                resource["known_total"] += summary["known_total"]
                resource["known_available"] += summary["known_available"]
        for resource_key, summary in intelligence["global_resources"].items():
            planner_summary = self._planner_resource_summary(summary)
            resource = by_resource.setdefault(
                resource_key,
                {"regions": {}, "known_total": 0, "known_available": 0},
            )
            resource["global"] = planner_summary
            resource["known_total"] += summary["known_total"]
            resource["known_available"] += summary["known_available"]

        # A completed, visible Region is also authoritative for resources that
        # have no persisted Pool row.  Keep this as a Planner-only projection:
        # no synthetic DB row is created and the Player resource list remains a
        # faithful view of persisted resources.
        zero_resource_definitions = tuple(self.definition.world.resources)
        for region_key, state in self.region_states().items():
            planner_region = planner_regions[region_key]
            region_resources = planner_region.setdefault("resources", {})
            for definition_resource in zero_resource_definitions:
                if definition_resource.key in region_resources:
                    continue
                if (
                    resource_knowledge_status(
                        inventory_visibility=state.resource_inventory_visibility,
                        survey_completed=state.resource_survey_completed,
                        has_visible_pool=False,
                    )
                    != "KNOWN_ZERO"
                ):
                    continue
                zero_summary = {
                    "resource_name": definition_resource.name,
                    **(
                        {"unit": definition_resource.unit}
                        if definition_resource.unit is not None
                        else {}
                    ),
                    **(
                        {"display_unit": definition_resource.display_unit}
                        if definition_resource.display_unit is not None
                        else {}
                    ),
                    "known_total": 0,
                    "known_available": 0,
                    "knowledge_status": "KNOWN_ZERO",
                    "pools": [],
                }
                region_resources[definition_resource.key] = zero_summary
                planner_resource = by_resource.setdefault(
                    definition_resource.key,
                    {"regions": {}, "known_total": 0, "known_available": 0},
                )
                planner_resource["regions"][region_key] = {
                    "known_total": 0,
                    "known_available": 0,
                    "knowledge_status": "KNOWN_ZERO",
                    "pools": [],
                }
        return {
            "regions": planner_regions,
            "resources": by_resource,
            "total_regions": intelligence["total_regions"],
            "visible_region_count": intelligence["visible_region_count"],
        }

    @staticmethod
    def _planner_resource_summary(summary: dict[str, Any]) -> dict[str, Any]:
        """Remove storage-only Pool identity from the Planner projection."""

        return {key: value for key, value in summary.items() if key != "pools"} | {
            "pools": [
                {key: value for key, value in pool.items() if key != "pool_key"}
                for pool in summary["pools"]
            ]
        }

    def associated_known_resources(self, facility_key: str) -> list[dict[str, Any]]:
        resource_names = {item.key: item.name for item in self.definition.world.resources}
        return [
            {
                "resource_key": pool.resource_key,
                "resource_name": resource_names.get(pool.resource_key, pool.resource_key),
                "facility_name": self._facility_name(pool.facility_key),
                "quantity": pool.quantity,
                "available_quantity": pool.available_quantity,
                "availability": pool.availability.value,
                "availability_requirement": pool.availability_requirement,
                "availability_requirement_status": pool.availability_requirement_status,
            }
            for pool in self.visible_resource_pools()
            if pool.facility_key == facility_key
            and pool.availability != ResourcePoolAvailability.AVAILABLE
        ]

    def _resource_summary(
        self,
        pools: list[KnownResourcePoolView],
        resource_name: str,
    ) -> dict[str, Any]:
        return {
            "resource_name": resource_name,
            "known_total": sum(pool.quantity for pool in pools),
            "known_available": sum(
                pool.available_quantity
                for pool in pools
                if pool.availability == ResourcePoolAvailability.AVAILABLE
            ),
            "pools": [
                {
                    "pool_key": pool.pool_key,
                    "quantity": pool.quantity,
                    "available_quantity": pool.available_quantity,
                    "facility_key": pool.facility_key,
                    "facility_name": self._facility_name(pool.facility_key),
                    "availability": pool.availability.value,
                    **(
                        {"availability_requirement": pool.availability_requirement}
                        if pool.availability_requirement is not None
                        else {}
                    ),
                    **(
                        {"availability_requirement_status": pool.availability_requirement_status}
                        if pool.availability_requirement_status is not None
                        else {}
                    ),
                }
                for pool in pools
            ],
        }

    def _facility_name(self, facility_key: str | None) -> str | None:
        if facility_key is None:
            return None
        facility = self.definition.world.node(facility_key)
        return facility.name if facility is not None else None

    def availability_requirement_for_pool(
        self, row: GameInstanceResourceState
    ) -> dict[str, Any] | None:
        return self.known_requirement(self.raw_availability_requirement_for_pool(row))

    def raw_availability_requirement_for_pool(
        self, row: GameInstanceResourceState
    ) -> dict[str, Any] | None:
        return self._static_pool_requirements.get(
            (row.resource_key, row.scope_node_key, row.pool_key),
            row.availability_requirement,
        )

    def known_requirement(self, raw: dict[str, Any] | None) -> dict[str, Any] | None:
        if not raw:
            return None
        node_key = raw.get("node_key")
        fact_key = raw.get("fact_key")
        if not isinstance(node_key, str) or not isinstance(fact_key, str):
            return {"status": "UNKNOWN"}
        known = self._requirement_visibility(node_key, fact_key)
        if known != Visibility.KNOWN:
            return {"status": "UNKNOWN"}
        fact_value = self.db.scalar(
            select(GameInstanceFactState.truth_value).where(
                GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                GameInstanceFactState.node_key == node_key,
                GameInstanceFactState.fact_key == fact_key,
            )
        )
        return {
            "status": "KNOWN",
            "node_key": node_key,
            "fact_key": fact_key,
            "value": raw.get("value"),
            "known_value": fact_value,
        }

    def _requirement_visibility(self, node_key: str, fact_key: str) -> Visibility | None:
        return self.db.scalar(
            select(GameInstanceFactState.visibility).where(
                GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                GameInstanceFactState.node_key == node_key,
                GameInstanceFactState.fact_key == fact_key,
            )
        )

    def requirement_status(self, raw: dict[str, Any] | None) -> str | None:
        """Expose only whether a declared unlock requirement is known."""

        if not raw:
            return None
        node_key = raw.get("node_key")
        fact_key = raw.get("fact_key")
        if not isinstance(node_key, str) or not isinstance(fact_key, str):
            return "UNKNOWN"
        return (
            "KNOWN"
            if self._requirement_visibility(node_key, fact_key) == Visibility.KNOWN
            else "UNKNOWN"
        )


def _enum_value(row: object, attribute: str, default: Any) -> Any:
    value = getattr(row, attribute, default) if row is not None else default
    try:
        return type(default)(value)
    except (TypeError, ValueError):
        return default


def _public_predicate_status(current: Any, predicate: dict[str, Any]) -> str:
    """Evaluate a projected predicate without exposing a second UI authority."""

    operator = predicate.get("operator")
    if not isinstance(operator, str):
        return "UNKNOWN"
    if operator.startswith("NOT_"):
        base = operator[4:]
        base_result = _source_predicate_is_satisfied(
            current,
            {**predicate, "operator": base},
        )
        return "SATISFIED" if not base_result else "UNSATISFIED"
    return "SATISFIED" if _source_predicate_is_satisfied(current, predicate) else "UNSATISFIED"


def _source_predicate_is_satisfied(current: Any, predicate: dict[str, Any]) -> bool:
    operator = predicate.get("operator")
    if operator == "EQ":
        return current == predicate.get("value")
    if operator == "NE":
        return current != predicate.get("value")
    values = predicate.get("values")
    if operator == "IN":
        return isinstance(values, list) and current in values
    if operator == "NOT_IN":
        return isinstance(values, list) and current not in values
    expected = predicate.get("value")
    if (
        isinstance(current, bool)
        or isinstance(expected, bool)
        or not isinstance(current, (int, float))
        or not isinstance(expected, (int, float))
    ):
        return False
    if operator == "GT":
        return current > expected
    if operator == "GTE":
        return current >= expected
    if operator == "LT":
        return current < expected
    if operator == "LTE":
        return current <= expected
    return False


__all__ = [
    "KnownResourcePoolView",
    "RegionResourceKnowledgeView",
    "SharedKnowledgeProjection",
    "resource_knowledge_status",
]
