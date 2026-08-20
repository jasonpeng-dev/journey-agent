"""Generic resource balance identity and Scenario initialization helpers."""

from __future__ import annotations

from app.domain.enums import ResourcePoolAvailability, ResourcePoolVisibility
from app.domain.scenario_v2 import (
    ResourceInitialStateV2,
    ResourcePoolDefinitionV2,
    ScenarioDefinitionV2,
)


def resource_identity(
    resource_key: str,
    scope_node_key: str | None = None,
    pool_key: str = "default",
) -> str:
    """Return the non-null persistence identity for a logical balance.

    The unsuffixed identity is intentionally the legacy global row identity, so
    existing ``Session.get(..., (instance_id, resource_key))`` callers keep
    working after scoped balances are introduced.
    """

    if pool_key == "default":
        return resource_key if scope_node_key is None else f"{resource_key}@{scope_node_key}"
    scope = scope_node_key or "global"
    return f"{resource_key}@{scope}@{pool_key}"


def resource_state_key(
    resource_key: str,
    scope_node_key: str | None = None,
    pool_key: str = "default",
) -> str:
    """Return the mapping key used by the declarative engine for one balance."""

    return resource_identity(resource_key, scope_node_key, pool_key)


def resource_pool_initial_states(
    definition: ScenarioDefinitionV2,
) -> tuple[ResourcePoolDefinitionV2, ...]:
    """Return explicit Pools, converting legacy balance authoring losslessly."""

    configured = definition.initialization.resource_pools
    configured_resources = {item.resource_key for item in configured}
    converted: list[ResourcePoolDefinitionV2] = list(configured)
    legacy_states = definition.initialization.resource_initial_states
    if legacy_states:
        converted.extend(
            ResourcePoolDefinitionV2(
                pool_key="default",
                resource_key=item.resource_key,
                region_key=item.scope_node_key,
                quantity=item.value,
                reserved_value=item.reserved_value,
                visibility=ResourcePoolVisibility.VISIBLE,
                availability=ResourcePoolAvailability.AVAILABLE,
            )
            for item in legacy_states
            if item.resource_key not in configured_resources
        )
    configured_or_legacy = configured_resources | {item.resource_key for item in legacy_states}
    if not configured and not legacy_states:
        converted.extend(
            ResourcePoolDefinitionV2(
                pool_key="default",
                resource_key=item.key,
                quantity=item.initial_value,
            )
            for item in definition.world.resources
        )
    else:
        converted.extend(
            ResourcePoolDefinitionV2(
                pool_key="default",
                resource_key=item.key,
                quantity=item.initial_value,
            )
            for item in definition.world.resources
            if item.key not in configured_or_legacy
        )
    return tuple(converted)


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
    pool_key: str = "default",
) -> bool:
    """Return whether a persisted runtime Resource identity belongs to a Version.

    A scoped Resource row is materialized lazily.  The Scenario still defines
    which scopes are legal; it does not need to enumerate every future balance
    row in ``resource_initial_states``.
    """

    if resource_key not in {item.key for item in definition.world.resources}:
        return False
    if pool_key != "default" and pool_key not in {
        item.pool_key for item in definition.initialization.resource_pools
    }:
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
    "resource_pool_initial_states",
    "resource_state_key",
    "valid_resource_state_identity",
]
