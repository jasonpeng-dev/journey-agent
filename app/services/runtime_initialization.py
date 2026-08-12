"""Transactional, idempotent Runtime initialization from an exact ScenarioVersion."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import GameInstanceStatus, NodeStatus, NPCRole
from app.domain.world import AccessState, Visibility, WorldNodeType
from app.infrastructure.db.models import (
    NPC,
    ConversationSession,
    GameInstance,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceOfficerAppointment,
    GameInstanceResourceState,
    GameInstanceWorldFact,
    Player,
)
from app.scenarios.runtime_binding import require_runtime_implementation
from app.scenarios.starfire.compatibility import legacy_fact_key
from app.scenarios.versions import ScenarioVersionRepository


class RuntimeInitializationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class InitializedRuntime:
    instance: GameInstance
    session: ConversationSession
    created: bool


class RuntimeInitializationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        player_id: UUID,
        scenario_version_id: UUID,
        creation_key: str,
    ) -> InitializedRuntime:
        """Create a complete runtime inside a savepoint owned by the caller transaction."""

        if not creation_key.strip():
            raise RuntimeInitializationError(
                "RUNTIME_CREATION_KEY_REQUIRED",
                "Runtime initialization requires a non-empty idempotency key",
            )
        existing = self._existing(player_id, creation_key)
        if existing is not None:
            return self._replay(existing, scenario_version_id)
        try:
            with self.db.begin_nested():
                runtime = self._initialize(
                    player_id=player_id,
                    scenario_version_id=scenario_version_id,
                    creation_key=creation_key,
                )
        except IntegrityError:
            self.db.expire_all()
            concurrent = self._existing(player_id, creation_key)
            if concurrent is not None:
                return self._replay(concurrent, scenario_version_id)
            raise
        except Exception:
            self.db.expire_all()
            raise
        return runtime

    def _initialize(
        self,
        *,
        player_id: UUID,
        scenario_version_id: UUID,
        creation_key: str,
    ) -> InitializedRuntime:
        if self.db.get(Player, player_id) is None:
            raise RuntimeInitializationError(
                "RUNTIME_PLAYER_NOT_FOUND",
                "The Runtime Player does not exist",
            )
        snapshot = ScenarioVersionRepository(self.db).load(scenario_version_id)
        definition = snapshot.definition
        require_runtime_implementation(definition.behavior_bundle)
        start_nodes = tuple(
            node for node in definition.world.nodes if node.node_type == WorldNodeType.HEADQUARTERS
        )
        if len(start_nodes) != 1:
            raise RuntimeInitializationError(
                "RUNTIME_START_NODE_INVALID",
                "A ScenarioVersion must define exactly one HEADQUARTERS start Node",
            )
        start = start_nodes[0]
        instance = GameInstance(
            player_id=player_id,
            scenario_version_id=scenario_version_id,
            status=GameInstanceStatus.PENDING_INITIALIZATION,
            current_node_key=start.key,
            creation_key=creation_key,
            runtime_revision=0,
        )
        self.db.add(instance)
        self.db.flush()
        for node in definition.world.nodes:
            status = (
                NodeStatus.ENTERED
                if node.key == start.key
                else (
                    NodeStatus.LOCKED
                    if node.initial_access == AccessState.LOCKED
                    else NodeStatus.AVAILABLE
                )
            )
            self.db.add(
                GameInstanceNodeState(
                    game_instance_id=instance.id,
                    node_key=node.key,
                    status=status,
                    visibility=node.initial_visibility,
                )
            )
            for fact in node.facts:
                self.db.add(
                    GameInstanceFactState(
                        game_instance_id=instance.id,
                        node_key=node.key,
                        fact_key=fact.key,
                        truth_value=fact.initial_value,
                        visibility=fact.initial_visibility,
                    )
                )
                legacy_key = legacy_fact_key(node.key, fact.key)
                if legacy_key is not None and fact.initial_visibility == Visibility.KNOWN:
                    self.db.add(
                        GameInstanceWorldFact(
                            game_instance_id=instance.id,
                            key=legacy_key,
                            value={"status": fact.initial_value},
                        )
                    )
        for resource in definition.world.resources:
            self.db.add(
                GameInstanceResourceState(
                    game_instance_id=instance.id,
                    resource_key=resource.key,
                    value=resource.initial_value,
                    reserved_value=0,
                )
            )
        officers = self.db.scalars(
            select(NPC).where(
                NPC.enabled.is_(True),
                NPC.role.in_([NPCRole.STRATEGIST, NPCRole.GENERAL, NPCRole.STEWARD]),
            )
        ).all()
        strategist = next(
            (officer for officer in officers if officer.role == NPCRole.STRATEGIST),
            None,
        )
        if strategist is None:
            raise RuntimeInitializationError(
                "RUNTIME_STRATEGIST_UNAVAILABLE",
                "Runtime initialization requires an enabled Strategist",
            )
        for officer in officers:
            self.db.add(
                GameInstanceOfficerAppointment(
                    game_instance_id=instance.id,
                    npc_id=officer.id,
                )
            )
        conversation = ConversationSession(
            player_id=player_id,
            game_instance_id=instance.id,
            npc_id=strategist.id,
        )
        self.db.add(conversation)
        instance.status = GameInstanceStatus.ACTIVE
        instance.runtime_revision = 1
        self.db.flush()
        return InitializedRuntime(instance=instance, session=conversation, created=True)

    def _existing(self, player_id: UUID, creation_key: str) -> GameInstance | None:
        return self.db.scalar(
            select(GameInstance).where(
                GameInstance.player_id == player_id,
                GameInstance.creation_key == creation_key,
            )
        )

    def _replay(
        self,
        instance: GameInstance,
        requested_version_id: UUID,
    ) -> InitializedRuntime:
        if instance.scenario_version_id != requested_version_id:
            raise RuntimeInitializationError(
                "RUNTIME_CREATION_KEY_REUSED",
                "The creation key is already bound to another ScenarioVersion",
            )
        if instance.status != GameInstanceStatus.ACTIVE:
            raise RuntimeInitializationError(
                "RUNTIME_INITIALIZATION_INCOMPLETE",
                "The idempotent GameInstance is not fully initialized",
            )
        snapshot = ScenarioVersionRepository(self.db).load(instance.scenario_version_id)
        require_runtime_implementation(snapshot.definition.behavior_bundle)
        conversations = self.db.scalars(
            select(ConversationSession).where(ConversationSession.game_instance_id == instance.id)
        ).all()
        expected_nodes = len(snapshot.definition.world.nodes)
        expected_facts = sum(len(node.facts) for node in snapshot.definition.world.nodes)
        expected_resources = len(snapshot.definition.world.resources)
        actual_nodes = self.db.scalar(
            select(func.count())
            .select_from(GameInstanceNodeState)
            .where(GameInstanceNodeState.game_instance_id == instance.id)
        )
        actual_facts = self.db.scalar(
            select(func.count())
            .select_from(GameInstanceFactState)
            .where(GameInstanceFactState.game_instance_id == instance.id)
        )
        actual_resources = self.db.scalar(
            select(func.count())
            .select_from(GameInstanceResourceState)
            .where(GameInstanceResourceState.game_instance_id == instance.id)
        )
        actual_officers = self.db.scalar(
            select(func.count())
            .select_from(GameInstanceOfficerAppointment)
            .where(GameInstanceOfficerAppointment.game_instance_id == instance.id)
        )
        if (
            len(conversations) != 1
            or actual_nodes != expected_nodes
            or actual_facts != expected_facts
            or actual_resources != expected_resources
            or not actual_officers
        ):
            raise RuntimeInitializationError(
                "RUNTIME_INITIALIZATION_INCOMPLETE",
                "The idempotent GameInstance runtime graph is incomplete",
            )
        return InitializedRuntime(
            instance=instance,
            session=conversations[0],
            created=False,
        )


__all__ = [
    "InitializedRuntime",
    "RuntimeInitializationError",
    "RuntimeInitializationService",
]
