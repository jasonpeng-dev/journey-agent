"""Generic immediate/async Action and WorldOperation lifecycle."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import WorldOperationStatus
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import (
    ActionDefinitionV2,
    ActionExecutionMode,
    ScenarioDefinitionV2,
    StrictScalar,
)
from app.infrastructure.db.models import WorldOperation
from app.scenarios.versions import ScenarioVersionRepository
from app.services.generic_game import AppliedRuleResult, GenericGameService


class GenericActionError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ActionExecutionResult:
    operation: WorldOperation
    applied: AppliedRuleResult | None
    replayed: bool


class GenericActionService:
    def __init__(self, db: Session, scope: RuntimeScope) -> None:
        self.db = db
        self.scope = scope
        self.game = GenericGameService(db, scope)

    def execute_action(
        self,
        *,
        actor_key: str,
        action_key: str,
        target_key: str,
        parameters: dict[str, StrictScalar],
        idempotency_key: str,
        task_id: UUID | None = None,
        source_step_id: UUID | None = None,
    ) -> ActionExecutionResult:
        if not idempotency_key.strip():
            raise GenericActionError(
                "ACTION_IDEMPOTENCY_KEY_REQUIRED",
                "Action execution requires an Instance-scoped idempotency key",
            )
        existing = self.db.scalar(
            select(WorldOperation).where(
                WorldOperation.game_instance_id == self.scope.game_instance_id,
                WorldOperation.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.action_key != action_key
                or existing.actor_key != actor_key
                or existing.target_key != target_key
                or existing.parameters != parameters
            ):
                raise GenericActionError(
                    "ACTION_IDEMPOTENCY_CONFLICT",
                    "The idempotency key is already bound to different Action input",
                )
            return ActionExecutionResult(existing, None, True)
        action = self._action(action_key)
        preflight = self.game.preflight(
            actor_key=actor_key,
            action_key=action_key,
            target_node_key=target_key,
            parameters=parameters,
        )
        if preflight is not None and preflight.failure is not None:
            raise GenericActionError(
                preflight.failure.code,
                preflight.failure.message,
                retryable=preflight.failure.retryable,
            )
        operation = WorldOperation(
            player_id=self.scope.player_id,
            game_instance_id=self.scope.game_instance_id,
            task_id=task_id,
            source_step_id=source_step_id,
            officer_npc_id=None,
            actor_key=actor_key,
            action_key=action_key,
            operation_type=action_key,
            execution_mode=action.execution_mode.value,
            target_key=target_key,
            parameters=parameters,
            idempotency_key=idempotency_key,
            status=WorldOperationStatus.PENDING,
        )
        self.db.add(operation)
        self.db.flush()
        if action.execution_mode == ActionExecutionMode.ASYNC:
            return ActionExecutionResult(operation, None, False)
        applied = self.game.execute(
            actor_key=actor_key,
            action_key=action_key,
            target_node_key=target_key,
            parameters=parameters,
        )
        self._complete(operation, applied, resolution_key=idempotency_key)
        return ActionExecutionResult(operation, applied, False)

    def resolve_operation(
        self,
        operation_id: UUID,
        *,
        resolution_key: str,
    ) -> ActionExecutionResult:
        operation = self.db.scalar(
            select(WorldOperation)
            .where(
                WorldOperation.id == operation_id,
                WorldOperation.game_instance_id == self.scope.game_instance_id,
            )
            .with_for_update()
        )
        if operation is None:
            raise GenericActionError(
                "WORLD_OPERATION_NOT_FOUND", "The Operation does not belong to this Instance"
            )
        if operation.status == WorldOperationStatus.RESOLVED:
            if operation.resolution_key != resolution_key:
                raise GenericActionError(
                    "WORLD_OPERATION_RESOLUTION_CONFLICT",
                    "The Operation was resolved with a different resolution key",
                )
            return ActionExecutionResult(operation, None, True)
        if not operation.action_key or not operation.actor_key:
            raise GenericActionError(
                "WORLD_OPERATION_LEGACY_UNSUPPORTED",
                "Generic resolution requires a versioned Action and Actor",
            )
        applied = self.game.execute(
            actor_key=operation.actor_key,
            action_key=operation.action_key,
            target_node_key=operation.target_key,
            parameters=operation.parameters,
            operation_status=operation.status.value,
        )
        self._complete(operation, applied, resolution_key=resolution_key)
        return ActionExecutionResult(operation, applied, False)

    def _action(self, action_key: str) -> ActionDefinitionV2:
        snapshot = ScenarioVersionRepository(self.db).load(self.scope.scenario_version_id)
        definition = snapshot.definition
        if not isinstance(definition, ScenarioDefinitionV2):
            raise GenericActionError(
                "GENERIC_RUNTIME_SCHEMA_REQUIRED", "Generic Actions require ScenarioDefinition v2"
            )
        action = next((item for item in definition.actions if item.key == action_key), None)
        if action is None:
            raise GenericActionError("ACTION_NOT_FOUND", "Action is absent from the exact Version")
        return action

    def _complete(
        self,
        operation: WorldOperation,
        applied: AppliedRuleResult,
        *,
        resolution_key: str,
    ) -> None:
        operation.status = WorldOperationStatus.RESOLVED
        operation.resolution_key = resolution_key
        operation.resolved_at = datetime.now(UTC)
        operation.outcome = {
            "selected_rule_key": applied.outcome.selected_rule_key,
            "outcome_code": applied.outcome.outcome_code,
            "runtime_revision": applied.runtime_revision,
            "failure": (
                asdict(applied.outcome.failure) if applied.outcome.failure is not None else None
            ),
        }
        self.db.flush()


__all__ = ["ActionExecutionResult", "GenericActionError", "GenericActionService"]
