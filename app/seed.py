from app.infrastructure.db.session import SessionLocal
from app.services.seed import seed_demo_world


def main() -> None:
    with SessionLocal() as db, db.begin():
        seed_demo_world(db)


if __name__ == "__main__":
    main()
