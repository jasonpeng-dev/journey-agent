"""Credential-gated Developer projection, physically separate from Player APIs."""

from __future__ import annotations

import hmac
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.games import game_summary
from app.api.schemas.phase_d import DeveloperGameSnapshotResponse
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentPlan,
    AgentStep,
    AgentTask,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceMemoryEvent,
    GameInstanceResourceState,
    WorldOperation,
)
from app.infrastructure.db.session import get_db
from app.services.game_lifecycle import GameLifecycleError, GameLifecycleService


def require_developer_access(
    x_developer_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    configured = settings.developer_api_token
    if (
        configured is None
        or x_developer_token is None
        or not hmac.compare_digest(x_developer_token, configured.get_secret_value())
    ):
        raise AppError(
            "DEVELOPER_ACCESS_DENIED",
            "Developer View requires the configured server credential",
            status_code=403,
        )


router = APIRouter(
    prefix="/api/v1/developer/games",
    tags=["developer"],
    dependencies=[Depends(require_developer_access)],
)


@router.get("/{game_instance_id}/snapshot", response_model=DeveloperGameSnapshotResponse)
def developer_snapshot(
    game_instance_id: UUID, db: Session = Depends(get_db)
) -> DeveloperGameSnapshotResponse:
    try:
        game = GameLifecycleService(db).get(game_instance_id)
    except GameLifecycleError as exc:
        raise AppError(exc.code, exc.message, status_code=404) from exc
    facts = tuple(
        db.scalars(
            select(GameInstanceFactState).where(GameInstanceFactState.game_instance_id == game.id)
        )
    )
    resources = tuple(
        db.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == game.id
            )
        )
    )
    actors = tuple(
        db.scalars(select(GameInstanceActor).where(GameInstanceActor.game_instance_id == game.id))
    )
    tasks = tuple(db.scalars(select(AgentTask).where(AgentTask.game_instance_id == game.id)))
    plans = tuple(
        db.scalars(select(AgentPlan).where(AgentPlan.task_id.in_([item.id for item in tasks])))
    )
    steps = tuple(
        db.scalars(select(AgentStep).where(AgentStep.plan_id.in_([item.id for item in plans])))
    )
    operations = tuple(
        db.scalars(select(WorldOperation).where(WorldOperation.game_instance_id == game.id))
    )
    decisions = tuple(
        db.scalars(
            select(ActionDecisionRequest).where(ActionDecisionRequest.game_instance_id == game.id)
        )
    )
    memories = tuple(
        db.scalars(
            select(GameInstanceMemoryEvent).where(
                GameInstanceMemoryEvent.game_instance_id == game.id
            )
        )
    )
    task_rows = [
        {
            "id": str(item.id),
            "status": item.status.value,
            "goal": item.goal_description,
            "objective_scope": item.objective_scope_keys,
            "objective_scope_hash": item.objective_scope_hash,
            "replans": item.replan_count,
            "rejected_proposals": item.rejected_proposal_signatures,
            "last_error_code": item.last_error_code,
            "provider_calls": (item.objective_resolution_metadata or {}).get("provider_calls", []),
        }
        for item in tasks
    ]
    plan_rows = [
        {
            "id": str(item.id),
            "task_id": str(item.task_id),
            "version": item.version,
            "status": item.status.value,
            "reason": item.replan_reason,
            "steps": [
                {
                    "id": str(step.id),
                    "sequence": step.sequence,
                    "status": step.status.value,
                    "action": step.action_intent,
                    "arguments": step.tool_arguments,
                    "result": step.actual_result,
                }
                for step in steps
                if step.plan_id == item.id
            ],
        }
        for item in plans
    ]
    operation_rows = [
        {
            "id": str(item.id),
            "task_id": str(item.task_id) if item.task_id else None,
            "action": item.action_key,
            "status": item.status.value,
            "parameters": item.parameters,
            "outcome": item.outcome,
        }
        for item in operations
    ]
    decision_rows = [
        {
            "id": str(item.id),
            "task_id": str(item.task_id) if item.task_id else None,
            "status": item.status.value,
            "action": item.action_key,
            "parameters": item.parameters,
            "reason": item.reason_code,
        }
        for item in decisions
    ]
    memory_rows = [
        {
            "id": str(item.id),
            "actor": item.actor_key,
            "event": item.event_key,
            "content": item.content,
            "rule": item.source_rule_key,
        }
        for item in memories
    ]
    truth = {
        "facts": {f"{item.node_key}.{item.fact_key}": item.truth_value for item in facts},
        "resources": {
            item.resource_key: {"value": item.value, "reserved": item.reserved_value}
            for item in resources
        },
    }
    knowledge = {
        "facts": {
            f"{item.node_key}.{item.fact_key}": item.truth_value
            for item in facts
            if item.visibility == Visibility.KNOWN
        }
    }
    return DeveloperGameSnapshotResponse(
        game=game_summary(db, game),
        truth=truth,
        knowledge=knowledge,
        actors=[
            {
                "key": item.actor_key,
                "role": item.role_key,
                "status": item.status,
                "node": item.current_node_key,
                "authority": item.authority_policy,
            }
            for item in actors
        ],
        tasks=task_rows,
        plans=plan_rows,
        operations=operation_rows,
        rule_outcomes=[item["outcome"] for item in operation_rows if item["outcome"]],
        decisions=decision_rows,
        memory=memory_rows,
        history=[*task_rows, *operation_rows, *decision_rows, *memory_rows],
    )


@router.get("/{game_instance_id}/history")
def developer_history(
    game_instance_id: UUID, db: Session = Depends(get_db)
) -> dict[str, list[dict[str, Any]]]:
    snapshot = developer_snapshot(game_instance_id, db)
    return {"history": snapshot.history}


__all__ = ["require_developer_access", "router"]
