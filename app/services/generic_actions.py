"""Generic immediate/async Action and WorldOperation lifecycle."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import actor_binding_matches, evaluate_authority
from app.domain.enums import AuthorityOutcome, DecisionStatus, WorldOperationStatus
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import (
    ActionDefinitionV2,
    ActionExecutionMode,
    ActionParameters,
    ScenarioDefinitionV2,
    normalize_action_parameters,
)
from app.engine.rules import GenericRuleOutcome, RuleEngineError, RuleFailure
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    GameInstance,
    GameInstanceActor,
    WorldOperation,
)
from app.scenarios.versions import ScenarioVersionRepository
from app.services.game_lifecycle import require_scope_writable
from app.services.generic_game import AppliedRuleResult, GenericGameError, GenericGameService


class GenericActionError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class GenericApprovalRequired(GenericActionError):
    def __init__(self, decision: ActionDecisionRequest) -> None:
        super().__init__("ACTION_APPROVAL_REQUIRED", "The Action requires player approval")
        self.decision = decision


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
        parameters: ActionParameters,
        idempotency_key: str,
        task_id: UUID | None = None,
        source_step_id: UUID | None = None,
        decision_id: UUID | None = None,
    ) -> ActionExecutionResult:
        require_scope_writable(self.db, self.scope.game_instance_id)
        if not idempotency_key.strip():
            raise GenericActionError(
                "ACTION_IDEMPOTENCY_KEY_REQUIRED",
                "Action execution requires an Instance-scoped idempotency key",
            )
        action = self._action(action_key)
        try:
            parameters = normalize_action_parameters(action, parameters)
        except ValueError as exc:
            raise GenericActionError("ACTION_PARAMETERS_INVALID", str(exc)) from exc
        existing = self.db.scalar(
            select(WorldOperation).where(
                WorldOperation.game_instance_id == self.scope.game_instance_id,
                WorldOperation.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            existing_parameters = dict(existing.parameters or {})
            try:
                existing_parameters = normalize_action_parameters(action, existing_parameters)
            except ValueError:
                existing_parameters = {}
            if (
                existing.action_key != action_key
                or existing.actor_key != actor_key
                or existing.target_key != target_key
                or existing_parameters != parameters
            ):
                raise GenericActionError(
                    "ACTION_IDEMPOTENCY_CONFLICT",
                    "The idempotency key is already bound to different Action input",
                )
            return ActionExecutionResult(existing, None, True)
        actor = self.db.get(GameInstanceActor, (self.scope.game_instance_id, actor_key))
        if actor is None:
            raise GenericActionError(
                "RUNTIME_ACTOR_NOT_FOUND", "Actor is absent from this Instance"
            )
        definition = (
            ScenarioVersionRepository(self.db).load(self.scope.scenario_version_id).definition
        )
        if not isinstance(definition, ScenarioDefinitionV2) or not actor_binding_matches(
            definition, actor
        ):
            raise GenericActionError(
                "RUNTIME_ACTOR_BINDING_INVALID",
                "Actor authority does not match the exact ScenarioVersion",
            )
        authority = evaluate_authority(actor, action, parameters)
        if authority.outcome == AuthorityOutcome.DENY:
            raise GenericActionError(authority.reason_code, "Actor authority denied the Action")
        approval_granted = False
        if authority.outcome == AuthorityOutcome.REQUIRE_PLAYER_DECISION:
            decision = self._decision(
                decision_id=decision_id,
                actor_key=actor_key,
                action_key=action_key,
                target_key=target_key,
                parameters=parameters,
                idempotency_key=idempotency_key,
                task_id=task_id,
                source_step_id=source_step_id,
                reason_code=authority.reason_code,
                details=authority.details,
            )
            if decision.status == DecisionStatus.REJECTED:
                raise GenericActionError("ACTION_APPROVAL_REJECTED", "Player rejected the Action")
            if decision.status != DecisionStatus.APPROVED:
                raise GenericApprovalRequired(decision)
            approval_granted = True
            decision_id = decision.id
        try:
            preflight = self.game.preflight(
                actor_key=actor_key,
                action_key=action_key,
                target_node_key=target_key,
                parameters=parameters,
                approval_granted=approval_granted,
            )
        except GenericGameError as exc:
            raise GenericActionError(exc.code, exc.message, retryable=exc.retryable) from exc
        except RuleEngineError as exc:
            raise GenericActionError(exc.code, exc.message) from exc
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
            actor_key=actor_key,
            action_key=action_key,
            execution_mode=action.execution_mode.value,
            target_key=target_key,
            parameters=parameters,
            idempotency_key=idempotency_key,
            status=WorldOperationStatus.PENDING,
        )
        self.db.add(operation)
        self.db.flush()
        if approval_granted:
            assert decision_id is not None
            approved = self.db.get(ActionDecisionRequest, decision_id)
            assert approved is not None
            approved.status = DecisionStatus.CONSUMED
        if action.execution_mode == ActionExecutionMode.ASYNC:
            return ActionExecutionResult(operation, None, False)
        try:
            applied = self.game.execute(
                actor_key=actor_key,
                action_key=action_key,
                target_node_key=target_key,
                parameters=parameters,
                approval_granted=approval_granted,
            )
        except GenericGameError as exc:
            applied = self._runtime_failure(exc.code, exc.message, retryable=exc.retryable)
        except RuleEngineError as exc:
            applied = self._runtime_failure(exc.code, exc.message, retryable=False)
        self._complete(operation, applied, resolution_key=idempotency_key)
        return ActionExecutionResult(operation, applied, False)

    def resolve_operation(
        self,
        operation_id: UUID,
        *,
        resolution_key: str,
    ) -> ActionExecutionResult:
        require_scope_writable(self.db, self.scope.game_instance_id)
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
        try:
            action = self._action(operation.action_key)
        except GenericActionError as exc:
            applied = self._runtime_failure(exc.code, exc.message, retryable=False)
        else:
            try:
                operation.parameters = normalize_action_parameters(
                    action, dict(operation.parameters or {})
                )
            except ValueError as exc:
                applied = self._runtime_failure(
                    "ACTION_PARAMETERS_INVALID", str(exc), retryable=False
                )
            else:
                try:
                    applied = self.game.execute(
                        actor_key=operation.actor_key,
                        action_key=operation.action_key,
                        target_node_key=operation.target_key,
                        parameters=operation.parameters,
                        operation_status=operation.status.value,
                        approval_granted=True,
                    )
                except GenericGameError as exc:
                    applied = self._runtime_failure(exc.code, exc.message, retryable=exc.retryable)
                except RuleEngineError as exc:
                    applied = self._runtime_failure(exc.code, exc.message, retryable=False)
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

    def decide(self, decision_id: UUID, *, approve: bool) -> ActionDecisionRequest:
        require_scope_writable(self.db, self.scope.game_instance_id)
        decision = self.db.scalar(
            select(ActionDecisionRequest)
            .where(
                ActionDecisionRequest.id == decision_id,
                ActionDecisionRequest.game_instance_id == self.scope.game_instance_id,
            )
            .with_for_update()
        )
        if decision is None or decision.status != DecisionStatus.PENDING:
            raise GenericActionError("ACTION_DECISION_INVALID", "Decision is absent or not pending")
        decision.status = DecisionStatus.APPROVED if approve else DecisionStatus.REJECTED
        decision.decided_at = datetime.now(UTC)
        self.db.flush()
        return decision

    def _decision(
        self,
        *,
        decision_id: UUID | None,
        actor_key: str,
        action_key: str,
        target_key: str,
        parameters: ActionParameters,
        idempotency_key: str,
        task_id: UUID | None,
        source_step_id: UUID | None,
        reason_code: str,
        details: dict[str, object],
    ) -> ActionDecisionRequest:
        decision = self.db.get(ActionDecisionRequest, decision_id) if decision_id else None
        if decision is None:
            decision = self.db.scalar(
                select(ActionDecisionRequest).where(
                    ActionDecisionRequest.game_instance_id == self.scope.game_instance_id,
                    ActionDecisionRequest.idempotency_key == idempotency_key,
                )
            )
        if decision is None:
            decision = ActionDecisionRequest(
                player_id=self.scope.player_id,
                game_instance_id=self.scope.game_instance_id,
                task_id=task_id,
                source_step_id=source_step_id,
                actor_key=actor_key,
                action_key=action_key,
                target_key=target_key,
                parameters=parameters,
                idempotency_key=idempotency_key,
                reason_code=reason_code,
                policy_details=details,
            )
            self.db.add(decision)
            self.db.flush()
        if (
            decision.game_instance_id != self.scope.game_instance_id
            or decision.player_id != self.scope.player_id
            or decision.actor_key != actor_key
            or decision.action_key != action_key
            or decision.target_key != target_key
            or decision.parameters != parameters
        ):
            raise GenericActionError(
                "ACTION_DECISION_SCOPE_INVALID", "Decision input does not match"
            )
        return decision

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
            "actor_location_update": applied.outcome.actor_location_update,
            # Keep the deterministic resource delta in the persisted
            # operation snapshot so Player projections can describe what
            # actually happened.  This is observational data only; Runtime
            # mutation has already been applied by GenericGameService.
            "resource_mutations": [asdict(item) for item in applied.outcome.resource_mutations],
            "failure": (
                asdict(applied.outcome.failure) if applied.outcome.failure is not None else None
            ),
            "knowledge_changes": [asdict(item) for item in applied.knowledge_changes],
        }
        self.db.flush()

    def _runtime_failure(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
    ) -> AppliedRuleResult:
        instance = self.db.get(GameInstance, self.scope.game_instance_id)
        runtime_revision = instance.runtime_revision if instance is not None else 0
        return AppliedRuleResult(
            outcome=GenericRuleOutcome(
                selected_rule_key="APPLICATION_RUNTIME_ERROR",
                failure=RuleFailure(code=code, message=message, retryable=retryable),
            ),
            runtime_revision=runtime_revision,
        )


__all__ = [
    "ActionExecutionResult",
    "GenericActionError",
    "GenericActionService",
    "GenericApprovalRequired",
]
