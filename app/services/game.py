from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, AuthorizationError, ConflictError, NotFoundError
from app.domain.enums import (
    EncounterStatus,
    NodeStatus,
    NPCRole,
    QuestStatus,
    RelationshipAttitude,
    RewardStatus,
    WorldOperationStatus,
)
from app.infrastructure.db.models import (
    NPC,
    EncounterDefinition,
    EncounterRun,
    InventoryItem,
    ItemDefinition,
    OfficerAppointment,
    Player,
    PlayerDomainState,
    PlayerNodeState,
    PlayerNPCRelationship,
    PlayerWorldFact,
    Quest,
    QuestTemplate,
    WorldNode,
    WorldNodeEdge,
    WorldOperation,
)

SEED_NAMESPACE = UUID("3e16a11d-9cf5-4981-af7a-152c28331300")


def attitude_for(score: int) -> RelationshipAttitude:
    if score <= -50:
        return RelationshipAttitude.HOSTILE
    if score <= -15:
        return RelationshipAttitude.UNFRIENDLY
    if score < 25:
        return RelationshipAttitude.NEUTRAL
    if score < 70:
        return RelationshipAttitude.FRIENDLY
    return RelationshipAttitude.TRUSTED


class GameService:
    def __init__(self, db: Session):
        self.db = db

    def get_player(self, player_id: UUID, *, lock: bool = False) -> Player:
        query = select(Player).where(Player.id == player_id)
        if lock:
            query = query.with_for_update()
        player = self.db.scalar(query)
        if not player:
            raise NotFoundError("player", player_id)
        return player

    def create_player(self, name: str) -> Player:
        start = self.db.scalar(select(WorldNode).where(WorldNode.key == "journey_start"))
        if not start:
            raise AppError("WORLD_NOT_SEEDED", "Demo world has not been seeded", status_code=503)
        player = Player(name=name, current_node_id=start.id)
        self.db.add(player)
        self.db.flush()
        nodes = self.db.scalars(select(WorldNode)).all()
        for node in nodes:
            status = NodeStatus.ENTERED if node.id == start.id else node.default_status
            self.db.add(PlayerNodeState(player_id=player.id, node_id=node.id, status=status))
        strategic_roles = {NPCRole.STRATEGIST, NPCRole.GENERAL, NPCRole.STEWARD}
        for npc in self.db.scalars(select(NPC)).all():
            self.db.add(PlayerNPCRelationship(player_id=player.id, npc_id=npc.id))
            if npc.role in strategic_roles:
                self.db.add(
                    OfficerAppointment(
                        player_id=player.id,
                        npc_id=npc.id,
                        status="ACTIVE",
                    )
                )
        self.db.add(
            PlayerDomainState(
                player_id=player.id,
                soldiers_total=300,
                soldiers_committed=0,
                food=100,
                morale=60,
            )
        )
        initial_facts: dict[str, dict[str, object]] = {
            "valley_intelligence": {"status": "INCOMPLETE"},
            "enemy_supply_route": {"status": "UNKNOWN"},
            "valley_security": {"status": "UNSAFE"},
            "village_support": {"status": "NONE"},
            "starfire_outpost_status": {"status": "DAMAGED"},
            "northern_trade_route_status": {"status": "CLOSED"},
        }
        for key, value in initial_facts.items():
            self.db.add(PlayerWorldFact(player_id=player.id, key=key, value=value))
        self.db.flush()
        return player

    def list_nodes(self, player_id: UUID) -> list[tuple[WorldNode, PlayerNodeState]]:
        self.get_player(player_id)
        return list(
            self.db.execute(
                select(WorldNode, PlayerNodeState)
                .join(PlayerNodeState, PlayerNodeState.node_id == WorldNode.id)
                .where(PlayerNodeState.player_id == player_id)
            ).tuples()
        )

    def enter_node(self, player_id: UUID, node_id: UUID) -> Player:
        player = self.get_player(player_id, lock=True)
        state = self.db.get(PlayerNodeState, (player_id, node_id))
        if not state:
            raise NotFoundError("node", node_id)
        if state.status == NodeStatus.LOCKED:
            raise AppError("NODE_LOCKED", "Target node is locked")
        if player.current_node_id != node_id:
            edge = self.db.scalar(
                select(WorldNodeEdge).where(
                    WorldNodeEdge.source_node_id == player.current_node_id,
                    WorldNodeEdge.target_node_id == node_id,
                )
            )
            if not edge:
                raise AppError("NODE_NOT_REACHABLE", "Target node is not adjacent")
        state.status = NodeStatus.ENTERED
        state.version += 1
        player.current_node_id = node_id
        player.version += 1
        self.db.flush()
        return player

    def unlock_node(self, player_id: UUID, node_key: str) -> PlayerNodeState:
        node = self.db.scalar(select(WorldNode).where(WorldNode.key == node_key))
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

    def complete_current_node(self, player_id: UUID, node_id: UUID) -> PlayerNodeState:
        player = self.get_player(player_id, lock=True)
        if player.current_node_id != node_id:
            raise AppError("NODE_NOT_CURRENT", "Only the current node can be completed")
        state = self.db.get(PlayerNodeState, (player_id, node_id))
        if not state or state.status != NodeStatus.ENTERED:
            raise AppError("NODE_NOT_ENTERED", "Node must be entered before completion")
        state.status = NodeStatus.COMPLETED
        state.version += 1
        player.version += 1
        self.db.flush()
        return state

    def update_relationship(
        self, player_id: UUID, npc_id: UUID, delta: int, reason_code: str
    ) -> PlayerNPCRelationship:
        if reason_code not in {
            "PLAYER_HELPED_NPC",
            "PLAYER_THREATENED_NPC",
            "PLAYER_KEPT_PROMISE",
            "PLAYER_BROKE_PROMISE",
        }:
            raise AppError("INVALID_REASON_CODE", "Relationship reason is not allowed")
        rel = self.db.get(PlayerNPCRelationship, (player_id, npc_id))
        if not rel:
            raise NotFoundError("relationship", npc_id)
        rel.score = max(-100, min(100, rel.score + delta))
        rel.attitude = attitude_for(rel.score)
        rel.version += 1
        self.db.flush()
        return rel

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

    def inspect_starfire_requirements(self, player_id: UUID) -> dict[str, object]:
        player = self.get_player(player_id)
        relationship = self.db.get(PlayerNPCRelationship, (player_id, seed_id("npc:captain_aria")))
        return {
            "player_level": player.level,
            "road_safe": bool(self.get_world_fact(player_id, "starfire_road_safe").get("value")),
            "assistance_active": bool(
                self.get_world_fact(player_id, "starfire_assistance").get("value")
            ),
            "outpost_operational": bool(
                self.get_world_fact(player_id, "starfire_outpost_operational").get("value")
            ),
            "access_granted": bool(
                self.get_world_fact(player_id, "starfire_access_granted").get("value")
            ),
            "captain_relationship": relationship.score if relationship else 0,
        }

    def prepare_starfire_route(self, player_id: UUID) -> PlayerNodeState:
        template = self.db.scalar(
            select(QuestTemplate).where(QuestTemplate.key == "secure_starfire_road")
        )
        quest = (
            None
            if template is None
            else self.db.scalar(
                select(Quest).where(
                    Quest.player_id == player_id,
                    Quest.template_id == template.id,
                    Quest.status.in_([QuestStatus.AVAILABLE, QuestStatus.ACTIVE]),
                )
            )
        )
        if quest is None:
            raise AppError(
                "STARFIRE_QUEST_REQUIRED",
                "The road can only be opened after the approved quest is issued",
            )
        return self.unlock_node(player_id, "starfire_road")

    def request_starfire_assistance(self, player_id: UUID) -> PlayerWorldFact:
        return self.set_world_fact(
            player_id,
            "starfire_assistance",
            {"value": True, "source": "captain_aria"},
        )

    def restore_starfire_outpost(self, player_id: UUID) -> PlayerWorldFact:
        if not self.get_world_fact(player_id, "starfire_road_safe").get("value"):
            raise AppError(
                "STARFIRE_ROAD_UNSAFE",
                "The outpost cannot be restored until the road is verified safe",
                retryable=True,
            )
        return self.set_world_fact(
            player_id,
            "starfire_outpost_operational",
            {"value": True, "source": "verified_road_clearance"},
        )

    def grant_starfire_access(self, player_id: UUID) -> PlayerNodeState:
        if not self.get_world_fact(player_id, "starfire_outpost_operational").get("value"):
            raise AppError(
                "STARFIRE_OUTPOST_OFFLINE",
                "Access cannot be granted until the outpost is operational",
                retryable=True,
            )
        state = self.unlock_node(player_id, "starfire_outpost")
        self.set_world_fact(
            player_id,
            "starfire_access_granted",
            {"value": True, "source": "captain_aria"},
        )
        return state

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

    def preflight_recon_operation(
        self,
        *,
        player_id: UUID,
        troop_count: int,
    ) -> None:
        self._ensure_soldiers_available(player_id, troop_count, lock=False)

    def preflight_military_operation(
        self,
        *,
        player_id: UUID,
        troop_count: int,
        mission_type: str,
    ) -> None:
        if mission_type == "DISRUPT_SUPPLY":
            supply = self.get_world_fact(player_id, "enemy_supply_route").get("status")
            if supply != "ACTIVE":
                raise AppError(
                    "ENEMY_SUPPLY_ROUTE_UNKNOWN",
                    "The enemy supply route must be discovered before it can be disrupted",
                    retryable=True,
                )
        self._ensure_soldiers_available(player_id, troop_count, lock=False)

    def preflight_village_support(
        self,
        *,
        player_id: UUID,
        food_offer: int,
    ) -> None:
        if self._domain_state(player_id).food < food_offer:
            raise AppError(
                "SUPPLY_INSUFFICIENT",
                "The domain does not have enough food for this offer",
                retryable=True,
            )

    def preflight_outpost_repair(
        self,
        *,
        player_id: UUID,
        food_commitment: int,
        gold_commitment: int,
    ) -> None:
        if self.get_world_fact(player_id, "valley_security").get("status") != "SAFE":
            raise AppError(
                "VALLEY_UNSAFE",
                "The outpost cannot be repaired until the valley is safe",
                retryable=True,
            )
        player = self.get_player(player_id)
        domain = self._domain_state(player_id)
        if domain.food < food_commitment or player.gold < gold_commitment:
            raise AppError(
                "RESOURCE_INSUFFICIENT",
                "The domain lacks the resources required for repair",
                retryable=True,
            )

    def preflight_trade_route_test(self, *, player_id: UUID) -> None:
        if self.get_world_fact(player_id, "starfire_outpost_status").get("status") not in {
            "OPERATIONAL",
            "RESTORED",
        }:
            raise AppError(
                "STARFIRE_OUTPOST_OFFLINE",
                "The outpost must be operational before testing the trade route",
                retryable=True,
            )
        if self.get_world_fact(player_id, "valley_security").get("status") != "SAFE":
            raise AppError("VALLEY_UNSAFE", "The valley must be safe before testing trade")
        if self.get_world_fact(player_id, "village_support").get("status") not in {
            "GUIDE",
            "SUPPLIES",
        }:
            raise AppError(
                "TRADE_SUPPORT_REQUIRED",
                "Village support or escort capacity is required for the trade test",
                retryable=True,
            )

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
        if target_key != "valley_entrance":
            raise AppError("RECON_TARGET_INVALID", "The target cannot be reconnoitered")
        if approach not in {"CAUTIOUS", "STANDARD", "AGGRESSIVE"}:
            raise AppError("RECON_APPROACH_INVALID", "The reconnaissance approach is invalid")
        return self._start_strategic_operation(
            player_id=player_id,
            officer_npc_id=officer_npc_id,
            task_id=task_id,
            source_step_id=source_step_id,
            operation_type="RECONNAISSANCE",
            target_key=target_key,
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
        allowed_missions = {"CLEAR_VALLEY", "DISRUPT_SUPPLY", "ESCORT", "DEFEND"}
        if mission_type not in allowed_missions:
            raise AppError("MILITARY_MISSION_INVALID", "The military mission is invalid")
        if target_key not in {"ambush_valley", "enemy_north_supply_route"}:
            raise AppError("MILITARY_TARGET_INVALID", "The military target is invalid")
        if strategy not in {"CAUTIOUS", "STANDARD", "AGGRESSIVE"}:
            raise AppError("MILITARY_STRATEGY_INVALID", "The strategy is invalid")
        parameters = {
            "troop_count": troop_count,
            "mission_type": mission_type,
            "strategy": strategy,
        }
        existing = self._matching_operation(
            player_id,
            idempotency_key,
            operation_type="MILITARY",
            target_key=target_key,
            parameters=parameters,
        )
        if existing is not None:
            return existing
        if mission_type == "DISRUPT_SUPPLY":
            supply = self.get_world_fact(player_id, "enemy_supply_route").get("status")
            if supply != "ACTIVE":
                raise AppError(
                    "ENEMY_SUPPLY_ROUTE_UNKNOWN",
                    "The enemy supply route must be discovered before it can be disrupted",
                    retryable=True,
                )
        return self._start_strategic_operation(
            player_id=player_id,
            officer_npc_id=officer_npc_id,
            task_id=task_id,
            source_step_id=source_step_id,
            operation_type="MILITARY",
            target_key=target_key,
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
        if requested_support not in {"INTELLIGENCE", "GUIDE", "SUPPLIES"}:
            raise AppError("VILLAGE_SUPPORT_INVALID", "The requested village support is invalid")
        domain = self._domain_state(player_id, lock=True)
        if domain.food < food_offer:
            raise AppError(
                "SUPPLY_INSUFFICIENT",
                "The domain does not have enough food for this offer",
                retryable=True,
            )
        domain.food -= food_offer
        domain.version += 1
        support = requested_support if food_offer >= 20 else "INTELLIGENCE"
        fact = self.set_world_fact(
            player_id,
            "village_support",
            {"status": support, "food_offer": food_offer},
        )
        self.db.flush()
        return {
            "village_support": support,
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
        repair_level: str,
        food_commitment: int,
        gold_commitment: int,
        idempotency_key: str,
    ) -> WorldOperation:
        if repair_level not in {"TEMPORARY", "FULL"}:
            raise AppError("REPAIR_LEVEL_INVALID", "The repair level is invalid")
        parameters = {
            "repair_level": repair_level,
            "food_commitment": food_commitment,
            "gold_commitment": gold_commitment,
        }
        existing = self._matching_operation(
            player_id,
            idempotency_key,
            operation_type="CONSTRUCTION",
            target_key="starfire_outpost",
            parameters=parameters,
        )
        if existing is not None:
            return existing
        if self.get_world_fact(player_id, "valley_security").get("status") != "SAFE":
            raise AppError(
                "VALLEY_UNSAFE",
                "The outpost cannot be repaired until the valley is safe",
                retryable=True,
            )
        player = self.get_player(player_id, lock=True)
        domain = self._domain_state(player_id, lock=True)
        if domain.food < food_commitment or player.gold < gold_commitment:
            raise AppError(
                "RESOURCE_INSUFFICIENT",
                "The domain lacks the resources required for repair",
                retryable=True,
            )
        domain.food -= food_commitment
        domain.version += 1
        player.gold -= gold_commitment
        player.version += 1
        return self._create_operation(
            player_id=player_id,
            officer_npc_id=officer_npc_id,
            task_id=task_id,
            source_step_id=source_step_id,
            operation_type="CONSTRUCTION",
            target_key="starfire_outpost",
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
        if route_key != "northern_trade_route":
            raise AppError("TRADE_ROUTE_INVALID", "The trade route is unknown")
        existing = self._matching_operation(
            player_id,
            idempotency_key,
            operation_type="TRADE_TEST",
            target_key=route_key,
            parameters={},
        )
        if existing is not None:
            return existing
        if self.get_world_fact(player_id, "starfire_outpost_status").get("status") not in {
            "OPERATIONAL",
            "RESTORED",
        }:
            raise AppError(
                "STARFIRE_OUTPOST_OFFLINE",
                "The outpost must be operational before testing the trade route",
                retryable=True,
            )
        if self.get_world_fact(player_id, "valley_security").get("status") != "SAFE":
            raise AppError("VALLEY_UNSAFE", "The valley must be safe before testing trade")
        if self.get_world_fact(player_id, "village_support").get("status") not in {
            "GUIDE",
            "SUPPLIES",
        }:
            raise AppError(
                "TRADE_SUPPORT_REQUIRED",
                "Village support or escort capacity is required for the trade test",
                retryable=True,
            )
        return self._start_strategic_operation(
            player_id=player_id,
            officer_npc_id=officer_npc_id,
            task_id=task_id,
            source_step_id=source_step_id,
            operation_type="TRADE_TEST",
            target_key=route_key,
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
            or existing.target_key != target_key
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
        self._release_operation_troops(operation, casualties=0, morale_delta=0)
        self.set_world_fact(
            operation.player_id,
            "valley_intelligence",
            {"status": "PARTIAL", "operation_id": str(operation.id)},
        )
        self.unlock_node(operation.player_id, "ambush_valley")
        return {
            "result": "PARTIAL_SUCCESS",
            "facts_discovered": ["valley_intelligence"],
            "casualties": 0,
        }

    def _resolve_military(self, operation: WorldOperation) -> dict[str, Any]:
        mission = str(operation.parameters.get("mission_type"))
        support = self.get_world_fact(operation.player_id, "village_support").get("status")
        if mission == "DISRUPT_SUPPLY":
            casualties = 2 if support == "GUIDE" else 4
            self._release_operation_troops(operation, casualties=casualties, morale_delta=3)
            self.set_world_fact(
                operation.player_id,
                "enemy_supply_route",
                {"status": "DISRUPTED", "operation_id": str(operation.id)},
            )
            return {
                "result": "VICTORY",
                "mission_type": mission,
                "casualties": casualties,
                "facts_changed": ["enemy_supply_route"],
            }
        supply_status = self.get_world_fact(operation.player_id, "enemy_supply_route").get("status")
        if mission == "CLEAR_VALLEY" and supply_status != "DISRUPTED":
            casualties = 18
            self._release_operation_troops(operation, casualties=casualties, morale_delta=-10)
            self.set_world_fact(
                operation.player_id,
                "enemy_supply_route",
                {"status": "ACTIVE", "operation_id": str(operation.id)},
            )
            self.set_world_fact(
                operation.player_id,
                "valley_intelligence",
                {"status": "COMPLETE", "operation_id": str(operation.id)},
            )
            return {
                "result": "DEFEAT",
                "mission_type": mission,
                "failure_code": "ENCOUNTER_DEFEAT",
                "casualties": casualties,
                "facts_discovered": ["enemy_supply_route"],
            }
        casualties = 3 if support == "GUIDE" else 6
        self._release_operation_troops(operation, casualties=casualties, morale_delta=5)
        self.set_world_fact(
            operation.player_id,
            "valley_security",
            {"status": "SAFE", "operation_id": str(operation.id)},
        )
        self.set_world_fact(
            operation.player_id,
            "starfire_road_safe",
            {"value": True, "operation_id": str(operation.id)},
        )
        self.unlock_node(operation.player_id, "starfire_outpost")
        return {
            "result": "VICTORY",
            "mission_type": mission,
            "casualties": casualties,
            "facts_changed": ["valley_security"],
        }

    def _resolve_construction(self, operation: WorldOperation) -> dict[str, Any]:
        if self.get_world_fact(operation.player_id, "valley_security").get("status") != "SAFE":
            return {
                "result": "FAILED",
                "failure_code": "VALLEY_UNSAFE",
                "facts_changed": [],
            }
        repair_level = str(operation.parameters.get("repair_level"))
        status = "RESTORED" if repair_level == "FULL" else "OPERATIONAL"
        self.set_world_fact(
            operation.player_id,
            "starfire_outpost_status",
            {"status": status, "operation_id": str(operation.id)},
        )
        self.set_world_fact(
            operation.player_id,
            "starfire_outpost_operational",
            {"value": True, "operation_id": str(operation.id)},
        )
        self.unlock_node(operation.player_id, "starfire_outpost")
        return {
            "result": "COMPLETED",
            "outpost_status": status,
            "facts_changed": ["starfire_outpost_status"],
        }

    def _resolve_trade_test(self, operation: WorldOperation) -> dict[str, Any]:
        current = self.inspect_command_state(operation.player_id)
        world = current["world"]
        assert isinstance(world, dict)
        invalidated = []
        if world.get("valley_security") != "SAFE":
            invalidated.append("valley_security")
        if world.get("starfire_outpost_status") not in {"OPERATIONAL", "RESTORED"}:
            invalidated.append("starfire_outpost_status")
        if world.get("village_support") not in {"GUIDE", "SUPPLIES"}:
            invalidated.append("village_support")
        if invalidated:
            return {
                "result": "FAILED",
                "failure_code": "WORLD_STATE_CHANGED",
                "invalidated_prerequisites": invalidated,
                "facts_changed": [],
            }
        self.set_world_fact(
            operation.player_id,
            "northern_trade_route_status",
            {"status": "OPEN", "operation_id": str(operation.id)},
        )
        self.unlock_node(operation.player_id, "northern_trade_route")
        return {
            "result": "COMPLETED",
            "trade_route_status": "OPEN",
            "facts_changed": ["northern_trade_route_status"],
        }

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

    def create_quest(
        self,
        player_id: UUID,
        npc_id: UUID,
        template_key: str,
        title: str,
        description: str,
        idempotency_key: str,
    ) -> Quest:
        npc = self.db.get(NPC, npc_id)
        template = self.db.scalar(select(QuestTemplate).where(QuestTemplate.key == template_key))
        if not npc or not npc.enabled:
            raise NotFoundError("npc", npc_id)
        if not template:
            raise NotFoundError("quest_template", template_key)
        if npc.role.value not in template.allowed_roles:
            raise AuthorizationError(
                "NPC_PERMISSION_DENIED", "NPC cannot issue this quest", npc_id=npc_id
            )
        existing = self.db.scalar(
            select(Quest).where(
                Quest.player_id == player_id,
                Quest.template_id == template.id,
                Quest.status.in_(
                    [QuestStatus.AVAILABLE, QuestStatus.ACTIVE, QuestStatus.COMPLETED]
                ),
            )
        )
        if existing:
            raise ConflictError("QUEST_ALREADY_ACTIVE", "Player already has this quest")
        quest = Quest(
            template_id=template.id,
            player_id=player_id,
            issuer_npc_id=npc_id,
            idempotency_key=idempotency_key,
            narrative_title=title,
            narrative_description=description,
        )
        self.db.add(quest)
        self.db.flush()
        return quest

    def accept_quest(self, player_id: UUID, quest_id: UUID) -> Quest:
        quest = self.db.get(Quest, quest_id)
        if not quest or quest.player_id != player_id:
            raise NotFoundError("quest", quest_id)
        if quest.status == QuestStatus.AVAILABLE:
            quest.status = QuestStatus.ACTIVE
            self.db.flush()
        return quest

    def start_encounter(
        self, player_id: UUID, encounter_id: UUID, idempotency_key: str
    ) -> EncounterRun:
        player = self.get_player(player_id)
        definition = self.db.get(EncounterDefinition, encounter_id)
        if not definition:
            raise NotFoundError("encounter", encounter_id)
        if player.current_node_id != definition.node_id:
            raise AppError("ENCOUNTER_WRONG_NODE", "Player is not at encounter node")
        existing = self.db.scalar(
            select(EncounterRun).where(
                EncounterRun.player_id == player_id,
                EncounterRun.idempotency_key == idempotency_key,
            )
        )
        if existing:
            return existing
        run = EncounterRun(
            player_id=player_id,
            encounter_id=encounter_id,
            idempotency_key=idempotency_key,
            status=EncounterStatus.ACTIVE,
        )
        self.db.add(run)
        self.db.flush()
        return run

    def attempt_encounter(self, run_id: UUID, strategy: str, idempotency_key: str) -> EncounterRun:
        run = self.db.scalar(
            select(EncounterRun).where(EncounterRun.id == run_id).with_for_update()
        )
        if not run:
            raise NotFoundError("encounter_run", run_id)
        if run.status in {EncounterStatus.VICTORY, EncounterStatus.DEFEAT}:
            if run.settlement_idempotency_key == idempotency_key:
                return run
            raise ConflictError("ENCOUNTER_ALREADY_COMPLETED", "Encounter has already been settled")
        definition = run.encounter
        if strategy not in definition.allowed_strategies:
            raise AppError("INVALID_STRATEGY", "Strategy is not allowed")
        player = self.get_player(run.player_id, lock=True)
        has_talisman = bool(
            self.db.scalar(
                select(InventoryItem)
                .join(ItemDefinition)
                .where(
                    InventoryItem.player_id == player.id,
                    ItemDefinition.key == "water_talisman",
                    InventoryItem.quantity > 0,
                )
            )
        )
        strategy_bonus = {"CAUTIOUS": 2, "AGGRESSIVE": 1, "NEGOTIATE": 0}[strategy]
        assistance_bonus = 0
        if definition.key == "starfire_road_raiders":
            assistance_bonus = (
                2 if self.get_world_fact(player.id, "starfire_assistance").get("value") else 0
            )
        score = player.level + strategy_bonus + (2 if has_talisman else 0) + assistance_bonus
        victory = score >= definition.difficulty
        run.selected_strategy = strategy
        run.status = EncounterStatus.VICTORY if victory else EncounterStatus.DEFEAT
        run.result = run.status.value
        run.settlement_idempotency_key = idempotency_key
        run.completed_at = datetime.now(UTC)
        if victory:
            if definition.key == "starfire_road_raiders":
                self.set_world_fact(
                    player.id,
                    "starfire_road_safe",
                    {"value": True, "encounter_run_id": str(run.id)},
                )
            quests = self.db.scalars(
                select(Quest).where(
                    Quest.player_id == player.id,
                    Quest.status == QuestStatus.ACTIVE,
                )
            ).all()
            for quest in quests:
                if (
                    quest.template.objective_type.value == "COMPLETE_ENCOUNTER"
                    and quest.template.objective_target == definition.key
                ):
                    quest.progress = quest.template.objective_quantity
                    quest.status = QuestStatus.COMPLETED
                    quest.reward_status = RewardStatus.ELIGIBLE
                    quest.completed_at = datetime.now(UTC)
        self.db.flush()
        return run

    def claim_reward(self, player_id: UUID, quest_id: UUID) -> Quest:
        quest = self.db.scalar(
            select(Quest)
            .where(Quest.id == quest_id, Quest.player_id == player_id)
            .with_for_update()
        )
        if not quest:
            raise NotFoundError("quest", quest_id)
        if quest.reward_status == RewardStatus.CLAIMED:
            raise ConflictError("REWARD_ALREADY_CLAIMED", "Reward was already claimed")
        if quest.status != QuestStatus.COMPLETED:
            raise AppError("QUEST_NOT_COMPLETED", "Quest is not completed")
        player = self.get_player(player_id, lock=True)
        reward = quest.template.reward
        player.gold += int(reward.get("gold", 0))
        player.version += 1
        for item_key, quantity in reward.get("items", {}).items():
            definition = self.db.scalar(
                select(ItemDefinition).where(ItemDefinition.key == item_key)
            )
            if not definition:
                raise AppError("ITEM_NOT_FOUND", "Reward item is not defined")
            inventory = self.db.get(InventoryItem, (player_id, definition.id))
            if not inventory:
                inventory = InventoryItem(
                    player_id=player_id,
                    item_definition_id=definition.id,
                    quantity=0,
                    version=1,
                )
                self.db.add(inventory)
            if inventory.quantity + int(quantity) > definition.max_stack:
                raise AppError("ITEM_STACK_EXCEEDED", "Reward exceeds item stack limit")
            inventory.quantity += int(quantity)
            inventory.version = (inventory.version or 0) + 1
        unlock = reward.get("unlock_node")
        if unlock:
            self.unlock_node(player_id, str(unlock))
        quest.reward_status = RewardStatus.CLAIMED
        quest.status = QuestStatus.REWARDED
        self.db.flush()
        return quest


def seed_id(key: str) -> UUID:
    return uuid5(SEED_NAMESPACE, key)
