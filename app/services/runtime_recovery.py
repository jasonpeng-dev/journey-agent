"""Fail-closed recovery and ownership verification for a v2 Runtime graph."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import GameInstanceStatus, WorldOperationStatus
from app.domain.runtime_scope import GameInstanceId, RuntimeScope
from app.domain.scenario_v2 import NodeDefinitionV2, ResourceDefinitionV2
from app.infrastructure.db.models import (
    AgentTask,
    ConversationSession,
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceMemoryEvent,
    GameInstanceNodeState,
    GameInstanceResourceState,
    WorldOperation,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_instances import GameInstanceService


class RuntimeRecoveryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class RecoveredRuntime:
    scope: RuntimeScope
    instance: GameInstance
    actors: tuple[GameInstanceActor, ...]
    generic_memories: tuple[GameInstanceMemoryEvent, ...]
    sessions: tuple[ConversationSession, ...]
    tasks: tuple[AgentTask, ...]
    pending_operations: tuple[WorldOperation, ...]


class RuntimeRecoveryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def recover(self, game_instance_id: GameInstanceId) -> RecoveredRuntime:
        scope = GameInstanceService(self.db).load(game_instance_id)
        instance = self.db.get(GameInstance, game_instance_id)
        assert instance is not None
        if instance.status not in {GameInstanceStatus.ACTIVE, GameInstanceStatus.SUSPENDED}:
            raise RuntimeRecoveryError(
                "RUNTIME_NOT_RECOVERABLE",
                "Only active or suspended GameInstances can be recovered",
            )
        definition = ScenarioVersionRepository(self.db).load(scope.scenario_version_id).definition
        actors = tuple(
            self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == game_instance_id
                )
            ).all()
        )
        memories = tuple(
            self.db.scalars(
                select(GameInstanceMemoryEvent).where(
                    GameInstanceMemoryEvent.game_instance_id == game_instance_id
                )
            ).all()
        )
        sessions = tuple(
            self.db.scalars(
                select(ConversationSession).where(
                    ConversationSession.game_instance_id == game_instance_id
                )
            ).all()
        )
        tasks = tuple(
            self.db.scalars(
                select(AgentTask).where(AgentTask.game_instance_id == game_instance_id)
            ).all()
        )
        operations = tuple(
            self.db.scalars(
                select(WorldOperation).where(WorldOperation.game_instance_id == game_instance_id)
            ).all()
        )
        actor_keys = {actor.actor_key for actor in actors}
        session_ids = {session.id for session in sessions}
        task_ids = {task.id for task in tasks}
        if not sessions or any(
            session.player_id != scope.player_id or session.actor_key not in actor_keys
            for session in sessions
        ):
            self._corrupt("ConversationSession")
        if any(
            task.player_id != scope.player_id
            or task.origin_session_id not in session_ids
            or task.last_session_id not in session_ids
            for task in tasks
        ):
            self._corrupt("AgentTask")
        if any(
            operation.player_id != scope.player_id
            or (operation.task_id is not None and operation.task_id not in task_ids)
            for operation in operations
        ):
            self._corrupt("WorldOperation")
        self._verify_snapshot_state(
            instance,
            definition.world.nodes,
            definition.world.resources,
            {actor.key for actor in definition.actors.actor_profiles},
        )
        return RecoveredRuntime(
            scope=scope,
            instance=instance,
            actors=actors,
            generic_memories=memories,
            sessions=sessions,
            tasks=tasks,
            pending_operations=tuple(
                operation
                for operation in operations
                if operation.status == WorldOperationStatus.PENDING
            ),
        )

    def _verify_snapshot_state(
        self,
        instance: GameInstance,
        nodes: tuple[NodeDefinitionV2, ...],
        resources: tuple[ResourceDefinitionV2, ...],
        actor_keys: set[str],
    ) -> None:
        node_rows = self.db.scalars(
            select(GameInstanceNodeState).where(
                GameInstanceNodeState.game_instance_id == instance.id
            )
        ).all()
        fact_rows = self.db.scalars(
            select(GameInstanceFactState).where(
                GameInstanceFactState.game_instance_id == instance.id
            )
        ).all()
        resource_rows = self.db.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == instance.id
            )
        ).all()
        actor_rows = self.db.scalars(
            select(GameInstanceActor).where(GameInstanceActor.game_instance_id == instance.id)
        ).all()
        if (
            len(node_rows) != len(nodes)
            or len(fact_rows) != sum(len(node.facts) for node in nodes)
            or len(resource_rows) != len(resources)
            or {actor.actor_key for actor in actor_rows} != actor_keys
        ):
            self._corrupt("initialized state")

    @staticmethod
    def _corrupt(component: str) -> None:
        raise RuntimeRecoveryError(
            "RUNTIME_GRAPH_OWNERSHIP_INVALID",
            f"The recovered {component} graph crosses its GameInstance boundary",
        )


__all__ = ["RecoveredRuntime", "RuntimeRecoveryError", "RuntimeRecoveryService"]
