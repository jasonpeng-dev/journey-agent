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


def valid_resource_state_identity(
    definition: ScenarioDefinitionV2,
    resource_key: str,
    scope_node_key: str | None,
) -> bool:
    """Return whether a persisted runtime Resource identity belongs to a Version.

    A scoped Resource row is materialized lazily.  The Scenario still defines
    which scopes are legal; it does not need to enumerate every future balance
    row in ``resource_initial_states``.
    """

    if resource_key not in {item.key for item in definition.world.resources}:
        return False
    if scope_node_key is None:
        return True
    locality = definition.metadata.locality
    if not locality.enabled or not locality.scoped_resources:
        return False
    node = definition.world.node(scope_node_key)
    return node is not None and node.node_type_key == locality.region_node_type_key


__all__ = [
    "resource_identity",
    "resource_initial_states",
    "resource_state_key",
    "valid_resource_state_identity",
]
