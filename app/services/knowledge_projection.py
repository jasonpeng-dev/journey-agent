"""Shared Knowledge-safe projection for Player and Planner consumers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.domain.enums import (
    RelationVisibility,
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import (
    ActionBehavior,
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

    def known_action_requirements(self) -> tuple[dict[str, Any], ...]:
        """Expose action requirements that are already supported by Knowledge.

        Static role/relation contracts are public Scenario metadata.  Dynamic
        Fact requirements are included only for Facts whose current visibility
        is KNOWN; hidden Facts and their values are omitted entirely.  The same
        projection is consumed by both PlanningContext and Player API.
        """

        known_nodes = {row.node_key for row in self.known_node_rows()}
        known_facts = {
            (row.node_key, row.fact_key): row.truth_value for row in self.known_fact_rows()
        }
        role_names = {role.key: role.name for role in self.definition.actors.roles}
        result: list[dict[str, Any]] = []
        for action in sorted(self.definition.actions, key=lambda item: item.key):
            if not (
                action.required_actor_role_key is not None
                or action.source_relation_type_key is not None
                or action.behavior
                in {
                    ActionBehavior.SUPPLY_POWER,
                    ActionBehavior.DEPLOY_HEAVY_ENGINEERING_SUPPORT,
                }
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
            if action.source_relation_type_key is not None:
                entry["source_relation_type_key"] = action.source_relation_type_key
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
            result.append(entry)
        return tuple(result)

    def planner_action_requirements(self) -> tuple[dict[str, Any], ...]:
        """Return a sparse, target-oriented Planner requirement projection.

        ``known_action_requirements`` is also consumed by the Player API and
        intentionally keeps its action-oriented compatibility shape.  The
        provider does not need that shape's repeated per-known-node Fact
        cards, though.  This projection keeps only target-specific repair
        contracts that can be derived from Knowledge-safe PREFLIGHT rules:
        role, resource costs, and known Fact prerequisites.

        It deliberately skips a rule when its target selector is not itself
        known.  In particular, hidden target Facts never become a target name
        or a requirement hint merely because a matching Rule exists in the
        immutable ScenarioVersion.
        """

        known_nodes = {row.node_key for row in self.known_node_rows()}
        known_facts = {
            (row.node_key, row.fact_key): row.truth_value for row in self.known_fact_rows()
        }
        by_target: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

        for action in sorted(self.definition.actions, key=lambda item: item.key):
            for rule in self.definition.rules:
                if rule.action_key != action.key or rule.phase.value != "PREFLIGHT":
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
                if not selector_conditions:
                    continue
                selector_ids = {id(condition) for condition in selector_conditions}
                for target_key in sorted(known_nodes):
                    if not all(
                        self._target_selector_matches(
                            condition,
                            target_key,
                            known_facts,
                        )
                        for condition in selector_conditions
                    ):
                        continue
                    requirement = by_target[target_key].setdefault(
                        action.key,
                        {
                            "action_key": action.key,
                            **(
                                {"required_actor_role_key": action.required_actor_role_key}
                                if action.required_actor_role_key is not None
                                else {}
                            ),
                        },
                    )
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
                            continue
                        special = self._planner_fact_requirement(
                            condition,
                            positive=positive,
                            target_key=target_key,
                            known_facts=known_facts,
                        )
                        if special is not None:
                            special_requirements = requirement.setdefault(
                                "special_requirements", []
                            )
                            assert isinstance(special_requirements, list)
                            if special not in special_requirements:
                                special_requirements.append(special)

        result: list[dict[str, Any]] = []
        for target_key in sorted(by_target):
            requirements = [
                requirement
                for _action_key, requirement in sorted(by_target[target_key].items())
                if len(requirement) > 1
            ]
            if requirements:
                result.append(
                    {
                        "target_key": target_key,
                        "requirements": requirements,
                    }
                )
        return tuple(result)

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
    ) -> bool:
        if condition.fact_key is None:
            return False
        current_value = known_facts.get((target_key, condition.fact_key))
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
    ) -> dict[str, Any] | None:
        if condition.node is None or condition.fact_key is None:
            return None
        if condition.node.kind == NodeSelectorKind.EXPLICIT:
            node_key = condition.node.node_key
        elif condition.node.kind == NodeSelectorKind.CURRENT_TARGET:
            node_key = target_key
        else:
            return None
        if node_key is None or (node_key, condition.fact_key) not in known_facts:
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
        return {
            "node_key": node_key,
            "fact_key": condition.fact_key,
            "operator": operator,
            "value": value,
        }

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
            if region_key is not None:
                region_state = region_states.get(region_key)
                if (
                    region_state is not None
                    and region_state.resource_inventory_visibility
                    != ResourceInventoryVisibility.VISIBLE
                    and row.facility_key is None
                ):
                    continue
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
                    availability_requirement=self.known_requirement(row.availability_requirement),
                    availability_requirement_status=self.requirement_status(
                        row.availability_requirement
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

        resource_names = {item.key: item.name for item in self.definition.world.resources}
        grouped: dict[tuple[str | None, str], list[KnownResourcePoolView]] = defaultdict(list)
        for pool in self.visible_resource_pools():
            grouped[(pool.region_key, pool.resource_key)].append(pool)
        regions: dict[str, dict[str, Any]] = {}
        for region_key, state in self.region_states().items():
            region_node = self.definition.world.node(region_key)
            resources: dict[str, Any] = {}
            for (pool_region, resource_key), pools in grouped.items():
                if pool_region != region_key:
                    continue
                resources[resource_key] = self._resource_summary(
                    pools,
                    resource_names[resource_key],
                )
            regions[region_key] = {
                "region_name": region_node.name if region_node is not None else region_key,
                "resource_inventory_visibility": state.resource_inventory_visibility.value,
                "resource_survey_completed": state.resource_survey_completed,
                "resources": resources,
            }
        global_resources: dict[str, Any] = {}
        for (global_region_key, resource_key), pools in grouped.items():
            if global_region_key is None:
                global_resources[resource_key] = self._resource_summary(
                    pools, resource_names[resource_key]
                )
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

    def known_requirement(self, raw: dict[str, Any] | None) -> dict[str, Any] | None:
        if not raw:
            return None
        node_key = raw.get("node_key")
        fact_key = raw.get("fact_key")
        if not isinstance(node_key, str) or not isinstance(fact_key, str):
            return None
        known = self.db.scalar(
            select(GameInstanceFactState.visibility).where(
                GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                GameInstanceFactState.node_key == node_key,
                GameInstanceFactState.fact_key == fact_key,
            )
        )
        if known != Visibility.KNOWN:
            return None
        result = dict(raw)
        fact_value = self.db.scalar(
            select(GameInstanceFactState.truth_value).where(
                GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                GameInstanceFactState.node_key == node_key,
                GameInstanceFactState.fact_key == fact_key,
            )
        )
        result["known_value"] = fact_value
        return result

    def requirement_status(self, raw: dict[str, Any] | None) -> str | None:
        """Expose only whether a declared unlock requirement is known."""

        if not raw:
            return None
        return "KNOWN" if self.known_requirement(raw) is not None else "UNKNOWN"


def _enum_value(row: object, attribute: str, default: Any) -> Any:
    value = getattr(row, attribute, default) if row is not None else default
    try:
        return type(default)(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "KnownResourcePoolView",
    "RegionResourceKnowledgeView",
    "SharedKnowledgeProjection",
]
