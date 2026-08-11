from sqlalchemy.orm import Session

from app.domain.enums import RelationshipAttitude
from app.services.game import GameService, attitude_for, seed_id


def test_attitude_boundaries() -> None:
    assert attitude_for(-100) == RelationshipAttitude.HOSTILE
    assert attitude_for(-15) == RelationshipAttitude.UNFRIENDLY
    assert attitude_for(0) == RelationshipAttitude.NEUTRAL
    assert attitude_for(25) == RelationshipAttitude.FRIENDLY
    assert attitude_for(70) == RelationshipAttitude.TRUSTED


def test_relationship_is_clamped(session: Session) -> None:
    service = GameService(session)
    with session.begin():
        player = service.create_player("悟空")
        relationship = service.update_relationship(
            player.id, seed_id("npc:guanyin"), 5, "PLAYER_HELPED_NPC"
        )
        relationship.score = 99
        relationship = service.update_relationship(
            player.id, seed_id("npc:guanyin"), 5, "PLAYER_HELPED_NPC"
        )
    assert relationship.score == 100
    assert relationship.attitude == RelationshipAttitude.TRUSTED
