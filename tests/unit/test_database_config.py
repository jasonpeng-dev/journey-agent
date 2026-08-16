from pathlib import Path

from app.core.config import resolved_database_target


def test_relative_sqlite_target_is_absolute_and_secret_free() -> None:
    expected_path = (Path.cwd() / "journey_dev.db").resolve().as_posix()

    assert resolved_database_target("sqlite+pysqlite:///./journey_dev.db") == (
        f"sqlite+pysqlite:///{expected_path}"
    )


def test_docker_sqlite_target_keeps_the_container_path() -> None:
    assert resolved_database_target("sqlite+pysqlite:////app/data/journey.db") == (
        "sqlite+pysqlite:////app/data/journey.db"
    )


def test_non_sqlite_target_hides_password() -> None:
    target = resolved_database_target(
        "postgresql+psycopg://journey:secret@localhost:5432/journey"
    )

    assert target == "postgresql+psycopg://journey:***@localhost:5432/journey"
    assert "secret" not in target
