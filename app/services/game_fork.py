"""Atomic materialization of an independent GameInstance from an archive."""

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


class GameForkError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ForkedRuntime:
    instance: GameInstance
    session: ConversationSession
    created: bool


class GameForkService:
    """Create an ACTIVE runtime with the exact state and stable history of an archive."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.lifecycle = GameLifecycleService(db)
        self.materializer = GameMaterializer(db)

    def materialize(
        self,
        *,
        source_game_instance_id: UUID,
        player_id: UUID,
        creation_key: str,
    ) -> ForkedRuntime:
        if not creation_key.strip():
            raise GameForkError(
                "FORK_CREATION_KEY_REQUIRED",
                "Fork creation requires a non-empty idempotency key",
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
            raise GameForkError("GAME_INSTANCE_NOT_FOUND", "The source Game does not exist")
        if source.status != GameInstanceStatus.ARCHIVED:
            raise GameForkError(
                "FORK_SOURCE_NOT_ARCHIVED",
                "Only an Archived GameInstance can be forked",
            )
        try:
            self.lifecycle.assert_stable_point(source)
        except GameLifecycleError as exc:
            raise GameForkError(exc.code, exc.message) from exc

        existing = self._existing(player_id, creation_key)
        if existing is not None:
            return self._replay(existing, source)

        try:
            version = ScenarioVersionRepository(self.db).load(source.scenario_version_id)
            definition = version.definition
            if not isinstance(definition, ScenarioDefinitionV2):
                raise GameForkError(
                    "FORK_SCENARIO_VERSION_INVALID",
                    "Fork requires a v2 ScenarioVersion runtime definition",
                )
            runtime = self.materializer.load_runtime(source, definition)
            with self.db.begin_nested():
                target = GameInstance(
                    player_id=source.player_id,
                    scenario_version_id=source.scenario_version_id,
                    forked_from_game_instance_id=source.id,
                    checkpointed_from_game_instance_id=None,
                    checkpoint_source_runtime_revision=None,
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
                target.inherited_task_count = history.inherited_task_count
                target.status = GameInstanceStatus.ACTIVE
                target.runtime_revision = 1
                self.db.flush()
        except IntegrityError as exc:
            self.db.expire_all()
            concurrent = self._existing(player_id, creation_key)
            if concurrent is not None:
                source = self.db.get(GameInstance, source_game_instance_id)
                if source is not None:
                    return self._replay(concurrent, source)
            raise GameForkError(
                "FORK_MATERIALIZATION_FAILED",
                "The Fork runtime graph could not be materialized",
            ) from exc
        except (MaterializationError, ScenarioVersionError) as exc:
            self.db.expire_all()
            raise self._translate_materialization_error(exc) from exc
        except GameForkError:
            self.db.expire_all()
            raise
        return ForkedRuntime(instance=target, session=history.session, created=True)

    def _existing(self, player_id: UUID, creation_key: str) -> GameInstance | None:
        return self.db.scalar(
            select(GameInstance)
            .where(
                GameInstance.player_id == player_id,
                GameInstance.creation_key == creation_key,
            )
            .with_for_update()
        )

    def _replay(self, target: GameInstance, source: GameInstance) -> ForkedRuntime:
        if (
            target.forked_from_game_instance_id != source.id
            or target.scenario_version_id != source.scenario_version_id
            or target.checkpointed_from_game_instance_id is not None
        ):
            raise GameForkError(
                "FORK_CREATION_KEY_REUSED",
                "The creation key is already bound to another Fork source",
            )
        if target.status != GameInstanceStatus.ACTIVE:
            raise GameForkError(
                "FORK_INITIALIZATION_INCOMPLETE",
                "The idempotent Fork target is not fully initialized",
            )
        session = self.db.scalar(
            select(ConversationSession)
            .where(ConversationSession.game_instance_id == target.id)
            .order_by(ConversationSession.created_at, ConversationSession.id)
        )
        if session is None:
            raise GameForkError(
                "FORK_INITIALIZATION_INCOMPLETE",
                "The idempotent Fork target has no ConversationSession",
            )
        return ForkedRuntime(instance=target, session=session, created=False)

    @staticmethod
    def _translate_materialization_error(
        exc: MaterializationError | ScenarioVersionError,
    ) -> GameForkError:
        if isinstance(exc, ScenarioVersionError):
            return GameForkError("FORK_SCENARIO_VERSION_INVALID", exc.message)
        code_map = {
            "MATERIALIZATION_RUNTIME_SCHEMA_REQUIRED": "FORK_RUNTIME_SCHEMA_REQUIRED",
            "MATERIALIZATION_SOURCE_RESERVATION_ACTIVE": "FORK_SOURCE_RESERVATION_ACTIVE",
            "MATERIALIZATION_RUNTIME_INVALID": "FORK_SOURCE_RUNTIME_INVALID",
            "MATERIALIZATION_SCENARIO_INVALID": "FORK_SCENARIO_VERSION_INVALID",
            "MATERIALIZATION_PLANNING_IN_FLIGHT": "FORK_SOURCE_HISTORY_INVALID",
            "MATERIALIZATION_HISTORY_INVALID": "FORK_SOURCE_HISTORY_INVALID",
            "MATERIALIZATION_HISTORY_UNSTABLE": "FORK_SOURCE_HISTORY_INVALID",
        }
        return GameForkError(code_map.get(exc.code, "FORK_SOURCE_RUNTIME_INVALID"), exc.message)


__all__ = ["ForkedRuntime", "GameForkError", "GameForkService"]
