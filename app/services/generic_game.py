"""Instance-scoped generic state service and declarative outcome applicator."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import actor_binding_matches, evaluate_authority
from app.domain.enums import AuthorityOutcome, NodeStatus
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import ScenarioDefinitionV2, StrictScalar
from app.domain.world import AccessState, Visibility
from app.engine.rules import (
    ActionRuleContext,
    DeclarativeRuleEngine,
    DeclarativeRuleState,
    GenericRuleOutcome,
    RuleFactState,
    RuleNodeState,
)
from app.infrastructure.db.models import (
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceMemoryEvent,
    GameInstanceNodeState,
    GameInstanceResourceState,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import require_scope_writable


class GenericGameError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class AppliedRuleResult:
    outcome: GenericRuleOutcome
    runtime_revision: int


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
        self._require_authority(actor, action, parameters, approval_granted)
        target = definition.world.node(target_node_key)
        if target is None or action.required_interaction_key not in target.interaction_keys:
            raise GenericGameError(
                "ACTION_TARGET_INVALID",
                "The target does not support the Action's required Interaction",
            )
        state = self._locked_state(definition)
        target_state = state.nodes[target_node_key]
        if (
            target_state.visibility != Visibility.KNOWN
            or target_state.access != AccessState.AVAILABLE
        ):
            raise GenericGameError(
                "ACTION_TARGET_UNAVAILABLE",
                "The target is not known and accessible in this Instance",
            )
        context = ActionRuleContext(
            action_key=action_key,
            target_node_key=target_node_key,
            parameters=parameters,
            actor_key=actor_key,
            operation_status=operation_status,
        )
        engine = DeclarativeRuleEngine(definition)
        preflight = engine.evaluate_preflight(state, context)
        if preflight is not None:
            return AppliedRuleResult(
                outcome=preflight,
                runtime_revision=self._instance().runtime_revision,
            )
        outcome = engine.evaluate_resolution(state, context)
        self._apply(definition, actor_key, outcome)
        instance = self._instance()
        instance.runtime_revision += 1
        self.db.flush()
        return AppliedRuleResult(outcome=outcome, runtime_revision=instance.runtime_revision)

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
        self._require_authority(actor, action, parameters, approval_granted)
        target = definition.world.node(target_node_key)
        if target is None or action.required_interaction_key not in target.interaction_keys:
            raise GenericGameError(
                "ACTION_TARGET_INVALID",
                "The target does not support the Action's required Interaction",
            )
        state = self._locked_state(definition, lock=False)
        target_state = state.nodes[target_node_key]
        if (
            target_state.visibility != Visibility.KNOWN
            or target_state.access != AccessState.AVAILABLE
        ):
            raise GenericGameError(
                "ACTION_TARGET_UNAVAILABLE",
                "The target is not known and accessible in this Instance",
            )
        return DeclarativeRuleEngine(definition).evaluate_preflight(
            state,
            ActionRuleContext(
                action_key=action_key,
                target_node_key=target_node_key,
                parameters=parameters,
                actor_key=actor_key,
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
        if lock:
            node_query = node_query.with_for_update()
            fact_query = fact_query.with_for_update()
            resource_query = resource_query.with_for_update()
        nodes = self.db.scalars(node_query).all()
        facts = self.db.scalars(fact_query).all()
        resources = self.db.scalars(resource_query).all()
        if (
            {row.node_key for row in nodes} != {node.key for node in definition.world.nodes}
            or {(row.node_key, row.fact_key) for row in facts}
            != {(node.key, fact.key) for node in definition.world.nodes for fact in node.facts}
            or {row.resource_key for row in resources}
            != {resource.key for resource in definition.world.resources}
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
            resources={row.resource_key: row.value for row in resources},
            resource_reservations={row.resource_key: row.reserved_value for row in resources},
        )

    def _apply(
        self,
        definition: ScenarioDefinitionV2,
        actor_key: str,
        outcome: GenericRuleOutcome,
    ) -> None:
        resources = {item.key: item for item in definition.world.resources}
        resource_rows: dict[str, GameInstanceResourceState] = {}
        resource_keys = {
            balance_item.resource_key for balance_item in outcome.resource_mutations
        } | {reserve_item.resource_key for reserve_item in outcome.resource_reservations}
        for resource_key in resource_keys:
            if resource_key not in resource_rows:
                resource_row = self.db.get(
                    GameInstanceResourceState,
                    (self.scope.game_instance_id, resource_key),
                )
                if resource_row is None:
                    raise GenericGameError(
                        "RUNTIME_RESOURCE_MISSING", "A Rule referenced missing Instance state"
                    )
                resource_rows[resource_key] = resource_row
        projected_values = {key: row.value for key, row in resource_rows.items()}
        projected_reserved = {key: row.reserved_value for key, row in resource_rows.items()}
        for balance_mutation in outcome.resource_mutations:
            projected_values[balance_mutation.resource_key] += balance_mutation.amount
        for reserve_mutation in outcome.resource_reservations:
            projected_reserved[reserve_mutation.resource_key] += reserve_mutation.amount
        for key, value in projected_values.items():
            resource = resources[key]
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


__all__ = ["AppliedRuleResult", "GenericGameError", "GenericGameService"]
