from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.domain.completion import CompletionStatus, OperationMatchConstraint
from app.domain.enums import WorldOperationStatus
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import AgentTask, WorldOperation
from app.services.completion_kernel import evaluate_operation_requirement
from app.services.game_instances import GameInstanceService
from tests.unit.test_transport_resource import _definition, _runtime


def _task(session: Session, runtime: object, definition: object) -> AgentTask:
    instance = runtime.instance  # type: ignore[attr-defined]
    session_object = runtime.session  # type: ignore[attr-defined]
    task = AgentTask(
        player_id=session_object.player_id,
        game_instance_id=instance.id,
        owner_actor_key=session_object.actor_key,
        origin_session_id=session_object.id,
        last_session_id=session_object.id,
        goal_description="deliver cargo",
        scenario_key=definition.metadata.key,  # type: ignore[attr-defined]
        objective_resolution_status="CONFIRMED",
        objective_scope_hash="0" * 64,
        objective_frozen_at=datetime.now(UTC),
        objective_freeze_source="TEST",
    )
    session.add(task)
    session.flush()
    return task


def _resolved_operation(
    session: Session,
    task: AgentTask,
    *,
    status: WorldOperationStatus = WorldOperationStatus.RESOLVED,
) -> WorldOperation:
    operation = WorldOperation(
        player_id=task.player_id,
        game_instance_id=task.game_instance_id,
        task_id=task.id,
        actor_key="carrier",
        action_key="transport_resource",
        execution_mode="IMMEDIATE",
        target_key="region_b",
        status=status,
        parameters={"resource_key": "cargo_alpha", "amount": 10},
        outcome=(
            {
                "outcome_code": "TRANSPORTED",
                "failure": None,
                "resource_mutations": [
                    {
                        "resource_key": "cargo_alpha",
                        "amount": -10,
                        "scope_node_key": "region_a",
                    },
                    {
                        "resource_key": "cargo_alpha",
                        "amount": 10,
                        "scope_node_key": "region_b",
                    },
                ],
            }
            if status == WorldOperationStatus.RESOLVED
            else None
        ),
        idempotency_key=f"completion-{status.value}",
    )
    session.add(operation)
    session.flush()
    return operation


def test_matching_successful_operation_produces_authoritative_proof(session: Session) -> None:
    definition = _definition()
    runtime, _scope = _runtime(session, definition, "completion-kernel-success")
    task = _task(session, runtime, definition)
    constraint = OperationMatchConstraint(
        action_key="transport_resource",
        target_key="region_b",
        bindings=({"role": "source_region", "value": "region_a"},),
        parameters={"resource_key": "cargo_alpha", "amount": 10},
    )
    operation = _resolved_operation(session, task)

    result = evaluate_operation_requirement(
        session,
        GameInstanceService(session).load(GameInstanceId(task.game_instance_id)),
        task,
        definition,
        requirement_identity="operation/transport",
        constraint=constraint,
    )

    assert result.status == CompletionStatus.COMPLETED
    assert result.satisfied is True
    assert result.authoritative_evidence is not None
    assert result.authoritative_evidence.operation_id == operation.id


def test_operation_match_is_exact_and_pending_or_failed_is_not_completion(session: Session) -> None:
    definition = _definition()
    runtime, _scope = _runtime(session, definition, "completion-kernel-negative")
    task = _task(session, runtime, definition)
    scope = GameInstanceService(session).load(GameInstanceId(task.game_instance_id))
    constraint = OperationMatchConstraint(
        action_key="transport_resource",
        target_key="region_b",
        parameters={"resource_key": "cargo_alpha", "amount": 10},
    )
    operation = _resolved_operation(session, task, status=WorldOperationStatus.PENDING)

    pending = evaluate_operation_requirement(
        session,
        scope,
        task,
        definition,
        requirement_identity="operation/transport",
        constraint=constraint,
    )
    assert pending.status == CompletionStatus.UNSATISFIED

    operation.status = WorldOperationStatus.RESOLVED
    operation.outcome = {
        "outcome_code": "TRANSPORTED",
        "failure": {"code": "TRANSPORT_BLOCKED"},
    }
    session.flush()
    failed = evaluate_operation_requirement(
        session,
        scope,
        task,
        definition,
        requirement_identity="operation/transport",
        constraint=constraint,
    )
    assert failed.status == CompletionStatus.UNSATISFIED

    operation.outcome = {
        "outcome_code": "TRANSPORTED",
        "failure": None,
        "resource_mutations": [
            {"resource_key": "cargo_alpha", "amount": -10, "scope_node_key": "region_a"},
            {"resource_key": "cargo_alpha", "amount": 10, "scope_node_key": "region_b"},
        ],
    }
    session.flush()
    wrong_amount = evaluate_operation_requirement(
        session,
        scope,
        task,
        definition,
        requirement_identity="operation/transport",
        constraint=constraint.model_copy(
            update={"parameters": {"resource_key": "cargo_alpha", "amount": 11}}
        ),
    )
    assert wrong_amount.status == CompletionStatus.UNSATISFIED
