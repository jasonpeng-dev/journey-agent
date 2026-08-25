from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import get_db
from app.main import app
from app.scenarios.builtin import require_builtin_v2_version
from tests.scenario_fixtures import STARFIRE_TEST


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        with db.begin():
            # Generic runtime tests use this test-only fixture; production seed remains v2.0-only.
            require_builtin_v2_version(db, STARFIRE_TEST)
        yield db


@pytest.fixture
def client(session: Session) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite+pysqlite:///:memory:",
        model_provider="mock",
        model_name="mock-model",
        model_api_key=None,
        developer_api_token=SecretStr("test-developer"),
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
