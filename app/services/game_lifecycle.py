"""Browser-product Game lifecycle over the exact-Version Runtime services."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.enums import (
    AgentTaskStatus,
    DecisionStatus,
    GameInstanceStatus,
    WorldOperationStatus,
)
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentPlan,
    AgentStep,
    AgentTask,
    ConversationMessage,
    ConversationSession,
    GameInstance,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceMemoryEvent,
    GameInstanceNodeState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceRelationKnowledge,
    GameInstanceResourceState,
    PlanningAttempt,
    PlanningCycle,
    Player,
    PlayerExecutionCheckpoint,
    Scenario,
    ScenarioVersion,
    WorldOperation,
)
from app.services.runtime_initialization import InitializedRuntime, RuntimeInitializationService

PLATFORM_PLAYER_ID = uuid5(NAMESPACE_URL, "journey-agent:phase-d:platform-player")


class GameLifecycleError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GameLifecycleService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def platform_player(self) -> Player:
        player = self.db.get(Player, PLATFORM_PLAYER_ID)
        if player is None:
            player = Player(id=PLATFORM_PLAYER_ID, name="Browser Player")
            self.db.add(player)
            self.db.flush()
        return player

    def create(self, *, scenario_version_id: UUID, idempotency_key: str) -> InitializedRuntime:
        version = self.db.get(ScenarioVersion, scenario_version_id)
        if version is None:
            raise GameLifecycleError(
                "SCENARIO_VERSION_NOT_FOUND", "The published ScenarioVersion does not exist"
            )
        scenario = self.db.get(Scenario, version.scenario_id)
        if scenario is None or scenario.status == "ARCHIVED":
            raise GameLifecycleError(
                "SCENARIO_ARCHIVED", "An archived Scenario cannot start a new Game"
            )
        player = self.platform_player()
        return RuntimeInitializationService(self.db).create(
            player_id=player.id,
            scenario_version_id=version.id,
            creation_key=idempotency_key,
        )

    def list(self, *, archived: bool) -> tuple[GameInstance, ...]:
        player = self.platform_player()
        query = select(GameInstance).where(GameInstance.player_id == player.id)
        query = query.where(
            GameInstance.status == GameInstanceStatus.ARCHIVED
            if archived
            else GameInstance.status != GameInstanceStatus.ARCHIVED
        )
        return tuple(self.db.scalars(query.order_by(GameInstance.updated_at.desc())))

    def get(self, game_instance_id: UUID) -> GameInstance:
        player = self.platform_player()
        instance = self.db.get(GameInstance, game_instance_id)
        if instance is None or instance.player_id != player.id:
            raise GameLifecycleError("GAME_INSTANCE_NOT_FOUND", "The Game does not exist")
        return instance

    def archive(self, game_instance_id: UUID, *, expected_runtime_revision: int) -> GameInstance:
        instance = self._locked_owned_instance(game_instance_id)
        if instance.status != GameInstanceStatus.ACTIVE:
            raise GameLifecycleError(
                "GAME_INSTANCE_TRANSITION_INVALID", "Only an active Game can archive"
            )
        if instance.runtime_revision != expected_runtime_revision:
            raise GameLifecycleError(
                "GAME_INSTANCE_CONFLICT",
                "The GameInstance changed before the lifecycle transition",
            )
        self.assert_stable_point(instance)
        instance.status = GameInstanceStatus.ARCHIVED
        instance.runtime_revision += 1
        self.db.flush()
        return instance

    def delete(self, game_instance_id: UUID) -> UUID:
        """Permanently remove one owned Game and every instance-scoped row atomically."""
        player = self.platform_player()
        instance = self.db.scalar(
            select(GameInstance)
            .where(
                GameInstance.id == game_instance_id,
                GameInstance.player_id == player.id,
            )
            .with_for_update()
        )
        if instance is None:
            raise GameLifecycleError("GAME_INSTANCE_NOT_FOUND", "The Game does not exist")
        deleted_id = instance.id
        task_ids = tuple(
            self.db.scalars(select(AgentTask.id).where(AgentTask.game_instance_id == deleted_id))
        )
        session_ids = tuple(
            self.db.scalars(
                select(ConversationSession.id).where(
                    ConversationSession.game_instance_id == deleted_id
                )
            )
        )
        plan_ids = (
            tuple(self.db.scalars(select(AgentPlan.id).where(AgentPlan.task_id.in_(task_ids))))
            if task_ids
            else ()
        )
        self.db.execute(
            sql_delete(ActionDecisionRequest).where(
                ActionDecisionRequest.game_instance_id == deleted_id
            )
        )
        self.db.execute(
            sql_delete(PlayerExecutionCheckpoint).where(
                PlayerExecutionCheckpoint.game_instance_id == deleted_id
            )
        )
        self.db.execute(
            sql_delete(WorldOperation).where(WorldOperation.game_instance_id == deleted_id)
        )
        if plan_ids:
            self.db.execute(sql_delete(AgentStep).where(AgentStep.plan_id.in_(plan_ids)))
            self.db.execute(sql_delete(AgentPlan).where(AgentPlan.id.in_(plan_ids)))
        if task_ids:
            self.db.execute(sql_delete(AgentTask).where(AgentTask.id.in_(task_ids)))
        if session_ids:
            self.db.execute(
                sql_delete(ConversationMessage).where(
                    ConversationMessage.session_id.in_(session_ids)
                )
            )
            self.db.execute(
                sql_delete(ConversationSession).where(ConversationSession.id.in_(session_ids))
            )
        for model in (
            GameInstanceMemoryEvent,
            GameInstanceActor,
            GameInstanceResourceState,
            GameInstanceRegionResourceKnowledge,
            GameInstanceRelationKnowledge,
            GameInstanceFactState,
            GameInstanceNodeState,
        ):
            self.db.execute(sql_delete(model).where(model.game_instance_id == deleted_id))
        self.db.execute(
            update(GameInstance)
            .where(GameInstance.forked_from_game_instance_id == deleted_id)
            .values(forked_from_game_instance_id=None)
        )
        self.db.execute(sql_delete(GameInstance).where(GameInstance.id == deleted_id))
        self.db.flush()
        return deleted_id

    def abandon_task(self, game_instance_id: UUID, task_id: UUID) -> AgentTask:
        instance = self._locked_owned_instance(game_instance_id)
        require_active_instance(instance)
        task = self.db.get(AgentTask, task_id)
        if task is None or task.game_instance_id != instance.id:
            raise GameLifecycleError(
                "AGENT_TASK_NOT_FOUND", "The Task does not belong to this Game"
            )
        if task.status not in _NON_TERMINAL_TASK_STATUSES:
            raise GameLifecycleError("AGENT_TASK_NOT_ACTIVE", "The Task is already terminal")
        self._cancel_pending(instance.id, abort_task=False, task_id=task.id)
        task.status = AgentTaskStatus.ABORTED
        task.completed_at = datetime.now(UTC)
        self.db.flush()
        return task

    def _locked_owned_instance(self, game_instance_id: UUID) -> GameInstance:
        player = self.platform_player()
        instance = self.db.scalar(
            select(GameInstance)
            .where(
                GameInstance.id == game_instance_id,
                GameInstance.player_id == player.id,
            )
            .with_for_update()
        )
        if instance is None:
            raise GameLifecycleError("GAME_INSTANCE_NOT_FOUND", "The Game does not exist")
        return instance

    def _has_non_terminal_task(self, game_instance_id: UUID) -> bool:
        return (
            self.db.scalar(
                select(func.count())
                .select_from(AgentTask)
                .where(
                    AgentTask.game_instance_id == game_instance_id,
                    AgentTask.status.in_(_NON_TERMINAL_TASK_STATUSES),
                )
            )
            or 0
        ) > 0

    def _has_pending_operation(self, game_instance_id: UUID) -> bool:
        return (
            self.db.scalar(
                select(func.count())
                .select_from(WorldOperation)
                .where(
                    WorldOperation.game_instance_id == game_instance_id,
                    WorldOperation.status == WorldOperationStatus.PENDING,
                )
            )
            or 0
        ) > 0

    def _has_pending_decision(self, game_instance_id: UUID) -> bool:
        return (
            self.db.scalar(
                select(func.count())
                .select_from(ActionDecisionRequest)
                .where(
                    ActionDecisionRequest.game_instance_id == game_instance_id,
                    ActionDecisionRequest.status == DecisionStatus.PENDING,
                )
            )
            or 0
        ) > 0

    def _has_reservation(self, game_instance_id: UUID) -> bool:
        return (
            self.db.scalar(
                select(func.count())
                .select_from(GameInstanceResourceState)
                .where(
                    GameInstanceResourceState.game_instance_id == game_instance_id,
                    GameInstanceResourceState.reserved_value != 0,
                )
            )
            or 0
        ) > 0

    def _has_running_planning_cycle(self, game_instance_id: UUID) -> bool:
        return (
            self.db.scalar(
                select(func.count())
                .select_from(PlanningCycle)
                .where(
                    PlanningCycle.game_instance_id == game_instance_id,
                    PlanningCycle.status == "RUNNING",
                )
            )
            or 0
        ) > 0

    def _has_running_planning_attempt(self, game_instance_id: UUID) -> bool:
        return (
            self.db.scalar(
                select(func.count())
                .select_from(PlanningAttempt)
                .where(
                    PlanningAttempt.task_id.in_(
                        select(AgentTask.id).where(AgentTask.game_instance_id == game_instance_id)
                    ),
                    PlanningAttempt.status == "RUNNING",
                )
            )
            or 0
        ) > 0

    def assert_stable_point(self, instance: GameInstance) -> None:
        if self._has_non_terminal_task(instance.id):
            raise GameLifecycleError(
                "GAME_INSTANCE_ARCHIVE_TASK_ACTIVE",
                "The Game has an active Task and cannot archive",
            )
        if self._has_pending_operation(instance.id):
            raise GameLifecycleError(
                "GAME_INSTANCE_ARCHIVE_OPERATION_PENDING",
                "The Game has a pending WorldOperation and cannot archive",
            )
        if self._has_pending_decision(instance.id):
            raise GameLifecycleError(
                "GAME_INSTANCE_ARCHIVE_DECISION_PENDING",
                "The Game has a pending ActionDecisionRequest and cannot archive",
            )
        if self._has_reservation(instance.id):
            raise GameLifecycleError(
                "GAME_INSTANCE_ARCHIVE_RESERVATION_ACTIVE",
                "The Game has reserved resources and cannot archive",
            )
        if self._has_running_planning_cycle(instance.id) or self._has_running_planning_attempt(
            instance.id
        ):
            raise GameLifecycleError(
                "GAME_INSTANCE_ARCHIVE_PLANNING_IN_FLIGHT",
                "The Game has an in-flight planning cycle and cannot materialize",
            )

    def _cancel_pending(
        self,
        game_instance_id: UUID,
        *,
        abort_task: bool,
        task_id: UUID | None = None,
    ) -> None:
        for decision in self.db.scalars(
            select(ActionDecisionRequest).where(
                ActionDecisionRequest.game_instance_id == game_instance_id,
                ActionDecisionRequest.status == DecisionStatus.PENDING,
            )
        ):
            if task_id is None or decision.task_id == task_id:
                decision.status = DecisionStatus.CANCELLED
                decision.decided_at = datetime.now(UTC)
        for operation in self.db.scalars(
            select(WorldOperation).where(
                WorldOperation.game_instance_id == game_instance_id,
                WorldOperation.status == WorldOperationStatus.PENDING,
            )
        ):
            if task_id is None or operation.task_id == task_id:
                operation.status = WorldOperationStatus.CANCELLED
                operation.resolved_at = datetime.now(UTC)
        if abort_task:
            for task in self.db.scalars(
                select(AgentTask).where(
                    AgentTask.game_instance_id == game_instance_id,
                    AgentTask.status.in_(_NON_TERMINAL_TASK_STATUSES),
                )
            ):
                task.status = AgentTaskStatus.ABORTED
                task.completed_at = datetime.now(UTC)


_NON_TERMINAL_TASK_STATUSES = (
    AgentTaskStatus.ACTIVE,
    AgentTaskStatus.REQUIRES_PLAYER_DECISION,
    AgentTaskStatus.WAITING_FOR_PLAYER_ACTION,
    AgentTaskStatus.WAITING_FOR_WORLD_EVENT,
)


def require_active_instance(instance: GameInstance) -> None:
    if instance.status != GameInstanceStatus.ACTIVE:
        raise GameLifecycleError(
            "GAME_INSTANCE_READ_ONLY",
            "Only an active GameInstance may mutate Runtime state",
        )


def require_scope_writable(db: Session, game_instance_id: UUID) -> None:
    with db.no_autoflush:
        instance = db.scalar(
            select(GameInstance).where(GameInstance.id == game_instance_id).with_for_update()
        )
    if instance is None:
        raise GameLifecycleError("GAME_INSTANCE_NOT_FOUND", "The Game does not exist")
    require_active_instance(instance)


__all__ = [
    "PLATFORM_PLAYER_ID",
    "GameLifecycleError",
    "GameLifecycleService",
    "require_active_instance",
    "require_scope_writable",
]
