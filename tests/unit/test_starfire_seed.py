from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import NodeStatus
from app.infrastructure.db.models import PlayerDomainState, PlayerNodeState, WorldNode
from app.services.game import GameService, seed_id


def test_seed_contains_only_canonical_starfire_nodes(session: Session) -> None:
    nodes = list(session.scalars(select(WorldNode).order_by(WorldNode.key)).all())

    assert {node.key for node in nodes} == {
        "capital_council",
        "north_village",
        "northern_valley",
        "enemy_north_supply_route",
        "starfire_outpost",
        "northern_trade_route",
    }
    assert all(node.key not in {"valley_entrance", "ambush_valley"} for node in nodes)


def test_player_initialization_projects_canonical_definition_to_legacy_runtime(
    session: Session,
) -> None:
    service = GameService(session)
    player = service.create_player("Canonical Lord")
    domain = session.get(PlayerDomainState, player.id)
    supply_node = session.scalar(
        select(WorldNode).where(WorldNode.key == "enemy_north_supply_route")
    )
    assert domain is not None and supply_node is not None
    supply_state = session.get(PlayerNodeState, (player.id, supply_node.id))

    assert player.gold == 80
    assert domain.soldiers_total == 300
    assert domain.food == 100
    assert domain.morale == 60
    assert service.get_world_fact(player.id, "enemy_supply_route") == {"status": "UNKNOWN"}
    assert supply_state is not None
    assert supply_state.status == NodeStatus.LOCKED


def test_legacy_valley_unlock_keys_resolve_without_rewriting_the_caller(
    session: Session,
) -> None:
    service = GameService(session)
    player = service.create_player("Legacy Alias Lord")

    entrance_state = service.unlock_node(player.id, "valley_entrance")
    ambush_state = service.unlock_node(player.id, "ambush_valley")
    northern_valley = session.get(WorldNode, entrance_state.node_id)

    assert entrance_state is ambush_state
    assert northern_valley is not None
    assert northern_valley.key == "northern_valley"


def test_first_clearance_failure_makes_formal_supply_node_available(
    session: Session,
) -> None:
    service = GameService(session)
    player = service.create_player("Supply Discovery Lord")
    operation = service.start_military_operation(
        player_id=player.id,
        officer_npc_id=seed_id("npc:han_lie"),
        task_id=None,
        source_step_id=None,
        target_key="ambush_valley",
        troop_count=180,
        mission_type="CLEAR_VALLEY",
        strategy="STANDARD",
        idempotency_key="supply-discovery-operation",
    )

    resolved = service.resolve_world_operation(operation.id, "supply-discovery-resolution")
    supply_node = session.scalar(
        select(WorldNode).where(WorldNode.key == "enemy_north_supply_route")
    )
    assert resolved.outcome is not None
    assert resolved.outcome["failure_code"] == "ENCOUNTER_DEFEAT"
    assert supply_node is not None
    supply_state = session.get(PlayerNodeState, (player.id, supply_node.id))
    assert supply_state is not None
    assert supply_state.status == NodeStatus.AVAILABLE
    assert service.get_world_fact(player.id, "enemy_supply_route")["status"] == "ACTIVE"
