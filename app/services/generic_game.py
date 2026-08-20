"""Instance-scoped generic state service and declarative outcome applicator."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import actor_binding_matches, evaluate_authority
from app.domain.enums import (
    AuthorityOutcome,
    CommandReachability,
    NodeStatus,
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
)
from app.domain.resources import (
    resource_identity,
    resource_pool_initial_states,
    resource_state_key,
    valid_resource_state_identity,
)
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import (
    ActionBehavior,
    ActionTargetKind,
    ScenarioDefinitionV2,
    StrictScalar,
    normalize_action_parameters,
)
from app.domain.world import AccessState, Visibility
from app.engine.locality import (
    LocalityEngineError,
    passability_fact,
    region_for_node,
    transport_between,
    validate_action_locality,
)
from app.engine.rules import (
    ActionRuleContext,
    ActorCommandReachabilityMutation,
    DeclarativeRuleEngine,
    DeclarativeRuleState,
    FactVisibilityMutation,
    GenericRuleOutcome,
    RegionResourceSurveyMutation,
    RegionResourceVisibilityMutation,
    ResourceMutation,
    ResourcePoolVisibilityMutation,
    RuleActorState,
    RuleEngineError,
    RuleFactState,
    RuleFailure,
    RuleNodeState,
    RuleRegionResourceKnowledgeState,
    RuleResourcePoolState,
)
from app.infrastructure.db.models import (
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceMemoryEvent,
    GameInstanceNodeState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceResourceState,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import require_scope_writable


class GenericGameError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class AppliedRuleResult:
    outcome: GenericRuleOutcome
    runtime_revision: int
    knowledge_changes: tuple[PlayerKnowledgeChange, ...] = ()


@dataclass(frozen=True, slots=True)
class PlayerKnowledgeChange:
    kind: str
    key: str
    name: str
    value: StrictScalar | None = None


class GenericGameService:
    """Apply exact-Version rules to one and only one GameInstance."""

    def __init__(self, db: Session, scope: RuntimeScope) -> None:
        self.db = db
        self.scope = scope

    def execute(
        self,
        *,
        actor_key: str,
        action_key: str,
        target_node_key: str,
        parameters: dict[str, StrictScalar],
        operation_status: str | None = None,
        approval_granted: bool = False,
    ) -> AppliedRuleResult:
        require_scope_writable(self.db, self.scope.game_instance_id)
        definition = self._definition()
        actor = self._actor(actor_key)
        if not actor_binding_matches(definition, actor):
            raise GenericGameError(
                "RUNTIME_ACTOR_BINDING_INVALID",
                "Runtime Actor authority drifted from the exact ScenarioVersion",
            )
        action = next((item for item in definition.actions if item.key == action_key), None)
        if action is None:
            raise GenericGameError(
                "ACTION_NOT_AUTHORIZED",
                "The exact Version Actor is not authorized for this Action",
            )
        try:
            parameters = normalize_action_parameters(action, parameters)
        except ValueError as exc:
            raise GenericGameError("ACTION_PARAMETERS_INVALID", str(exc)) from exc
        target_actor = None
        if action.target_kind == ActionTargetKind.ACTOR:
            target_actor = self._actor(target_node_key)
            if not actor_binding_matches(definition, target_actor):
                raise GenericGameError(
                    "RUNTIME_ACTOR_BINDING_INVALID",
                    "The target Actor authority drifted from the exact ScenarioVersion",
                )
        else:
            target = definition.world.node(target_node_key)
            if target is None or action.required_interaction_key not in target.interaction_keys:
                raise GenericGameError(
                    "ACTION_TARGET_INVALID",
                    "The target does not support the Action's required Interaction",
                )
        self._require_command_reachability(actor, action, target_actor)
        self._require_authority(actor, action, parameters, approval_granted)
        self._validate_locality(
            definition,
            action,
            actor.current_node_key,
            target_node_key,
            parameters,
            target_actor_current_node_key=(
                target_actor.current_node_key if target_actor is not None else None
            ),
        )
        state = self._locked_state(definition)
        if action.target_kind == ActionTargetKind.NODE:
            target_state = state.nodes[target_node_key]
            if (
                target_state.visibility != Visibility.KNOWN
                or target_state.access != AccessState.AVAILABLE
            ):
                raise GenericGameError(
                    "ACTION_TARGET_UNAVAILABLE",
                    "The target is not known and accessible in this Instance",
                    retryable=True,
                )
        context = ActionRuleContext(
            action_key=action_key,
            target_node_key=target_node_key,
            parameters=parameters,
            actor_key=actor_key,
            target_actor_key=(target_actor.actor_key if target_actor is not None else None),
            operation_status=operation_status,
            actor_current_node_key=actor.current_node_key,
        )
        engine = DeclarativeRuleEngine(definition)
        preflight = engine.evaluate_preflight(state, context)
        if preflight is not None:
            return AppliedRuleResult(
                outcome=preflight,
                runtime_revision=self._instance().runtime_revision,
            )
        try:
            outcome = engine.evaluate_resolution(state, context)
        except RuleEngineError as exc:
            if (
                action.behavior != ActionBehavior.SURVEY_RESOURCES
                or exc.code != "RULE_RESOLUTION_NOT_FOUND"
            ):
                raise GenericGameError(exc.code, exc.message) from exc
            outcome = GenericRuleOutcome(
                selected_rule_key=f"generic:{action.key}",
                outcome_code=next(
                    (item.code for item in action.expected_outcomes if item.success),
                    None,
                ),
            )
        outcome = self._apply_behavior(
            definition,
            action,
            actor,
            target_node_key,
            state,
            outcome,
            parameters,
            target_actor=target_actor,
        )
        newly_known_facts = {
            (item.node_key, item.fact_key)
            for item in outcome.fact_visibility_updates
            if item.visibility == Visibility.KNOWN
            and state.facts[(item.node_key, item.fact_key)].visibility != Visibility.KNOWN
        }
        newly_known_nodes = {
            item.node_key
            for item in outcome.node_visibility_updates
            if item.visibility == Visibility.KNOWN
            and state.nodes[item.node_key].visibility != Visibility.KNOWN
        }
        newly_known_pools = tuple(
            pool
            for pool in state.resource_pools.values()
            if pool.visibility == ResourcePoolVisibility.HIDDEN
            and any(
                update.pool_key == pool.pool_key
                and update.visibility == ResourcePoolVisibility.VISIBLE
                for update in outcome.resource_pool_visibility_updates
            )
        )
        self._apply(definition, actor_key, outcome)
        instance = self._instance()
        instance.runtime_revision += 1
        self.db.flush()
        knowledge_changes: list[PlayerKnowledgeChange] = []
        for node_key in sorted(newly_known_nodes):
            node = definition.world.node(node_key)
            assert node is not None
            knowledge_changes.append(
                PlayerKnowledgeChange(
                    kind="NODE_REVEALED",
                    key=node_key,
                    name=node.name,
                )
            )
        for node_key, fact_key in sorted(newly_known_facts):
            node = definition.world.node(node_key)
            assert node is not None
            fact = next(item for item in node.facts if item.key == fact_key)
            row = self._fact_row(node_key, fact_key)
            knowledge_changes.append(
                PlayerKnowledgeChange(
                    kind="FACT_REVEALED",
                    key=f"{node_key}.{fact_key}",
                    name=fact.name,
                    value=row.truth_value,
                )
            )
        resource_names = {item.key: item.name for item in definition.world.resources}
        for pool in sorted(
            newly_known_pools,
            key=lambda item: (item.region_key or "", item.resource_key, item.pool_key),
        ):
            knowledge_changes.append(
                PlayerKnowledgeChange(
                    kind="RESOURCE_DISCOVERED",
                    key=resource_state_key(pool.resource_key, pool.region_key, pool.pool_key),
                    name=resource_names.get(pool.resource_key, pool.resource_key),
                    value=pool.quantity,
                )
            )
        return AppliedRuleResult(
            outcome=outcome,
            runtime_revision=instance.runtime_revision,
            knowledge_changes=tuple(knowledge_changes),
        )

    def preflight(
        self,
        *,
        actor_key: str,
        action_key: str,
        target_node_key: str,
        parameters: dict[str, StrictScalar],
        approval_granted: bool = False,
    ) -> GenericRuleOutcome | None:
        definition = self._definition()
        actor = self._actor(actor_key)
        if not actor_binding_matches(definition, actor):
            raise GenericGameError(
                "RUNTIME_ACTOR_BINDING_INVALID",
                "Runtime Actor authority drifted from the exact ScenarioVersion",
            )
        action = next((item for item in definition.actions if item.key == action_key), None)
        if action is None:
            raise GenericGameError(
                "ACTION_NOT_AUTHORIZED",
                "The exact Version Actor is not authorized for this Action",
            )
        try:
            parameters = normalize_action_parameters(action, parameters)
        except ValueError as exc:
            raise GenericGameError("ACTION_PARAMETERS_INVALID", str(exc)) from exc
        target_actor = None
        if action.target_kind == ActionTargetKind.ACTOR:
            target_actor = self._actor(target_node_key)
            if not actor_binding_matches(definition, target_actor):
                raise GenericGameError(
                    "RUNTIME_ACTOR_BINDING_INVALID",
                    "The target Actor authority drifted from the exact ScenarioVersion",
                )
        else:
            target = definition.world.node(target_node_key)
            if target is None or action.required_interaction_key not in target.interaction_keys:
                raise GenericGameError(
                    "ACTION_TARGET_INVALID",
                    "The target does not support the Action's required Interaction",
                )
        self._require_command_reachability(actor, action, target_actor)
        self._require_authority(actor, action, parameters, approval_granted)
        self._validate_locality(
            definition,
            action,
            actor.current_node_key,
            target_node_key,
            parameters,
            target_actor_current_node_key=(
                target_actor.current_node_key if target_actor is not None else None
            ),
        )
        state = self._locked_state(definition, lock=False)
        if action.target_kind == ActionTargetKind.NODE:
            target_state = state.nodes[target_node_key]
            if (
                target_state.visibility != Visibility.KNOWN
                or target_state.access != AccessState.AVAILABLE
            ):
                raise GenericGameError(
                    "ACTION_TARGET_UNAVAILABLE",
                    "The target is not known and accessible in this Instance",
                    retryable=True,
                )
        return DeclarativeRuleEngine(definition).evaluate_preflight(
            state,
            ActionRuleContext(
                action_key=action_key,
                target_node_key=target_node_key,
                parameters=parameters,
                actor_key=actor_key,
                target_actor_key=(target_actor.actor_key if target_actor is not None else None),
                actor_current_node_key=actor.current_node_key,
            ),
        )

    def state(self) -> DeclarativeRuleState:
        return self._locked_state(self._definition(), lock=False)

    def _definition(self) -> ScenarioDefinitionV2:
        persisted_scope = GameInstanceService(self.db).load(self.scope.game_instance_id)
        self.scope.assert_compatible(persisted_scope)
        snapshot = ScenarioVersionRepository(self.db).load(self.scope.scenario_version_id)
        if not isinstance(snapshot.definition, ScenarioDefinitionV2):
            raise GenericGameError(
                "GENERIC_RUNTIME_SCHEMA_REQUIRED",
                "GenericGameService requires an exact ScenarioDefinition v2 snapshot",
            )
        return snapshot.definition

    def _instance(self) -> GameInstance:
        instance = self.db.get(GameInstance, self.scope.game_instance_id)
        if instance is None:
            raise GenericGameError("GAME_INSTANCE_NOT_FOUND", "The GameInstance does not exist")
        return instance

    def _actor(self, actor_key: str) -> GameInstanceActor:
        actor = self.db.get(GameInstanceActor, (self.scope.game_instance_id, actor_key))
        if actor is None or actor.status != "ACTIVE":
            raise GenericGameError(
                "RUNTIME_ACTOR_NOT_FOUND", "The active Actor does not belong to this Instance"
            )
        return actor

    @staticmethod
    def _validate_locality(
        definition: ScenarioDefinitionV2,
        action: object,
        actor_current_node_key: str,
        target_node_key: str,
        parameters: dict[str, StrictScalar],
        target_actor_current_node_key: str | None = None,
    ) -> None:
        from app.domain.scenario_v2 import ActionDefinitionV2

        assert isinstance(action, ActionDefinitionV2)
        try:
            validate_action_locality(
                definition,
                action,
                actor_current_node_key=actor_current_node_key,
                target_node_key=target_node_key,
                parameters=parameters,
                target_actor_node_key=target_actor_current_node_key,
            )
        except LocalityEngineError as exc:
            raise GenericGameError(exc.code, exc.message, retryable=exc.retryable) from exc

    @staticmethod
    def _require_command_reachability(
        actor: GameInstanceActor,
        action: object,
        target_actor: GameInstanceActor | None,
    ) -> None:
        from app.domain.scenario_v2 import ActionDefinitionV2

        assert isinstance(action, ActionDefinitionV2)
        try:
            reachability = CommandReachability(actor.command_reachability)
        except ValueError as exc:
            raise GenericGameError(
                "RUNTIME_ACTOR_REACHABILITY_INVALID",
                "The Actor command reachability value is invalid",
            ) from exc
        if reachability != CommandReachability.ONLINE:
            raise GenericGameError(
                "ACTOR_COMMAND_DISCONNECTED",
                "A disconnected Actor cannot receive an ordinary Action",
                retryable=True,
            )
        if action.behavior == ActionBehavior.RELAY_MESSAGE:
            if target_actor is None:
                raise GenericGameError(
                    "RELAY_TARGET_INVALID",
                    "Relay requires an active target Actor",
                )
            try:
                target_reachability = CommandReachability(target_actor.command_reachability)
            except ValueError as exc:
                raise GenericGameError(
                    "RUNTIME_ACTOR_REACHABILITY_INVALID",
                    "The target Actor command reachability value is invalid",
                ) from exc
            if target_reachability != CommandReachability.DISCONNECTED:
                raise GenericGameError(
                    "RELAY_TARGET_NOT_DISCONNECTED",
                    "Relay requires a disconnected target Actor",
                    retryable=True,
                )

    def _apply_behavior(
        self,
        definition: ScenarioDefinitionV2,
        action: object,
        actor: GameInstanceActor,
        target_node_key: str,
        state: DeclarativeRuleState,
        outcome: GenericRuleOutcome,
        parameters: dict[str, StrictScalar],
        target_actor: GameInstanceActor | None = None,
    ) -> GenericRuleOutcome:
        from app.domain.scenario_v2 import ActionDefinitionV2

        assert isinstance(action, ActionDefinitionV2)
        if action.behavior == ActionBehavior.RELAY_MESSAGE:
            if target_actor is None:
                raise GenericGameError(
                    "RELAY_TARGET_INVALID",
                    "Relay requires an active target Actor",
                )
            updates = [
                item
                for item in outcome.actor_command_reachability_updates
                if item.actor_key != target_actor.actor_key
            ]
            updates.append(
                ActorCommandReachabilityMutation(
                    target_actor.actor_key,
                    CommandReachability.ONLINE,
                )
            )
            return replace(outcome, actor_command_reachability_updates=tuple(updates))
        if action.behavior == ActionBehavior.TRAVEL:
            connector = self._connector(definition, actor.current_node_key, target_node_key)
            if not self._is_passable(definition, connector, state):
                return self._blocked_outcome(
                    outcome,
                    code="TRAVEL_BLOCKED",
                    message="The one-hop Transport is currently blocked",
                    reveal=self._passability_reveal(definition, connector, state),
                )
            return replace(outcome, actor_location_update=target_node_key)
        if action.behavior == ActionBehavior.INSPECT:
            return replace(
                outcome,
                fact_visibility_updates=outcome.fact_visibility_updates
                + self._inspect_reveals(target_node_key, definition, state),
            )
        if action.behavior == ActionBehavior.SURVEY_RESOURCES:
            region = target_node_key
            knowledge = state.region_resource_knowledge.get(region)
            if knowledge is None:
                raise GenericGameError(
                    "RESOURCE_REGION_KNOWLEDGE_MISSING",
                    "The target Region resource knowledge state is missing",
                )
            if knowledge.resource_survey_completed:
                return self._blocked_outcome(
                    outcome,
                    code="RESOURCE_SURVEY_ALREADY_COMPLETED",
                    message="The target Region has already completed a resource survey",
                    retryable=False,
                )
            reveal_pools = tuple(
                ResourcePoolVisibilityMutation(
                    pool_key=pool.pool_key,
                    visibility=ResourcePoolVisibility.VISIBLE,
                )
                for pool in state.resource_pools.values()
                if (
                    pool.region_key == region
                    and pool.facility_key is not None
                    and pool.visibility == ResourcePoolVisibility.HIDDEN
                    and pool.survey_discoverable
                )
            )
            return replace(
                outcome,
                region_resource_visibility_updates=(
                    *outcome.region_resource_visibility_updates,
                    RegionResourceVisibilityMutation(
                        region_key=region,
                        visibility=ResourceInventoryVisibility.VISIBLE,
                    ),
                ),
                region_resource_survey_updates=(
                    *outcome.region_resource_survey_updates,
                    RegionResourceSurveyMutation(region_key=region, completed=True),
                ),
                resource_pool_visibility_updates=(
                    *outcome.resource_pool_visibility_updates,
                    *reveal_pools,
                ),
            )
        if action.behavior == ActionBehavior.TRANSPORT_RESOURCE:
            connector = self._connector(definition, actor.current_node_key, target_node_key)
            if not self._is_passable(definition, connector, state):
                return self._blocked_outcome(
                    outcome,
                    code="TRANSPORT_BLOCKED",
                    message="The one-hop Transport is currently blocked",
                    reveal=self._passability_reveal(definition, connector, state),
                )
            resource_key = parameters.get("resource_key")
            amount = parameters.get("amount")
            if not isinstance(resource_key, str) or not isinstance(amount, int):
                raise GenericGameError(
                    "TRANSPORT_PARAMETERS_INVALID",
                    "Transport requires a Resource key and integer amount",
                )
            source_region = region_for_node(definition, actor.current_node_key)
            target_region = region_for_node(definition, target_node_key)
            source_pools = self._known_source_pools(state, source_region, resource_key)
            if not source_pools:
                return self._blocked_outcome(
                    outcome,
                    code="TRANSPORT_RESOURCE_KNOWLEDGE_UNKNOWN",
                    message="The source Region Resource inventory is not known",
                    retryable=True,
                )
            available = sum(pool.quantity for pool in source_pools)
            if available < amount:
                return self._blocked_outcome(
                    outcome,
                    code="TRANSPORT_RESOURCE_INSUFFICIENT",
                    message="The source Region lacks the requested Resource amount",
                    retryable=True,
                )
            remaining = amount
            mutations: list[ResourceMutation] = []
            for pool in source_pools:
                if remaining <= 0:
                    break
                consumed = min(pool.quantity, remaining)
                mutations.append(
                    ResourceMutation(
                        resource_key,
                        -consumed,
                        source_region,
                        pool.pool_key,
                    )
                )
                remaining -= consumed
            mutations.append(
                ResourceMutation(
                    resource_key,
                    amount,
                    target_region,
                    "default",
                )
            )
            return replace(
                outcome,
                resource_mutations=(*outcome.resource_mutations, *mutations),
                actor_location_update=target_region,
            )
        return outcome

    @staticmethod
    def _known_source_pools(
        state: DeclarativeRuleState,
        source_region: str,
        resource_key: str,
    ) -> tuple[RuleResourcePoolState, ...]:
        knowledge = state.region_resource_knowledge.get(source_region)
        return tuple(
            sorted(
                (
                    pool
                    for pool in state.resource_pools.values()
                    if (
                        pool.region_key == source_region
                        and pool.resource_key == resource_key
                        and pool.visibility == ResourcePoolVisibility.VISIBLE
                        and pool.availability == ResourcePoolAvailability.AVAILABLE
                        and (
                            pool.facility_key is not None
                            or (
                                knowledge is not None
                                and knowledge.resource_inventory_visibility
                                == ResourceInventoryVisibility.VISIBLE
                            )
                        )
                    )
                ),
                key=lambda item: item.pool_key,
            )
        )

    @staticmethod
    def _blocked_outcome(
        outcome: GenericRuleOutcome,
        *,
        code: str,
        message: str,
        retryable: bool = True,
        reveal: tuple[FactVisibilityMutation, ...] = (),
    ) -> GenericRuleOutcome:
        return GenericRuleOutcome(
            selected_rule_key=outcome.selected_rule_key,
            outcome_code=None,
            failure=RuleFailure(code=code, message=message, retryable=retryable),
            fact_visibility_updates=reveal,
        )

    @staticmethod
    def _connector(
        definition: ScenarioDefinitionV2,
        actor_current_node_key: str,
        target_node_key: str,
    ) -> str:
        try:
            return transport_between(
                definition,
                region_for_node(definition, actor_current_node_key),
                target_node_key,
            )
        except LocalityEngineError as exc:
            raise GenericGameError(exc.code, exc.message, retryable=exc.retryable) from exc

    @staticmethod
    def _is_passable(
        definition: ScenarioDefinitionV2,
        transport_key: str,
        state: DeclarativeRuleState,
    ) -> bool:
        try:
            value = passability_fact(definition, transport_key, state)
        except LocalityEngineError as exc:
            raise GenericGameError(exc.code, exc.message, retryable=exc.retryable) from exc
        return value is None or value[1]

    @staticmethod
    def _passability_reveal(
        definition: ScenarioDefinitionV2,
        transport_key: str,
        state: DeclarativeRuleState,
    ) -> tuple[FactVisibilityMutation, ...]:
        fact_key = definition.metadata.locality.passability_fact_key
        if fact_key is None:
            return ()
        fact = state.facts.get((transport_key, fact_key))
        if fact is None or fact.visibility.value == "KNOWN":
            return ()
        return (FactVisibilityMutation(transport_key, fact_key, Visibility.KNOWN),)

    @staticmethod
    def _inspect_reveals(
        target_node_key: str,
        definition: ScenarioDefinitionV2,
        state: DeclarativeRuleState,
    ) -> tuple[FactVisibilityMutation, ...]:
        node = definition.world.node(target_node_key)
        if node is None:
            return ()
        return tuple(
            FactVisibilityMutation(target_node_key, fact.key, Visibility.KNOWN)
            for fact in node.facts
            if state.facts[(target_node_key, fact.key)].visibility != Visibility.KNOWN
        )

    @staticmethod
    def _require_authority(
        actor: GameInstanceActor,
        action: object,
        parameters: dict[str, StrictScalar],
        approval_granted: bool,
    ) -> None:
        from app.domain.scenario_v2 import ActionDefinitionV2

        assert isinstance(action, ActionDefinitionV2)
        decision = evaluate_authority(actor, action, parameters)
        if decision.outcome == AuthorityOutcome.DENY:
            raise GenericGameError(decision.reason_code, "Actor authority denied the Action")
        if decision.outcome == AuthorityOutcome.REQUIRE_PLAYER_DECISION and not approval_granted:
            raise GenericGameError(
                decision.reason_code, "The Action requires an approved Instance decision"
            )

    def _locked_state(
        self,
        definition: ScenarioDefinitionV2,
        *,
        lock: bool = True,
    ) -> DeclarativeRuleState:
        node_query = select(GameInstanceNodeState).where(
            GameInstanceNodeState.game_instance_id == self.scope.game_instance_id
        )
        fact_query = select(GameInstanceFactState).where(
            GameInstanceFactState.game_instance_id == self.scope.game_instance_id
        )
        resource_query = select(GameInstanceResourceState).where(
            GameInstanceResourceState.game_instance_id == self.scope.game_instance_id
        )
        region_knowledge_query = select(GameInstanceRegionResourceKnowledge).where(
            GameInstanceRegionResourceKnowledge.game_instance_id == self.scope.game_instance_id
        )
        actor_query = select(GameInstanceActor).where(
            GameInstanceActor.game_instance_id == self.scope.game_instance_id
        )
        if lock:
            node_query = node_query.with_for_update()
            fact_query = fact_query.with_for_update()
            resource_query = resource_query.with_for_update()
            region_knowledge_query = region_knowledge_query.with_for_update()
            actor_query = actor_query.with_for_update()
        nodes = self.db.scalars(node_query).all()
        facts = self.db.scalars(fact_query).all()
        resources = self.db.scalars(resource_query).all()
        region_knowledge = self.db.scalars(region_knowledge_query).all()
        actors = self.db.scalars(actor_query).all()
        expected_initial_resources = {
            (item.resource_key, item.region_key, item.pool_key)
            for item in resource_pool_initial_states(definition)
        }
        actual_resources = {
            (row.resource_key, row.scope_node_key, row.pool_key) for row in resources
        }
        expected_regions = {
            node.key
            for node in definition.world.nodes
            if definition.metadata.locality.enabled
            and node.node_type_key == definition.metadata.locality.region_node_type_key
        }
        if (
            {row.node_key for row in nodes} != {node.key for node in definition.world.nodes}
            or {(row.node_key, row.fact_key) for row in facts}
            != {(node.key, fact.key) for node in definition.world.nodes for fact in node.facts}
            or not expected_initial_resources.issubset(actual_resources)
            or any(
                not valid_resource_state_identity(
                    definition,
                    row.resource_key,
                    row.scope_node_key,
                    row.pool_key,
                )
                for row in resources
            )
            or {row.region_key for row in region_knowledge} != expected_regions
        ):
            raise GenericGameError(
                "RUNTIME_STATE_INCOMPLETE",
                "Instance state does not match its exact ScenarioVersion",
            )
        return DeclarativeRuleState(
            nodes={
                row.node_key: RuleNodeState(
                    visibility=row.visibility,
                    access=(
                        AccessState.LOCKED
                        if row.status == NodeStatus.LOCKED
                        else AccessState.AVAILABLE
                    ),
                )
                for row in nodes
            },
            facts={
                (row.node_key, row.fact_key): RuleFactState(
                    value=row.truth_value,
                    visibility=row.visibility,
                )
                for row in facts
            },
            resources={
                resource_state_key(row.resource_key, row.scope_node_key, row.pool_key): row.value
                for row in resources
                if (
                    ResourcePoolVisibility(row.visibility) == ResourcePoolVisibility.VISIBLE
                    and ResourcePoolAvailability(row.availability)
                    == ResourcePoolAvailability.AVAILABLE
                )
            },
            resource_reservations={
                resource_state_key(
                    row.resource_key, row.scope_node_key, row.pool_key
                ): row.reserved_value
                for row in resources
            },
            resource_pools={
                resource_state_key(
                    row.resource_key, row.scope_node_key, row.pool_key
                ): RuleResourcePoolState(
                    pool_key=row.pool_key,
                    resource_key=row.resource_key,
                    region_key=row.scope_node_key,
                    facility_key=row.facility_key,
                    quantity=row.value,
                    visibility=ResourcePoolVisibility(row.visibility),
                    availability=ResourcePoolAvailability(row.availability),
                    survey_discoverable=row.survey_discoverable,
                    availability_requirement=row.availability_requirement,
                )
                for row in resources
            },
            region_resource_knowledge={
                row.region_key: RuleRegionResourceKnowledgeState(
                    resource_inventory_visibility=ResourceInventoryVisibility(
                        row.resource_inventory_visibility
                    ),
                    resource_survey_completed=row.resource_survey_completed,
                )
                for row in region_knowledge
            },
            actors={
                row.actor_key: RuleActorState(
                    command_reachability=CommandReachability(row.command_reachability),
                    current_node_key=row.current_node_key,
                )
                for row in actors
            },
        )

    def _apply(
        self,
        definition: ScenarioDefinitionV2,
        actor_key: str,
        outcome: GenericRuleOutcome,
    ) -> None:
        resources = {item.key: item for item in definition.world.resources}
        resource_mutations = self._expand_resource_mutations(outcome.resource_mutations)
        resource_rows: dict[str, GameInstanceResourceState] = {}
        balance_mutations_by_identity: dict[str, list[ResourceMutation]] = {}
        for mutation in resource_mutations:
            balance_mutations_by_identity.setdefault(
                resource_state_key(
                    mutation.resource_key,
                    mutation.scope_node_key,
                    mutation.pool_key,
                ),
                [],
            ).append(mutation)
        reservation_identities = {
            resource_state_key(item.resource_key, item.scope_node_key, item.pool_key)
            for item in outcome.resource_reservations
        }
        resource_keys = set(balance_mutations_by_identity) | reservation_identities
        for identity in resource_keys:
            resource_row = self.db.get(
                GameInstanceResourceState,
                (self.scope.game_instance_id, identity),
            )
            if resource_row is not None:
                resource_rows[identity] = resource_row
                continue
            mutations = balance_mutations_by_identity.get(identity, [])
            candidate_mutation = mutations[0] if mutations else None
            if (
                candidate_mutation is None
                or not any(item.amount > 0 for item in mutations)
                or not valid_resource_state_identity(
                    definition,
                    candidate_mutation.resource_key,
                    candidate_mutation.scope_node_key,
                    candidate_mutation.pool_key,
                )
            ):
                raise GenericGameError(
                    "RUNTIME_RESOURCE_MISSING", "A Rule referenced missing Instance state"
                )
            resource_row = GameInstanceResourceState(
                game_instance_id=self.scope.game_instance_id,
                resource_identity=resource_identity(
                    candidate_mutation.resource_key,
                    candidate_mutation.scope_node_key,
                    candidate_mutation.pool_key,
                ),
                resource_key=candidate_mutation.resource_key,
                scope_node_key=candidate_mutation.scope_node_key,
                pool_key=candidate_mutation.pool_key,
                visibility=ResourcePoolVisibility.VISIBLE,
                availability=ResourcePoolAvailability.AVAILABLE,
                survey_discoverable=False,
                value=0,
                reserved_value=0,
                version=1,
            )
            self.db.add(resource_row)
            resource_rows[identity] = resource_row
        projected_values = {key: row.value for key, row in resource_rows.items()}
        projected_reserved = {key: row.reserved_value for key, row in resource_rows.items()}
        for balance_mutation in resource_mutations:
            identity = resource_state_key(
                balance_mutation.resource_key,
                balance_mutation.scope_node_key,
                balance_mutation.pool_key,
            )
            projected_values[identity] += balance_mutation.amount
        for reserve_mutation in outcome.resource_reservations:
            identity = resource_state_key(
                reserve_mutation.resource_key,
                reserve_mutation.scope_node_key,
                reserve_mutation.pool_key,
            )
            projected_reserved[identity] += reserve_mutation.amount
        for key, value in projected_values.items():
            resource = resources[resource_rows[key].resource_key]
            reserved = projected_reserved[key]
            if (
                value < resource.minimum
                or (resource.maximum is not None and value > resource.maximum)
                or reserved < 0
                or reserved > value
            ):
                raise GenericGameError(
                    "RULE_OUTCOME_RESOURCE_INVALID",
                    "The complete Rule outcome would violate Resource bounds",
                )
        for fact_mutation in outcome.fact_updates:
            fact_row = self._fact_row(fact_mutation.node_key, fact_mutation.fact_key)
            fact_row.truth_value = fact_mutation.value
            fact_row.version += 1
        for fact_visibility_mutation in outcome.fact_visibility_updates:
            visibility_fact_row = self._fact_row(
                fact_visibility_mutation.node_key,
                fact_visibility_mutation.fact_key,
            )
            visibility_fact_row.visibility = fact_visibility_mutation.visibility
            visibility_fact_row.version += 1
        for node_visibility_mutation in outcome.node_visibility_updates:
            visibility_node_row = self._node_row(node_visibility_mutation.node_key)
            visibility_node_row.visibility = node_visibility_mutation.visibility
            visibility_node_row.version += 1
        for access_mutation in outcome.node_access_updates:
            access_node_row = self._node_row(access_mutation.node_key)
            access_node_row.status = (
                NodeStatus.LOCKED
                if access_mutation.access == AccessState.LOCKED
                else NodeStatus.AVAILABLE
            )
            access_node_row.version += 1
        for key, persisted_resource in resource_rows.items():
            persisted_resource.value = projected_values[key]
            persisted_resource.reserved_value = projected_reserved[key]
            persisted_resource.version += 1
        for visibility_mutation in outcome.region_resource_visibility_updates:
            knowledge_row = self.db.get(
                GameInstanceRegionResourceKnowledge,
                (self.scope.game_instance_id, visibility_mutation.region_key),
            )
            if knowledge_row is None:
                raise GenericGameError(
                    "RESOURCE_REGION_KNOWLEDGE_MISSING",
                    "A Rule referenced missing Region Resource Knowledge",
                )
            knowledge_row.resource_inventory_visibility = visibility_mutation.visibility
            knowledge_row.version += 1
        for survey_mutation in outcome.region_resource_survey_updates:
            knowledge_row = self.db.get(
                GameInstanceRegionResourceKnowledge,
                (self.scope.game_instance_id, survey_mutation.region_key),
            )
            if knowledge_row is None:
                raise GenericGameError(
                    "RESOURCE_REGION_KNOWLEDGE_MISSING",
                    "A Rule referenced missing Region Resource Knowledge",
                )
            knowledge_row.resource_survey_completed = survey_mutation.completed
            knowledge_row.version += 1
        pool_rows = self.db.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == self.scope.game_instance_id
            )
        ).all()
        for visibility_pool_mutation in outcome.resource_pool_visibility_updates:
            matching = [
                row for row in pool_rows if row.pool_key == visibility_pool_mutation.pool_key
            ]
            if not matching:
                raise GenericGameError(
                    "RUNTIME_RESOURCE_POOL_MISSING",
                    "A Rule referenced missing Resource Pool state",
                )
            for pool_row in matching:
                pool_row.visibility = visibility_pool_mutation.visibility
                pool_row.version += 1
        for availability_pool_mutation in outcome.resource_pool_availability_updates:
            matching = [
                row for row in pool_rows if row.pool_key == availability_pool_mutation.pool_key
            ]
            if not matching:
                raise GenericGameError(
                    "RUNTIME_RESOURCE_POOL_MISSING",
                    "A Rule referenced missing Resource Pool state",
                )
            for pool_row in matching:
                pool_row.availability = availability_pool_mutation.availability
                pool_row.version += 1
        if outcome.actor_location_update is not None:
            actor = self._actor(actor_key)
            actor.current_node_key = outcome.actor_location_update
            actor.version += 1
        for reachability_mutation in outcome.actor_command_reachability_updates:
            target_actor = self._actor(reachability_mutation.actor_key)
            target_actor.command_reachability = reachability_mutation.command_reachability.value
            target_actor.version += 1
        for event in outcome.memory_events:
            self.db.add(
                GameInstanceMemoryEvent(
                    game_instance_id=self.scope.game_instance_id,
                    actor_key=actor_key,
                    event_key=event.key,
                    content=event.content,
                    source_rule_key=outcome.selected_rule_key,
                )
            )

    def _expand_resource_mutations(
        self,
        mutations: tuple[ResourceMutation, ...],
    ) -> tuple[ResourceMutation, ...]:
        expanded: list[ResourceMutation] = []
        for mutation in mutations:
            if mutation.amount >= 0 or mutation.pool_key != "default":
                expanded.append(mutation)
                continue
            remaining = -mutation.amount
            rows = sorted(
                self.db.scalars(
                    select(GameInstanceResourceState).where(
                        GameInstanceResourceState.game_instance_id == self.scope.game_instance_id,
                        GameInstanceResourceState.resource_key == mutation.resource_key,
                        GameInstanceResourceState.scope_node_key == mutation.scope_node_key,
                        GameInstanceResourceState.visibility == ResourcePoolVisibility.VISIBLE,
                        GameInstanceResourceState.availability
                        == ResourcePoolAvailability.AVAILABLE,
                    )
                ).all(),
                key=lambda row: row.pool_key,
            )
            knowledge = self.db.get(
                GameInstanceRegionResourceKnowledge,
                (self.scope.game_instance_id, mutation.scope_node_key),
            )
            rows = [
                row
                for row in rows
                if mutation.scope_node_key is None
                or row.facility_key is not None
                or (
                    knowledge is not None
                    and ResourceInventoryVisibility(knowledge.resource_inventory_visibility)
                    == ResourceInventoryVisibility.VISIBLE
                )
            ]
            if not rows:
                raise GenericGameError(
                    "RESOURCE_INVENTORY_UNKNOWN",
                    "The Resource inventory is not known or available",
                    retryable=True,
                )
            available = sum(row.value for row in rows)
            if available < remaining:
                raise GenericGameError(
                    "RULE_OUTCOME_RESOURCE_INVALID",
                    "The complete Resource outcome would violate Resource bounds",
                )
            for row in rows:
                if remaining <= 0:
                    break
                consumed = min(row.value, remaining)
                expanded.append(
                    ResourceMutation(
                        mutation.resource_key,
                        -consumed,
                        mutation.scope_node_key,
                        row.pool_key,
                    )
                )
                remaining -= consumed
        return tuple(expanded)

    def _fact_row(self, node_key: str, fact_key: str) -> GameInstanceFactState:
        row = self.db.get(
            GameInstanceFactState,
            (self.scope.game_instance_id, node_key, fact_key),
        )
        if row is None:
            raise GenericGameError("RUNTIME_FACT_MISSING", "Rule Fact state is missing")
        return row

    def _node_row(self, node_key: str) -> GameInstanceNodeState:
        row = self.db.get(GameInstanceNodeState, (self.scope.game_instance_id, node_key))
        if row is None:
            raise GenericGameError("RUNTIME_NODE_MISSING", "Rule Node state is missing")
        return row


__all__ = [
    "AppliedRuleResult",
    "GenericGameError",
    "GenericGameService",
    "PlayerKnowledgeChange",
]
