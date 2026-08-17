"""Transactional, idempotent Runtime initialization from an exact v2 ScenarioVersion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import GameInstanceStatus, NodeStatus
from app.domain.resources import (
    resource_identity,
    resource_initial_states,
    valid_resource_state_identity,
)
from app.domain.world import AccessState
from app.infrastructure.db.models import (
    ConversationSession,
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceResourceState,
    Player,
)
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
                "RUNTIME_PLAYER_NOT_FOUND", "The Runtime Player does not exist"
            )
        definition = ScenarioVersionRepository(self.db).load(scenario_version_id).definition
        start_key = definition.initialization.start_node_key
        instance = GameInstance(
            player_id=player_id,
            scenario_version_id=scenario_version_id,
            status=GameInstanceStatus.PENDING_INITIALIZATION,
            current_node_key=start_key,
            creation_key=creation_key,
            runtime_revision=0,
        )
        self.db.add(instance)
        self.db.flush()
        for node in definition.world.nodes:
            status = (
                NodeStatus.ENTERED
                if node.key == start_key
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
        resource_states = resource_initial_states(definition)
        if self._supports_scoped_resource_schema():
            for resource_state in resource_states:
                self.db.add(
                    GameInstanceResourceState(
                        game_instance_id=instance.id,
                        resource_identity=resource_identity(
                            resource_state.resource_key,
                            resource_state.scope_node_key,
                        ),
                        resource_key=resource_state.resource_key,
                        scope_node_key=resource_state.scope_node_key,
                        value=resource_state.value,
                        reserved_value=resource_state.reserved_value,
                    )
                )
        else:
            if any(item.scope_node_key is not None for item in resource_states):
                raise RuntimeInitializationError(
                    "RUNTIME_SCOPED_RESOURCE_SCHEMA_REQUIRED",
                    "This database must be upgraded before a scoped Resource Scenario can start",
                )
            now = datetime.now(UTC)
            for resource_state in resource_states:
                self.db.execute(
                    text(
                        """
                        INSERT INTO game_instance_resource_states
                            (game_instance_id, resource_key, value, reserved_value,
                             version, created_at, updated_at)
                        VALUES (:game_instance_id, :resource_key, :value, :reserved_value,
                                :version, :created_at, :updated_at)
                        """
                    ),
                    {
                        "game_instance_id": instance.id.hex,
                        "resource_key": resource_state.resource_key,
                        "value": resource_state.value,
                        "reserved_value": resource_state.reserved_value,
                        "version": 1,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
        roles = {role.key: role for role in definition.actors.roles}
        primary_key = definition.initialization.primary_actor_key
        for actor in definition.actors.actor_profiles:
            role = roles[actor.role_key]
            self.db.add(
                GameInstanceActor(
                    game_instance_id=instance.id,
                    actor_key=actor.key,
                    role_key=actor.role_key,
                    name=actor.name,
                    persona=actor.persona,
                    doctrine={item.key: item.value for item in actor.doctrine},
                    current_node_key=actor.initial_node_key,
                    allowed_action_keys=list(actor.allowed_action_keys),
                    authority_policy=actor.authority_policy.model_dump(mode="json"),
                    capabilities=[capability.value for capability in role.capabilities],
                    is_primary=actor.key == primary_key,
                )
            )
        session = ConversationSession(
            player_id=player_id,
            game_instance_id=instance.id,
            actor_key=primary_key,
        )
        self.db.add(session)
        instance.status = GameInstanceStatus.ACTIVE
        instance.runtime_revision = 1
        self.db.flush()
        return InitializedRuntime(instance=instance, session=session, created=True)

    def _existing(self, player_id: UUID, creation_key: str) -> GameInstance | None:
        return self.db.scalar(
            select(GameInstance).where(
                GameInstance.player_id == player_id,
                GameInstance.creation_key == creation_key,
            )
        )

    def _supports_scoped_resource_schema(self) -> bool:
        # Use the Session's active connection.  Inspecting the Engine would
        # borrow a second SQLite connection; with StaticPool that connection
        # is the same handle and its inspector rollback can invalidate the
        # initialization savepoint.
        columns = inspect(self.db.connection()).get_columns("game_instance_resource_states")
        return "resource_identity" in {str(item["name"]) for item in columns}

    def _replay(self, instance: GameInstance, requested_version_id: UUID) -> InitializedRuntime:
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
        definition = (
            ScenarioVersionRepository(self.db).load(instance.scenario_version_id).definition
        )
        sessions = self.db.scalars(
            select(ConversationSession).where(ConversationSession.game_instance_id == instance.id)
        ).all()
        resource_rows = self.db.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == instance.id
            )
        ).all()
        counts = tuple(
            int(value or 0)
            for value in (
                self.db.scalar(
                    select(func.count())
                    .select_from(GameInstanceNodeState)
                    .where(GameInstanceNodeState.game_instance_id == instance.id)
                ),
                self.db.scalar(
                    select(func.count())
                    .select_from(GameInstanceFactState)
                    .where(GameInstanceFactState.game_instance_id == instance.id)
                ),
                self.db.scalar(
                    select(func.count())
                    .select_from(GameInstanceActor)
                    .where(GameInstanceActor.game_instance_id == instance.id)
                ),
            )
        )
        expected = (
            len(definition.world.nodes),
            sum(len(node.facts) for node in definition.world.nodes),
            len(definition.actors.actor_profiles),
        )
        expected_resources = {
            (item.resource_key, item.scope_node_key)
            for item in resource_initial_states(definition)
        }
        actual_resources = {(row.resource_key, row.scope_node_key) for row in resource_rows}
        resources_valid = expected_resources.issubset(actual_resources) and all(
            valid_resource_state_identity(
                definition,
                row.resource_key,
                row.scope_node_key,
            )
            for row in resource_rows
        )
        if len(sessions) != 1 or counts != expected or not resources_valid:
            raise RuntimeInitializationError(
                "RUNTIME_INITIALIZATION_INCOMPLETE",
                "The idempotent GameInstance runtime graph is incomplete",
            )
        return InitializedRuntime(instance=instance, session=sessions[0], created=False)


__all__ = ["InitializedRuntime", "RuntimeInitializationError", "RuntimeInitializationService"]
