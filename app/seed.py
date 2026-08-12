from app.infrastructure.db.session import SessionLocal
from app.services.seed import seed_demo_world, seed_scenario_definitions


def main() -> None:
    with SessionLocal() as db, db.begin():
        seed_demo_world(db)
        seed_scenario_definitions(db)


if __name__ == "__main__":
    main()
