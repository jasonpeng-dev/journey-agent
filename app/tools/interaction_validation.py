"""Resolve declarative Tool interaction requirements against a scenario world."""

from collections.abc import Mapping

from pydantic import BaseModel

from app.core.errors import AppError
from app.domain.world import AccessState, NodeDefinition
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
    target_argument="target_key",
    interaction_key="repair",
    infer_unique_target=True,
)
TRADE_ROUTE_INTERACTION = InteractionRequirement(
    target_argument="target_key",
    legacy_target_arguments=("route_key",),
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
    target_arguments = (
        (requirement.target_argument,) if requirement.target_argument is not None else ()
    ) + requirement.legacy_target_arguments
    supplied_targets = [
        (argument, values.get(argument))
        for argument in target_arguments
        if isinstance(values.get(argument), str) and values.get(argument)
    ]
    if supplied_targets:
        resolved_targets = [
            (
                argument,
                raw_target,
                resolver.resolve_target(scenario_key, raw_target, required_interaction),
            )
            for argument, raw_target in supplied_targets
            if isinstance(raw_target, str)
        ]
        canonical_keys = {node.key for _argument, _raw_target, node in resolved_targets}
        if len(canonical_keys) != 1:
            raise AppError(
                "INTERACTION_TARGET_CONFLICT",
                "The tool target arguments resolve to different scenario nodes",
                details={
                    "required_interaction": required_interaction,
                    "targets": {
                        argument: {"raw": raw_target, "canonical": node.key}
                        for argument, raw_target, node in resolved_targets
                    },
                },
            )
        return resolved_targets[0][2]
    if requirement.infer_unique_target:
        return resolver.resolve_unique_target(scenario_key, required_interaction)
    raise AppError(
        "INTERACTION_TARGET_MISSING",
        "The tool did not provide a valid interaction target",
        details={"target_argument": requirement.target_argument},
    )


def interaction_target_guidance(
    scenario_key: str,
    requirement: InteractionRequirement,
    *,
    resolver: InteractionTargetResolver = interaction_target_resolver,
    target_states: Mapping[str, AccessState] | None = None,
) -> str:
    """Build model-facing target guidance, omitting nodes absent from knowledge."""

    if requirement.interaction_key is not None:
        target_text = _interaction_targets_text(
            scenario_key,
            requirement.interaction_key,
            resolver,
            target_states,
        )
    else:
        target_text = "; ".join(
            f"{operation} -> "
            f"{_interaction_targets_text(scenario_key, interaction, resolver, target_states)}"
            for operation, interaction in requirement.operation_interactions.items()
        )
    return (
        "Known canonical targets with current runtime access (execution rechecks access): "
        f"{target_text}."
    )


def _interaction_targets_text(
    scenario_key: str,
    interaction: str,
    resolver: InteractionTargetResolver,
    target_states: Mapping[str, AccessState] | None,
) -> str:
    targets = resolver.supported_target_keys(scenario_key, interaction)
    if target_states is not None:
        targets = tuple(target for target in targets if target in target_states)
        rendered = ", ".join(f"{target} [{target_states[target].value}]" for target in targets)
    else:
        rendered = ", ".join(targets)
    return f"{interaction}: {rendered or '(none known)'}"


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
