"""Run a complete deterministic domain demo against the configured database."""

from uuid import uuid4

from sqlalchemy import select

from app.infrastructure.db.models import NPC, EncounterDefinition
from app.infrastructure.db.session import SessionLocal
from app.services.game import GameService
from app.services.seed import seed_demo_world


def main() -> None:
    with SessionLocal() as db:
        with db.begin():
            seed_demo_world(db)
            service = GameService(db)
            player = service.create_player(f"Demo-{uuid4().hex[:8]}")
            guanyin = db.scalar(select(NPC).where(NPC.key == "guanyin"))
            assert guanyin is not None
            quest = service.create_quest(
                player.id,
                guanyin.id,
                "clear_fire_foothills",
                "Calm the Fire Mountain Foothills",
                "Defeat the guardians and report back.",
                f"demo-quest-{player.id}",
            )
            service.accept_quest(player.id, quest.id)
            service.unlock_node(player.id, "fire_foothills")
            player.current_node_id = guanyin.current_node_id
            service.enter_node(
                player.id,
                next(
                    node_id
                    for (node_id,) in db.execute(
                        select(EncounterDefinition.node_id).where(
                            EncounterDefinition.key == "fire_foothills_guardians"
                        )
                    )
                ),
            )
            player.level = 2
            encounter = db.scalar(
                select(EncounterDefinition).where(
                    EncounterDefinition.key == "fire_foothills_guardians"
                )
            )
            assert encounter is not None
            run = service.start_encounter(player.id, encounter.id, f"demo-start-{player.id}")
            service.attempt_encounter(run.id, "CAUTIOUS", f"demo-attempt-{player.id}")
            service.claim_reward(player.id, quest.id)
        print(
            {
                "player_id": str(player.id),
                "quest": quest.status.value,
                "encounter": run.status.value,
                "gold": player.gold,
            }
        )


if __name__ == "__main__":
    main()
