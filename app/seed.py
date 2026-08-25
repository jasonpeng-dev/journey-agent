from app.infrastructure.db.session import SessionLocal
from app.services.seed import seed_scenario_definitions


def main() -> None:
    with SessionLocal() as db, db.begin():
        seed_scenario_definitions(db)


if __name__ == "__main__":
    main()
