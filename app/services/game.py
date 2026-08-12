from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario import ScenarioVersionSnapshot
from app.domain.world import AccessState, Visibility
from app.infrastructure.db.models import (
    NPC,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceResourceState,
    GameInstanceWorldFact,
    OfficerAppointment,
    Player,
    PlayerDomainState,
    PlayerNodeState,
    PlayerWorldFact,
    PlayerWorldFactState,
    WorldNode,
    WorldOperation,
)
from app.scenarios.starfire.compatibility import (
    LEGACY_FACT_REFS,
    canonical_fact_ref,
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
    StarfireKnowledgeState,
    StarfireResources,
    StarfireRuleset,
    StarfireRuleState,
    StarfireRuleViolation,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService

SEED_NAMESPACE = UUID("3e16a11d-9cf5-4981-af7a-152c28331300")


@dataclass(slots=True)
class _InstanceResourceLedger:
    rows: dict[str, GameInstanceResourceState]

    def _row(self, key: str) -> GameInstanceResourceState:
        row = self.rows.get(key)
        if row is None:
            raise AppError(
                "INSTANCE_RESOURCE_NOT_INITIALIZED",
                f"The GameInstance resource {key} is missing",
            )
        return row

    @property
    def soldiers_total(self) -> int:
        return self._row("soldiers").value

    @soldiers_total.setter
    def soldiers_total(self, value: int) -> None:
        self._row("soldiers").value = value

    @property
    def soldiers_committed(self) -> int:
        return self._row("soldiers").reserved_value

    @soldiers_committed.setter
    def soldiers_committed(self, value: int) -> None:
        self._row("soldiers").reserved_value = value

    @property
    def food(self) -> int:
        return self._row("food").value

    @food.setter
    def food(self, value: int) -> None:
        self._row("food").value = value

    @property
    def morale(self) -> int:
        return self._row("morale").value

    @morale.setter
    def morale(self, value: int) -> None:
        self._row("morale").value = value

    @property
    def gold(self) -> int:
        return self._row("gold").value

    @gold.setter
    def gold(self, value: int) -> None:
        self._row("gold").value = value

    @property
    def version(self) -> int:
        return max(row.version for row in self.rows.values())

    @version.setter
    def version(self, value: int) -> None:
        for row in self.rows.values():
            row.version = value


class GameService:
    def __init__(self, db: Session, scope: RuntimeScope | None = None):
        self.db = db
        self.ruleset = StarfireRuleset()
        self.scope = scope
        self._version_snapshot: ScenarioVersionSnapshot | None = None
        if scope is not None:
            persisted = GameInstanceService(db).load(scope.game_instance_id)
            scope.assert_compatible(persisted)
            self._version_snapshot = ScenarioVersionRepository(db).load(scope.scenario_version_id)

    def get_player(self, player_id: UUID, *, lock: bool = False) -> Player:
        self._assert_player(player_id)
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
        node_definitions = {node.key: node for node in STARFIRE_WORLD.nodes}
        for node in nodes:
            status = NodeStatus.ENTERED if node.id == start.id else node.default_status
            definition = node_definitions.get(node.key)
            visibility = (
                definition.initial_visibility if definition is not None else Visibility.KNOWN
            )
            self.db.add(
                PlayerNodeState(
                    player_id=player.id,
                    node_id=node.id,
                    status=status,
                    visibility=visibility,
                )
            )
            if definition is not None:
                for fact in definition.facts:
                    self.db.add(
                        PlayerWorldFactState(
                            player_id=player.id,
                            node_id=node.id,
                            fact_key=fact.key,
                            truth_value=fact.initial_value,
                            visibility=fact.initial_visibility,
                        )
                    )
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

    def list_nodes(self, player_id: UUID) -> list[tuple[Any, Any]]:
        """Return the player's strategic map nodes and their verified state."""
        self.get_player(player_id)
        if self.scope is not None:
            states = {
                state.node_key: state
                for state in self.db.scalars(
                    select(GameInstanceNodeState).where(
                        GameInstanceNodeState.game_instance_id == self.scope.game_instance_id
                    )
                ).all()
            }
            return [
                (node, states[node.key])
                for node in self._world_definition().nodes
                if node.key in states
            ]
        return list(
            self.db.execute(
                select(WorldNode, PlayerNodeState)
                .join(PlayerNodeState, PlayerNodeState.node_id == WorldNode.id)
                .where(PlayerNodeState.player_id == player_id)
            ).tuples()
        )

    def unlock_node(self, player_id: UUID, node_key: str) -> Any:
        canonical_key = canonical_node_key(node_key)
        self._assert_player(player_id)
        if self.scope is not None:
            instance_state = self.db.get(
                GameInstanceNodeState,
                (self.scope.game_instance_id, canonical_key),
            )
            if instance_state is None:
                raise NotFoundError("game_instance_node", canonical_key)
            if instance_state.status == NodeStatus.LOCKED:
                instance_state.status = NodeStatus.AVAILABLE
                instance_state.version += 1
                self.db.flush()
            return instance_state
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
        if self.scope is not None:
            instance_fact = self.db.get(GameInstanceWorldFact, (self.scope.game_instance_id, key))
            return {} if instance_fact is None else dict(instance_fact.value)
        fact = self.db.get(PlayerWorldFact, (player_id, key))
        return {} if fact is None else dict(fact.value)

    def set_world_fact(self, player_id: UUID, key: str, value: dict[str, object]) -> Any:
        self.get_player(player_id, lock=True)
        if self.scope is not None:
            instance_fact = self.db.get(GameInstanceWorldFact, (self.scope.game_instance_id, key))
            if instance_fact is None:
                instance_fact = GameInstanceWorldFact(
                    game_instance_id=self.scope.game_instance_id,
                    key=key,
                    value=value,
                    version=1,
                )
                self.db.add(instance_fact)
            else:
                instance_fact.value = value
                instance_fact.version += 1
            self._sync_legacy_fact_to_canonical(player_id, key, value)
            self.db.flush()
            return instance_fact
        fact = self.db.get(PlayerWorldFact, (player_id, key))
        if fact is None:
            fact = PlayerWorldFact(player_id=player_id, key=key, value=value, version=1)
            self.db.add(fact)
        else:
            fact.value = value
            fact.version += 1
        self._sync_legacy_fact_to_canonical(player_id, key, value)
        self.db.flush()
        return fact

    def inspect_command_state(
        self,
        player_id: UUID,
        *,
        known_state: StarfireKnowledgeState | None = None,
    ) -> dict[str, object]:
        player = self.get_player(player_id)
        domain = self._domain_state(player_id)
        resources = {
            "soldiers_total": domain.soldiers_total,
            "soldiers_available": domain.soldiers_total - domain.soldiers_committed,
            "soldiers_committed": domain.soldiers_committed,
            "food": domain.food,
            "gold": self._gold(player, domain),
            "morale": domain.morale,
            "version": domain.version,
        }
        known = known_state or self.scenario_known_state(player_id)
        world: dict[str, object] = {}
        for legacy_key, ref in LEGACY_FACT_REFS.items():
            if not known.fact_known(ref.node_key, ref.fact_key):
                continue
            world[legacy_key] = known.fact_value(ref.node_key, ref.fact_key)
        return {
            "resources": resources,
            "world": world,
            **resources,
            **world,
        }

    def scenario_state(self, player_id: UUID) -> StarfireRuleState:
        """Compatibility alias for authoritative scenario truth."""

        return self.scenario_truth_state(player_id)

    def scenario_truth_state(self, player_id: UUID) -> StarfireRuleState:
        """Return authoritative canonical truth for rules and objectives."""

        return self._starfire_rule_state(player_id)

    def scenario_known_state(self, player_id: UUID) -> StarfireKnowledgeState:
        """Return a projection which physically excludes hidden nodes and fact values."""

        player = self.get_player(player_id)
        domain = self._domain_state(player_id)
        if self.scope is not None:
            node_rows = self.db.scalars(
                select(GameInstanceNodeState).where(
                    GameInstanceNodeState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceNodeState.visibility == Visibility.KNOWN,
                )
            ).all()
            node_access = {row.node_key: self._access_state(row.status) for row in node_rows}
            known_node_keys = frozenset(node_access)
            instance_fact_rows = self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == self.scope.game_instance_id,
                    GameInstanceFactState.node_key.in_(known_node_keys),
                    GameInstanceFactState.visibility == Visibility.KNOWN,
                )
            ).all()
            return StarfireKnowledgeState(
                facts={
                    (fact.node_key, fact.fact_key): str(fact.truth_value)
                    for fact in instance_fact_rows
                },
                node_access=node_access,
                resources=self._resources(player, domain),
            )
        rows = self.db.execute(
            select(WorldNode, PlayerNodeState)
            .join(PlayerNodeState, PlayerNodeState.node_id == WorldNode.id)
            .where(
                PlayerNodeState.player_id == player_id,
                PlayerNodeState.visibility == Visibility.KNOWN,
            )
        ).all()
        node_access = {node.key: self._access_state(node_state.status) for node, node_state in rows}
        node_ids = {node.id for node, _node_state in rows}
        fact_rows = self.db.scalars(
            select(PlayerWorldFactState).where(
                PlayerWorldFactState.player_id == player_id,
                PlayerWorldFactState.node_id.in_(node_ids),
                PlayerWorldFactState.visibility == Visibility.KNOWN,
            )
        ).all()
        keys_by_id = {node.id: node.key for node, _node_state in rows}
        return StarfireKnowledgeState(
            facts={
                (keys_by_id[fact.node_id], fact.fact_key): str(fact.truth_value)
                for fact in fact_rows
            },
            node_access=node_access,
            resources=self._resources(player, domain),
        )

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
        self._ensure_known_accessible_node(player_id, canonical_target)
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
        self._ensure_known_accessible_node(player_id, canonical_target)
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
        self._ensure_known_accessible_node(player_id, canonical_target)
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
        self._ensure_known_accessible_node(player_id, canonical_target)
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
        self._ensure_known_accessible_node(player_id, canonical_target)
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
        self._ensure_known_accessible_node(player_id, canonical_target)
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
        self._ensure_known_accessible_node(player_id, canonical_target)
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
        if self.scope is None:
            player.gold += outcome.gold_delta
            player.version += 1
        else:
            assert isinstance(domain, _InstanceResourceLedger)
            domain.gold += outcome.gold_delta
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
        self._ensure_known_accessible_node(player_id, canonical_target)
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
        self._assert_operation_scope(operation)
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
            game_instance_id=(self.scope.game_instance_id if self.scope is not None else None),
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
        self._assert_player(player_id)
        instance_id = self.scope.game_instance_id if self.scope is not None else None
        return self.db.scalar(
            select(WorldOperation).where(
                WorldOperation.player_id == player_id,
                WorldOperation.game_instance_id == instance_id,
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

    def _domain_state(
        self, player_id: UUID, *, lock: bool = False
    ) -> PlayerDomainState | _InstanceResourceLedger:
        self._assert_player(player_id)
        if self.scope is not None:
            instance_query = select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == self.scope.game_instance_id
            )
            if lock:
                instance_query = instance_query.with_for_update()
            rows = self.db.scalars(instance_query).all()
            if not rows:
                raise AppError(
                    "DOMAIN_STATE_NOT_INITIALIZED",
                    "GameInstance strategic resources are missing",
                )
            return _InstanceResourceLedger({row.resource_key: row for row in rows})
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
    ) -> PlayerDomainState | _InstanceResourceLedger:
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

    def _ensure_known_accessible_node(self, player_id: UUID, node_key: str) -> None:
        self._assert_player(player_id)
        if self.scope is not None:
            instance_state = self.db.get(
                GameInstanceNodeState,
                (self.scope.game_instance_id, node_key),
            )
            if instance_state is None or instance_state.visibility != Visibility.KNOWN:
                raise AppError(
                    "INTERACTION_TARGET_HIDDEN",
                    "The interaction target has not been discovered",
                    retryable=True,
                )
            if instance_state.status == NodeStatus.LOCKED:
                raise AppError(
                    "INTERACTION_TARGET_LOCKED",
                    "The interaction target is not currently accessible",
                    retryable=True,
                )
            return
        node = self.db.scalar(select(WorldNode).where(WorldNode.key == node_key))
        state = self.db.get(PlayerNodeState, (player_id, node.id)) if node is not None else None
        if state is None or state.visibility != Visibility.KNOWN:
            raise AppError(
                "INTERACTION_TARGET_HIDDEN",
                "The interaction target has not been discovered",
                retryable=True,
            )
        if state.status == NodeStatus.LOCKED:
            raise AppError(
                "INTERACTION_TARGET_LOCKED",
                "The interaction target is not currently accessible",
                retryable=True,
            )

    def _starfire_rule_state(
        self,
        player_id: UUID,
        *,
        player: Player | None = None,
        domain: PlayerDomainState | _InstanceResourceLedger | None = None,
    ) -> StarfireRuleState:
        current_player = player or self.get_player(player_id)
        current_domain = domain or self._domain_state(player_id)
        if self.scope is not None:
            fact_rows = self.db.scalars(
                select(GameInstanceFactState).where(
                    GameInstanceFactState.game_instance_id == self.scope.game_instance_id
                )
            ).all()
            facts = {
                (fact.node_key, fact.fact_key): StarfireFactState(str(fact.truth_value))
                for fact in fact_rows
            }
            return StarfireRuleState(
                facts=facts,
                resources=self._resources(current_player, current_domain),
            )
        rows = self.db.execute(
            select(PlayerWorldFactState, WorldNode)
            .join(WorldNode, WorldNode.id == PlayerWorldFactState.node_id)
            .where(PlayerWorldFactState.player_id == player_id)
        ).all()
        facts = {
            (node.key, fact.fact_key): StarfireFactState(str(fact.truth_value))
            for fact, node in rows
        }
        if not facts:
            facts = self._legacy_truth_fallback(player_id)
        return StarfireRuleState(
            facts=facts,
            resources=self._resources(current_player, current_domain),
        )

    @staticmethod
    def _resources(
        player: Player,
        domain: PlayerDomainState | _InstanceResourceLedger,
    ) -> StarfireResources:
        return StarfireResources(
            soldiers_available=domain.soldiers_total - domain.soldiers_committed,
            food=domain.food,
            gold=domain.gold if isinstance(domain, _InstanceResourceLedger) else player.gold,
            morale=domain.morale,
        )

    def _legacy_truth_fallback(self, player_id: UUID) -> dict[tuple[str, str], StarfireFactState]:
        """Read old databases safely before the 0.7 migration has been applied."""

        supply_status, _ = self._persisted_fact_status(player_id, "enemy_supply_route", "UNKNOWN")
        supply = project_legacy_supply_status(supply_status)
        facts: dict[tuple[str, str], StarfireFactState] = {}
        for node in self._world_definition().nodes:
            for definition in node.facts:
                legacy_key = legacy_fact_key(node.key, definition.key)
                value = str(definition.initial_value)
                if legacy_key is not None:
                    value = self._persisted_fact_status(player_id, legacy_key, value)[0]
                facts[(node.key, definition.key)] = StarfireFactState(value)
        facts[("enemy_north_supply_route", "supply_status")] = StarfireFactState(
            supply.truth_status
        )
        return facts

    def _persisted_fact_status(
        self,
        player_id: UUID,
        legacy_key: str,
        default: str,
    ) -> tuple[str, bool]:
        if self.scope is not None:
            instance_fact = self.db.get(
                GameInstanceWorldFact,
                (self.scope.game_instance_id, legacy_key),
            )
            if instance_fact is None:
                return default, False
            status = instance_fact.value.get("status")
            return (str(status), True) if status is not None else (default, False)
        fact = self.db.get(PlayerWorldFact, (player_id, legacy_key))
        if fact is None:
            return default, False
        status = fact.value.get("status")
        return (str(status), True) if status is not None else (default, False)

    def _apply_rule_updates(
        self,
        player_id: UUID,
        outcome: RuleOutcome,
        *,
        operation_id: UUID | None = None,
    ) -> dict[tuple[str, str], Any]:
        persisted: dict[tuple[str, str], Any] = {}
        for update in outcome.fact_updates:
            truth_value = update.value.get("status")
            if truth_value is None:
                raise AppError(
                    "STARFIRE_RULE_OUTCOME_INVALID",
                    "A Starfire rule fact update is missing its canonical status value",
                )
            self._set_fact_truth(
                player_id,
                update.node_key,
                update.fact_key,
                truth_value,
            )
        for node_key in outcome.reveal_node_keys:
            self._set_node_visibility(player_id, node_key, Visibility.KNOWN)
        for node_key, fact_key in outcome.reveal_fact_refs:
            self._set_fact_visibility(player_id, node_key, fact_key, Visibility.KNOWN)
        updated_refs = {(update.node_key, update.fact_key) for update in outcome.fact_updates}
        for update in outcome.fact_updates:
            fact_state = self._fact_record(player_id, update.node_key, update.fact_key)
            if fact_state.visibility != Visibility.KNOWN:
                continue
            value = dict(update.value)
            if operation_id is not None:
                value["operation_id"] = str(operation_id)
            persisted[(update.node_key, update.fact_key)] = self._write_legacy_fact(
                player_id,
                update.node_key,
                update.fact_key,
                value,
            )
        for node_key, fact_key in outcome.reveal_fact_refs:
            if (node_key, fact_key) not in updated_refs:
                self._project_known_fact_to_legacy(player_id, node_key, fact_key)
        for node_key in outcome.unlock_node_keys:
            self.unlock_node(player_id, node_key)
        return persisted

    def _sync_legacy_fact_to_canonical(
        self,
        player_id: UUID,
        legacy_key: str,
        value: dict[str, object],
    ) -> None:
        ref = canonical_fact_ref(legacy_key)
        status = value.get("status")
        if ref is None or status is None:
            return
        visibility = Visibility.KNOWN
        truth_value: object = status
        if legacy_key == "enemy_supply_route":
            projection = project_legacy_supply_status(str(status))
            truth_value = projection.truth_status
            visibility = Visibility.KNOWN if projection.known else Visibility.HIDDEN
            self._set_node_visibility(player_id, ref.node_key, visibility)
            if projection.known:
                self.unlock_node(player_id, ref.node_key)
        self._set_fact_truth(
            player_id,
            ref.node_key,
            ref.fact_key,
            truth_value,
            visibility=visibility,
        )

    def _fact_record(
        self,
        player_id: UUID,
        node_key: str,
        fact_key: str,
    ) -> Any:
        if self.scope is not None:
            instance_fact = self.db.get(
                GameInstanceFactState,
                (self.scope.game_instance_id, node_key, fact_key),
            )
            if instance_fact is None:
                definition = self._world_definition().node(node_key)
                initial = definition.fact(fact_key) if definition is not None else None
                if initial is None:
                    raise AppError(
                        "STARFIRE_DEFINITION_INVALID",
                        "A required ScenarioVersion fact definition is missing",
                    )
                instance_fact = GameInstanceFactState(
                    game_instance_id=self.scope.game_instance_id,
                    node_key=node_key,
                    fact_key=fact_key,
                    truth_value=initial.initial_value,
                    visibility=initial.initial_visibility,
                )
                self.db.add(instance_fact)
                self.db.flush()
            return instance_fact
        node = self.db.scalar(select(WorldNode).where(WorldNode.key == node_key))
        if node is None:
            raise NotFoundError("node", node_key)
        fact = self.db.get(PlayerWorldFactState, (player_id, node.id, fact_key))
        if fact is None:
            definition = self._world_definition().node(node_key)
            initial = definition.fact(fact_key) if definition is not None else None
            if initial is None:
                raise AppError(
                    "STARFIRE_DEFINITION_INVALID",
                    "A required Starfire fact definition is missing",
                )
            fact = PlayerWorldFactState(
                player_id=player_id,
                node_id=node.id,
                fact_key=fact_key,
                truth_value=initial.initial_value,
                visibility=initial.initial_visibility,
            )
            self.db.add(fact)
            self.db.flush()
        return fact

    def _set_fact_truth(
        self,
        player_id: UUID,
        node_key: str,
        fact_key: str,
        truth_value: object,
        *,
        visibility: Visibility | None = None,
    ) -> Any:
        fact = self._fact_record(player_id, node_key, fact_key)
        changed = fact.truth_value != truth_value
        if changed:
            fact.truth_value = truth_value
        if visibility is not None and fact.visibility != visibility:
            fact.visibility = visibility
            changed = True
        if changed:
            fact.version += 1
        return fact

    def _set_fact_visibility(
        self,
        player_id: UUID,
        node_key: str,
        fact_key: str,
        visibility: Visibility,
    ) -> Any:
        fact = self._fact_record(player_id, node_key, fact_key)
        if fact.visibility != visibility:
            fact.visibility = visibility
            fact.version += 1
        return fact

    def _set_node_visibility(
        self,
        player_id: UUID,
        node_key: str,
        visibility: Visibility,
    ) -> Any:
        if self.scope is not None:
            instance_state = self.db.get(
                GameInstanceNodeState,
                (self.scope.game_instance_id, node_key),
            )
            if instance_state is None:
                raise NotFoundError("game_instance_node", node_key)
            if instance_state.visibility != visibility:
                instance_state.visibility = visibility
                instance_state.version += 1
            return instance_state
        node = self.db.scalar(select(WorldNode).where(WorldNode.key == node_key))
        if node is None:
            raise NotFoundError("node", node_key)
        state = self.db.get(PlayerNodeState, (player_id, node.id))
        if state is None:
            raise NotFoundError("player_node", node.id)
        if state.visibility != visibility:
            state.visibility = visibility
            state.version += 1
        return state

    def _project_known_fact_to_legacy(
        self,
        player_id: UUID,
        node_key: str,
        fact_key: str,
    ) -> None:
        legacy_key = legacy_fact_key(node_key, fact_key)
        if legacy_key is None:
            return
        fact = self._fact_record(player_id, node_key, fact_key)
        value = {"status": fact.truth_value}
        if self.scope is not None:
            instance_legacy = self.db.get(
                GameInstanceWorldFact,
                (self.scope.game_instance_id, legacy_key),
            )
            if instance_legacy is None:
                self.db.add(
                    GameInstanceWorldFact(
                        game_instance_id=self.scope.game_instance_id,
                        key=legacy_key,
                        value=value,
                    )
                )
            elif instance_legacy.value.get("status") != fact.truth_value:
                instance_legacy.value = value
                instance_legacy.version += 1
            return
        legacy = self.db.get(PlayerWorldFact, (player_id, legacy_key))
        if legacy is None:
            self.db.add(PlayerWorldFact(player_id=player_id, key=legacy_key, value=value))
        elif legacy.value.get("status") != fact.truth_value:
            legacy.value = value
            legacy.version += 1

    def _write_legacy_fact(
        self,
        player_id: UUID,
        node_key: str,
        fact_key: str,
        value: dict[str, object],
    ) -> Any:
        legacy_key = legacy_fact_key(node_key, fact_key)
        if legacy_key is None:
            raise AppError(
                "STARFIRE_RULE_OUTCOME_INVALID",
                "A Starfire rule produced a fact without a persistence projection",
            )
        if self.scope is not None:
            instance_legacy = self.db.get(
                GameInstanceWorldFact,
                (self.scope.game_instance_id, legacy_key),
            )
            if instance_legacy is None:
                instance_legacy = GameInstanceWorldFact(
                    game_instance_id=self.scope.game_instance_id,
                    key=legacy_key,
                    value=value,
                )
                self.db.add(instance_legacy)
            else:
                instance_legacy.value = value
                instance_legacy.version += 1
            return instance_legacy
        legacy = self.db.get(PlayerWorldFact, (player_id, legacy_key))
        if legacy is None:
            legacy = PlayerWorldFact(player_id=player_id, key=legacy_key, value=value)
            self.db.add(legacy)
        else:
            legacy.value = value
            legacy.version += 1
        return legacy

    @staticmethod
    def _access_state(status: NodeStatus) -> AccessState:
        return AccessState.LOCKED if status == NodeStatus.LOCKED else AccessState.AVAILABLE

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

    def _assert_player(self, player_id: UUID) -> None:
        if self.scope is not None and self.scope.player_id != player_id:
            raise AppError(
                "RUNTIME_SCOPE_PLAYER_MISMATCH",
                "The requested Player does not own this GameInstance runtime",
                status_code=403,
            )

    def _assert_operation_scope(self, operation: WorldOperation) -> None:
        if self.scope is None:
            if operation.game_instance_id is not None:
                raise AppError(
                    "RUNTIME_SCOPE_REQUIRED",
                    "An instance-owned WorldOperation requires an explicit RuntimeScope",
                    status_code=409,
                )
            return
        if (
            operation.game_instance_id != self.scope.game_instance_id
            or operation.player_id != self.scope.player_id
        ):
            raise AppError(
                "RUNTIME_SCOPE_INSTANCE_MISMATCH",
                "The WorldOperation belongs to another GameInstance",
                status_code=403,
            )

    def _world_definition(self) -> Any:
        return (
            self._version_snapshot.definition.world
            if self._version_snapshot is not None
            else STARFIRE_WORLD
        )

    @staticmethod
    def _gold(
        player: Player,
        domain: PlayerDomainState | _InstanceResourceLedger,
    ) -> int:
        return domain.gold if isinstance(domain, _InstanceResourceLedger) else player.gold


def seed_id(key: str) -> UUID:
    return uuid5(SEED_NAMESPACE, key)
