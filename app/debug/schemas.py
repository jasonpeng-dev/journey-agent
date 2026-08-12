from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictDebugRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrategicResetRequest(StrictDebugRequest):
    pass


class StrategicCommandRequest(StrictDebugRequest):
    session_id: UUID
    command: str = Field(min_length=10, max_length=1000)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)


class StrategicDecisionRequest(StrictDebugRequest):
    session_id: UUID
    option_id: str = Field(min_length=1, max_length=80)


class StrategicGoalClarificationRequest(StrictDebugRequest):
    session_id: UUID
    objective_keys: list[str] | None = Field(default=None, min_length=1, max_length=5)
    clarification_text: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def exactly_one_clarification_input(self) -> StrategicGoalClarificationRequest:
        if (self.objective_keys is None) == (self.clarification_text is None):
            raise ValueError("exactly one of objective_keys or clarification_text is required")
        return self


class StrategicWorldEventRequest(StrictDebugRequest):
    session_id: UUID
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)
