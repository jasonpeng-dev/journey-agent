"""One stable engine Tool for every versioned gameplay Action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.domain.runtime_scope import RuntimeScope
from app.domain.scenario_v2 import StrictScalar
from app.services.generic_actions import GenericActionService


class ExecuteActionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_key: str = Field(min_length=1, max_length=80)
    target_key: str = Field(min_length=1, max_length=80)
    parameters: dict[str, StrictScalar] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=160)


@dataclass(frozen=True, slots=True)
class GenericActionToolContext:
    scope: RuntimeScope
    actor_key: str
    task_id: UUID | None = None
    step_id: UUID | None = None


def execute_action(
    db: Session,
    context: GenericActionToolContext,
    args: ExecuteActionArgs,
) -> dict[str, Any]:
    result = GenericActionService(db, context.scope).execute_action(
        actor_key=context.actor_key,
        action_key=args.action_key,
        target_key=args.target_key,
        parameters=args.parameters,
        idempotency_key=args.idempotency_key,
        task_id=context.task_id,
        source_step_id=context.step_id,
    )
    return {
        "operation_id": str(result.operation.id),
        "status": result.operation.status.value,
        "execution_mode": result.operation.execution_mode,
        "outcome": result.operation.outcome,
        "replayed": result.replayed,
    }


__all__ = ["ExecuteActionArgs", "GenericActionToolContext", "execute_action"]
