"""Player-facing GameInstance lifecycle HTTP adapter."""

from __future__ import annotations

from typing import Any, Literal, Never
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentError
from app.agent.provider import GenericProviderError
from app.api.schemas.phase_d import (
    ApprovalDecisionRequest,
    ArchiveGameRequest,
    ForkGameRequest,
    GameSummaryResponse,
    GoalSubmissionRequest,
    GoalSubmissionResponse,
    GoalSubmissionStatus,
    NewGameRequest,
    PlayerGameStateResponse,
    PlayerPacingRequest,
    PublicGameStatus,
)
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.domain.enums import AgentTaskStatus
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentTask,
    GameInstance,
    ScenarioVersion,
    WorldOperation,
)
from app.infrastructure.db.session import get_db
from app.services.composition import configured_play_orchestrator
from app.services.game_fork import GameForkError, GameForkService
from app.services.game_instances import GameInstanceError
from app.services.game_lifecycle import GameLifecycleError, GameLifecycleService
from app.services.generic_actions import GenericActionError
from app.services.play import PlayError
from app.services.player_projection import PlayerProjectionService
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
    game_status: Literal["active", "archived"] = Query(default="active", alias="status"),
    db: Session = Depends(get_db),
) -> list[GameSummaryResponse]:
    return [
        game_summary(db, game)
        for game in GameLifecycleService(db).list(archived=game_status == "archived")
    ]


@router.post("", response_model=GameSummaryResponse, status_code=status.HTTP_201_CREATED)
def create_game(request: NewGameRequest, db: Session = Depends(get_db)) -> GameSummaryResponse:
    try:
        runtime = GameLifecycleService(db).create(
            scenario_version_id=request.scenario_version_id,
            idempotency_key=request.idempotency_key,
        )
        db.commit()
        return game_summary(db, runtime.instance)
    except (GameLifecycleError, RuntimeInitializationError) as exc:
        db.rollback()
        _raise_http(exc)


@router.get("/{game_instance_id}", response_model=GameSummaryResponse)
def get_game(game_instance_id: UUID, db: Session = Depends(get_db)) -> GameSummaryResponse:
    try:
        return game_summary(db, GameLifecycleService(db).get(game_instance_id))
    except (GameInstanceError, GameLifecycleError) as exc:
        _raise_http(exc)


@router.get("/{game_instance_id}/play", response_model=PlayerGameStateResponse)
def get_play_state(
    game_instance_id: UUID,
    task_id: UUID | None = Query(default=None),
    db: Session = Depends(get_db),
) -> PlayerGameStateResponse:
    try:
        return PlayerProjectionService(db).game_state(
            GameInstanceId(game_instance_id), selected_task_id=task_id
        )
    except (GameInstanceError, GameLifecycleError) as exc:
        _raise_http(exc)


@router.post("/{game_instance_id}/goals", response_model=GoalSubmissionResponse)
def submit_goal(
    game_instance_id: UUID,
    request: GoalSubmissionRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> GoalSubmissionResponse:
    try:
        submission = configured_play_orchestrator(
            db, GameInstanceId(game_instance_id), settings
        ).submit_goal(request.goal, idempotency_key=request.idempotency_key)
        if submission.task is None:
            db.rollback()
            status_value = GoalSubmissionStatus(submission.resolution.status)
            return GoalSubmissionResponse(
                status=status_value,
                clarification_prompt=submission.resolution.clarification_prompt,
                candidate_objective_names=list(submission.resolution.candidate_keys),
                explanation="Goal must map to an Objective in this exact ScenarioVersion",
            )
        db.commit()
        state = PlayerProjectionService(db).game_state(GameInstanceId(game_instance_id))
        assert state.current_task is not None
        return GoalSubmissionResponse(status=GoalSubmissionStatus.ACCEPTED, task=state.current_task)
    except (
        GameInstanceError,
        GameLifecycleError,
        GenericAgentError,
        GenericActionError,
        GenericProviderError,
        PlayError,
    ) as exc:
        db.rollback()
        _raise_http(exc)


@router.post(
    "/{game_instance_id}/play/start-planning",
    response_model=PlayerGameStateResponse,
)
def start_initial_planning(
    game_instance_id: UUID,
    request: PlayerPacingRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlayerGameStateResponse:
    try:
        configured_play_orchestrator(
            db, GameInstanceId(game_instance_id), settings
        ).start_initial_planning(expected_pacing_version=request.expected_pacing_version)
        db.commit()
        return PlayerProjectionService(db).game_state(GameInstanceId(game_instance_id))
    except (
        GameInstanceError,
        GameLifecycleError,
        GenericAgentError,
        GenericActionError,
        GenericProviderError,
        PlayError,
    ) as exc:
        db.rollback()
        _raise_http(exc)


@router.post(
    "/{game_instance_id}/play/acknowledge-action",
    response_model=PlayerGameStateResponse,
)
def acknowledge_action(
    game_instance_id: UUID,
    request: PlayerPacingRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlayerGameStateResponse:
    try:
        configured_play_orchestrator(
            db, GameInstanceId(game_instance_id), settings
        ).acknowledge_action(expected_pacing_version=request.expected_pacing_version)
        db.commit()
        return PlayerProjectionService(db).game_state(GameInstanceId(game_instance_id))
    except (
        GameInstanceError,
        GameLifecycleError,
        GenericAgentError,
        GenericActionError,
        GenericProviderError,
        PlayError,
    ) as exc:
        db.rollback()
        _raise_http(exc)


@router.post(
    "/{game_instance_id}/play/acknowledge-debrief",
    response_model=PlayerGameStateResponse,
)
def acknowledge_debrief(
    game_instance_id: UUID,
    request: PlayerPacingRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlayerGameStateResponse:
    try:
        configured_play_orchestrator(
            db, GameInstanceId(game_instance_id), settings
        ).acknowledge_debrief(expected_pacing_version=request.expected_pacing_version)
        db.commit()
        return PlayerProjectionService(db).game_state(GameInstanceId(game_instance_id))
    except (
        GameInstanceError,
        GameLifecycleError,
        GenericAgentError,
        GenericActionError,
        GenericProviderError,
        PlayError,
    ) as exc:
        db.rollback()
        _raise_http(exc)


@router.post(
    "/{game_instance_id}/play/replan",
    response_model=PlayerGameStateResponse,
)
def replan_play(
    game_instance_id: UUID,
    request: PlayerPacingRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlayerGameStateResponse:
    try:
        configured_play_orchestrator(db, GameInstanceId(game_instance_id), settings).replan(
            expected_pacing_version=request.expected_pacing_version
        )
        db.commit()
        return PlayerProjectionService(db).game_state(GameInstanceId(game_instance_id))
    except (
        GameInstanceError,
        GameLifecycleError,
        GenericAgentError,
        GenericActionError,
        GenericProviderError,
        PlayError,
    ) as exc:
        db.rollback()
        _raise_http(exc)


@router.post(
    "/{game_instance_id}/approvals/{decision_id}/approve",
    response_model=PlayerGameStateResponse,
)
def approve_action(
    game_instance_id: UUID,
    decision_id: UUID,
    request: ApprovalDecisionRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlayerGameStateResponse:
    return _decide_action(db, game_instance_id, decision_id, request, settings, approve=True)


@router.post(
    "/{game_instance_id}/approvals/{decision_id}/reject",
    response_model=PlayerGameStateResponse,
)
def reject_action(
    game_instance_id: UUID,
    decision_id: UUID,
    request: ApprovalDecisionRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PlayerGameStateResponse:
    return _decide_action(db, game_instance_id, decision_id, request, settings, approve=False)


@router.post("/{game_instance_id}/archive", response_model=GameSummaryResponse)
def archive_game(
    game_instance_id: UUID,
    request: ArchiveGameRequest,
    db: Session = Depends(get_db),
) -> GameSummaryResponse:
    try:
        game = GameLifecycleService(db).archive(
            game_instance_id,
            expected_runtime_revision=request.expected_runtime_revision,
        )
        db.commit()
        return game_summary(db, game)
    except GameLifecycleError as exc:
        db.rollback()
        _raise_http(exc)


@router.post(
    "/{game_instance_id}/fork",
    response_model=GameSummaryResponse,
    status_code=status.HTTP_201_CREATED,
)
def fork_game(
    game_instance_id: UUID,
    request: ForkGameRequest,
    db: Session = Depends(get_db),
) -> GameSummaryResponse:
    try:
        player = GameLifecycleService(db).platform_player()
        runtime = GameForkService(db).materialize(
            source_game_instance_id=game_instance_id,
            player_id=player.id,
            creation_key=request.creation_key,
        )
        db.commit()
        return game_summary(db, runtime.instance)
    except (GameForkError, GameLifecycleError) as exc:
        db.rollback()
        _raise_http(exc)


@router.delete("/{game_instance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_game(game_instance_id: UUID, db: Session = Depends(get_db)) -> Response:
    try:
        GameLifecycleService(db).delete(game_instance_id)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
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
        .order_by(AgentTask.created_at, AgentTask.id)
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


def game_summary(db: Session, game: GameInstance) -> GameSummaryResponse:
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
        runtime_revision=game.runtime_revision,
        active_task_id=active_task,
        created_at=game.created_at,
        updated_at=game.updated_at,
    )


def _decide_action(
    db: Session,
    game_instance_id: UUID,
    decision_id: UUID,
    request: ApprovalDecisionRequest,
    settings: Settings,
    *,
    approve: bool,
) -> PlayerGameStateResponse:
    try:
        configured_play_orchestrator(db, GameInstanceId(game_instance_id), settings).decide(
            decision_id,
            approve=approve,
            expected_task_version=request.expected_task_version,
        )
        db.commit()
        return PlayerProjectionService(db).game_state(GameInstanceId(game_instance_id))
    except (
        GameInstanceError,
        GameLifecycleError,
        GenericAgentError,
        GenericActionError,
        GenericProviderError,
        PlayError,
    ) as exc:
        db.rollback()
        _raise_http(exc)


def _raise_http(
    exc: (
        GameInstanceError
        | GameLifecycleError
        | GenericActionError
        | GenericAgentError
        | GenericProviderError
        | GameForkError
        | PlayError
        | RuntimeInitializationError
    ),
) -> Never:
    if exc.code == "MODEL_PROVIDER_TIMEOUT":
        status_code = 504
    elif exc.code.startswith("MODEL_PROVIDER_"):
        status_code = 502
    elif exc.code == "RUNTIME_CONTRACT_ERROR" or exc.code.startswith("RULE_"):
        status_code = 500
    else:
        status_code = 404 if exc.code.endswith("NOT_FOUND") else 409
    raise AppError(exc.code, exc.message, status_code=status_code) from exc


__all__ = ["game_summary", "router"]
