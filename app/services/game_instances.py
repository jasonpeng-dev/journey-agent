"""GameInstance aggregate and exact RuntimeScope resolution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import GameInstanceStatus
from app.domain.runtime_scope import (
    GameInstanceId,
    PlayerId,
    RuntimeScope,
    ScenarioVersionId,
)
from app.infrastructure.db.models import GameInstance, Player
from app.scenarios.versions import ScenarioVersionRepository


class GameInstanceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GameInstanceService:
    """Create and resolve games without any current-version fallback."""

    _TRANSITIONS: ClassVar[Mapping[GameInstanceStatus, frozenset[GameInstanceStatus]]] = {
        GameInstanceStatus.PENDING_INITIALIZATION: frozenset(
            {GameInstanceStatus.ACTIVE, GameInstanceStatus.FAILED}
        ),
        GameInstanceStatus.ACTIVE: frozenset(
            {
                GameInstanceStatus.SUSPENDED,
                GameInstanceStatus.COMPLETED,
                GameInstanceStatus.FAILED,
            }
        ),
        GameInstanceStatus.SUSPENDED: frozenset(
            {
                GameInstanceStatus.ACTIVE,
                GameInstanceStatus.COMPLETED,
                GameInstanceStatus.FAILED,
            }
        ),
        GameInstanceStatus.COMPLETED: frozenset(),
        GameInstanceStatus.FAILED: frozenset(),
    }

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, *, player_id: UUID, scenario_version_id: UUID) -> GameInstance:
        if self.db.get(Player, player_id) is None:
            raise GameInstanceError(
                "GAME_INSTANCE_PLAYER_NOT_FOUND",
                "The GameInstance Player does not exist",
            )
        ScenarioVersionRepository(self.db).load(scenario_version_id)
        instance = GameInstance(
            player_id=player_id,
            scenario_version_id=scenario_version_id,
            status=GameInstanceStatus.PENDING_INITIALIZATION,
            runtime_revision=0,
        )
        self.db.add(instance)
        self.db.flush()
        return instance

    def load(self, game_instance_id: GameInstanceId) -> RuntimeScope:
        instance = self.db.get(GameInstance, game_instance_id)
        if instance is None:
            raise GameInstanceError(
                "GAME_INSTANCE_NOT_FOUND",
                "The explicitly requested GameInstance does not exist",
            )
        if self.db.get(Player, instance.player_id) is None:
            raise GameInstanceError(
                "GAME_INSTANCE_PLAYER_BINDING_MISSING",
                "The GameInstance Player binding is missing",
            )
        ScenarioVersionRepository(self.db).load(instance.scenario_version_id)
        return RuntimeScope(
            game_instance_id=GameInstanceId(instance.id),
            player_id=PlayerId(instance.player_id),
            scenario_version_id=ScenarioVersionId(instance.scenario_version_id),
        )

    def transition(
        self,
        game_instance_id: UUID,
        *,
        expected_runtime_revision: int,
        new_status: GameInstanceStatus,
    ) -> GameInstance:
        instance = self.db.scalar(select(GameInstance).where(GameInstance.id == game_instance_id))
        if instance is None:
            raise GameInstanceError("GAME_INSTANCE_NOT_FOUND", "The GameInstance does not exist")
        if new_status not in self._TRANSITIONS[instance.status]:
            raise GameInstanceError(
                "GAME_INSTANCE_TRANSITION_INVALID",
                f"Cannot transition GameInstance from {instance.status} to {new_status}",
            )
        changed = self.db.execute(
            update(GameInstance)
            .where(
                GameInstance.id == game_instance_id,
                GameInstance.runtime_revision == expected_runtime_revision,
                GameInstance.status == instance.status,
            )
            .values(
                status=new_status,
                runtime_revision=GameInstance.runtime_revision + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if getattr(changed, "rowcount", 0) != 1:
            raise GameInstanceError(
                "GAME_INSTANCE_CONFLICT",
                "The GameInstance changed before the lifecycle transition",
            )
        self.db.flush()
        self.db.refresh(instance)
        return instance


__all__ = ["GameInstanceError", "GameInstanceService"]
