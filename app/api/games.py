"""Player-facing GameInstance lifecycle HTTP adapter."""

from __future__ import annotations

from typing import Any, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas.phase_d import GameSummaryResponse, NewGameRequest, PublicGameStatus
from app.core.errors import AppError
from app.domain.enums import AgentTaskStatus
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentTask,
    GameInstance,
    ScenarioVersion,
    WorldOperation,
)
from app.infrastructure.db.session import get_db
from app.services.game_lifecycle import GameLifecycleError, GameLifecycleService
from app.services.runtime_initialization import RuntimeInitializationError

router = APIRouter(prefix="/api/v1/games", tags=["games"])

_ACTIVE_TASK_STATUSES = (
    AgentTaskStatus.ACTIVE,
    AgentTaskStatus.REQUIRES_PLAYER_DECISION,
    AgentTaskStatus.WAITING_FOR_PLAYER_ACTION,
    AgentTaskStatus.WAITING_FOR_WORLD_EVENT,
)


@router.get("", response_model=list[GameSummaryResponse])
def list_games(
    archived: bool = Query(default=False), db: Session = Depends(get_db)
) -> list[GameSummaryResponse]:
    return [_summary(db, game) for game in GameLifecycleService(db).list(archived=archived)]


@router.post("", response_model=GameSummaryResponse, status_code=status.HTTP_201_CREATED)
def create_game(request: NewGameRequest, db: Session = Depends(get_db)) -> GameSummaryResponse:
    try:
        runtime = GameLifecycleService(db).create(
            scenario_version_id=request.scenario_version_id,
            idempotency_key=request.idempotency_key,
        )
        db.commit()
        return _summary(db, runtime.instance)
    except (GameLifecycleError, RuntimeInitializationError) as exc:
        db.rollback()
        _raise_http(exc)


@router.get("/{game_instance_id}", response_model=GameSummaryResponse)
def get_game(game_instance_id: UUID, db: Session = Depends(get_db)) -> GameSummaryResponse:
    try:
        return _summary(db, GameLifecycleService(db).get(game_instance_id))
    except GameLifecycleError as exc:
        _raise_http(exc)


@router.post("/{game_instance_id}/archive", response_model=GameSummaryResponse)
def archive_game(game_instance_id: UUID, db: Session = Depends(get_db)) -> GameSummaryResponse:
    try:
        game = GameLifecycleService(db).archive(game_instance_id)
        db.commit()
        return _summary(db, game)
    except GameLifecycleError as exc:
        db.rollback()
        _raise_http(exc)


@router.post("/{game_instance_id}/tasks/{task_id}/abandon")
def abandon_task(
    game_instance_id: UUID, task_id: UUID, db: Session = Depends(get_db)
) -> dict[str, str]:
    try:
        task = GameLifecycleService(db).abandon_task(game_instance_id, task_id)
        db.commit()
        return {"task_id": str(task.id), "status": task.status.value}
    except GameLifecycleError as exc:
        db.rollback()
        _raise_http(exc)


@router.get("/{game_instance_id}/history")
def game_history(
    game_instance_id: UUID, db: Session = Depends(get_db)
) -> dict[str, list[dict[str, Any]]]:
    try:
        game = GameLifecycleService(db).get(game_instance_id)
    except GameLifecycleError as exc:
        _raise_http(exc)
    tasks = db.scalars(
        select(AgentTask)
        .where(AgentTask.game_instance_id == game.id)
        .order_by(AgentTask.created_at)
    )
    operations = db.scalars(
        select(WorldOperation)
        .where(WorldOperation.game_instance_id == game.id)
        .order_by(WorldOperation.created_at)
    )
    decisions = db.scalars(
        select(ActionDecisionRequest)
        .where(ActionDecisionRequest.game_instance_id == game.id)
        .order_by(ActionDecisionRequest.created_at)
    )
    return {
        "tasks": [
            {"id": str(item.id), "goal": item.goal_description, "status": item.status.value}
            for item in tasks
        ],
        "operations": [
            {
                "id": str(item.id),
                "action_key": item.action_key,
                "status": item.status.value,
                "outcome": item.outcome,
            }
            for item in operations
        ],
        "decisions": [
            {"id": str(item.id), "action_key": item.action_key, "status": item.status.value}
            for item in decisions
        ],
    }


def _summary(db: Session, game: GameInstance) -> GameSummaryResponse:
    version = db.get(ScenarioVersion, game.scenario_version_id)
    assert version is not None
    active_task = db.scalar(
        select(AgentTask.id).where(
            AgentTask.game_instance_id == game.id,
            AgentTask.status.in_(_ACTIVE_TASK_STATUSES),
        )
    )
    return GameSummaryResponse(
        id=game.id,
        scenario_id=version.scenario_id,
        scenario_version_id=version.id,
        scenario_version_number=version.version_number,
        scenario_content_hash=version.content_hash,
        status=PublicGameStatus(game.status.value),
        active_task_id=active_task,
        created_at=game.created_at,
        updated_at=game.updated_at,
    )


def _raise_http(exc: GameLifecycleError | RuntimeInitializationError) -> Never:
    status_code = 404 if exc.code.endswith("NOT_FOUND") else 409
    raise AppError(exc.code, exc.message, status_code=status_code) from exc


__all__ = ["router"]
