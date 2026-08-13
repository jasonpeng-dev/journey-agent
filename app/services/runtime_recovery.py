"""Fail-closed recovery and ownership verification for an existing Runtime graph."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import GameInstanceStatus, WorldOperationStatus
from app.domain.runtime_scope import GameInstanceId, RuntimeScope
from app.domain.scenario import ScenarioDefinition
from app.domain.scenario_v2 import (
    NodeDefinitionV2,
    ResourceDefinitionV2,
    ScenarioDefinitionV2,
)
from app.domain.world import NodeDefinition, ResourceDefinition
from app.infrastructure.db.models import (
    AgentTask,
    ConversationSession,
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceOfficerAppointment,
    GameInstanceResourceState,
    Memory,
    PlayerDecisionRequest,
    WorldOperation,
)
from app.scenarios.runtime_binding import require_runtime_implementation
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
    sessions: tuple[ConversationSession, ...]
    tasks: tuple[AgentTask, ...]
    memories: tuple[Memory, ...]
    decisions: tuple[PlayerDecisionRequest, ...]
    pending_operations: tuple[WorldOperation, ...]


class RuntimeRecoveryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def recover(self, game_instance_id: GameInstanceId) -> RecoveredRuntime:
        scope = GameInstanceService(self.db).load(game_instance_id)
        instance = self.db.get(GameInstance, game_instance_id)
        assert instance is not None
        if instance.status not in {
            GameInstanceStatus.ACTIVE,
            GameInstanceStatus.SUSPENDED,
        }:
            raise RuntimeRecoveryError(
                "RUNTIME_NOT_RECOVERABLE",
                "Only active or suspended GameInstances can be recovered",
            )
        snapshot = ScenarioVersionRepository(self.db).load(scope.scenario_version_id)
        definition = snapshot.definition
        if isinstance(definition, ScenarioDefinition):
            require_runtime_implementation(definition.behavior_bundle)
        actors = tuple(
            self.db.scalars(
                select(GameInstanceActor).where(
                    GameInstanceActor.game_instance_id == game_instance_id
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
        memories = tuple(
            self.db.scalars(select(Memory).where(Memory.game_instance_id == game_instance_id)).all()
        )
        operations = tuple(
            self.db.scalars(
                select(WorldOperation).where(WorldOperation.game_instance_id == game_instance_id)
            ).all()
        )
        decisions = tuple(
            self.db.scalars(
                select(PlayerDecisionRequest).where(
                    PlayerDecisionRequest.game_instance_id == game_instance_id
                )
            ).all()
        )
        task_ids = {task.id for task in tasks}
        session_ids = {session.id for session in sessions}
        if not sessions or any(session.player_id != scope.player_id for session in sessions):
            self._corrupt("ConversationSession")
        if isinstance(definition, ScenarioDefinitionV2):
            actor_keys = {actor.actor_key for actor in actors}
            if any(
                session.actor_key not in actor_keys or session.npc_id is not None
                for session in sessions
            ):
                self._corrupt("ConversationSession Actor")
        if any(
            task.player_id != scope.player_id
            or task.origin_session_id not in session_ids
            or task.last_session_id not in session_ids
            for task in tasks
        ):
            self._corrupt("AgentTask")
        if any(
            memory.player_id != scope.player_id
            or (
                memory.source_session_id is not None and memory.source_session_id not in session_ids
            )
            for memory in memories
        ):
            self._corrupt("Memory")
        if any(
            operation.player_id != scope.player_id
            or (operation.task_id is not None and operation.task_id not in task_ids)
            for operation in operations
        ):
            self._corrupt("WorldOperation")
        if any(
            decision.player_id != scope.player_id or decision.task_id not in task_ids
            for decision in decisions
        ):
            self._corrupt("PlayerDecisionRequest")
        self._verify_snapshot_state(
            instance,
            definition.world.nodes,
            definition.world.resources,
            expected_actor_keys=(
                {actor.key for actor in definition.actors.actor_profiles}
                if isinstance(definition, ScenarioDefinitionV2)
                else None
            ),
        )
        return RecoveredRuntime(
            scope=scope,
            instance=instance,
            actors=actors,
            sessions=sessions,
            tasks=tasks,
            memories=memories,
            decisions=decisions,
            pending_operations=tuple(
                operation
                for operation in operations
                if operation.status == WorldOperationStatus.PENDING
            ),
        )

    def _verify_snapshot_state(
        self,
        instance: GameInstance,
        nodes: tuple[NodeDefinition, ...] | tuple[NodeDefinitionV2, ...],
        resources: tuple[ResourceDefinition, ...] | tuple[ResourceDefinitionV2, ...],
        *,
        expected_actor_keys: set[str] | None,
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
        officer_rows = self.db.scalars(
            select(GameInstanceOfficerAppointment).where(
                GameInstanceOfficerAppointment.game_instance_id == instance.id
            )
        ).all()
        actor_rows = self.db.scalars(
            select(GameInstanceActor).where(GameInstanceActor.game_instance_id == instance.id)
        ).all()
        expected_facts = sum(len(node.facts) for node in nodes)
        if (
            len(node_rows) != len(nodes)
            or len(fact_rows) != expected_facts
            or len(resource_rows) != len(resources)
            or (expected_actor_keys is None and not officer_rows)
            or (
                expected_actor_keys is not None
                and {actor.actor_key for actor in actor_rows} != expected_actor_keys
            )
        ):
            self._corrupt("initialized state")

    @staticmethod
    def _corrupt(component: str) -> None:
        raise RuntimeRecoveryError(
            "RUNTIME_GRAPH_OWNERSHIP_INVALID",
            f"The recovered {component} graph crosses its GameInstance boundary",
        )


__all__ = ["RecoveredRuntime", "RuntimeRecoveryError", "RuntimeRecoveryService"]
