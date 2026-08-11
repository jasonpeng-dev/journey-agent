from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PlayerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PlayerView(ORMModel):
    id: UUID
    name: str
    level: int
    gold: int
    current_node_id: UUID | None
    status: str
    version: int


class NodeView(BaseModel):
    id: UUID
    key: str
    name: str
    type: str
    status: str


class QuestCreate(BaseModel):
    template_key: str
    difficulty: Literal["EASY", "NORMAL", "HARD"] = "NORMAL"
    narrative_title: str = Field(min_length=1, max_length=160)
    narrative_description: str = Field(min_length=1, max_length=1000)


class EncounterStart(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=160)


class EncounterAttempt(BaseModel):
    strategy: Literal["CAUTIOUS", "AGGRESSIVE", "NEGOTIATE"]
    idempotency_key: str = Field(min_length=8, max_length=160)


class RelationshipUpdate(BaseModel):
    delta: int = Field(ge=-5, le=5)
    reason_code: Literal[
        "PLAYER_HELPED_NPC",
        "PLAYER_THREATENED_NPC",
        "PLAYER_KEPT_PROMISE",
        "PLAYER_BROKE_PROMISE",
    ]
    evidence: str = Field(min_length=1, max_length=500)


class GameAction(BaseModel):
    action: Literal["UNLOCK_NODE", "GRANT_ITEM", "GRANT_GOLD", "COMPLETE_QUEST", "STORE_MEMORY"]
    parameters: dict[str, Any]
    source_type: Literal["QUEST", "ENCOUNTER", "CONVERSATION"]
    source_id: str
    idempotency_key: str = Field(min_length=8, max_length=160)
