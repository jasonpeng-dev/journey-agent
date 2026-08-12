"""Resolve declarative Tool interaction requirements against a scenario world."""

from collections.abc import Mapping

from pydantic import BaseModel

from app.core.errors import AppError
from app.domain.world import NodeDefinition
from app.services.interaction_targets import (
    InteractionTargetResolver,
    interaction_target_resolver,
)
from app.tools.base import InteractionRequirement

RECON_INTERACTION = InteractionRequirement(
    target_argument="target_key",
    interaction_key="reconnaissance",
)
MILITARY_INTERACTION = InteractionRequirement(
    target_argument="target_key",
    operation_argument="mission_type",
    operation_interactions={
        "CLEAR_VALLEY": "clear_threat",
        "DISRUPT_SUPPLY": "disrupt_supply",
        "ESCORT": "clear_threat",
        "DEFEND": "clear_threat",
    },
)
VILLAGE_SUPPORT_INTERACTION = InteractionRequirement(
    interaction_key="negotiate_support",
    infer_unique_target=True,
)
REPAIR_INTERACTION = InteractionRequirement(
    interaction_key="repair",
    infer_unique_target=True,
)
TRADE_ROUTE_INTERACTION = InteractionRequirement(
    target_argument="route_key",
    interaction_key="test_trade_route",
)


def resolve_tool_interaction(
    scenario_key: str,
    requirement: InteractionRequirement,
    arguments: BaseModel | Mapping[str, object],
    *,
    resolver: InteractionTargetResolver = interaction_target_resolver,
) -> NodeDefinition:
    values = arguments.model_dump(mode="python") if isinstance(arguments, BaseModel) else arguments
    required_interaction = _required_interaction(requirement, values)
    if requirement.infer_unique_target:
        return resolver.resolve_unique_target(scenario_key, required_interaction)
    target_argument = requirement.target_argument
    raw_target = values.get(target_argument) if target_argument is not None else None
    if not isinstance(raw_target, str) or not raw_target:
        raise AppError(
            "INTERACTION_TARGET_MISSING",
            "The tool did not provide a valid interaction target",
            details={"target_argument": target_argument},
        )
    return resolver.resolve_target(
        scenario_key,
        raw_target,
        required_interaction,
    )


def _required_interaction(
    requirement: InteractionRequirement,
    arguments: Mapping[str, object],
) -> str:
    if requirement.interaction_key is not None:
        return requirement.interaction_key
    operation_argument = requirement.operation_argument
    operation = arguments.get(operation_argument) if operation_argument is not None else None
    interaction = (
        requirement.operation_interactions.get(operation) if isinstance(operation, str) else None
    )
    if interaction is None:
        raise AppError(
            "TOOL_INTERACTION_OPERATION_INVALID",
            "The tool operation does not map to a registered interaction requirement",
            details={
                "operation_argument": operation_argument,
                "operation": operation,
            },
        )
    return interaction
