from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import authority_policy_errors, effective_authority_limits
from app.core.config import Settings, get_settings
from app.core.errors import AppError, NotFoundError
from app.domain.enums import QuestStatus, RewardStatus
from app.infrastructure.db.models import (
    NPC,
    AgentTask,
    ConversationSession,
    OfficerAppointment,
    Quest,
    QuestTemplate,
    World,
    WorldOperation,
)
from app.infrastructure.db.session import get_db
from app.services.game import GameService, seed_id

router = APIRouter(prefix="/api/v1/debug", tags=["debug"])


class StrictDebugRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StarfireFixtureCreate(StrictDebugRequest):
    variant: str = "strategic"


class StarfirePlayerTurn(StrictDebugRequest):
    strategy: str = "CAUTIOUS"


class WorldEventResolve(StrictDebugRequest):
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)


@router.get("/context")
def debug_context(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return safe, read-only metadata needed to bootstrap the debug console."""
    npcs = db.scalars(select(NPC).where(NPC.enabled.is_(True)).order_by(NPC.name)).all()
    worlds = db.scalars(select(World).order_by(World.chapter, World.name)).all()
    npc_payload = [
        {
            "id": npc.id,
            "key": npc.key,
            "name": npc.name,
            "role": npc.role,
            "current_node_id": npc.current_node_id,
            "personality_summary": npc.persona,
            "doctrine": npc.doctrine,
            "doctrine_summary": npc.doctrine,
            "authority_limits": npc.authority_limits,
            "profile_version": npc.profile_version,
            "permissions": sorted(
                name for name, allowed in npc.permission_profile.items() if allowed
            ),
            "tool_permissions": sorted(
                name for name, allowed in npc.permission_profile.items() if allowed
            ),
        }
        for npc in npcs
    ]
    return {
        "environment": settings.app_env,
        "provider": {
            "type": settings.model_provider,
            "model": "mock-model" if settings.model_provider == "mock" else settings.model_name,
            "key_configured": settings.model_api_key is not None,
        },
        "npcs": npc_payload,
        "officers": [
            item
            for item in npc_payload
            if str(item["role"]) in {"STRATEGIST", "GENERAL", "STEWARD"}
        ],
        "worlds": [
            {
                "id": world.id,
                "key": world.key,
                "name": world.name,
                "chapter": world.chapter,
            }
            for world in worlds
        ],
    }


@router.post("/scenarios/starfire", status_code=201)
def create_starfire_fixture(
    payload: StarfireFixtureCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _require_debug_environment(settings)
    if payload.variant not in {"underpowered", "combat_ready", "strategic"}:
        raise AppError("FIXTURE_VARIANT_INVALID", "Unknown Starfire fixture variant")
    service = GameService(db)
    player = service.create_player(f"Starfire-{payload.variant}")
    player.level = 1 if payload.variant == "underpowered" else 2
    if payload.variant == "strategic":
        player.gold = 80
        command_owner_id = seed_id("npc:shen_ce")
    else:
        command_owner_id = seed_id("npc:captain_aria")
    command_owner = db.get(NPC, command_owner_id)
    if command_owner is None:
        raise AppError("WORLD_NOT_SEEDED", "Starfire content has not been seeded", status_code=503)
    session = ConversationSession(player_id=player.id, npc_id=command_owner.id)
    db.add(session)
    db.commit()
    officer_keys = ["shen_ce", "han_lie", "lu_ning"]
    officers = list(db.scalars(select(NPC).where(NPC.key.in_(officer_keys))).all())
    appointments = {
        appointment.npc_id: appointment
        for appointment in db.scalars(
            select(OfficerAppointment).where(
                OfficerAppointment.player_id == player.id,
            )
        ).all()
    }
    return {
        "variant": payload.variant,
        "player_id": str(player.id),
        "npc_id": str(command_owner.id),
        "commanding_officer_id": str(command_owner.id),
        "session_id": str(session.id),
        "level": player.level,
        "officers": [
            {
                "id": str(officer.id),
                "key": officer.key,
                "name": officer.name,
                "role": officer.role.value,
                "doctrine": officer.doctrine,
                "authority_limits": effective_authority_limits(
                    officer,
                    (
                        appointments[officer.id].authority_overrides
                        if officer.id in appointments
                        else None
                    ),
                ),
                "authority_policy_version": (
                    appointments[officer.id].version
                    if officer.id in appointments
                    else officer.profile_version
                ),
                "authority_policy_status": (
                    "INVALID"
                    if authority_policy_errors(
                        officer.authority_limits,
                        (
                            appointments[officer.id].authority_overrides
                            if officer.id in appointments
                            else None
                        ),
                    )
                    else "VALID"
                ),
                "permissions": sorted(
                    name for name, allowed in officer.permission_profile.items() if allowed
                ),
            }
            for officer in officers
        ],
    }


@router.post("/world-events/{event_id}/resolve")
def resolve_world_event(
    event_id: UUID,
    payload: WorldEventResolve,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _require_debug_environment(settings)
    operation = db.get(WorldOperation, event_id)
    if operation is None:
        raise NotFoundError("world_operation", event_id)
    resolution_key = payload.idempotency_key or f"debug-resolve-{event_id}"
    operation = GameService(db).resolve_world_operation(event_id, resolution_key)
    db.commit()
    task = db.get(AgentTask, operation.task_id) if operation.task_id else None
    return {
        "event": "WORLD_EVENT_RESOLVED",
        "id": str(operation.id),
        "operation_id": str(operation.id),
        "event_type": f"{operation.operation_type}_RESOLVED",
        "status": operation.status.value,
        "outcome": operation.outcome,
        "task_id": str(task.id) if task is not None else None,
    }


@router.post("/scenarios/starfire/{player_id}/encounter-turn")
def play_starfire_encounter_turn(
    player_id: UUID,
    payload: StarfirePlayerTurn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _require_debug_environment(settings)
    service = GameService(db)
    player = service.get_player(player_id)
    template = db.scalar(select(QuestTemplate).where(QuestTemplate.key == "secure_starfire_road"))
    if template is None:
        raise AppError("WORLD_NOT_SEEDED", "Starfire content has not been seeded", status_code=503)
    quest = db.scalar(
        select(Quest).where(
            Quest.player_id == player.id,
            Quest.template_id == template.id,
        )
    )
    if quest is None:
        raise NotFoundError("starfire_quest", player.id)
    if quest.status == QuestStatus.AVAILABLE:
        service.accept_quest(player.id, quest.id)
    crossroads_id = seed_id("node:starfire_crossroads")
    road_id = seed_id("node:starfire_road")
    if player.current_node_id != road_id:
        if player.current_node_id != crossroads_id:
            service.enter_node(player.id, crossroads_id)
        service.enter_node(player.id, road_id)
    nonce = uuid4().hex
    encounter = service.start_encounter(
        player.id,
        seed_id("encounter:starfire_road_raiders"),
        f"fixture-start-{nonce}",
    )
    encounter = service.attempt_encounter(
        encounter.id,
        payload.strategy,
        f"fixture-attempt-{nonce}",
    )
    if encounter.result == "VICTORY":
        db.refresh(quest)
        if quest.reward_status == RewardStatus.ELIGIBLE:
            service.claim_reward(player.id, quest.id)
    db.commit()
    return {
        "encounter_run_id": str(encounter.id),
        "result": encounter.result,
        "strategy": payload.strategy,
        "quest_status": quest.status.value,
        "reward_status": quest.reward_status.value,
        "player_level": player.level,
        "player_gold": player.gold,
    }


def _require_debug_environment(settings: Settings) -> None:
    if settings.app_env == "production":
        raise AppError(
            "DEBUG_FIXTURE_DISABLED",
            "Debug scenario fixtures are disabled in production",
            status_code=404,
        )
