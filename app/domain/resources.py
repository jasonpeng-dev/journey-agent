"""Generic resource balance identity and Scenario initialization helpers."""

from __future__ import annotations

from app.domain.scenario_v2 import ResourceInitialStateV2, ScenarioDefinitionV2


def resource_identity(resource_key: str, scope_node_key: str | None = None) -> str:
    """Return the non-null persistence identity for a logical balance.

    The unsuffixed identity is intentionally the legacy global row identity, so
    existing ``Session.get(..., (instance_id, resource_key))`` callers keep
    working after scoped balances are introduced.
    """

    return resource_key if scope_node_key is None else f"{resource_key}@{scope_node_key}"


def resource_state_key(resource_key: str, scope_node_key: str | None = None) -> str:
    """Return the mapping key used by the declarative engine for one balance."""

    return resource_identity(resource_key, scope_node_key)


def resource_initial_states(
    definition: ScenarioDefinitionV2,
) -> tuple[ResourceInitialStateV2, ...]:
    """Expand legacy global Resource definitions into explicit initial rows."""

    configured = definition.initialization.resource_initial_states
    if configured:
        return configured
    return tuple(
        ResourceInitialStateV2(resource_key=item.key, value=item.initial_value)
        for item in definition.world.resources
    )


__all__ = ["resource_identity", "resource_initial_states", "resource_state_key"]
