"""Immutable ScenarioDefinition v2 document/domain contract.

V2 deliberately uses a closed, non-executable vocabulary.  It is both the
persisted aggregate contract and the immutable value returned by the version
decoder; later Phase R stages interpret its rules without generating code.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from app.domain.enums import (
    CommandReachability,
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.world import AccessState, FactValueType, Visibility

type StableKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_]{0,79}$",
    ),
]
type EngineContractKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_-]{0,99}$",
    ),
]
type SymbolicCode = Annotated[
    str,
    Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z][A-Za-z0-9_]{0,99}$",
    ),
]
type StrictScalar = StrictStr | StrictInt | StrictBool


class FrozenDefinitionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EngineCapability(StrEnum):
    PLAN = "PLAN"
    EXECUTE_ACTION = "EXECUTE_ACTION"
    INSPECT_STATE = "INSPECT_STATE"
    LOGISTICS = "LOGISTICS"


class ActionBehavior(StrEnum):
    """Generic runtime behavior layered on top of declarative Rules."""

    RULE = "RULE"
    TRAVEL = "TRAVEL"
    INSPECT = "INSPECT"
    TRANSPORT_RESOURCE = "TRANSPORT_RESOURCE"
    RELAY_MESSAGE = "RELAY_MESSAGE"
    SURVEY_RESOURCES = "SURVEY_RESOURCES"


class ActionLocality(StrEnum):
    NONE = "NONE"
    LOCAL_TARGET = "LOCAL_TARGET"
    FACILITY_REGION = "FACILITY_REGION"
    TRANSPORT_ENDPOINT = "TRANSPORT_ENDPOINT"
    ACTOR_REGION = "ACTOR_REGION"
    REGION = "REGION"


class ActionTargetKind(StrEnum):
    NODE = "NODE"
    ACTOR = "ACTOR"


class ResourceScopeKind(StrEnum):
    EXPLICIT = "EXPLICIT"
    ACTOR_CURRENT_REGION = "ACTOR_CURRENT_REGION"
    CURRENT_TARGET_REGION = "CURRENT_TARGET_REGION"


class ActionExecutionMode(StrEnum):
    IMMEDIATE = "IMMEDIATE"
    ASYNC = "ASYNC"


class ActionParameterType(StrEnum):
    STRING = "STRING"
    ENUM = "ENUM"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"


class RulePhase(StrEnum):
    PREFLIGHT = "PREFLIGHT"
    RESOLVE = "RESOLVE"


class ComparisonOperator(StrEnum):
    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"


class NodeSelectorKind(StrEnum):
    CURRENT_TARGET = "CURRENT_TARGET"
    EXPLICIT = "EXPLICIT"
    RELATED = "RELATED"


class RelationDirection(StrEnum):
    SOURCE = "SOURCE"
    TARGET = "TARGET"


class ConditionKind(StrEnum):
    ALL = "ALL"
    ANY = "ANY"
    NOT = "NOT"
    FACT_EQUALS = "FACT_EQUALS"
    FACT_NOT_EQUALS = "FACT_NOT_EQUALS"
    FACT_IN = "FACT_IN"
    FACT_COMPARE = "FACT_COMPARE"
    RESOURCE_COMPARE = "RESOURCE_COMPARE"
    PARAMETER_COMPARE = "PARAMETER_COMPARE"
    NODE_VISIBLE = "NODE_VISIBLE"
    NODE_ACCESSIBLE = "NODE_ACCESSIBLE"
    RELATION_EXISTS = "RELATION_EXISTS"


class ValueSource(StrEnum):
    LITERAL = "LITERAL"
    PARAMETER = "PARAMETER"


class EffectKind(StrEnum):
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
    EMIT_FAILURE = "EMIT_FAILURE"
    WRITE_MEMORY_EVENT = "WRITE_MEMORY_EVENT"
    SET_ACTOR_COMMAND_REACHABILITY = "SET_ACTOR_COMMAND_REACHABILITY"
    SET_REGION_RESOURCE_VISIBILITY = "SET_REGION_RESOURCE_VISIBILITY"
    SET_RESOURCE_POOL_VISIBILITY = "SET_RESOURCE_POOL_VISIBILITY"
    SET_RESOURCE_POOL_AVAILABILITY = "SET_RESOURCE_POOL_AVAILABILITY"


class ResourceInitialStateV2(FrozenDefinitionModel):
    """One initialized balance, optionally scoped to a Region Node."""

    resource_key: StableKey
    scope_node_key: StableKey | None = None
    value: int = Field(ge=0)
    reserved_value: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_reservation(self) -> ResourceInitialStateV2:
        if self.reserved_value > self.value:
            raise ValueError("Resource initial reserved_value cannot exceed value")
        return self


class ResourceAvailabilityRequirementV2(FrozenDefinitionModel):
    """Known Fact that can unlock a currently unavailable Resource Pool."""

    node_key: StableKey
    fact_key: StableKey
    value: StrictScalar


class ResourcePoolDefinitionV2(FrozenDefinitionModel):
    """One author-declared initial Resource Pool."""

    pool_key: StableKey
    resource_key: StableKey
    region_key: StableKey | None = None
    facility_key: StableKey | None = None
    quantity: int = Field(ge=0)
    reserved_value: int = Field(default=0, ge=0)
    visibility: ResourcePoolVisibility = ResourcePoolVisibility.VISIBLE
    availability: ResourcePoolAvailability = ResourcePoolAvailability.AVAILABLE
    survey_discoverable: bool = False
    availability_requirement: ResourceAvailabilityRequirementV2 | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_reservation(self) -> ResourcePoolDefinitionV2:
        if self.reserved_value > self.quantity:
            raise ValueError("Resource Pool reserved_value cannot exceed quantity")
        return self


class RegionResourceKnowledgeInitialStateV2(FrozenDefinitionModel):
    """Optional initial Region resource intelligence state."""

    region_key: StableKey
    resource_inventory_visibility: ResourceInventoryVisibility = ResourceInventoryVisibility.VISIBLE
    resource_survey_completed: bool = True


class LocalityContractV2(FrozenDefinitionModel):
    """Explicit opt-in contract for Region/Facility/Transport semantics."""

    enabled: bool = False
    scoped_resources: bool = False
    region_node_type_key: StableKey | None = None
    facility_node_type_key: StableKey | None = None
    transport_node_type_key: StableKey | None = None
    located_in_relation_type_key: StableKey | None = None
    transport_endpoint_relation_type_key: StableKey | None = None
    passability_fact_key: StableKey | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> LocalityContractV2:
        keys = (
            self.region_node_type_key,
            self.facility_node_type_key,
            self.transport_node_type_key,
            self.located_in_relation_type_key,
            self.transport_endpoint_relation_type_key,
        )
        if not self.enabled:
            if self.scoped_resources or any(
                key is not None for key in (*keys, self.passability_fact_key)
            ):
                raise ValueError("Locality fields require explicit enabled opt-in")
        elif any(key is None for key in keys):
            raise ValueError(
                "Enabled locality requires Region/Facility/Transport type and relation keys"
            )
        return self


class ResourceScopeV2(FrozenDefinitionModel):
    kind: ResourceScopeKind
    node_key: StableKey | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> ResourceScopeV2:
        if self.kind == ResourceScopeKind.EXPLICIT and self.node_key is None:
            raise ValueError("EXPLICIT Resource scope requires node_key")
        if self.kind != ResourceScopeKind.EXPLICIT and self.node_key is not None:
            raise ValueError("Actor/target Resource scope cannot define node_key")
        return self


class ScenarioMetadataV2(FrozenDefinitionModel):
    key: StableKey
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    locality: LocalityContractV2 = Field(default_factory=LocalityContractV2)


class EngineContractV2(FrozenDefinitionModel):
    key: EngineContractKey
    version: str = Field(min_length=1, max_length=100)


class InitializationV2(FrozenDefinitionModel):
    start_node_key: StableKey
    primary_actor_key: StableKey
    resource_initial_states: tuple[ResourceInitialStateV2, ...] = ()
    resource_pools: tuple[ResourcePoolDefinitionV2, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )
    region_resource_knowledge: tuple[RegionResourceKnowledgeInitialStateV2, ...] = Field(
        default=(),
        exclude_if=lambda value: not value,
    )


class NodeTypeDefinitionV2(FrozenDefinitionModel):
    key: StableKey
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)


class InteractionDefinitionV2(FrozenDefinitionModel):
    key: StableKey
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)


class FactDefinitionV2(FrozenDefinitionModel):
    key: StableKey
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    value_type: FactValueType
    initial_value: StrictScalar
    initial_visibility: Visibility
    allowed_values: tuple[StrictScalar, ...] = ()

    @model_validator(mode="after")
    def validate_value_domain(self) -> FactDefinitionV2:
        if self.value_type == FactValueType.STRING:
            valid = isinstance(self.initial_value, str)
        elif self.value_type == FactValueType.INTEGER:
            valid = isinstance(self.initial_value, int) and not isinstance(self.initial_value, bool)
        elif self.value_type == FactValueType.BOOLEAN:
            valid = isinstance(self.initial_value, bool)
        else:
            valid = bool(self.allowed_values) and self.initial_value in self.allowed_values
        if not valid:
            raise ValueError("initial_value does not match the Fact value_type")
        if self.value_type != FactValueType.ENUM and self.allowed_values:
            raise ValueError("allowed_values are valid only for ENUM Facts")
        if len(set(self.allowed_values)) != len(self.allowed_values):
            raise ValueError("Fact allowed_values must be unique")
        if self.value_type == FactValueType.ENUM and any(
            type(value) is not type(self.initial_value) for value in self.allowed_values
        ):
            raise ValueError("ENUM values must share one scalar type")
        return self


class NodeDefinitionV2(FrozenDefinitionModel):
    key: StableKey
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    node_type_key: StableKey
    initial_access: AccessState
    initial_visibility: Visibility
    interaction_keys: tuple[StableKey, ...] = ()
    facts: tuple[FactDefinitionV2, ...] = ()

    @model_validator(mode="after")
    def validate_local_keys(self) -> NodeDefinitionV2:
        _require_unique(self.interaction_keys, "Node interaction keys")
        _require_unique((fact.key for fact in self.facts), "Node Fact keys")
        return self

    def fact(self, key: str) -> FactDefinitionV2 | None:
        return next((fact for fact in self.facts if fact.key == key), None)


class RelationDefinitionV2(FrozenDefinitionModel):
    source_node_key: StableKey
    relation_type_key: StableKey
    target_node_key: StableKey


class ResourceDefinitionV2(FrozenDefinitionModel):
    key: StableKey
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    initial_value: int
    minimum: int
    maximum: int | None = None
    reservation_supported: bool = False

    @model_validator(mode="after")
    def validate_bounds(self) -> ResourceDefinitionV2:
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("Resource maximum must be at least its minimum")
        if self.initial_value < self.minimum or (
            self.maximum is not None and self.initial_value > self.maximum
        ):
            raise ValueError("Resource initial_value is outside its bounds")
        return self


class WorldDefinitionV2(FrozenDefinitionModel):
    key: StableKey
    name: str = Field(min_length=1, max_length=160)
    node_types: tuple[NodeTypeDefinitionV2, ...]
    nodes: tuple[NodeDefinitionV2, ...]
    relations: tuple[RelationDefinitionV2, ...] = ()
    resources: tuple[ResourceDefinitionV2, ...] = ()

    def node(self, key: str) -> NodeDefinitionV2 | None:
        return next((node for node in self.nodes if node.key == key), None)


class RoleDefinitionV2(FrozenDefinitionModel):
    key: StableKey
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    capabilities: tuple[EngineCapability, ...]

    @model_validator(mode="after")
    def validate_capabilities(self) -> RoleDefinitionV2:
        _require_unique(self.capabilities, "Role capabilities")
        if not self.capabilities:
            raise ValueError("A Role needs at least one engine capability")
        return self


class AuthorityLimitV2(FrozenDefinitionModel):
    parameter_key: StableKey
    maximum: int


class AuthorityApprovalValueV2(FrozenDefinitionModel):
    parameter_key: StableKey
    values: tuple[StrictScalar, ...] = Field(min_length=1)


class AuthorityPolicyV2(FrozenDefinitionModel):
    autonomous_limits: tuple[AuthorityLimitV2, ...] = ()
    approval_required_values: tuple[AuthorityApprovalValueV2, ...] = ()

    @model_validator(mode="after")
    def validate_policy_keys(self) -> AuthorityPolicyV2:
        _require_unique(
            (limit.parameter_key for limit in self.autonomous_limits),
            "Authority limit parameter keys",
        )
        _require_unique(
            (rule.parameter_key for rule in self.approval_required_values),
            "Authority approval parameter keys",
        )
        return self


class DoctrineEntryV2(FrozenDefinitionModel):
    key: StableKey
    value: StrictScalar


class ActorProfileV2(FrozenDefinitionModel):
    key: StableKey
    name: str = Field(min_length=1, max_length=160)
    role_key: StableKey
    persona: str = Field(min_length=1, max_length=4000)
    doctrine: tuple[DoctrineEntryV2, ...] = ()
    initial_node_key: StableKey
    allowed_action_keys: tuple[StableKey, ...]
    authority_policy: AuthorityPolicyV2 = Field(default_factory=AuthorityPolicyV2)
    command_reachability: CommandReachability = Field(
        default=CommandReachability.ONLINE,
        exclude_if=lambda value: value == CommandReachability.ONLINE,
    )

    @model_validator(mode="after")
    def validate_actor_keys(self) -> ActorProfileV2:
        _require_unique((item.key for item in self.doctrine), "Actor doctrine keys")
        _require_unique(self.allowed_action_keys, "Actor allowed Action keys")
        return self


class ActorCatalogV2(FrozenDefinitionModel):
    roles: tuple[RoleDefinitionV2, ...]
    actor_profiles: tuple[ActorProfileV2, ...]


class ActionParameterV2(FrozenDefinitionModel):
    key: StableKey
    name: str = Field(min_length=1, max_length=160)
    value_type: ActionParameterType
    required: bool = True
    minimum: int | None = None
    maximum: int | None = None
    allowed_values: tuple[StrictScalar, ...] = ()
    default: StrictScalar | None = None

    @model_validator(mode="after")
    def validate_parameter(self) -> ActionParameterV2:
        if self.minimum is not None and self.maximum is not None and self.maximum < self.minimum:
            raise ValueError("Action parameter maximum must be at least its minimum")
        if self.value_type != ActionParameterType.INTEGER and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("Only INTEGER Action parameters may define numeric bounds")
        if self.value_type == ActionParameterType.ENUM:
            if not self.allowed_values or len(set(self.allowed_values)) != len(self.allowed_values):
                raise ValueError("ENUM Action parameters need unique allowed_values")
            scalar_type = type(self.allowed_values[0])
            if any(type(value) is not scalar_type for value in self.allowed_values):
                raise ValueError("ENUM Action parameter values must share one scalar type")
        elif self.allowed_values:
            raise ValueError("allowed_values are valid only for ENUM Action parameters")
        if self.default is not None:
            _validate_parameter_value(self, self.default, field="default")
        if self.required and self.default is not None:
            raise ValueError("A required Action parameter cannot define a default")
        return self


class FactReferenceV2(FrozenDefinitionModel):
    node_key: StableKey
    fact_key: StableKey


class ExpectedOutcomeV2(FrozenDefinitionModel):
    code: SymbolicCode
    name: str = Field(min_length=1, max_length=160)
    success: bool


class ActionPlanningProjectionV2(FrozenDefinitionModel):
    terminal_effects: tuple[FactReferenceV2, ...] = ()
    supporting_effects: tuple[FactReferenceV2, ...] = ()
    success_outcome_codes: tuple[SymbolicCode, ...] = ()
    wait_success_outcome_codes: tuple[SymbolicCode, ...] = ()
    hints: tuple[str, ...] = ()


class ActionDefinitionV2(FrozenDefinitionModel):
    key: StableKey
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    required_interaction_key: StableKey
    execution_mode: ActionExecutionMode
    parameters: tuple[ActionParameterV2, ...] = ()
    allowed_actor_capabilities: tuple[EngineCapability, ...]
    authority_policy: AuthorityPolicyV2 = Field(default_factory=AuthorityPolicyV2)
    expected_outcomes: tuple[ExpectedOutcomeV2, ...]
    planning: ActionPlanningProjectionV2 = Field(default_factory=ActionPlanningProjectionV2)
    behavior: ActionBehavior = ActionBehavior.RULE
    locality: ActionLocality = ActionLocality.NONE
    target_kind: ActionTargetKind = Field(
        default=ActionTargetKind.NODE,
        exclude_if=lambda value: value == ActionTargetKind.NODE,
    )

    @model_validator(mode="after")
    def validate_action(self) -> ActionDefinitionV2:
        _require_unique((item.key for item in self.parameters), "Action parameter keys")
        _require_unique(
            self.allowed_actor_capabilities,
            "Action allowed actor capabilities",
        )
        _require_unique((item.code for item in self.expected_outcomes), "Action outcome codes")
        if not self.allowed_actor_capabilities:
            raise ValueError("An Action needs at least one allowed actor capability")
        if not self.expected_outcomes:
            raise ValueError("An Action needs at least one expected outcome")
        if self.execution_mode == ActionExecutionMode.IMMEDIATE and (
            self.planning.wait_success_outcome_codes
        ):
            raise ValueError("An IMMEDIATE Action cannot define WAIT success outcomes")
        if (
            self.target_kind == ActionTargetKind.ACTOR
            and self.locality != ActionLocality.ACTOR_REGION
        ):
            raise ValueError("ACTOR Actions require ACTOR_REGION locality")
        if (
            self.behavior == ActionBehavior.RELAY_MESSAGE
            and self.target_kind != ActionTargetKind.ACTOR
        ):
            raise ValueError("RELAY_MESSAGE Actions require an ACTOR target")
        if (
            self.behavior == ActionBehavior.SURVEY_RESOURCES
            and self.locality != ActionLocality.REGION
        ):
            raise ValueError("SURVEY_RESOURCES Actions require REGION locality")
        return self


class NodeSelectorV2(FrozenDefinitionModel):
    kind: NodeSelectorKind
    node_key: StableKey | None = None
    anchor_node_key: StableKey | None = None
    relation_type_key: StableKey | None = None
    direction: RelationDirection | None = None
    required_fact_key: StableKey | None = None

    @model_validator(mode="after")
    def validate_selector(self) -> NodeSelectorV2:
        related_fields = (
            self.anchor_node_key,
            self.relation_type_key,
            self.direction,
            self.required_fact_key,
        )
        if self.kind == NodeSelectorKind.CURRENT_TARGET:
            if self.node_key is not None or any(value is not None for value in related_fields):
                raise ValueError("CURRENT_TARGET selector cannot define reference fields")
        elif self.kind == NodeSelectorKind.EXPLICIT:
            if self.node_key is None or any(value is not None for value in related_fields):
                raise ValueError("EXPLICIT selector requires only node_key")
        elif self.node_key is not None or self.relation_type_key is None or self.direction is None:
            raise ValueError(
                "RELATED selector requires relation_type_key/direction and no node_key"
            )
        return self


class ValueExpressionV2(FrozenDefinitionModel):
    source: ValueSource
    literal: StrictScalar | None = None
    parameter_key: StableKey | None = None

    @model_validator(mode="after")
    def validate_source(self) -> ValueExpressionV2:
        if self.source == ValueSource.LITERAL:
            if self.literal is None or self.parameter_key is not None:
                raise ValueError("LITERAL expression requires only literal")
        elif self.parameter_key is None or self.literal is not None:
            raise ValueError("PARAMETER expression requires only parameter_key")
        return self


class IntegerExpressionV2(FrozenDefinitionModel):
    source: ValueSource
    literal: int | None = None
    parameter_key: StableKey | None = None
    multiplier: int = 1

    @model_validator(mode="after")
    def validate_source(self) -> IntegerExpressionV2:
        if self.source == ValueSource.LITERAL:
            if self.literal is None or self.parameter_key is not None:
                raise ValueError("LITERAL integer expression requires only literal")
        elif self.parameter_key is None or self.literal is not None:
            raise ValueError("PARAMETER integer expression requires only parameter_key")
        return self


class ConditionV2(FrozenDefinitionModel):
    kind: ConditionKind
    conditions: tuple[ConditionV2, ...] = ()
    condition: ConditionV2 | None = None
    node: NodeSelectorV2 | None = None
    fact_key: StableKey | None = None
    resource_key: StableKey | None = None
    resource_scope: ResourceScopeV2 | None = None
    parameter_key: StableKey | None = None
    operator: ComparisonOperator | None = None
    value: StrictScalar | None = None
    values: tuple[StrictScalar, ...] = ()
    visibility: Visibility | None = None
    access: AccessState | None = None
    relation_type_key: StableKey | None = None
    relation_direction: RelationDirection | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> ConditionV2:
        if self.kind in {ConditionKind.ALL, ConditionKind.ANY}:
            if not self.conditions or self.condition is not None:
                raise ValueError("ALL/ANY conditions require a non-empty conditions list")
        elif self.kind == ConditionKind.NOT:
            if self.condition is None or self.conditions:
                raise ValueError("NOT requires exactly one condition")
        elif self.kind in {
            ConditionKind.FACT_EQUALS,
            ConditionKind.FACT_NOT_EQUALS,
        }:
            if self.node is None or self.fact_key is None or self.value is None:
                raise ValueError("Fact equality condition requires node/fact/value")
        elif self.kind == ConditionKind.FACT_IN:
            if self.node is None or self.fact_key is None or not self.values:
                raise ValueError("FACT_IN requires node/fact/values")
        elif self.kind == ConditionKind.FACT_COMPARE:
            if (
                self.node is None
                or self.fact_key is None
                or self.operator is None
                or self.value is None
            ):
                raise ValueError("FACT_COMPARE requires node/fact/operator/value")
        elif self.kind == ConditionKind.RESOURCE_COMPARE:
            if self.resource_key is None or self.operator is None or self.value is None:
                raise ValueError("RESOURCE_COMPARE requires resource/operator/value")
        elif self.kind == ConditionKind.PARAMETER_COMPARE:
            if self.parameter_key is None or self.operator is None or self.value is None:
                raise ValueError("PARAMETER_COMPARE requires parameter/operator/value")
        elif self.kind == ConditionKind.NODE_VISIBLE:
            if self.node is None or self.visibility is None:
                raise ValueError("NODE_VISIBLE requires node/visibility")
        elif self.kind == ConditionKind.NODE_ACCESSIBLE:
            if self.node is None or self.access is None:
                raise ValueError("NODE_ACCESSIBLE requires node/access")
        elif self.node is None or self.relation_type_key is None or self.relation_direction is None:
            raise ValueError("RELATION_EXISTS requires node/relation_type_key/direction")
        return self


class EffectV2(FrozenDefinitionModel):
    kind: EffectKind
    node: NodeSelectorV2 | None = None
    fact_key: StableKey | None = None
    value: ValueExpressionV2 | None = None
    access: AccessState | None = None
    resource_key: StableKey | None = None
    resource_scope: ResourceScopeV2 | None = None
    amount: IntegerExpressionV2 | None = None
    outcome_code: SymbolicCode | None = None
    failure_code: SymbolicCode | None = None
    message: str | None = Field(default=None, max_length=2000)
    retryable: bool = False
    memory_key: StableKey | None = None
    memory_content: str | None = Field(default=None, max_length=4000)
    actor_key: StableKey | None = Field(default=None, exclude_if=lambda value: value is None)
    command_reachability: CommandReachability | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    region_key: StableKey | None = Field(default=None, exclude_if=lambda value: value is None)
    pool_key: StableKey | None = Field(default=None, exclude_if=lambda value: value is None)
    visibility: ResourceInventoryVisibility | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    availability: ResourcePoolAvailability | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_shape(self) -> EffectV2:
        if self.kind == EffectKind.SET_FACT:
            if self.node is None or self.fact_key is None or self.value is None:
                raise ValueError("SET_FACT requires node/fact/value")
        elif self.kind in {EffectKind.REVEAL_FACT, EffectKind.HIDE_FACT}:
            if self.node is None or self.fact_key is None:
                raise ValueError("Fact visibility Effect requires node/fact")
        elif self.kind in {EffectKind.REVEAL_NODE, EffectKind.HIDE_NODE}:
            if self.node is None:
                raise ValueError("Node visibility Effect requires node")
        elif self.kind == EffectKind.SET_NODE_ACCESS:
            if self.node is None or self.access is None:
                raise ValueError("SET_NODE_ACCESS requires node/access")
        elif self.kind in {
            EffectKind.ADJUST_RESOURCE,
            EffectKind.RESERVE_RESOURCE,
            EffectKind.RELEASE_RESOURCE,
        }:
            if self.resource_key is None or self.amount is None:
                raise ValueError("Resource Effect requires resource_key/amount")
        elif self.kind == EffectKind.EMIT_OUTCOME:
            if self.outcome_code is None:
                raise ValueError("EMIT_OUTCOME requires outcome_code")
        elif self.kind == EffectKind.EMIT_FAILURE:
            if self.failure_code is None or not self.message:
                raise ValueError("EMIT_FAILURE requires failure_code/message")
        elif self.kind == EffectKind.WRITE_MEMORY_EVENT and (
            self.memory_key is None or not self.memory_content
        ):
            raise ValueError("WRITE_MEMORY_EVENT requires memory_key/content")
        elif (
            self.kind == EffectKind.SET_ACTOR_COMMAND_REACHABILITY
            and self.command_reachability is None
        ):
            raise ValueError("SET_ACTOR_COMMAND_REACHABILITY requires command_reachability")
        elif self.kind == EffectKind.SET_REGION_RESOURCE_VISIBILITY and (
            self.region_key is None or self.visibility is None
        ):
            raise ValueError("SET_REGION_RESOURCE_VISIBILITY requires region_key/visibility")
        elif self.kind == EffectKind.SET_RESOURCE_POOL_VISIBILITY and (
            self.pool_key is None or self.visibility is None
        ):
            raise ValueError("SET_RESOURCE_POOL_VISIBILITY requires pool_key/visibility")
        elif self.kind == EffectKind.SET_RESOURCE_POOL_AVAILABILITY and (
            self.pool_key is None or self.availability is None
        ):
            raise ValueError("SET_RESOURCE_POOL_AVAILABILITY requires pool_key/availability")
        return self


class RuleDefinitionV2(FrozenDefinitionModel):
    key: StableKey
    phase: RulePhase
    action_key: StableKey
    priority: int
    condition: ConditionV2 | None = None
    effects: tuple[EffectV2, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_phase_effects(self) -> RuleDefinitionV2:
        terminals = [
            effect
            for effect in self.effects
            if effect.kind in {EffectKind.EMIT_OUTCOME, EffectKind.EMIT_FAILURE}
        ]
        if self.phase == RulePhase.PREFLIGHT:
            if any(effect.kind != EffectKind.EMIT_FAILURE for effect in self.effects):
                raise ValueError("PREFLIGHT rules may only emit a deterministic failure")
        elif len(terminals) != 1:
            raise ValueError("A RESOLVE rule requires exactly one outcome or failure Effect")
        return self


class ObjectiveRequirementV2(FrozenDefinitionModel):
    key: StableKey
    node_key: StableKey
    fact_key: StableKey
    accepted_values: tuple[StrictScalar, ...] = Field(min_length=1)
    description: str = Field(min_length=1, max_length=2000)


class ObjectivePrerequisiteV2(FrozenDefinitionModel):
    key: StableKey
    description: str = Field(min_length=1, max_length=2000)
    requirements: tuple[ObjectiveRequirementV2, ...] = Field(min_length=1)


class ObjectiveDefinitionV2(FrozenDefinitionModel):
    key: StableKey
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=4000)
    completion_requirements: tuple[ObjectiveRequirementV2, ...] = Field(min_length=1)
    prerequisites: tuple[ObjectivePrerequisiteV2, ...] = ()
    subsumes: tuple[StableKey, ...] = ()
    goal_aliases: tuple[str, ...] = ()
    goal_examples: tuple[str, ...] = ()
    planning_guidance: str | None = Field(
        default=None,
        max_length=4000,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def validate_objective_keys(self) -> ObjectiveDefinitionV2:
        _require_unique(
            (item.key for item in self.completion_requirements),
            "Objective requirement keys",
        )
        _require_unique(
            (item.key for item in self.prerequisites),
            "Objective prerequisite keys",
        )
        _require_unique(self.subsumes, "Objective subsumption keys")
        normalized_aliases = [alias.strip().casefold() for alias in self.goal_aliases]
        if any(not alias for alias in normalized_aliases):
            raise ValueError("Objective goal aliases cannot be blank")
        _require_unique(normalized_aliases, "Objective goal aliases")
        return self


class GoalResolutionV2(FrozenDefinitionModel):
    allow_llm_fallback: bool = True
    clarification_prompt: str = Field(min_length=1, max_length=2000)


class RecoveryHintV2(FrozenDefinitionModel):
    failure_code: SymbolicCode
    hint: str = Field(min_length=1, max_length=2000)


class PlanningDefinitionV2(FrozenDefinitionModel):
    instructions: tuple[str, ...] = ()
    recovery_hints: tuple[RecoveryHintV2, ...] = ()

    @model_validator(mode="after")
    def validate_recovery_hints(self) -> PlanningDefinitionV2:
        _require_unique(
            (item.failure_code for item in self.recovery_hints),
            "Planning recovery failure codes",
        )
        return self


class ScenarioDefinitionV2(FrozenDefinitionModel):
    schema_version: Literal[2] = 2
    metadata: ScenarioMetadataV2
    engine_contract: EngineContractV2
    initialization: InitializationV2
    world: WorldDefinitionV2
    actors: ActorCatalogV2
    interactions: tuple[InteractionDefinitionV2, ...]
    actions: tuple[ActionDefinitionV2, ...]
    rules: tuple[RuleDefinitionV2, ...]
    objectives: tuple[ObjectiveDefinitionV2, ...]
    goal_resolution: GoalResolutionV2
    planning: PlanningDefinitionV2 = Field(default_factory=PlanningDefinitionV2)

    @property
    def objective_catalog_version(self) -> str:
        return f"scenario-v2:{self.metadata.key}"

    @property
    def objective_definitions(self):  # type: ignore[no-untyped-def]
        return MappingProxyType({objective.key: objective for objective in self.objectives})

    @model_validator(mode="after")
    def validate_references(self) -> ScenarioDefinitionV2:
        if self.metadata.key != self.world.key:
            raise ValueError("Scenario metadata key must match the World key")
        _validate_v2_references(self)
        return self


def _validate_parameter_value(
    definition: ActionParameterV2,
    value: StrictScalar,
    *,
    field: str,
) -> None:
    if definition.value_type == ActionParameterType.STRING:
        valid = isinstance(value, str)
    elif definition.value_type == ActionParameterType.INTEGER:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif definition.value_type == ActionParameterType.BOOLEAN:
        valid = isinstance(value, bool)
    else:
        valid = value in definition.allowed_values
    if not valid:
        raise ValueError(f"Action parameter {field} does not match value_type")
    if isinstance(value, int) and not isinstance(value, bool):
        if definition.minimum is not None and value < definition.minimum:
            raise ValueError(f"Action parameter {field} is below minimum")
        if definition.maximum is not None and value > definition.maximum:
            raise ValueError(f"Action parameter {field} is above maximum")


def validate_action_parameters(
    action: ActionDefinitionV2, parameters: dict[str, StrictScalar]
) -> None:
    """Validate one Action input without reading runtime state.

    Callers that need to persist or execute the input should use
    :func:`normalize_action_parameters` so declared defaults are materialized
    before the value crosses an application boundary.
    """

    normalize_action_parameters(action, parameters)


def normalize_action_parameters(
    action: ActionDefinitionV2, parameters: dict[str, StrictScalar]
) -> dict[str, StrictScalar]:
    """Merge Action defaults and strictly validate one canonical input.

    Scenario authors declare the parameter schema, including defaults.  A
    missing optional value therefore has one canonical representation in the
    runtime: the declared default is present in the normalized mapping.  The
    function never mutates the caller's dictionary.
    """

    definitions = {item.key: item for item in action.parameters}
    unknown = set(parameters) - set(definitions)
    if unknown:
        raise ValueError("Action input contains unknown parameters")
    normalized: dict[str, StrictScalar] = dict(parameters)
    for key, definition in definitions.items():
        if key not in parameters:
            if definition.required and definition.default is None:
                raise ValueError(f"Action input is missing required parameter {key}")
            if definition.default is not None:
                normalized[key] = definition.default
        if key in normalized:
            _validate_parameter_value(definition, normalized[key], field=key)
    return normalized


def _validate_v2_references(definition: ScenarioDefinitionV2) -> None:
    world = definition.world
    _require_unique((item.key for item in world.node_types), "World Node Type keys")
    _require_unique((item.key for item in world.nodes), "World Node keys")
    _require_unique((item.key for item in world.resources), "World Resource keys")
    _require_unique((item.key for item in definition.interactions), "Interaction keys")
    _require_unique((item.key for item in definition.actors.roles), "Role keys")
    _require_unique((item.key for item in definition.actors.actor_profiles), "Actor keys")
    _require_unique((item.key for item in definition.actions), "Action keys")
    _require_unique((item.key for item in definition.rules), "Rule keys")
    _require_unique((item.key for item in definition.objectives), "Objective keys")

    node_types = {item.key for item in world.node_types}
    nodes = {item.key: item for item in world.nodes}
    resources = {item.key: item for item in world.resources}
    interactions = {item.key for item in definition.interactions}
    roles = {item.key: item for item in definition.actors.roles}
    actors = {item.key: item for item in definition.actors.actor_profiles}
    actions = {item.key: item for item in definition.actions}
    objectives = {item.key: item for item in definition.objectives}

    _validate_locality_contract(definition, nodes, node_types)
    _validate_resource_initial_states(definition, nodes)
    _validate_resource_pools(definition, nodes)
    _validate_region_resource_knowledge(definition, nodes)

    _require_key(nodes, definition.initialization.start_node_key, "start Node")
    primary = _require_key(
        actors,
        definition.initialization.primary_actor_key,
        "primary Actor",
    )
    primary_role = _require_key(roles, primary.role_key, "primary Actor Role")
    if EngineCapability.PLAN not in primary_role.capabilities:
        raise ValueError("The primary Actor Role must have PLAN capability")

    for node in world.nodes:
        _require_key(node_types, node.node_type_key, f"Node {node.key} type")
        for interaction_key in node.interaction_keys:
            _require_key(interactions, interaction_key, f"Node {node.key} Interaction")
    for relation in world.relations:
        _require_key(nodes, relation.source_node_key, "Relation source Node")
        _require_key(nodes, relation.target_node_key, "Relation target Node")
    if len(set(world.relations)) != len(world.relations):
        raise ValueError("World Relations must be unique")

    for actor in definition.actors.actor_profiles:
        _require_key(roles, actor.role_key, f"Actor {actor.key} Role")
        _require_key(nodes, actor.initial_node_key, f"Actor {actor.key} initial Node")
        for action_key in actor.allowed_action_keys:
            _require_key(actions, action_key, f"Actor {actor.key} allowed Action")
        for limit in actor.authority_policy.autonomous_limits:
            if not any(
                limit.parameter_key in {parameter.key for parameter in action.parameters}
                for action in actions.values()
                if action.key in actor.allowed_action_keys
            ):
                raise ValueError(
                    f"Actor {actor.key} authority limit references an unavailable parameter"
                )

    for action in definition.actions:
        if (
            action.behavior != ActionBehavior.RULE or action.locality != ActionLocality.NONE
        ) and not definition.metadata.locality.enabled:
            raise ValueError(
                f"Action {action.key} requires the Scenario locality contract to be enabled"
            )
        if (
            action.behavior == ActionBehavior.TRANSPORT_RESOURCE
            and not definition.metadata.locality.scoped_resources
        ):
            raise ValueError(
                f"Action {action.key} requires scoped_resources in the locality contract"
            )
        _require_key(
            interactions,
            action.required_interaction_key,
            f"Action {action.key} Interaction",
        )
        parameters = {parameter.key: parameter for parameter in action.parameters}
        _validate_authority_parameters(action.authority_policy, parameters, action.key)
        outcome_codes = {outcome.code for outcome in action.expected_outcomes}
        for code in (
            *action.planning.success_outcome_codes,
            *action.planning.wait_success_outcome_codes,
        ):
            _require_key(outcome_codes, code, f"Action {action.key} planning outcome")
        for fact_ref in (
            *action.planning.terminal_effects,
            *action.planning.supporting_effects,
        ):
            _require_fact(nodes, fact_ref.node_key, fact_ref.fact_key, "Action planning Effect")

    for rule in definition.rules:
        action = _require_key(actions, rule.action_key, f"Rule {rule.key} Action")
        parameters = {parameter.key: parameter for parameter in action.parameters}
        if rule.condition is not None:
            _validate_condition_refs(
                rule.condition,
                nodes,
                resources,
                parameters,
                definition.metadata.locality,
            )
        for effect in rule.effects:
            _validate_effect_refs(
                effect,
                nodes,
                resources,
                actors,
                parameters,
                action,
                definition.metadata.locality,
                {item.pool_key for item in definition.initialization.resource_pools},
            )

    aliases: set[str] = set()
    subsumption_graph: dict[str, tuple[str, ...]] = {}
    for objective in definition.objectives:
        for requirement in objective.completion_requirements:
            _validate_objective_requirement(requirement, nodes)
        for prerequisite in objective.prerequisites:
            for requirement in prerequisite.requirements:
                _validate_objective_requirement(requirement, nodes)
        for key in objective.subsumes:
            if key == objective.key:
                raise ValueError("An Objective cannot subsume itself")
            _require_key(objectives, key, f"Objective {objective.key} subsumption")
        subsumption_graph[objective.key] = objective.subsumes
        for alias in objective.goal_aliases:
            normalized = alias.strip().casefold()
            if normalized in aliases:
                raise ValueError("Objective goal aliases must be globally unique")
            aliases.add(normalized)
    _require_acyclic(subsumption_graph)


def _validate_authority_parameters(
    policy: AuthorityPolicyV2,
    parameters: dict[str, ActionParameterV2],
    action_key: str,
) -> None:
    for limit in policy.autonomous_limits:
        parameter = _require_key(
            parameters,
            limit.parameter_key,
            f"Action {action_key} authority parameter",
        )
        if parameter.value_type != ActionParameterType.INTEGER:
            raise ValueError("Authority limits may reference only INTEGER parameters")
    for approval in policy.approval_required_values:
        parameter = _require_key(
            parameters,
            approval.parameter_key,
            f"Action {action_key} approval parameter",
        )
        for value in approval.values:
            _validate_parameter_value(parameter, value, field="approval value")


def _validate_condition_refs(
    condition: ConditionV2,
    nodes: dict[str, NodeDefinitionV2],
    resources: dict[str, ResourceDefinitionV2],
    parameters: dict[str, ActionParameterV2],
    locality: LocalityContractV2,
) -> None:
    for child in condition.conditions:
        _validate_condition_refs(child, nodes, resources, parameters, locality)
    if condition.condition is not None:
        _validate_condition_refs(condition.condition, nodes, resources, parameters, locality)
    _validate_selector(condition.node, nodes)
    if condition.node is not None and condition.fact_key is not None:
        _validate_selector_fact(condition.node, condition.fact_key, nodes)
    if condition.resource_key is not None:
        _require_key(resources, condition.resource_key, "Condition Resource")
        _validate_resource_scope(condition.resource_scope, nodes, locality, "Condition")
    if condition.parameter_key is not None:
        parameter = _require_key(parameters, condition.parameter_key, "Condition parameter")
        if condition.value is not None:
            _validate_parameter_value(parameter, condition.value, field="comparison value")


def _validate_effect_refs(
    effect: EffectV2,
    nodes: dict[str, NodeDefinitionV2],
    resources: dict[str, ResourceDefinitionV2],
    actors: dict[str, ActorProfileV2],
    parameters: dict[str, ActionParameterV2],
    action: ActionDefinitionV2,
    locality: LocalityContractV2,
    pool_keys: set[str],
) -> None:
    if effect.actor_key is not None:
        _require_key(actors, effect.actor_key, "Effect Actor")
    _validate_selector(effect.node, nodes)
    if effect.node is not None and effect.fact_key is not None:
        _validate_selector_fact(effect.node, effect.fact_key, nodes)
    if effect.resource_key is not None:
        resource = _require_key(resources, effect.resource_key, "Effect Resource")
        _validate_resource_scope(effect.resource_scope, nodes, locality, "Effect")
        if effect.kind in {EffectKind.RESERVE_RESOURCE, EffectKind.RELEASE_RESOURCE} and not (
            resource.reservation_supported
        ):
            raise ValueError("Resource reservation Effect requires reservation_supported")
    if effect.kind == EffectKind.SET_REGION_RESOURCE_VISIBILITY:
        assert effect.region_key is not None
        if not locality.enabled or not locality.scoped_resources:
            raise ValueError("Region Resource Visibility requires scoped locality")
        region = _require_key(nodes, effect.region_key, "Region Resource Visibility Region")
        if region.node_type_key != locality.region_node_type_key:
            raise ValueError("Region Resource Visibility must target a Region Node")
    if effect.kind in {
        EffectKind.SET_RESOURCE_POOL_VISIBILITY,
        EffectKind.SET_RESOURCE_POOL_AVAILABILITY,
    }:
        assert effect.pool_key is not None
        if not locality.enabled or not locality.scoped_resources:
            raise ValueError("Resource Pool Effects require scoped locality")
        if effect.pool_key != "default" and effect.pool_key not in pool_keys:
            raise ValueError(f"Resource Pool Effect references unknown Pool {effect.pool_key}")
    expressions = [effect.value, effect.amount]
    for expression in expressions:
        if expression is not None and expression.parameter_key is not None:
            _require_key(parameters, expression.parameter_key, "Effect parameter")
    if effect.outcome_code is not None:
        _require_key(
            {outcome.code for outcome in action.expected_outcomes},
            effect.outcome_code,
            f"Action {action.key} emitted outcome",
        )


def _validate_selector(
    selector: NodeSelectorV2 | None,
    nodes: dict[str, NodeDefinitionV2],
) -> None:
    if selector is None:
        return
    if selector.node_key is not None:
        _require_key(nodes, selector.node_key, "Node selector")
    if selector.anchor_node_key is not None:
        _require_key(nodes, selector.anchor_node_key, "Relation selector anchor")


def _validate_selector_fact(
    selector: NodeSelectorV2,
    fact_key: str,
    nodes: dict[str, NodeDefinitionV2],
) -> None:
    if selector.kind == NodeSelectorKind.EXPLICIT and selector.node_key is not None:
        _require_fact(nodes, selector.node_key, fact_key, "Rule Fact")


def _validate_objective_requirement(
    requirement: ObjectiveRequirementV2,
    nodes: dict[str, NodeDefinitionV2],
) -> None:
    fact = _require_fact(
        nodes,
        requirement.node_key,
        requirement.fact_key,
        "Objective requirement",
    )
    for value in requirement.accepted_values:
        if fact.value_type == FactValueType.ENUM and value not in fact.allowed_values:
            raise ValueError("Objective value is outside the ENUM Fact domain")
        if fact.value_type == FactValueType.STRING and not isinstance(value, str):
            raise ValueError("Objective value does not match STRING Fact")
        if fact.value_type == FactValueType.INTEGER and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            raise ValueError("Objective value does not match INTEGER Fact")
        if fact.value_type == FactValueType.BOOLEAN and not isinstance(value, bool):
            raise ValueError("Objective value does not match BOOLEAN Fact")


def _validate_locality_contract(
    definition: ScenarioDefinitionV2,
    nodes: dict[str, NodeDefinitionV2],
    node_types: set[str],
) -> None:
    locality = definition.metadata.locality
    if not locality.enabled:
        return
    for key, label in (
        (locality.region_node_type_key, "Region Node Type"),
        (locality.facility_node_type_key, "Facility Node Type"),
        (locality.transport_node_type_key, "Transport Node Type"),
    ):
        assert key is not None
        _require_key(node_types, key, label)
    relation_types = {item.relation_type_key for item in definition.world.relations}
    for key, label in (
        (locality.located_in_relation_type_key, "located_in Relation Type"),
        (locality.transport_endpoint_relation_type_key, "endpoint Relation Type"),
    ):
        assert key is not None
        _require_key(relation_types, key, label)
    if locality.passability_fact_key is not None and not any(
        node.fact(locality.passability_fact_key) is not None for node in nodes.values()
    ):
        raise ValueError("Locality passability_fact_key does not reference a Fact")


def _validate_resource_initial_states(
    definition: ScenarioDefinitionV2,
    nodes: dict[str, NodeDefinitionV2],
) -> None:
    locality = definition.metadata.locality
    resources = {item.key: item for item in definition.world.resources}
    states = definition.initialization.resource_initial_states
    if not states:
        return
    seen: set[tuple[str, str | None]] = set()
    for state in states:
        resource = _require_key(resources, state.resource_key, "Initial Resource state")
        scope = state.scope_node_key
        if scope is not None:
            if not locality.enabled or not locality.scoped_resources:
                raise ValueError("Scoped initial Resource state requires locality.scoped_resources")
            node = _require_key(nodes, scope, "Initial Resource scope")
            if node.node_type_key != locality.region_node_type_key:
                raise ValueError("Scoped initial Resource state must target a Region Node")
        identity = (state.resource_key, scope)
        if identity in seen:
            raise ValueError("Initial Resource state identities must be unique")
        seen.add(identity)
        if state.value < resource.minimum or (
            resource.maximum is not None and state.value > resource.maximum
        ):
            raise ValueError("Initial Resource state value is outside its Resource bounds")
        if state.reserved_value > state.value:
            raise ValueError("Initial Resource reserved_value cannot exceed value")
    # When Pool authoring is present, resources not listed in either section
    # still receive the backward-compatible default Pool at runtime.  The
    # legacy balance-only form retains its original "initialize every
    # Resource" contract.
    if not definition.initialization.resource_pools and {
        item.resource_key for item in states
    } != set(resources):
        raise ValueError("Initial Resource states must initialize every Resource definition")


def _validate_resource_pools(
    definition: ScenarioDefinitionV2,
    nodes: dict[str, NodeDefinitionV2],
) -> None:
    pools = definition.initialization.resource_pools
    if not pools:
        return
    locality = definition.metadata.locality
    resources = {item.key: item for item in definition.world.resources}
    seen_pool_keys: set[str] = set()
    seen_identities: set[tuple[str, str, str | None]] = set()
    for pool in pools:
        if pool.pool_key in seen_pool_keys:
            raise ValueError("Resource Pool keys must be globally unique")
        seen_pool_keys.add(pool.pool_key)
        resource = _require_key(resources, pool.resource_key, "Resource Pool Resource")
        if pool.quantity < resource.minimum or (
            resource.maximum is not None and pool.quantity > resource.maximum
        ):
            raise ValueError("Resource Pool quantity is outside its Resource bounds")
        if pool.reserved_value > pool.quantity:
            raise ValueError("Resource Pool reserved_value cannot exceed quantity")
        if pool.region_key is not None:
            if not locality.enabled or not locality.scoped_resources:
                raise ValueError("Region Resource Pools require locality.scoped_resources")
            region = _require_key(nodes, pool.region_key, "Resource Pool Region")
            if region.node_type_key != locality.region_node_type_key:
                raise ValueError("Resource Pool region_key must target a Region Node")
        if pool.facility_key is not None:
            if not locality.enabled or pool.region_key is None:
                raise ValueError("A Facility-bound Resource Pool requires a Region")
            facility = _require_key(nodes, pool.facility_key, "Resource Pool Facility")
            if facility.node_type_key != locality.facility_node_type_key:
                raise ValueError("Resource Pool facility_key must target a Facility Node")
            if (
                region_for_node_key := _static_facility_region(definition, pool.facility_key)
            ) and region_for_node_key != pool.region_key:
                raise ValueError("Resource Pool Facility must belong to its Region")
        identity = (pool.resource_key, pool.region_key or "", pool.pool_key)
        if identity in seen_identities:
            raise ValueError("Resource Pool identities must be unique")
        seen_identities.add(identity)
        if pool.availability_requirement is not None:
            _require_fact(
                nodes,
                pool.availability_requirement.node_key,
                pool.availability_requirement.fact_key,
                "Resource Pool availability requirement",
            )


def _validate_region_resource_knowledge(
    definition: ScenarioDefinitionV2,
    nodes: dict[str, NodeDefinitionV2],
) -> None:
    states = definition.initialization.region_resource_knowledge
    if not states:
        return
    locality = definition.metadata.locality
    seen: set[str] = set()
    for state in states:
        if state.region_key in seen:
            raise ValueError("Region Resource Knowledge keys must be unique")
        seen.add(state.region_key)
        if not locality.enabled or not locality.scoped_resources:
            raise ValueError("Region Resource Knowledge requires locality.scoped_resources")
        node = _require_key(nodes, state.region_key, "Region Resource Knowledge Region")
        if node.node_type_key != locality.region_node_type_key:
            raise ValueError("Region Resource Knowledge must target a Region Node")


def _static_facility_region(definition: ScenarioDefinitionV2, facility_key: str) -> str | None:
    locality = definition.metadata.locality
    relation_key = locality.located_in_relation_type_key
    if relation_key is None:
        return None
    matches = [
        item.target_node_key
        for item in definition.world.relations
        if item.source_node_key == facility_key and item.relation_type_key == relation_key
    ]
    return matches[0] if len(matches) == 1 else None


def _validate_resource_scope(
    scope: ResourceScopeV2 | None,
    nodes: dict[str, NodeDefinitionV2],
    locality: LocalityContractV2,
    label: str,
) -> None:
    if scope is None:
        return
    if not locality.enabled or not locality.scoped_resources:
        raise ValueError(f"{label} Resource scope requires locality.scoped_resources")
    if scope.kind == ResourceScopeKind.EXPLICIT:
        assert scope.node_key is not None
        node = _require_key(nodes, scope.node_key, f"{label} Resource scope")
        if node.node_type_key != locality.region_node_type_key:
            raise ValueError(f"{label} Resource scope must target a Region Node")


def _require_fact(
    nodes: dict[str, NodeDefinitionV2],
    node_key: str,
    fact_key: str,
    label: str,
) -> FactDefinitionV2:
    node = _require_key(nodes, node_key, f"{label} Node")
    fact = node.fact(fact_key)
    if fact is None:
        raise ValueError(f"{label} references unknown Fact {node_key}.{fact_key}")
    return fact


def _require_key[T](catalog: set[str] | dict[str, T], key: str, label: str) -> T:
    if isinstance(catalog, set):
        if key not in catalog:
            raise ValueError(f"{label} references unknown key {key}")
        return None  # type: ignore[return-value]
    try:
        return catalog[key]
    except KeyError:
        raise ValueError(f"{label} references unknown key {key}") from None


def _require_unique(values, label: str) -> None:  # type: ignore[no-untyped-def]
    materialized = tuple(values)
    if len(set(materialized)) != len(materialized):
        raise ValueError(f"{label} must be unique")


def _require_acyclic(graph: dict[str, tuple[str, ...]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise ValueError("Objective subsumption must be acyclic")
        if key in visited:
            return
        visiting.add(key)
        for child in graph[key]:
            visit(child)
        visiting.remove(key)
        visited.add(key)

    for key in graph:
        visit(key)


__all__ = [
    "ActionBehavior",
    "ActionDefinitionV2",
    "ActionExecutionMode",
    "ActionLocality",
    "ActionParameterType",
    "ConditionKind",
    "EffectKind",
    "EngineCapability",
    "LocalityContractV2",
    "RegionResourceKnowledgeInitialStateV2",
    "ResourceAvailabilityRequirementV2",
    "ResourceInitialStateV2",
    "ResourcePoolDefinitionV2",
    "ResourceScopeKind",
    "ResourceScopeV2",
    "RulePhase",
    "ScenarioDefinitionV2",
]
