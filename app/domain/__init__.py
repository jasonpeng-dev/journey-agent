"""Domain types and contracts shared by application and infrastructure layers."""

from app.domain.runtime_scope import (
    RUNTIME_OWNERSHIP,
    GameInstanceContext,
    GameInstanceId,
    PlayerId,
    RuntimeOwner,
    RuntimeOwnershipContract,
    RuntimeScope,
    RuntimeScopeContractError,
    RuntimeScopeResolver,
    ScenarioVersionId,
)

__all__ = [
    "RUNTIME_OWNERSHIP",
    "GameInstanceContext",
    "GameInstanceId",
    "PlayerId",
    "RuntimeOwner",
    "RuntimeOwnershipContract",
    "RuntimeScope",
    "RuntimeScopeContractError",
    "RuntimeScopeResolver",
    "ScenarioVersionId",
]
