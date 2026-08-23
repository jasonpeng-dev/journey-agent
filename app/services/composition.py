"""Application composition for configured model-backed Formal Play."""

from sqlalchemy.orm import Session

from app.agent.provider import build_generic_provider
from app.core.config import Settings
from app.domain.runtime_scope import GameInstanceId
from app.services.play import PlayOrchestrator


def configured_play_orchestrator(
    db: Session,
    game_instance_id: GameInstanceId,
    settings: Settings,
) -> PlayOrchestrator:
    provider = build_generic_provider(settings)
    return PlayOrchestrator(
        db,
        game_instance_id,
        provider=provider,
        model_max_repair_attempts_per_cycle=(settings.model_max_repair_attempts_per_cycle),
    )


__all__ = ["configured_play_orchestrator"]
