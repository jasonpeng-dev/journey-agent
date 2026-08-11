from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.authority import authority_policy_errors, effective_authority_limits
from app.core.errors import NotFoundError
from app.infrastructure.db.models import (
    NPC,
    EncounterRun,
    InventoryItem,
    ItemDefinition,
    Memory,
    OfficerAppointment,
    PlayerDomainState,
    PlayerNPCRelationship,
    PlayerWorldFact,
    Quest,
    World,
    WorldNode,
    WorldOperation,
)
from app.infrastructure.db.session import get_db
from app.schemas.game import (
    EncounterAttempt,
    EncounterStart,
    NodeView,
    PlayerCreate,
    PlayerView,
)
from app.services.game import GameService

router = APIRouter(prefix="/api/v1")


@router.post("/players", response_model=PlayerView, status_code=201)
def create_player(payload: PlayerCreate, db: Session = Depends(get_db)) -> PlayerView:
    with db.begin():
        player = GameService(db).create_player(payload.name)
    return PlayerView.model_validate(player)


@router.get("/players/{player_id}", response_model=PlayerView)
def get_player(player_id: UUID, db: Session = Depends(get_db)) -> PlayerView:
    return PlayerView.model_validate(GameService(db).get_player(player_id))


@router.get("/players/{player_id}/state")
def player_state(player_id: UUID, db: Session = Depends(get_db)) -> dict[str, object]:
    player = GameService(db).get_player(player_id)
    domain = db.get(PlayerDomainState, player_id)
    return {
        "player": PlayerView.model_validate(player),
        "nodes": list_nodes(player_id, db),
        "quests": quests(player_id, db),
        "inventory": inventory(player_id, db),
        "facts": [
            {"key": fact.key, "value": fact.value, "version": fact.version}
            for fact in db.scalars(
                select(PlayerWorldFact)
                .where(PlayerWorldFact.player_id == player_id)
                .order_by(PlayerWorldFact.key)
            ).all()
        ],
        "relationships": [
            {
                "npc_key": npc.key,
                "npc_name": npc.name,
                "score": relationship.score,
                "attitude": relationship.attitude,
                "version": relationship.version,
            }
            for relationship, npc in db.execute(
                select(PlayerNPCRelationship, NPC)
                .join(NPC, NPC.id == PlayerNPCRelationship.npc_id)
                .where(PlayerNPCRelationship.player_id == player_id)
                .order_by(NPC.name)
            ).all()
        ],
        "domain": (
            None
            if domain is None
            else {
                "soldiers_total": domain.soldiers_total,
                "soldiers_available": domain.soldiers_total - domain.soldiers_committed,
                "soldiers_committed": domain.soldiers_committed,
                "food": domain.food,
                "gold": player.gold,
                "morale": domain.morale,
                "version": domain.version,
            }
        ),
        "officers": [
            {
                "id": str(npc.id),
                "key": npc.key,
                "name": npc.name,
                "role": npc.role.value,
                "doctrine": npc.doctrine,
                "authority_limits": effective_authority_limits(
                    npc,
                    appointment.authority_overrides,
                ),
                "base_authority_limits": npc.authority_limits,
                "authority_overrides": appointment.authority_overrides,
                "authority_policy_version": appointment.version,
                "authority_policy_status": (
                    "INVALID"
                    if authority_policy_errors(
                        npc.authority_limits,
                        appointment.authority_overrides,
                    )
                    else "VALID"
                ),
                "authority_policy_errors": authority_policy_errors(
                    npc.authority_limits,
                    appointment.authority_overrides,
                ),
                "relationship": relationship.score,
                "appointment_status": appointment.status,
                "memory_summary": _officer_memory_summary(
                    db,
                    player_id,
                    npc.id,
                ),
            }
            for appointment, npc, relationship in db.execute(
                select(OfficerAppointment, NPC, PlayerNPCRelationship)
                .join(NPC, NPC.id == OfficerAppointment.npc_id)
                .join(
                    PlayerNPCRelationship,
                    (PlayerNPCRelationship.player_id == OfficerAppointment.player_id)
                    & (PlayerNPCRelationship.npc_id == OfficerAppointment.npc_id),
                )
                .where(OfficerAppointment.player_id == player_id)
                .order_by(NPC.name)
            ).all()
        ],
        "operations": [
            {
                "id": str(operation.id),
                "operation_type": operation.operation_type,
                "target_key": operation.target_key,
                "status": operation.status.value,
                "outcome": operation.outcome,
            }
            for operation in db.scalars(
                select(WorldOperation)
                .where(WorldOperation.player_id == player_id)
                .order_by(WorldOperation.created_at)
            ).all()
        ],
    }


def _officer_memory_summary(
    db: Session,
    player_id: UUID,
    officer_id: UUID,
) -> list[str]:
    return [
        memory.content
        for memory in db.scalars(
            select(Memory)
            .where(Memory.player_id == player_id, Memory.npc_id == officer_id)
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(3)
        ).all()
    ]


@router.get("/worlds/{world_id}")
def get_world(world_id: UUID, db: Session = Depends(get_db)) -> dict[str, object]:
    world = db.get(World, world_id)
    if not world:
        raise NotFoundError("world", world_id)
    nodes = db.scalars(select(WorldNode).where(WorldNode.world_id == world_id)).all()
    return {
        "id": world.id,
        "key": world.key,
        "name": world.name,
        "chapter": world.chapter,
        "nodes": [{"id": node.id, "key": node.key, "type": node.type} for node in nodes],
    }


@router.get("/players/{player_id}/nodes", response_model=list[NodeView])
def list_nodes(player_id: UUID, db: Session = Depends(get_db)) -> list[NodeView]:
    return [
        NodeView(id=node.id, key=node.key, name=node.name, type=node.type, status=state.status)
        for node, state in GameService(db).list_nodes(player_id)
    ]


@router.post("/players/{player_id}/nodes/{node_id}/enter", response_model=PlayerView)
def enter_node(player_id: UUID, node_id: UUID, db: Session = Depends(get_db)) -> PlayerView:
    with db.begin():
        player = GameService(db).enter_node(player_id, node_id)
    return PlayerView.model_validate(player)


@router.post("/players/{player_id}/nodes/{node_id}/complete")
def complete_node(
    player_id: UUID, node_id: UUID, db: Session = Depends(get_db)
) -> dict[str, object]:
    with db.begin():
        state = GameService(db).complete_current_node(player_id, node_id)
    return {"node_id": state.node_id, "status": state.status, "version": state.version}


@router.get("/players/{player_id}/inventory")
def inventory(player_id: UUID, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    GameService(db).get_player(player_id)
    rows = db.execute(
        select(InventoryItem, ItemDefinition)
        .join(ItemDefinition)
        .where(InventoryItem.player_id == player_id, InventoryItem.quantity > 0)
    ).all()
    return [
        {"key": item.key, "name": item.name, "quantity": inventory.quantity}
        for inventory, item in rows
    ]


@router.get("/players/{player_id}/quests")
def quests(player_id: UUID, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    GameService(db).get_player(player_id)
    values = db.scalars(select(Quest).where(Quest.player_id == player_id)).all()
    return [
        {
            "id": quest.id,
            "template_key": quest.template.key,
            "title": quest.narrative_title,
            "status": quest.status,
            "progress": quest.progress,
            "target": quest.template.objective_quantity,
            "reward_status": quest.reward_status,
        }
        for quest in values
    ]


@router.post("/players/{player_id}/quests/{quest_id}/accept")
def accept_quest(
    player_id: UUID, quest_id: UUID, db: Session = Depends(get_db)
) -> dict[str, object]:
    with db.begin():
        quest = GameService(db).accept_quest(player_id, quest_id)
    return {"id": quest.id, "status": quest.status}


@router.post("/players/{player_id}/quests/{quest_id}/claim-reward")
def claim_reward(
    player_id: UUID, quest_id: UUID, db: Session = Depends(get_db)
) -> dict[str, object]:
    with db.begin():
        quest = GameService(db).claim_reward(player_id, quest_id)
    return {"id": quest.id, "status": quest.status, "reward_status": quest.reward_status}


@router.post("/players/{player_id}/encounters/{encounter_id}/start", status_code=201)
def start_encounter(
    player_id: UUID,
    encounter_id: UUID,
    payload: EncounterStart,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    with db.begin():
        run = GameService(db).start_encounter(player_id, encounter_id, payload.idempotency_key)
    return {"id": run.id, "status": run.status}


@router.post("/encounter-runs/{run_id}/attempt")
def attempt_encounter(
    run_id: UUID, payload: EncounterAttempt, db: Session = Depends(get_db)
) -> dict[str, object]:
    with db.begin():
        run = GameService(db).attempt_encounter(run_id, payload.strategy, payload.idempotency_key)
    return {"id": run.id, "status": run.status, "result": run.result}


@router.get("/encounter-runs/{run_id}")
def get_encounter(run_id: UUID, db: Session = Depends(get_db)) -> dict[str, object]:
    run = db.get(EncounterRun, run_id)
    if not run:
        raise NotFoundError("encounter_run", run_id)
    return {"id": run.id, "status": run.status, "result": run.result}
