"""Pure deterministic interpreter for ScenarioDefinition v2 rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.domain.enums import (
    CommandReachability,
    RelationVisibility,
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.resources import is_runtime_known_inflow_pool, resource_state_key
from app.domain.scenario_v2 import (
    ActionDefinitionV2,
    ActionParameterType,
    ComparisonOperator,
    ConditionKind,
    ConditionV2,
    EffectKind,
    EffectV2,
    IntegerExpressionV2,
    NodeSelectorKind,
    NodeSelectorV2,
    RelationDirection,
    RuleDefinitionV2,
    RulePhase,
    ScenarioDefinitionV2,
    StrictScalar,
    ValueExpressionV2,
    ValueSource,
    normalize_action_parameters,
    transport_resource_entries,
)
from app.domain.world import AccessState, Visibility
from app.engine.locality import LocalityEngineError, resolve_resource_scope

type FactRef = tuple[str, str]


class RuleEngineError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class RuleNodeState:
    visibility: Visibility
    access: AccessState


@dataclass(frozen=True, slots=True)
class RuleFactState:
    value: StrictScalar
    visibility: Visibility


@dataclass(frozen=True, slots=True)
class RuleActorState:
    command_reachability: CommandReachability
    current_node_key: str


@dataclass(frozen=True, slots=True)
class RuleResourcePoolState:
    pool_key: str
    resource_key: str
    region_key: str | None
    facility_key: str | None
    quantity: int
    visibility: ResourcePoolVisibility
    availability: ResourcePoolAvailability
    survey_discoverable: bool
    availability_requirement: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class RuleRegionResourceKnowledgeState:
    resource_inventory_visibility: ResourceInventoryVisibility
    resource_survey_completed: bool


@dataclass(frozen=True, slots=True)
class RuleRelationKnowledgeState:
    visibility: RelationVisibility


@dataclass(frozen=True, slots=True)
class DeclarativeRuleState:
    nodes: Mapping[str, RuleNodeState]
    facts: Mapping[FactRef, RuleFactState]
    resources: Mapping[str, int]
    resource_reservations: Mapping[str, int]
    actors: Mapping[str, RuleActorState] = field(default_factory=dict)
    resource_pools: Mapping[str, RuleResourcePoolState] = field(default_factory=dict)
    region_resource_knowledge: Mapping[str, RuleRegionResourceKnowledgeState] = field(
        default_factory=dict
    )
    relation_knowledge: Mapping[str, RuleRelationKnowledgeState] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionRuleContext:
    action_key: str
    target_node_key: str
    parameters: Mapping[str, object]
    actor_key: str | None = None
    target_actor_key: str | None = None
    operation_status: str | None = None
    actor_current_node_key: str | None = None
    source_node_key: str | None = None


@dataclass(frozen=True, slots=True)
class FactMutation:
    node_key: str
    fact_key: str
    value: StrictScalar


@dataclass(frozen=True, slots=True)
class FactVisibilityMutation:
    node_key: str
    fact_key: str
    visibility: Visibility


@dataclass(frozen=True, slots=True)
class NodeVisibilityMutation:
    node_key: str
    visibility: Visibility


@dataclass(frozen=True, slots=True)
class NodeAccessMutation:
    node_key: str
    access: AccessState


@dataclass(frozen=True, slots=True)
class ResourceMutation:
    resource_key: str
    amount: int
    scope_node_key: str | None = None
    pool_key: str = "default"


@dataclass(frozen=True, slots=True)
class ResourceReservationMutation:
    resource_key: str
    amount: int
    scope_node_key: str | None = None
    pool_key: str = "default"


@dataclass(frozen=True, slots=True)
class RegionResourceVisibilityMutation:
    region_key: str
    visibility: ResourceInventoryVisibility


@dataclass(frozen=True, slots=True)
class ResourcePoolVisibilityMutation:
    pool_key: str
    visibility: ResourcePoolVisibility


@dataclass(frozen=True, slots=True)
class ResourcePoolAvailabilityMutation:
    pool_key: str
    availability: ResourcePoolAvailability


@dataclass(frozen=True, slots=True)
class RegionResourceSurveyMutation:
    region_key: str
    completed: bool


@dataclass(frozen=True, slots=True)
class RelationVisibilityMutation:
    relation_key: str
    visibility: RelationVisibility


@dataclass(frozen=True, slots=True)
class ActorCommandReachabilityMutation:
    actor_key: str
    command_reachability: CommandReachability


@dataclass(frozen=True, slots=True)
class MemoryEvent:
    key: str
    content: str


@dataclass(frozen=True, slots=True)
class RuleFailure:
    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class GenericRuleOutcome:
    selected_rule_key: str
    outcome_code: str | None = None
    failure: RuleFailure | None = None
    fact_updates: tuple[FactMutation, ...] = ()
    fact_visibility_updates: tuple[FactVisibilityMutation, ...] = ()
    node_visibility_updates: tuple[NodeVisibilityMutation, ...] = ()
    node_access_updates: tuple[NodeAccessMutation, ...] = ()
    resource_mutations: tuple[ResourceMutation, ...] = ()
    resource_reservations: tuple[ResourceReservationMutation, ...] = ()
    memory_events: tuple[MemoryEvent, ...] = ()
    actor_location_update: str | None = None
    actor_command_reachability_updates: tuple[ActorCommandReachabilityMutation, ...] = ()
    region_resource_visibility_updates: tuple[RegionResourceVisibilityMutation, ...] = ()
    region_resource_survey_updates: tuple[RegionResourceSurveyMutation, ...] = ()
    resource_pool_visibility_updates: tuple[ResourcePoolVisibilityMutation, ...] = ()
    resource_pool_availability_updates: tuple[ResourcePoolAvailabilityMutation, ...] = ()
    relation_visibility_updates: tuple[RelationVisibilityMutation, ...] = ()


class DeclarativeRuleEngine:
    """Evaluate one exact v2 definition without persistence or external I/O."""

    def __init__(self, definition: ScenarioDefinitionV2) -> None:
        self.definition = definition
        self._actions = {action.key: action for action in definition.actions}

    def evaluate(
        self,
        state: DeclarativeRuleState,
        context: ActionRuleContext,
    ) -> GenericRuleOutcome:
        preflight = self.evaluate_preflight(state, context)
        if preflight is not None:
            return preflight
        return self.evaluate_resolution(state, context)

    def evaluate_preflight(
        self,
        state: DeclarativeRuleState,
        context: ActionRuleContext,
    ) -> GenericRuleOutcome | None:
        action = self._action(context.action_key)
        self._validate_context(action, context)
        rule = self._select(RulePhase.PREFLIGHT, state, context, required=False)
        return self._outcome(rule, state, context) if rule is not None else None

    def evaluate_resolution(
        self,
        state: DeclarativeRuleState,
        context: ActionRuleContext,
    ) -> GenericRuleOutcome:
        action = self._action(context.action_key)
        self._validate_context(action, context)
        resolve = self._select(RulePhase.RESOLVE, state, context, required=True)
        assert resolve is not None
        return self._outcome(resolve, state, context)

    def _select(
        self,
        phase: RulePhase,
        state: DeclarativeRuleState,
        context: ActionRuleContext,
        *,
        required: bool,
    ) -> RuleDefinitionV2 | None:
        matches: list[RuleDefinitionV2] = []
        for rule in self.definition.rules:
            if rule.phase != phase or rule.action_key != context.action_key:
                continue
            if rule.condition is None:
                matches.append(rule)
                continue
            try:
                if self._condition(rule.condition, state, context):
                    matches.append(rule)
            except RuleEngineError as exc:
                if exc.code != "RULE_RESOURCE_MISSING":
                    raise
        if not matches:
            if required:
                raise RuleEngineError(
                    "RULE_RESOLUTION_NOT_FOUND",
                    "No declarative resolution rule matched this Action",
                )
            return None
        priority = max(rule.priority for rule in matches)
        winners = [rule for rule in matches if rule.priority == priority]
        if len(winners) != 1:
            raise RuleEngineError(
                "RULE_RESOLUTION_AMBIGUOUS",
                "Multiple declarative rules share the highest matching priority",
            )
        return winners[0]

    def _condition(
        self,
        condition: ConditionV2,
        state: DeclarativeRuleState,
        context: ActionRuleContext,
    ) -> bool:
        kind = condition.kind
        if kind == ConditionKind.ALL:
            return all(self._condition(item, state, context) for item in condition.conditions)
        if kind == ConditionKind.ANY:
            return any(self._condition(item, state, context) for item in condition.conditions)
        if kind == ConditionKind.NOT:
            assert condition.condition is not None
            return not self._condition(condition.condition, state, context)
        if kind in {
            ConditionKind.FACT_EQUALS,
            ConditionKind.FACT_NOT_EQUALS,
            ConditionKind.FACT_IN,
            ConditionKind.FACT_COMPARE,
        }:
            assert condition.node is not None and condition.fact_key is not None
            value = self._fact(
                state,
                self._one_node(condition.node, state, context),
                condition.fact_key,
            ).value
            if kind == ConditionKind.FACT_EQUALS:
                return value == condition.value
            if kind == ConditionKind.FACT_NOT_EQUALS:
                return value != condition.value
            if kind == ConditionKind.FACT_IN:
                return value in condition.values
            assert condition.operator is not None and condition.value is not None
            return _compare(value, condition.operator, condition.value)
        if kind == ConditionKind.RESOURCE_COMPARE:
            assert condition.resource_key and condition.operator and condition.value is not None
            key = self._resource_key(condition.resource_scope, context)
            value = self._resource_value(
                state,
                condition.resource_key,
                key,
            )
            return _compare(value, condition.operator, condition.value)
        if kind == ConditionKind.PARAMETER_COMPARE:
            assert condition.parameter_key and condition.operator and condition.value is not None
            parameter_value = context.parameters.get(condition.parameter_key)
            if parameter_value is None and condition.parameter_key in {"resource_key", "amount"}:
                try:
                    entries = transport_resource_entries(context.parameters)
                except ValueError as exc:
                    raise RuleEngineError("RULE_PARAMETER_INVALID", str(exc)) from exc
                if len(entries) == 1:
                    parameter_value = entries[0][
                        0 if condition.parameter_key == "resource_key" else 1
                    ]
            if parameter_value is None:
                raise RuleEngineError(
                    "RULE_PARAMETER_MISSING",
                    f"Required rule state is missing: {condition.parameter_key}",
                )
            return _compare(_strict_scalar(parameter_value), condition.operator, condition.value)
        if kind == ConditionKind.NODE_VISIBLE:
            assert condition.node and condition.visibility
            node = self._node(state, self._one_node(condition.node, state, context))
            return node.visibility == condition.visibility
        if kind == ConditionKind.NODE_ACCESSIBLE:
            assert condition.node and condition.access
            node = self._node(state, self._one_node(condition.node, state, context))
            return node.access == condition.access
        assert condition.node and condition.relation_type_key and condition.relation_direction
        anchor = self._one_node(condition.node, state, context)
        return bool(
            self._related_nodes(
                anchor,
                condition.relation_type_key,
                condition.relation_direction,
                required_fact_key=None,
            )
        )

    def _outcome(
        self,
        rule: RuleDefinitionV2,
        state: DeclarativeRuleState,
        context: ActionRuleContext,
    ) -> GenericRuleOutcome:
        facts: list[FactMutation] = []
        fact_visibility: list[FactVisibilityMutation] = []
        node_visibility: list[NodeVisibilityMutation] = []
        node_access: list[NodeAccessMutation] = []
        resources: list[ResourceMutation] = []
        reservations: list[ResourceReservationMutation] = []
        memories: list[MemoryEvent] = []
        actor_reachability: list[ActorCommandReachabilityMutation] = []
        region_resource_visibility: list[RegionResourceVisibilityMutation] = []
        resource_pool_visibility: list[ResourcePoolVisibilityMutation] = []
        resource_pool_availability: list[ResourcePoolAvailabilityMutation] = []
        relation_visibility: list[RelationVisibilityMutation] = []
        outcome_code: str | None = None
        failure: RuleFailure | None = None
        for effect in rule.effects:
            nodes = self._effect_nodes(effect, state, context)
            if effect.kind == EffectKind.SET_FACT:
                assert effect.fact_key and effect.value
                value = self._value(effect.value, context)
                facts.extend(FactMutation(node, effect.fact_key, value) for node in nodes)
            elif effect.kind in {EffectKind.REVEAL_FACT, EffectKind.HIDE_FACT}:
                assert effect.fact_key
                visibility = (
                    Visibility.KNOWN if effect.kind == EffectKind.REVEAL_FACT else Visibility.HIDDEN
                )
                fact_visibility.extend(
                    FactVisibilityMutation(node, effect.fact_key, visibility) for node in nodes
                )
            elif effect.kind in {EffectKind.REVEAL_NODE, EffectKind.HIDE_NODE}:
                visibility = (
                    Visibility.KNOWN if effect.kind == EffectKind.REVEAL_NODE else Visibility.HIDDEN
                )
                node_visibility.extend(NodeVisibilityMutation(node, visibility) for node in nodes)
            elif effect.kind == EffectKind.SET_NODE_ACCESS:
                assert effect.access
                node_access.extend(NodeAccessMutation(node, effect.access) for node in nodes)
            elif effect.kind == EffectKind.ADJUST_RESOURCE:
                assert effect.resource_key and effect.amount
                scope = self._resource_scope(effect.resource_scope, context)
                resources.append(
                    ResourceMutation(
                        effect.resource_key,
                        self._integer(effect.amount, context),
                        scope,
                    )
                )
            elif effect.kind in {
                EffectKind.RESERVE_RESOURCE,
                EffectKind.RELEASE_RESOURCE,
            }:
                assert effect.resource_key and effect.amount
                scope = self._resource_scope(effect.resource_scope, context)
                amount = self._integer(effect.amount, context)
                if effect.kind == EffectKind.RELEASE_RESOURCE:
                    amount = -amount
                reservations.append(ResourceReservationMutation(effect.resource_key, amount, scope))
            elif effect.kind == EffectKind.EMIT_OUTCOME:
                outcome_code = effect.outcome_code
            elif effect.kind == EffectKind.EMIT_FAILURE:
                assert effect.failure_code and effect.message
                failure = RuleFailure(
                    code=effect.failure_code,
                    message=effect.message,
                    retryable=effect.retryable,
                )
            elif effect.kind == EffectKind.WRITE_MEMORY_EVENT:
                assert effect.memory_key and effect.memory_content
                memories.append(MemoryEvent(effect.memory_key, effect.memory_content))
            elif effect.kind == EffectKind.SET_ACTOR_COMMAND_REACHABILITY:
                assert effect.command_reachability is not None
                actor_key = effect.actor_key or context.target_actor_key or context.actor_key
                if actor_key is None:
                    raise RuleEngineError(
                        "RULE_ACTOR_TARGET_MISSING",
                        "Actor reachability Effect has no target Actor",
                    )
                if actor_key not in state.actors:
                    raise RuleEngineError(
                        "RULE_ACTOR_STATE_MISSING",
                        "Actor reachability Effect references missing Actor state",
                    )
                actor_reachability.append(
                    ActorCommandReachabilityMutation(actor_key, effect.command_reachability)
                )
            elif effect.kind == EffectKind.SET_RELATION_VISIBILITY:
                assert effect.relation_key is not None and effect.visibility is not None
                relation_visibility.append(
                    RelationVisibilityMutation(
                        effect.relation_key,
                        RelationVisibility(effect.visibility.value),
                    )
                )
            elif effect.kind == EffectKind.SET_REGION_RESOURCE_VISIBILITY:
                assert effect.region_key is not None and effect.visibility is not None
                region_resource_visibility.append(
                    RegionResourceVisibilityMutation(
                        effect.region_key,
                        ResourceInventoryVisibility(effect.visibility.value),
                    )
                )
            elif effect.kind == EffectKind.SET_RESOURCE_POOL_VISIBILITY:
                assert effect.pool_key is not None and effect.visibility is not None
                resource_pool_visibility.append(
                    ResourcePoolVisibilityMutation(
                        effect.pool_key,
                        ResourcePoolVisibility(effect.visibility.value),
                    )
                )
            elif effect.kind == EffectKind.SET_RESOURCE_POOL_AVAILABILITY:
                assert effect.pool_key is not None and effect.availability is not None
                resource_pool_availability.append(
                    ResourcePoolAvailabilityMutation(effect.pool_key, effect.availability)
                )
        return GenericRuleOutcome(
            selected_rule_key=rule.key,
            outcome_code=outcome_code,
            failure=failure,
            fact_updates=tuple(facts),
            fact_visibility_updates=tuple(fact_visibility),
            node_visibility_updates=tuple(node_visibility),
            node_access_updates=tuple(node_access),
            resource_mutations=tuple(resources),
            resource_reservations=tuple(reservations),
            memory_events=tuple(memories),
            actor_command_reachability_updates=tuple(actor_reachability),
            region_resource_visibility_updates=tuple(region_resource_visibility),
            resource_pool_visibility_updates=tuple(resource_pool_visibility),
            resource_pool_availability_updates=tuple(resource_pool_availability),
            relation_visibility_updates=tuple(relation_visibility),
        )

    def _effect_nodes(
        self,
        effect: EffectV2,
        state: DeclarativeRuleState,
        context: ActionRuleContext,
    ) -> tuple[str, ...]:
        if effect.node is None:
            return ()
        return (self._one_node(effect.node, state, context),)

    def _one_node(
        self,
        selector: NodeSelectorV2,
        state: DeclarativeRuleState,
        context: ActionRuleContext,
    ) -> str:
        if selector.kind == NodeSelectorKind.CURRENT_TARGET:
            key = context.target_node_key
        elif selector.kind == NodeSelectorKind.ACTION_SOURCE:
            if context.source_node_key is None:
                raise RuleEngineError(
                    "RULE_ACTION_SOURCE_MISSING",
                    "An ACTION_SOURCE selector needs a source Node parameter",
                )
            key = context.source_node_key
        elif selector.kind == NodeSelectorKind.EXPLICIT:
            assert selector.node_key is not None
            key = selector.node_key
        else:
            anchor = selector.anchor_node_key or context.target_node_key
            assert selector.relation_type_key and selector.direction
            matches = self._related_nodes(
                anchor,
                selector.relation_type_key,
                selector.direction,
                required_fact_key=selector.required_fact_key,
            )
            if len(matches) != 1:
                raise RuleEngineError(
                    "RULE_NODE_SELECTOR_AMBIGUOUS",
                    "A singular related Node selector did not resolve exactly one Node",
                )
            key = matches[0]
        self._node(state, key)
        return key

    def _related_nodes(
        self,
        anchor: str,
        relation_type_key: str,
        direction: RelationDirection,
        *,
        required_fact_key: str | None,
    ) -> tuple[str, ...]:
        found: list[str] = []
        for relation in self.definition.world.relations:
            if relation.relation_type_key != relation_type_key:
                continue
            if direction == RelationDirection.SOURCE and relation.source_node_key == anchor:
                candidate = relation.target_node_key
            elif direction == RelationDirection.TARGET and relation.target_node_key == anchor:
                candidate = relation.source_node_key
            else:
                continue
            node = self.definition.world.node(candidate)
            if node is not None and (
                required_fact_key is None or node.fact(required_fact_key) is not None
            ):
                found.append(candidate)
        return tuple(sorted(set(found)))

    @staticmethod
    def _node(state: DeclarativeRuleState, node_key: str) -> RuleNodeState:
        return _required(state.nodes, node_key, "RULE_NODE_STATE_MISSING")

    @staticmethod
    def _fact(state: DeclarativeRuleState, node_key: str, fact_key: str) -> RuleFactState:
        return _required(state.facts, (node_key, fact_key), "RULE_FACT_STATE_MISSING")

    @staticmethod
    def _value(expression: ValueExpressionV2, context: ActionRuleContext) -> StrictScalar:
        if expression.source == ValueSource.LITERAL:
            assert expression.literal is not None
            return expression.literal
        assert expression.parameter_key is not None
        return _strict_scalar(
            _required(context.parameters, expression.parameter_key, "RULE_PARAMETER_MISSING")
        )

    @staticmethod
    def _integer(expression: IntegerExpressionV2, context: ActionRuleContext) -> int:
        if expression.source == ValueSource.LITERAL:
            assert expression.literal is not None
            value = expression.literal
        else:
            assert expression.parameter_key is not None
            raw = _required(context.parameters, expression.parameter_key, "RULE_PARAMETER_MISSING")
            if not isinstance(raw, int) or isinstance(raw, bool):
                raise RuleEngineError(
                    "RULE_PARAMETER_TYPE_INVALID",
                    "An integer Effect expression received a non-integer parameter",
                )
            value = raw
        return value * expression.multiplier

    def _action(self, key: str) -> ActionDefinitionV2:
        return _required(self._actions, key, "RULE_ACTION_NOT_FOUND")

    def _resource_scope(
        self,
        scope: object,
        context: ActionRuleContext,
    ) -> str | None:
        try:
            return resolve_resource_scope(
                self.definition,
                scope,  # type: ignore[arg-type]
                actor_current_node_key=context.actor_current_node_key,
                target_node_key=context.target_node_key,
            )
        except LocalityEngineError as exc:
            raise RuleEngineError(exc.code, exc.message) from exc

    def _resource_key(self, scope: object, context: ActionRuleContext) -> str | None:
        return self._resource_scope(scope, context)

    @staticmethod
    def _resource_value(
        state: DeclarativeRuleState,
        resource_key: str,
        scope_node_key: str | None,
    ) -> int:
        knowledge = (
            state.region_resource_knowledge.get(scope_node_key)
            if scope_node_key is not None
            else None
        )
        visible_pools = [
            pool
            for pool in state.resource_pools.values()
            if (
                pool.resource_key == resource_key
                and pool.region_key == scope_node_key
                and pool.visibility == ResourcePoolVisibility.VISIBLE
                and (
                    scope_node_key is None
                    or is_runtime_known_inflow_pool(pool.pool_key)
                    or (
                        knowledge is not None
                        and knowledge.resource_inventory_visibility
                        == ResourceInventoryVisibility.VISIBLE
                        and knowledge.resource_survey_completed
                    )
                )
            )
        ]
        if not visible_pools:
            # Keep the legacy, unpooled DeclarativeRuleState contract usable
            # for callers that do not provide pool metadata.
            direct = state.resources.get(resource_state_key(resource_key, scope_node_key))
            if direct is not None and not state.resource_pools:
                return direct
            raise RuleEngineError(
                "RULE_RESOURCE_MISSING",
                "The known available Resource state is missing",
            )
        return sum(
            pool.quantity
            for pool in visible_pools
            if pool.availability == ResourcePoolAvailability.AVAILABLE
        )

    @staticmethod
    def _validate_context(action: ActionDefinitionV2, context: ActionRuleContext) -> None:
        if action.behavior.value == "TRANSPORT_RESOURCE":
            try:
                normalize_action_parameters(action, context.parameters)
            except ValueError as exc:
                raise RuleEngineError("RULE_PARAMETER_INVALID", str(exc)) from exc
            return
        definitions = {parameter.key: parameter for parameter in action.parameters}
        if set(context.parameters).difference(definitions):
            raise RuleEngineError(
                "RULE_PARAMETER_UNKNOWN", "Action parameters contain unknown keys"
            )
        for key, definition in definitions.items():
            if key not in context.parameters:
                if definition.required:
                    raise RuleEngineError(
                        "RULE_PARAMETER_MISSING", f"Required Action parameter {key} is missing"
                    )
                continue
            value = context.parameters[key]
            valid = (
                (definition.value_type == ActionParameterType.STRING and isinstance(value, str))
                or (
                    definition.value_type == ActionParameterType.INTEGER
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                )
                or (
                    definition.value_type == ActionParameterType.BOOLEAN and isinstance(value, bool)
                )
                or (
                    definition.value_type == ActionParameterType.ENUM
                    and value in definition.allowed_values
                )
            )
            if not valid:
                raise RuleEngineError(
                    "RULE_PARAMETER_TYPE_INVALID",
                    f"Action parameter {key} does not match its versioned schema",
                )
            if isinstance(value, int) and not isinstance(value, bool):
                if definition.minimum is not None and value < definition.minimum:
                    raise RuleEngineError("RULE_PARAMETER_RANGE_INVALID", "Parameter below minimum")
                if definition.maximum is not None and value > definition.maximum:
                    raise RuleEngineError("RULE_PARAMETER_RANGE_INVALID", "Parameter above maximum")


def _required[Key, Value](mapping: Mapping[Key, Value], key: Key, code: str) -> Value:
    try:
        return mapping[key]
    except KeyError:
        raise RuleEngineError(code, f"Required rule state is missing: {key}") from None


def _strict_scalar(value: object) -> StrictScalar:
    if isinstance(value, (str, int, bool)):
        return value
    raise RuleEngineError(
        "RULE_PARAMETER_TYPE_INVALID",
        "Rule expressions require a scalar Action parameter",
    )


def key_for_resource(
    resource_key: str,
    scope_node_key: str | None,
    pool_key: str = "default",
) -> str:
    return resource_state_key(resource_key, scope_node_key, pool_key)


def _compare(left: StrictScalar, operator: ComparisonOperator, right: StrictScalar) -> bool:
    if operator == ComparisonOperator.EQ:
        return type(left) is type(right) and left == right
    if operator == ComparisonOperator.NE:
        return type(left) is not type(right) or left != right
    if type(left) is not type(right) or isinstance(left, bool) or isinstance(right, bool):
        raise RuleEngineError(
            "RULE_COMPARISON_TYPE_INVALID",
            "Ordered rule comparison requires operands of the same non-boolean type",
        )
    ordered_left: str | int = left
    ordered_right: str | int = right
    if operator == ComparisonOperator.LT:
        return ordered_left < ordered_right  # type: ignore[operator]
    if operator == ComparisonOperator.LTE:
        return ordered_left <= ordered_right  # type: ignore[operator]
    if operator == ComparisonOperator.GT:
        return ordered_left > ordered_right  # type: ignore[operator]
    return ordered_left >= ordered_right  # type: ignore[operator]


__all__ = [
    "ActionRuleContext",
    "ActorCommandReachabilityMutation",
    "DeclarativeRuleEngine",
    "DeclarativeRuleState",
    "GenericRuleOutcome",
    "RegionResourceSurveyMutation",
    "RegionResourceVisibilityMutation",
    "RelationVisibilityMutation",
    "ResourcePoolAvailabilityMutation",
    "ResourcePoolVisibilityMutation",
    "RuleActorState",
    "RuleEngineError",
    "RuleFactState",
    "RuleNodeState",
    "RuleRegionResourceKnowledgeState",
    "RuleRelationKnowledgeState",
    "RuleResourcePoolState",
]
