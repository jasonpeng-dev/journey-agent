from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
        env_parse_none_str="null",
        extra="ignore",
    )

    app_name: str = "Journey Agent Backend"
    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+pysqlite:///./journey_dev.db"
    model_provider: Literal["mock", "openai_compatible"] = "mock"
    model_base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4.1-mini"
    semantic_model: str | None = None
    goal_resolution_observability: Literal["NORMAL", "DEBUG"] = "NORMAL"
    model_api_key: SecretStr | None = None
    developer_api_token: SecretStr | None = None
    model_thinking_mode: Literal["disabled", "enabled"] = "disabled"
    model_reasoning_effort: Literal["low", "medium", "high"] = "low"
    # One independent Plan / Replan / Repair Provider invocation is bounded at
    # 300 seconds. HTTPX's phase timeout is deliberately disabled; the
    # invocation deadline below is the meaningful runtime safety boundary.
    plan_timeout_seconds: float = Field(default=300, gt=0, le=300)
    # Planning Cycle and Replan+Repair chains intentionally have no overall
    # wall-clock deadline. null/unlimited are the explicit env-file
    # representations of that policy; a positive value remains available for
    # a future opt-in operation-level cap.
    plan_total_timeout_seconds: float | None = Field(default=None, gt=0)
    goal_resolution_timeout_seconds: float = Field(default=20, gt=0, le=120)
    model_max_output_tokens: int | None = Field(default=8192, ge=256, le=32768)
    model_max_repair_attempts_per_cycle: int = 2
    agent_max_rounds: int = Field(default=5, ge=1, le=10)
    agent_max_tool_calls: int = Field(default=8, ge=1, le=20)
    planner_max_steps: int = Field(default=10, ge=1, le=12)
    planner_max_wait_steps: int = Field(default=4, ge=0, le=6)
    planner_max_replans: int = Field(default=2, ge=0, le=5)
    planner_max_generation_attempts: int = Field(default=2, ge=1, le=3)

    @field_validator("plan_total_timeout_seconds", mode="before")
    @classmethod
    def parse_unlimited_plan_total_timeout(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() in {
            "unlimited",
            "none",
            "null",
        }:
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
