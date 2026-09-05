"""Shared authoritative completion evaluation primitives."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.action_invocation import action_invocation_from_operation
from app.domain.completion import (
    CompletionEvidence,
    CompletionResult,
    CompletionStatus,
    OperationMatchConstraint,
)
from app.domain.enums import WorldOperationStatus
from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import AgentTask, WorldOperation


class CompletionKernelError(ValueError):
    """Raised when a completion constraint cannot be evaluated safely."""


def evaluate_state_requirement(
    requirement_identity: str,
    *,
    value: object,
    satisfied: bool,
    player_visible_satisfied: bool | None,
) -> CompletionResult:
    """Adapt existing authoritative state evaluation into the shared shape."""

    return CompletionResult(
        requirement_identity=requirement_identity,
        requirement_kind="STATE",
        status=(CompletionStatus.COMPLETED if satisfied else CompletionStatus.UNSATISFIED),
        value=value,
        authoritative_evidence=CompletionEvidence(source="WORLD_STATE"),
        player_visible_satisfied=player_visible_satisfied,
    )


def evaluate_operation_requirement(
    db: Session,
    scope: RuntimeScope,
    task: AgentTask,
    definition: ScenarioDefinitionV2,
    *,
    requirement_identity: str,
    constraint: OperationMatchConstraint,
) -> CompletionResult:
    """Evaluate one exact task-owned successful operation requirement."""

    actions = {item.key: item for item in definition.actions}
    action = actions.get(constraint.action_key)
    if action is None:
        raise CompletionKernelError(
            f"Operation completion references unknown Action {constraint.action_key}"
        )
    if task.game_instance_id != scope.game_instance_id:
        raise CompletionKernelError("Operation completion Task is outside the runtime scope")

    operations = db.scalars(
        select(WorldOperation)
        .where(
            WorldOperation.game_instance_id == scope.game_instance_id,
            WorldOperation.task_id == task.id,
            WorldOperation.status == WorldOperationStatus.RESOLVED,
        )
        .order_by(WorldOperation.created_at.asc(), WorldOperation.id.asc())
    ).all()
    for operation in operations:
        if not _operation_is_after_task_start(operation, task):
            continue
        if operation.action_key != action.key:
            continue
        outcome = operation.outcome
        if not isinstance(outcome, Mapping) or outcome.get("failure") is not None:
            continue
        outcome_code = outcome.get("outcome_code")
        successful_codes = {item.code for item in action.expected_outcomes if item.success}
        if not isinstance(outcome_code, str) or outcome_code not in successful_codes:
            continue
        invocation = action_invocation_from_operation(
            action,
            actor_key=operation.actor_key,
            target_key=operation.target_key,
            parameters=dict(operation.parameters or {}),
            outcome=outcome,
        )
        if constraint.actor_key is not None and invocation.actor_key != constraint.actor_key:
            continue
        if constraint.target_key is not None and invocation.target_key != constraint.target_key:
            continue
        if not _bindings_match(invocation, constraint):
            continue
        if constraint.parameters is not None:
            expected = action_invocation_from_operation(
                action,
                actor_key=invocation.actor_key,
                target_key=invocation.target_key,
                parameters=dict(constraint.parameters),
                bindings=(),
            )
            if invocation.parameters != expected.parameters:
                continue
        return CompletionResult(
            requirement_identity=requirement_identity,
            requirement_kind="OPERATION",
            status=CompletionStatus.COMPLETED,
            value=True,
            authoritative_evidence=CompletionEvidence(
                source="WORLD_OPERATION",
                operation_id=operation.id,
            ),
            player_visible_satisfied=True,
        )
    return CompletionResult(
        requirement_identity=requirement_identity,
        requirement_kind="OPERATION",
        status=CompletionStatus.UNSATISFIED,
        value=False,
        authoritative_evidence=None,
        player_visible_satisfied=False,
    )


def _bindings_match(invocation: object, constraint: OperationMatchConstraint) -> bool:
    actual = {item.role: item.value for item in getattr(invocation, "bindings", ())}
    return all(actual.get(item.role) == item.value for item in constraint.bindings)


def _operation_is_after_task_start(operation: WorldOperation, task: AgentTask) -> bool:
    task_created_at = getattr(task, "created_at", None)
    operation_created_at = getattr(operation, "created_at", None)
    if not isinstance(task_created_at, datetime) or not isinstance(operation_created_at, datetime):
        return True
    if task_created_at.tzinfo is None or operation_created_at.tzinfo is None:
        return operation_created_at >= task_created_at
    return operation_created_at >= task_created_at


__all__ = [
    "CompletionKernelError",
    "evaluate_operation_requirement",
    "evaluate_state_requirement",
]
