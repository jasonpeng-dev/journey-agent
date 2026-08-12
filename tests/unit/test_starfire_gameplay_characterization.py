from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import NodeStatus
from app.infrastructure.db.models import PlayerDomainState, PlayerNodeState, WorldNode
from app.services.game import GameService, seed_id


def test_reconnaissance_characterization(session: Session) -> None:
    service = GameService(session)
    player = service.create_player("Recon Characterization Lord")
    operation = service.start_recon_operation(
        player_id=player.id,
        officer_npc_id=seed_id("npc:han_lie"),
        task_id=None,
        source_step_id=None,
        target_key="valley_entrance",
        troop_count=60,
        approach="CAUTIOUS",
        idempotency_key="characterize-recon-0001",
    )

    resolved = service.resolve_world_operation(operation.id, "characterize-recon-resolution")
    domain = session.get(PlayerDomainState, player.id)

    assert resolved.outcome == {
        "result": "PARTIAL_SUCCESS",
        "facts_discovered": ["valley_intelligence"],
        "casualties": 0,
    }
    assert service.get_world_fact(player.id, "valley_intelligence")["status"] == "PARTIAL"
    assert service.get_world_fact(player.id, "ambush_status") == {}
    assert domain is not None
    assert (domain.soldiers_total, domain.soldiers_committed, domain.morale) == (300, 0, 60)


@pytest.mark.parametrize(
    ("food_offer", "requested_support", "granted_support", "food_remaining"),
    [
        (10, "GUIDE", "INTELLIGENCE", 90),
        (20, "GUIDE", "GUIDE", 80),
        (25, "SUPPLIES", "SUPPLIES", 75),
    ],
)
def test_village_support_characterization(
    session: Session,
    food_offer: int,
    requested_support: str,
    granted_support: str,
    food_remaining: int,
) -> None:
    service = GameService(session)
    player = service.create_player(f"Support Characterization {food_offer}")

    result = service.negotiate_village_support(
        player_id=player.id,
        food_offer=food_offer,
        requested_support=requested_support,
    )

    assert result == {
        "village_support": granted_support,
        "food_remaining": food_remaining,
        "fact_version": 2,
    }
    assert service.get_world_fact(player.id, "village_support") == {
        "status": granted_support,
        "food_offer": food_offer,
    }


def test_first_clear_threat_failure_characterization(session: Session) -> None:
    service = GameService(session)
    player = service.create_player("First Clear Characterization Lord")
    operation = service.start_military_operation(
        player_id=player.id,
        officer_npc_id=seed_id("npc:han_lie"),
        task_id=None,
        source_step_id=None,
        target_key="ambush_valley",
        troop_count=180,
        mission_type="CLEAR_VALLEY",
        strategy="STANDARD",
        idempotency_key="characterize-first-clear-0001",
    )

    resolved = service.resolve_world_operation(
        operation.id,
        "characterize-first-clear-resolution",
    )
    domain = session.get(PlayerDomainState, player.id)

    assert resolved.outcome == {
        "result": "DEFEAT",
        "mission_type": "CLEAR_VALLEY",
        "failure_code": "ENCOUNTER_DEFEAT",
        "casualties": 18,
        "facts_discovered": ["enemy_supply_route"],
    }
    assert service.get_world_fact(player.id, "enemy_supply_route")["status"] == "ACTIVE"
    assert service.get_world_fact(player.id, "valley_intelligence")["status"] == "COMPLETE"
    assert domain is not None
    assert (domain.soldiers_total, domain.soldiers_committed, domain.morale) == (282, 0, 50)
    assert _node_status(session, player.id, "enemy_north_supply_route") == NodeStatus.AVAILABLE


def test_disrupt_supply_characterization(session: Session) -> None:
    service = GameService(session)
    player = service.create_player("Disrupt Characterization Lord")
    service.set_world_fact(player.id, "enemy_supply_route", {"status": "ACTIVE"})
    service.set_world_fact(player.id, "village_support", {"status": "GUIDE"})
    operation = service.start_military_operation(
        player_id=player.id,
        officer_npc_id=seed_id("npc:han_lie"),
        task_id=None,
        source_step_id=None,
        target_key="enemy_north_supply_route",
        troop_count=80,
        mission_type="DISRUPT_SUPPLY",
        strategy="CAUTIOUS",
        idempotency_key="characterize-disrupt-0001",
    )

    resolved = service.resolve_world_operation(operation.id, "characterize-disrupt-resolution")
    domain = session.get(PlayerDomainState, player.id)

    assert resolved.outcome == {
        "result": "VICTORY",
        "mission_type": "DISRUPT_SUPPLY",
        "casualties": 2,
        "facts_changed": ["enemy_supply_route"],
    }
    assert service.get_world_fact(player.id, "enemy_supply_route")["status"] == "DISRUPTED"
    assert domain is not None
    assert (domain.soldiers_total, domain.soldiers_committed, domain.morale) == (298, 0, 63)


def test_second_clear_threat_success_characterization(session: Session) -> None:
    service = GameService(session)
    player = service.create_player("Second Clear Characterization Lord")
    service.set_world_fact(player.id, "enemy_supply_route", {"status": "DISRUPTED"})
    service.set_world_fact(player.id, "village_support", {"status": "GUIDE"})
    operation = service.start_military_operation(
        player_id=player.id,
        officer_npc_id=seed_id("npc:han_lie"),
        task_id=None,
        source_step_id=None,
        target_key="ambush_valley",
        troop_count=160,
        mission_type="CLEAR_VALLEY",
        strategy="CAUTIOUS",
        idempotency_key="characterize-second-clear-0001",
    )

    resolved = service.resolve_world_operation(
        operation.id,
        "characterize-second-clear-resolution",
    )
    domain = session.get(PlayerDomainState, player.id)

    assert resolved.outcome == {
        "result": "VICTORY",
        "mission_type": "CLEAR_VALLEY",
        "casualties": 3,
        "facts_changed": ["valley_security"],
    }
    assert service.get_world_fact(player.id, "valley_security")["status"] == "SAFE"
    assert service.get_world_fact(player.id, "ambush_status") == {}
    assert domain is not None
    assert (domain.soldiers_total, domain.soldiers_committed, domain.morale) == (297, 0, 65)
    assert _node_status(session, player.id, "starfire_outpost") == NodeStatus.AVAILABLE


def test_repair_characterization(session: Session) -> None:
    service = GameService(session)
    player = service.create_player("Repair Characterization Lord")
    service.set_world_fact(player.id, "valley_security", {"status": "SAFE"})
    operation = service.start_outpost_repair(
        player_id=player.id,
        officer_npc_id=seed_id("npc:lu_ning"),
        task_id=None,
        source_step_id=None,
        target_key="starfire_outpost",
        repair_level="TEMPORARY",
        food_commitment=20,
        gold_commitment=15,
        idempotency_key="characterize-repair-0001",
    )
    domain = session.get(PlayerDomainState, player.id)

    assert domain is not None
    assert (domain.food, player.gold) == (80, 65)

    resolved = service.resolve_world_operation(operation.id, "characterize-repair-resolution")

    assert resolved.outcome == {
        "result": "COMPLETED",
        "outpost_status": "OPERATIONAL",
        "facts_changed": ["starfire_outpost_status"],
    }
    assert service.get_world_fact(player.id, "starfire_outpost_status")["status"] == "OPERATIONAL"
    assert _node_status(session, player.id, "starfire_outpost") == NodeStatus.AVAILABLE


def test_trade_route_characterization(session: Session) -> None:
    service = GameService(session)
    player = service.create_player("Trade Characterization Lord")
    service.set_world_fact(player.id, "valley_security", {"status": "SAFE"})
    service.set_world_fact(player.id, "starfire_outpost_status", {"status": "OPERATIONAL"})
    service.set_world_fact(player.id, "village_support", {"status": "GUIDE"})
    operation = service.start_trade_route_test(
        player_id=player.id,
        officer_npc_id=seed_id("npc:lu_ning"),
        task_id=None,
        source_step_id=None,
        route_key="northern_trade_route",
        idempotency_key="characterize-trade-0001",
    )

    resolved = service.resolve_world_operation(operation.id, "characterize-trade-resolution")

    assert resolved.outcome == {
        "result": "COMPLETED",
        "trade_route_status": "OPEN",
        "facts_changed": ["northern_trade_route_status"],
    }
    assert service.get_world_fact(player.id, "northern_trade_route_status")["status"] == "OPEN"
    assert _node_status(session, player.id, "northern_trade_route") == NodeStatus.AVAILABLE


def _node_status(
    session: Session,
    player_id: UUID,
    node_key: str,
) -> NodeStatus:
    node = session.scalar(select(WorldNode).where(WorldNode.key == node_key))
    assert node is not None
    state = session.get(PlayerNodeState, (player_id, node.id))
    assert state is not None
    return state.status
