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
from app.domain.scenario import BehaviorBundleRef, ScenarioDefinition, ScenarioDefinitionAny
from app.domain.scenario_v2 import ScenarioDefinitionV2

__all__ = [
    "RUNTIME_OWNERSHIP",
    "BehaviorBundleRef",
    "GameInstanceContext",
    "GameInstanceId",
    "PlayerId",
    "RuntimeOwner",
    "RuntimeOwnershipContract",
    "RuntimeScope",
    "RuntimeScopeContractError",
    "RuntimeScopeResolver",
    "ScenarioDefinition",
    "ScenarioDefinitionAny",
    "ScenarioDefinitionV2",
    "ScenarioVersionId",
]
