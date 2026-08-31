"""Checkpoint materialization from one stable ACTIVE GameInstance."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import GameInstanceStatus
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import ConversationSession, GameInstance
from app.scenarios.versions import ScenarioVersionError, ScenarioVersionRepository
from app.services.game_lifecycle import GameLifecycleError, GameLifecycleService
from app.services.game_materialization import GameMaterializer, MaterializationError


class GameCheckpointError(GameLifecycleError):
    """A checkpoint request failed its immutable/stable-point contract."""


@dataclass(frozen=True, slots=True)
class CheckpointedRuntime:
    instance: GameInstance
    session: ConversationSession
    created: bool


class GameCheckpointService:
    """Create an independent ARCHIVED snapshot without mutating its source."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.lifecycle = GameLifecycleService(db)
        self.materializer = GameMaterializer(db)

    def materialize(
        self,
        *,
        source_game_instance_id: UUID,
        player_id: UUID,
        expected_runtime_revision: int,
        creation_key: str,
    ) -> CheckpointedRuntime:
        if not creation_key.strip():
            raise GameCheckpointError(
                "CHECKPOINT_CREATION_KEY_REQUIRED",
                "Checkpoint creation requires a non-empty idempotency key",
            )
        source = self.db.scalar(
            select(GameInstance)
            .where(
                GameInstance.id == source_game_instance_id,
                GameInstance.player_id == player_id,
            )
            .with_for_update()
        )
        if source is None:
            raise GameCheckpointError("GAME_INSTANCE_NOT_FOUND", "The source Game does not exist")
        if source.status != GameInstanceStatus.ACTIVE:
            raise GameCheckpointError(
                "CHECKPOINT_SOURCE_NOT_ACTIVE",
                "Only an active GameInstance can create a checkpoint",
            )
        if source.runtime_revision != expected_runtime_revision:
            raise GameCheckpointError(
                "GAME_INSTANCE_CONFLICT",
                "The GameInstance changed before checkpoint materialization",
            )
        self.lifecycle.assert_stable_point(source)

        existing = self._existing(player_id, creation_key)
        if existing is not None:
            return self._replay(existing, source)

        existing_revision = self.db.scalar(
            select(GameInstance)
            .where(
                GameInstance.checkpointed_from_game_instance_id == source.id,
                GameInstance.checkpoint_source_runtime_revision == source.runtime_revision,
            )
            .with_for_update()
        )
        if existing_revision is not None:
            if existing_revision.creation_key == creation_key:
                return self._replay(existing_revision, source)
            raise GameCheckpointError(
                "CHECKPOINT_ALREADY_EXISTS",
                "A checkpoint already exists for this source runtime revision",
            )

        try:
            version = ScenarioVersionRepository(self.db).load(source.scenario_version_id)
            definition = version.definition
            if not isinstance(definition, ScenarioDefinitionV2):
                raise GameCheckpointError(
                    "CHECKPOINT_SCENARIO_VERSION_INVALID",
                    "Checkpoint requires a v2 ScenarioVersion runtime definition",
                )
            runtime = self.materializer.load_runtime(source, definition)
            with self.db.begin_nested():
                target = GameInstance(
                    player_id=source.player_id,
                    scenario_version_id=source.scenario_version_id,
                    forked_from_game_instance_id=None,
                    checkpointed_from_game_instance_id=source.id,
                    checkpoint_source_runtime_revision=source.runtime_revision,
                    inherited_task_count=0,
                    status=GameInstanceStatus.PENDING_INITIALIZATION,
                    current_node_key=source.current_node_key,
                    creation_key=creation_key,
                    runtime_revision=0,
                )
                self.db.add(target)
                self.db.flush()
                self.materializer.copy_runtime(
                    source=source,
                    target=target,
                    snapshot=runtime,
                    definition=definition,
                )
                history = self.materializer.copy_history(source=source, target=target)
                # A Checkpoint is an archived snapshot, not a new playable branch.
                # Its copied tasks remain visible as history; the later Fork starts
                # its own boundary from the checkpoint's complete task history.
                target.inherited_task_count = 0
                target.status = GameInstanceStatus.ARCHIVED
                target.runtime_revision = 1
                self.db.flush()
        except IntegrityError as exc:
            self.db.expire_all()
            concurrent = self._existing(player_id, creation_key)
            if concurrent is not None:
                source = self.db.get(GameInstance, source_game_instance_id)
                if source is not None:
                    return self._replay(concurrent, source)
            existing_revision = self.db.scalar(
                select(GameInstance).where(
                    GameInstance.checkpointed_from_game_instance_id == source_game_instance_id,
                    GameInstance.checkpoint_source_runtime_revision == expected_runtime_revision,
                )
            )
            if existing_revision is not None:
                raise GameCheckpointError(
                    "CHECKPOINT_ALREADY_EXISTS",
                    "A checkpoint already exists for this source runtime revision",
                ) from exc
            raise GameCheckpointError(
                "CHECKPOINT_MATERIALIZATION_FAILED",
                "The checkpoint runtime graph could not be materialized",
            ) from exc
        except (MaterializationError, ScenarioVersionError) as exc:
            self.db.expire_all()
            if isinstance(exc, MaterializationError):
                raise GameCheckpointError(exc.code, exc.message) from exc
            raise GameCheckpointError(exc.code, exc.message) from exc
        except GameCheckpointError:
            self.db.expire_all()
            raise
        return CheckpointedRuntime(instance=target, session=history.session, created=True)

    def _existing(self, player_id: UUID, creation_key: str) -> GameInstance | None:
        return self.db.scalar(
            select(GameInstance)
            .where(
                GameInstance.player_id == player_id,
                GameInstance.creation_key == creation_key,
            )
            .with_for_update()
        )

    def _replay(self, target: GameInstance, source: GameInstance) -> CheckpointedRuntime:
        if (
            target.checkpointed_from_game_instance_id != source.id
            or target.checkpoint_source_runtime_revision != source.runtime_revision
            or target.scenario_version_id != source.scenario_version_id
        ):
            raise GameCheckpointError(
                "CHECKPOINT_CREATION_KEY_REUSED",
                "The creation key is already bound to another Checkpoint source",
            )
        if target.status != GameInstanceStatus.ARCHIVED:
            raise GameCheckpointError(
                "CHECKPOINT_INITIALIZATION_INCOMPLETE",
                "The idempotent Checkpoint target is not fully materialized",
            )
        session = self.db.scalar(
            select(ConversationSession)
            .where(ConversationSession.game_instance_id == target.id)
            .order_by(ConversationSession.created_at, ConversationSession.id)
        )
        if session is None:
            raise GameCheckpointError(
                "CHECKPOINT_INITIALIZATION_INCOMPLETE",
                "The idempotent Checkpoint target has no ConversationSession",
            )
        return CheckpointedRuntime(instance=target, session=session, created=False)


__all__ = ["CheckpointedRuntime", "GameCheckpointError", "GameCheckpointService"]
