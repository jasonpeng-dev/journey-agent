from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.debug.schemas import (
    StrategicCommandRequest,
    StrategicDecisionRequest,
    StrategicGoalClarificationRequest,
    StrategicResetRequest,
    StrategicWorldEventRequest,
)
from app.debug.snapshot_service import StrategicSnapshotService
from app.debug.strategic_controller import StrategicDebugController
from app.infrastructure.db.session import get_db

router = APIRouter(prefix="/api/v1/debug/strategic", tags=["strategic-debug"])


@router.get("/snapshot")
def strategic_snapshot(
    session_id: UUID,
    include_trace: bool = Query(default=False),
    include_hidden_truth: bool = Query(default=False),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _require_debug_environment(settings)
    return StrategicSnapshotService(db, settings).build(
        session_id,
        include_trace=include_trace,
        include_hidden_truth=include_hidden_truth,
    )


@router.post("/reset", status_code=201)
def reset_strategic_scenario(
    _payload: StrategicResetRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _require_debug_environment(settings)
    return StrategicDebugController(db, settings).reset()


@router.post("/commands", status_code=201)
async def issue_strategic_command(
    payload: StrategicCommandRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _require_debug_environment(settings)
    return await StrategicDebugController(db, settings).issue_command(
        payload.session_id,
        payload.command,
        payload.idempotency_key,
    )


@router.post("/tasks/{task_id}/decisions/{decision_id}/resolve")
async def resolve_strategic_decision(
    task_id: UUID,
    decision_id: UUID,
    payload: StrategicDecisionRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _require_debug_environment(settings)
    return await StrategicDebugController(db, settings).resolve_decision(
        task_id,
        decision_id,
        payload.session_id,
        payload.option_id,
    )


@router.post("/tasks/{task_id}/goal-clarification")
async def clarify_strategic_goal(
    task_id: UUID,
    payload: StrategicGoalClarificationRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _require_debug_environment(settings)
    return await StrategicDebugController(db, settings).clarify_goal(
        task_id,
        payload.session_id,
        objective_keys=payload.objective_keys,
        clarification_text=payload.clarification_text,
    )


@router.post("/tasks/{task_id}/world-events/{operation_id}/resolve")
async def resolve_strategic_world_event(
    task_id: UUID,
    operation_id: UUID,
    payload: StrategicWorldEventRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _require_debug_environment(settings)
    return await StrategicDebugController(db, settings).resolve_world_event(
        task_id,
        operation_id,
        payload.session_id,
        payload.idempotency_key,
    )


def _require_debug_environment(settings: Settings) -> None:
    if settings.app_env == "production":
        raise AppError(
            "DEBUG_FIXTURE_DISABLED",
            "Strategic debug controls are disabled in production",
            status_code=404,
        )
