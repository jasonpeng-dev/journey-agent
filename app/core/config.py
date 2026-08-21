from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


def resolved_database_target(database_url: str) -> str:
    """Render the resolved database target without exposing credentials."""

    url = make_url(database_url)
    if url.drivername.startswith("sqlite"):
        database = url.database
        if database is None or database == "":
            return f"{url.drivername}://"
        if database == ":memory:":
            return f"{url.drivername}:///:memory:"
        if database.startswith("/"):
            path_text = database
        else:
            path = Path(database)
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            path_text = path.as_posix()
        return f"{url.drivername}:///{path_text}"
    return url.render_as_string(hide_password=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Journey Agent Backend"
    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+pysqlite:///./journey_dev.db"
    model_provider: Literal["mock", "openai_compatible"] = "mock"
    model_base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4.1-mini"
    model_api_key: SecretStr | None = None
    developer_api_token: SecretStr | None = None
    model_timeout_seconds: float = Field(default=20, gt=0, le=120)
    model_total_timeout_seconds: float = Field(default=60, gt=0, le=300)
    model_max_output_tokens: int = Field(default=8192, ge=256, le=32768)
    agent_max_rounds: int = Field(default=5, ge=1, le=10)
    agent_max_tool_calls: int = Field(default=8, ge=1, le=20)
    planner_max_steps: int = Field(default=10, ge=1, le=12)
    planner_max_wait_steps: int = Field(default=4, ge=0, le=6)
    planner_max_replans: int = Field(default=2, ge=0, le=5)
    planner_max_generation_attempts: int = Field(default=2, ge=1, le=3)


@lru_cache
def get_settings() -> Settings:
    return Settings()
