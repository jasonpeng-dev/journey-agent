from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.domain.enums import (
    NodeStatus,
    NPCRole,
    WorldOperationStatus,
)
from app.infrastructure.db.models import (
    NPC,
    OfficerAppointment,
    Player,
    PlayerDomainState,
    PlayerNodeState,
    PlayerWorldFact,
    WorldNode,
    WorldOperation,
)
from app.scenarios.starfire.compatibility import (
    canonical_node_key,
    initial_legacy_world_facts,
    initial_resource_values,
    legacy_fact_key,
    legacy_target_supports_interaction,
    project_legacy_supply_status,
)
from app.scenarios.starfire.definition import STARFIRE_WORLD
from app.scenarios.starfire.ruleset import (
    RuleOutcome,
    StarfireFactState,
    StarfireResources,
    StarfireRuleset,
    StarfireRuleState,
    StarfireRuleViolation,
)

SEED_NAMESPACE = UUID("3e16a11d-9cf5-4981-af7a-152c28331300")


class GameService:
    def __init__(self, db: Session):
        self.db = db
        self.ruleset = StarfireRuleset()

    def get_player(self, player_id: UUID, *, lock: bool = False) -> Player:
        query = select(Player).where(Player.id == player_id)
        if lock:
            query = query.with_for_update()
        player = self.db.scalar(query)
        if not player:
            raise NotFoundError("player", player_id)
        return player

    def create_player(self, name: str) -> Player:
        initial_resources = initial_resource_values()
        start = self.db.scalar(select(WorldNode).where(WorldNode.key == "capital_council"))
        if not start:
            raise AppError("WORLD_NOT_SEEDED", "Demo world has not been seeded", status_code=503)
        player = Player(
            name=name,
            gold=initial_resources["gold"],
            current_node_id=start.id,
        )
        self.db.add(player)
        self.db.flush()
        nodes = self.db.scalars(select(WorldNode)).all()
        for node in nodes:
            status = NodeStatus.ENTERED if node.id == start.id else node.default_status
            self.db.add(PlayerNodeState(player_id=player.id, node_id=node.id, status=status))
        for npc in self.db.scalars(select(NPC)).all():
            if npc.role in {NPCRole.STRATEGIST, NPCRole.GENERAL, NPCRole.STEWARD}:
                self.db.add(OfficerAppointment(player_id=player.id, npc_id=npc.id, status="ACTIVE"))
        self.db.add(
            PlayerDomainState(
                player_id=player.id,
                soldiers_total=initial_resources["soldiers"],
                soldiers_committed=0,
                food=initial_resources["food"],
                morale=initial_resources["morale"],
            )
        )
        for key, value in initial_legacy_world_facts().items():
            self.db.add(PlayerWorldFact(player_id=player.id, key=key, value=value))
        self.db.flush()
        return player

    def list_nodes(self, player_id: UUID) -> list[tuple[WorldNode, PlayerNodeState]]:
        """Return the player's strategic map nodes and their verified state."""
        self.get_player(player_id)
        return list(
            self.db.execute(
                select(WorldNode, PlayerNodeState)
                .join(PlayerNodeState, PlayerNodeState.node_id == WorldNode.id)
                .where(PlayerNodeState.player_id == player_id)
            ).tuples()
        )

    def unlock_node(self, player_id: UUID, node_key: str) -> PlayerNodeState:
        canonical_key = canonical_node_key(node_key)
        node = self.db.scalar(select(WorldNode).where(WorldNode.key == canonical_key))
        if not node:
            raise NotFoundError("node", node_key)
        state = self.db.get(PlayerNodeState, (player_id, node.id))
        if not state:
            raise NotFoundError("player_node", node.id)
        if state.status == NodeStatus.LOCKED:
            state.status = NodeStatus.AVAILABLE
            state.version += 1
            self.db.flush()
        return state

    def get_world_fact(self, player_id: UUID, key: str) -> dict[str, object]:
        self.get_player(player_id)
        fact = self.db.get(PlayerWorldFact, (player_id, key))
        return {} if fact is None else dict(fact.value)

    def set_world_fact(
        self, player_id: UUID, key: str, value: dict[str, object]
    ) -> PlayerWorldFact:
        self.get_player(player_id, lock=True)
        fact = self.db.get(PlayerWorldFact, (player_id, key))
        if fact is None:
            fact = PlayerWorldFact(player_id=player_id, key=key, value=value, version=1)
            self.db.add(fact)
        else:
            fact.value = value
            fact.version += 1
        self.db.flush()
        return fact

    def inspect_command_state(self, player_id: UUID) -> dict[str, object]:
        player = self.get_player(player_id)
        domain = self.db.get(PlayerDomainState, player_id)
        if domain is None:
            raise AppError(
                "DOMAIN_STATE_NOT_INITIALIZED",
                "The player's strategic domain state is missing",
            )
        resources = {
            "soldiers_total": domain.soldiers_total,
            "soldiers_available": domain.soldiers_total - domain.soldiers_committed,
            "soldiers_committed": domain.soldiers_committed,
            "food": domain.food,
            "gold": player.gold,
            "morale": domain.morale,
            "version": domain.version,
        }
        world = {
            "valley_intelligence": self.get_world_fact(player_id, "valley_intelligence").get(
                "status", "INCOMPLETE"
            ),
            "enemy_supply_route": self.get_world_fact(player_id, "enemy_supply_route").get(
                "status", "UNKNOWN"
            ),
            "valley_security": self.get_world_fact(player_id, "valley_security").get(
                "status", "UNSAFE"
            ),
            "village_support": self.get_world_fact(player_id, "village_support").get(
                "status", "NONE"
            ),
            "starfire_outpost_status": self.get_world_fact(
                player_id, "starfire_outpost_status"
            ).get("status", "DAMAGED"),
            "northern_trade_route_status": self.get_world_fact(
                player_id, "northern_trade_route_status"
            ).get("status", "CLOSED"),
        }
        return {
            "resources": resources,
            "world": world,
            **resources,
            **world,
        }

    def scenario_state(self, player_id: UUID) -> StarfireRuleState:
        """Return the canonical domain projection used by scenario policies."""

        return self._starfire_rule_state(player_id)

    def preflight_recon_operation(
        self,
        *,
        player_id: UUID,
        troop_count: int,
        target_key: str,
        approach: str,
    ) -> None:
        canonical_target = self._canonical_interaction_target(
            target_key,
            "reconnaissance",
            "RECON_TARGET_INVALID",
            "The target cannot be reconnoitered",
        )
        self._evaluate_rule(
            lambda: self.ruleset.validate_reconnaissance(canonical_target, approach)
        )
        self._ensure_soldiers_available(player_id, troop_count, lock=False)

    def preflight_military_operation(
        self,
        *,
        player_id: UUID,
        troop_count: int,
        mission_type: str,
        target_key: str,
        strategy: str,
    ) -> None:
        canonical_target = self._validate_military_parameters(
            target_key,
            mission_type,
            strategy,
        )
        state = self._starfire_rule_state(player_id)
        self._evaluate_rule(
            lambda: self.ruleset.validate_military_operation(
                canonical_target,
                mission_type,
                strategy,
                state,
            )
        )
        self._ensure_soldiers_available(player_id, troop_count, lock=False)

    def preflight_village_support(
        self,
        *,
        player_id: UUID,
        food_offer: int,
        requested_support: str = "INTELLIGENCE",
    ) -> None:
        state = self._starfire_rule_state(player_id)
        self._evaluate_rule(
            lambda: self.ruleset.validate_village_support(
                state,
                food_offer,
                requested_support,
            )
        )

    def preflight_outpost_repair(
        self,
        *,
        player_id: UUID,
        food_commitment: int,
        gold_commitment: int,
        target_key: str = "starfire_outpost",
        repair_level: str = "TEMPORARY",
    ) -> None:
        canonical_target = canonical_node_key(target_key)
        state = self._starfire_rule_state(player_id)
        self._evaluate_rule(
            lambda: self.ruleset.validate_repair(
                canonical_target,
                repair_level,
                food_commitment,
                gold_commitment,
                state,
            )
        )

    def preflight_trade_route_test(
        self,
        *,
        player_id: UUID,
        route_key: str = "northern_trade_route",
    ) -> None:
        canonical_target = canonical_node_key(route_key)
        state = self._starfire_rule_state(player_id)
        self._evaluate_rule(lambda: self.ruleset.validate_trade_route(canonical_target, state))

    def start_recon_operation(
        self,
        *,
        player_id: UUID,
        officer_npc_id: UUID,
        task_id: UUID | None,
        source_step_id: UUID | None,
        target_key: str,
        troop_count: int,
        approach: str,
        idempotency_key: str,
    ) -> WorldOperation:
        canonical_target = self._canonical_interaction_target(
            target_key,
            "reconnaissance",
            "RECON_TARGET_INVALID",
            "The target cannot be reconnoitered",
        )
        self._evaluate_rule(
            lambda: self.ruleset.validate_reconnaissance(canonical_target, approach)
        )
        return self._start_strategic_operation(
            player_id=player_id,
            officer_npc_id=officer_npc_id,
            task_id=task_id,
            source_step_id=source_step_id,
            operation_type="RECONNAISSANCE",
            target_key=canonical_target,
            parameters={"troop_count": troop_count, "approach": approach},
            idempotency_key=idempotency_key,
        )

    def start_military_operation(
        self,
        *,
        player_id: UUID,
        officer_npc_id: UUID,
        task_id: UUID | None,
        source_step_id: UUID | None,
        target_key: str,
        troop_count: int,
        mission_type: str,
        strategy: str,
        idempotency_key: str,
    ) -> WorldOperation:
        canonical_target = self._validate_military_parameters(
            target_key,
            mission_type,
            strategy,
        )
        parameters = {
            "troop_count": troop_count,
            "mission_type": mission_type,
            "strategy": strategy,
        }
        existing = self._matching_operation(
            player_id,
            idempotency_key,
            operation_type="MILITARY",
            target_key=canonical_target,
            parameters=parameters,
        )
        if existing is not None:
            return existing
        state = self._starfire_rule_state(player_id)
        self._evaluate_rule(
            lambda: self.ruleset.validate_military_operation(
                canonical_target,
                mission_type,
                strategy,
                state,
            )
        )
        return self._start_strategic_operation(
            player_id=player_id,
            officer_npc_id=officer_npc_id,
            task_id=task_id,
            source_step_id=source_step_id,
            operation_type="MILITARY",
            target_key=canonical_target,
            parameters=parameters,
            idempotency_key=idempotency_key,
        )

    def negotiate_village_support(
        self,
        *,
        player_id: UUID,
        food_offer: int,
        requested_support: str,
    ) -> dict[str, object]:
        domain = self._domain_state(player_id, lock=True)
        player = self.get_player(player_id)
        state = self._starfire_rule_state(player_id, player=player, domain=domain)
        outcome = self._evaluate_rule(
            lambda: self.ruleset.negotiate_village_support(
                state,
                food_offer,
                requested_support,
            )
        )
        domain.food += outcome.food_delta
        domain.version += 1
        facts = self._apply_rule_updates(player_id, outcome)
        fact = next(iter(facts.values()))
        self.db.flush()
        return {
            **dict(outcome.payload),
            "food_remaining": domain.food,
            "fact_version": fact.version,
        }

    def start_outpost_repair(
        self,
        *,
        player_id: UUID,
        officer_npc_id: UUID,
        task_id: UUID | None,
        source_step_id: UUID | None,
        target_key: str,
        repair_level: str,
        food_commitment: int,
        gold_commitment: int,
        idempotency_key: str,
    ) -> WorldOperation:
        canonical_target = canonical_node_key(target_key)
        self._evaluate_rule(
            lambda: self.ruleset.validate_repair_parameters(
                canonical_target,
                repair_level,
            )
        )
        parameters = {
            "repair_level": repair_level,
            "food_commitment": food_commitment,
            "gold_commitment": gold_commitment,
        }
        existing = self._matching_operation(
            player_id,
            idempotency_key,
            operation_type="CONSTRUCTION",
            target_key=canonical_target,
            parameters=parameters,
        )
        if existing is not None:
            return existing
        player = self.get_player(player_id, lock=True)
        domain = self._domain_state(player_id, lock=True)
        state = self._starfire_rule_state(player_id, player=player, domain=domain)
        outcome = self._evaluate_rule(
            lambda: self.ruleset.prepare_repair(
                canonical_target,
                repair_level,
                food_commitment,
                gold_commitment,
                state,
            )
        )
        domain.food += outcome.food_delta
        domain.version += 1
        player.gold += outcome.gold_delta
        player.version += 1
        return self._create_operation(
            player_id=player_id,
            officer_npc_id=officer_npc_id,
            task_id=task_id,
            source_step_id=source_step_id,
            operation_type="CONSTRUCTION",
            target_key=canonical_target,
            parameters=parameters,
            idempotency_key=idempotency_key,
        )

    def start_trade_route_test(
        self,
        *,
        player_id: UUID,
        officer_npc_id: UUID,
        task_id: UUID | None,
        source_step_id: UUID | None,
        route_key: str,
        idempotency_key: str,
    ) -> WorldOperation:
        canonical_target = canonical_node_key(route_key)
        self._evaluate_rule(lambda: self.ruleset.validate_trade_route_target(canonical_target))
        existing = self._matching_operation(
            player_id,
            idempotency_key,
            operation_type="TRADE_TEST",
            target_key=canonical_target,
            parameters={},
        )
        if existing is not None:
            return existing
        state = self._starfire_rule_state(player_id)
        self._evaluate_rule(lambda: self.ruleset.validate_trade_route(canonical_target, state))
        return self._start_strategic_operation(
            player_id=player_id,
            officer_npc_id=officer_npc_id,
            task_id=task_id,
            source_step_id=source_step_id,
            operation_type="TRADE_TEST",
            target_key=canonical_target,
            parameters={},
            idempotency_key=idempotency_key,
        )

    def resolve_world_operation(
        self,
        operation_id: UUID,
        resolution_key: str,
    ) -> WorldOperation:
        operation = self.db.scalar(
            select(WorldOperation).where(WorldOperation.id == operation_id).with_for_update()
        )
        if operation is None:
            raise NotFoundError("world_operation", operation_id)
        if operation.status == WorldOperationStatus.RESOLVED:
            if operation.resolution_key == resolution_key:
                return operation
            raise ConflictError(
                "WORLD_EVENT_ALREADY_RESOLVED",
                "The world event has already been resolved",
            )
        if operation.operation_type == "RECONNAISSANCE":
            outcome = self._resolve_reconnaissance(operation)
        elif operation.operation_type == "MILITARY":
            outcome = self._resolve_military(operation)
        elif operation.operation_type == "CONSTRUCTION":
            outcome = self._resolve_construction(operation)
        elif operation.operation_type == "TRADE_TEST":
            outcome = self._resolve_trade_test(operation)
        else:
            raise AppError("WORLD_OPERATION_UNSUPPORTED", "The operation cannot be resolved")
        operation.status = WorldOperationStatus.RESOLVED
        operation.outcome = outcome
        operation.resolution_key = resolution_key
        operation.resolved_at = datetime.now(UTC)
        self.db.flush()
        return operation

    def _start_strategic_operation(
        self,
        *,
        player_id: UUID,
        officer_npc_id: UUID,
        task_id: UUID | None,
        source_step_id: UUID | None,
        operation_type: str,
        target_key: str,
        parameters: dict[str, Any],
        idempotency_key: str,
    ) -> WorldOperation:
        existing = self._matching_operation(
            player_id,
            idempotency_key,
            operation_type=operation_type,
            target_key=target_key,
            parameters=parameters,
        )
        if existing is not None:
            return existing
        if "troop_count" in parameters:
            troop_count = int(parameters["troop_count"])
            domain = self._ensure_soldiers_available(player_id, troop_count, lock=True)
            domain.soldiers_committed += troop_count
            domain.version += 1
        return self._create_operation(
            player_id=player_id,
            officer_npc_id=officer_npc_id,
            task_id=task_id,
            source_step_id=source_step_id,
            operation_type=operation_type,
            target_key=target_key,
            parameters=parameters,
            idempotency_key=idempotency_key,
        )

    def _create_operation(
        self,
        *,
        player_id: UUID,
        officer_npc_id: UUID,
        task_id: UUID | None,
        source_step_id: UUID | None,
        operation_type: str,
        target_key: str,
        parameters: dict[str, Any],
        idempotency_key: str,
    ) -> WorldOperation:
        operation = WorldOperation(
            player_id=player_id,
            task_id=task_id,
            source_step_id=source_step_id,
            officer_npc_id=officer_npc_id,
            operation_type=operation_type,
            target_key=target_key,
            parameters=parameters,
            idempotency_key=idempotency_key,
        )
        self.db.add(operation)
        self.db.flush()
        return operation

    def _find_operation(self, player_id: UUID, idempotency_key: str) -> WorldOperation | None:
        return self.db.scalar(
            select(WorldOperation).where(
                WorldOperation.player_id == player_id,
                WorldOperation.idempotency_key == idempotency_key,
            )
        )

    def _matching_operation(
        self,
        player_id: UUID,
        idempotency_key: str,
        *,
        operation_type: str,
        target_key: str,
        parameters: dict[str, Any],
    ) -> WorldOperation | None:
        existing = self._find_operation(player_id, idempotency_key)
        if existing is None:
            return None
        if (
            existing.operation_type != operation_type
            or canonical_node_key(existing.target_key) != canonical_node_key(target_key)
            or existing.parameters != parameters
        ):
            raise ConflictError(
                "IDEMPOTENCY_KEY_REUSED",
                "The idempotency key is already bound to a different world operation",
            )
        return existing

    def _domain_state(self, player_id: UUID, *, lock: bool = False) -> PlayerDomainState:
        query = select(PlayerDomainState).where(PlayerDomainState.player_id == player_id)
        if lock:
            query = query.with_for_update()
        domain = self.db.scalar(query)
        if domain is None:
            raise AppError("DOMAIN_STATE_NOT_INITIALIZED", "Strategic resources are missing")
        return domain

    def _ensure_soldiers_available(
        self,
        player_id: UUID,
        troop_count: int,
        *,
        lock: bool,
    ) -> PlayerDomainState:
        domain = self._domain_state(player_id, lock=lock)
        available = domain.soldiers_total - domain.soldiers_committed
        if troop_count < 1 or troop_count > available:
            raise AppError(
                "SOLDIERS_UNAVAILABLE",
                "The requested soldiers are not available",
                retryable=True,
            )
        return domain

    def _resolve_reconnaissance(self, operation: WorldOperation) -> dict[str, Any]:
        outcome = self._evaluate_rule(
            lambda: self.ruleset.resolve_reconnaissance(canonical_node_key(operation.target_key))
        )
        self._release_operation_troops(
            operation,
            casualties=outcome.casualties,
            morale_delta=outcome.morale_delta,
        )
        self._apply_rule_updates(operation.player_id, outcome, operation_id=operation.id)
        return dict(outcome.payload)

    def _resolve_military(self, operation: WorldOperation) -> dict[str, Any]:
        mission = str(operation.parameters.get("mission_type"))
        state = self._starfire_rule_state(operation.player_id)
        outcome = self._evaluate_rule(
            lambda: self.ruleset.resolve_military_operation(
                canonical_node_key(operation.target_key),
                mission,
                state,
            )
        )
        self._release_operation_troops(
            operation,
            casualties=outcome.casualties,
            morale_delta=outcome.morale_delta,
        )
        self._apply_rule_updates(operation.player_id, outcome, operation_id=operation.id)
        return dict(outcome.payload)

    def _resolve_construction(self, operation: WorldOperation) -> dict[str, Any]:
        state = self._starfire_rule_state(operation.player_id)
        repair_level = str(operation.parameters.get("repair_level"))
        outcome = self._evaluate_rule(
            lambda: self.ruleset.resolve_repair(
                canonical_node_key(operation.target_key),
                repair_level,
                state,
            )
        )
        self._apply_rule_updates(operation.player_id, outcome, operation_id=operation.id)
        return dict(outcome.payload)

    def _resolve_trade_test(self, operation: WorldOperation) -> dict[str, Any]:
        state = self._starfire_rule_state(operation.player_id)
        outcome = self._evaluate_rule(
            lambda: self.ruleset.resolve_trade_route_test(
                canonical_node_key(operation.target_key),
                state,
            )
        )
        self._apply_rule_updates(operation.player_id, outcome, operation_id=operation.id)
        return dict(outcome.payload)

    def _canonical_interaction_target(
        self,
        raw_target_key: str,
        interaction_key: str,
        error_code: str,
        error_message: str,
    ) -> str:
        canonical_target = canonical_node_key(raw_target_key)
        if not legacy_target_supports_interaction(raw_target_key, interaction_key):
            raise AppError(error_code, error_message)
        return canonical_target

    def _validate_military_parameters(
        self,
        raw_target_key: str,
        mission_type: str,
        strategy: str,
    ) -> str:
        canonical_target = canonical_node_key(raw_target_key)
        self._evaluate_rule(
            lambda: self.ruleset.validate_military_parameters(
                canonical_target,
                mission_type,
                strategy,
            )
        )
        interaction = "disrupt_supply" if mission_type == "DISRUPT_SUPPLY" else "clear_threat"
        if not legacy_target_supports_interaction(raw_target_key, interaction):
            raise AppError("MILITARY_TARGET_INVALID", "The military target is invalid")
        return canonical_target

    def _starfire_rule_state(
        self,
        player_id: UUID,
        *,
        player: Player | None = None,
        domain: PlayerDomainState | None = None,
    ) -> StarfireRuleState:
        current_player = player or self.get_player(player_id)
        current_domain = domain or self._domain_state(player_id)
        supply_status, _ = self._persisted_fact_status(
            player_id,
            "enemy_supply_route",
            "UNKNOWN",
        )
        supply = project_legacy_supply_status(supply_status)
        ambush_status, ambush_known = self._persisted_fact_status(
            player_id,
            "ambush_status",
            self._initial_fact_value("northern_valley", "ambush_status"),
        )
        facts = {
            ("north_village", "village_support"): StarfireFactState(
                self._persisted_fact_status(
                    player_id,
                    "village_support",
                    "NONE",
                )[0]
            ),
            ("northern_valley", "valley_intelligence"): StarfireFactState(
                self._persisted_fact_status(
                    player_id,
                    "valley_intelligence",
                    "INCOMPLETE",
                )[0]
            ),
            ("northern_valley", "valley_security"): StarfireFactState(
                self._persisted_fact_status(
                    player_id,
                    "valley_security",
                    "UNSAFE",
                )[0]
            ),
            ("northern_valley", "ambush_status"): StarfireFactState(
                ambush_status,
                known=ambush_known,
            ),
            ("enemy_north_supply_route", "supply_status"): StarfireFactState(
                supply.truth_status,
                known=supply.known,
            ),
            ("starfire_outpost", "outpost_status"): StarfireFactState(
                self._persisted_fact_status(
                    player_id,
                    "starfire_outpost_status",
                    "DAMAGED",
                )[0]
            ),
            ("northern_trade_route", "trade_route_status"): StarfireFactState(
                self._persisted_fact_status(
                    player_id,
                    "northern_trade_route_status",
                    "CLOSED",
                )[0]
            ),
        }
        return StarfireRuleState(
            facts=facts,
            resources=StarfireResources(
                soldiers_available=(
                    current_domain.soldiers_total - current_domain.soldiers_committed
                ),
                food=current_domain.food,
                gold=current_player.gold,
                morale=current_domain.morale,
            ),
        )

    def _persisted_fact_status(
        self,
        player_id: UUID,
        legacy_key: str,
        default: str,
    ) -> tuple[str, bool]:
        fact = self.db.get(PlayerWorldFact, (player_id, legacy_key))
        if fact is None:
            return default, False
        status = fact.value.get("status")
        return (str(status), True) if status is not None else (default, False)

    @staticmethod
    def _initial_fact_value(node_key: str, fact_key: str) -> str:
        node = STARFIRE_WORLD.node(node_key)
        fact = node.fact(fact_key) if node is not None else None
        if fact is None:
            raise AppError(
                "STARFIRE_DEFINITION_INVALID",
                "A required Starfire fact definition is missing",
            )
        return str(fact.initial_value)

    def _apply_rule_updates(
        self,
        player_id: UUID,
        outcome: RuleOutcome,
        *,
        operation_id: UUID | None = None,
    ) -> dict[tuple[str, str], PlayerWorldFact]:
        persisted: dict[tuple[str, str], PlayerWorldFact] = {}
        for update in outcome.fact_updates:
            key = legacy_fact_key(update.node_key, update.fact_key)
            if key is None:
                raise AppError(
                    "STARFIRE_RULE_OUTCOME_INVALID",
                    "A Starfire rule produced a fact without a persistence projection",
                )
            value = dict(update.value)
            if operation_id is not None:
                value["operation_id"] = str(operation_id)
            persisted[(update.node_key, update.fact_key)] = self.set_world_fact(
                player_id,
                key,
                value,
            )
        for node_key in outcome.unlock_node_keys:
            self.unlock_node(player_id, node_key)
        return persisted

    def _evaluate_rule[T](self, evaluator: Callable[[], T]) -> T:
        try:
            return evaluator()
        except StarfireRuleViolation as exc:
            raise AppError(
                exc.code,
                exc.message,
                retryable=exc.retryable,
            ) from None

    def _release_operation_troops(
        self,
        operation: WorldOperation,
        *,
        casualties: int,
        morale_delta: int,
    ) -> None:
        domain = self._domain_state(operation.player_id, lock=True)
        troop_count = int(operation.parameters.get("troop_count", 0))
        domain.soldiers_committed = max(0, domain.soldiers_committed - troop_count)
        domain.soldiers_total = max(
            domain.soldiers_committed,
            domain.soldiers_total - casualties,
        )
        domain.morale = max(0, min(100, domain.morale + morale_delta))
        domain.version += 1
        self.db.flush()


def seed_id(key: str) -> UUID:
    return uuid5(SEED_NAMESPACE, key)
