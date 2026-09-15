"""Shared completion result and operation-match contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictStr, model_validator

from app.domain.action_invocation import ActionInvocationBinding


class CompletionStatus(StrEnum):
    COMPLETED = "COMPLETED"
    UNSATISFIED = "UNSATISFIED"
    UNKNOWN = "UNKNOWN"


class CompletionEvidence(BaseModel):
    """Opaque, auditable proof metadata kept separate from player wording."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["WORLD_STATE", "WORLD_OPERATION"]
    operation_id: UUID | None = None


class CompletionResult(BaseModel):
    """One requirement's authoritative result and optional public status."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_identity: StrictStr = Field(min_length=1, max_length=300)
    requirement_kind: Literal["STATE", "OPERATION"]
    status: CompletionStatus
    value: JsonValue | None = None
    authoritative_evidence: CompletionEvidence | None = None
    player_visible_satisfied: bool | None = None

    @property
    def satisfied(self) -> bool:
        return self.status == CompletionStatus.COMPLETED


class OperationMatchConstraint(BaseModel):
    """Minimal Phase-1 exact operation matching constraint.

    ``parameters=None`` means that no parameter constraint is declared.  When
    present, the mapping is normalized by the referenced Action and matched as
    one complete invocation.  Cumulative/range/count semantics are deliberately
    outside this contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_key: StrictStr = Field(min_length=1, max_length=100)
    actor_key: StrictStr | None = Field(default=None, max_length=100)
    target_key: StrictStr | None = Field(default=None, max_length=160)
    bindings: tuple[ActionInvocationBinding, ...] = ()
    parameters: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_bindings(self) -> OperationMatchConstraint:
        roles = tuple(item.role for item in self.bindings)
        if len(set(roles)) != len(roles):
            raise ValueError("Operation match binding roles must be unique")
        ordered = tuple(sorted(self.bindings, key=lambda item: item.role))
        if ordered != self.bindings:
            object.__setattr__(self, "bindings", ordered)
        return self


__all__ = [
    "CompletionEvidence",
    "CompletionResult",
    "CompletionStatus",
    "OperationMatchConstraint",
]
