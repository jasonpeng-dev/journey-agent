from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import NodeStatus, QuestStatus, RewardStatus
from app.infrastructure.db.models import PlayerNodeState, Quest
from app.services.game import GameService, seed_id


def test_locked_and_unreachable_nodes_are_rejected(client: TestClient) -> None:
    created = client.post("/api/v1/players", json={"name": "悟空"}).json()
    player_id = created["id"]
    response = client.post(
        f"/api/v1/players/{player_id}/nodes/{seed_id('node:fire_foothills')}/enter"
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "NODE_LOCKED"


def test_encounter_quest_reward_is_idempotent(session: Session) -> None:
    service = GameService(session)
    with session.begin():
        player = service.create_player("悟空")
        quest = service.create_quest(
            player.id,
            seed_id("npc:guanyin"),
            "clear_fire_foothills",
            "平定山脚",
            "击败山脚守卫",
            "quest-create-0001",
        )
        service.accept_quest(player.id, quest.id)
        service.unlock_node(player.id, "fire_foothills")
        player.current_node_id = seed_id("node:guanyin_shrine")
        service.enter_node(player.id, seed_id("node:fire_foothills"))
        run = service.start_encounter(
            player.id, seed_id("encounter:fire_foothills_guardians"), "enc-start-0001"
        )
        run = service.attempt_encounter(run.id, "CAUTIOUS", "enc-attempt-0001")
    assert run.result == "DEFEAT"

    with session.begin():
        player.level = 2
        second = service.start_encounter(
            player.id, seed_id("encounter:fire_foothills_guardians"), "enc-start-0002"
        )
        service.attempt_encounter(second.id, "CAUTIOUS", "enc-attempt-0002")
        replay = service.start_encounter(
            player.id, seed_id("encounter:fire_foothills_guardians"), "enc-start-0002"
        )
        assert replay.id == second.id
        settlement_replay = service.attempt_encounter(second.id, "CAUTIOUS", "enc-attempt-0002")
        assert settlement_replay.id == second.id
        quest = session.scalar(select(Quest).where(Quest.id == quest.id))
        assert quest is not None
        assert quest.status == QuestStatus.COMPLETED
        service.claim_reward(player.id, quest.id)
    assert player.gold == 50
    state = session.get(PlayerNodeState, (player.id, seed_id("node:red_boy_cave")))
    assert state is not None and state.status == NodeStatus.AVAILABLE
    assert quest.reward_status == RewardStatus.CLAIMED

    session.rollback()
    response_error = None
    try:
        with session.begin():
            service.claim_reward(player.id, quest.id)
    except Exception as exc:
        response_error = getattr(exc, "code", None)
    assert response_error == "REWARD_ALREADY_CLAIMED"
    assert player.gold == 50
