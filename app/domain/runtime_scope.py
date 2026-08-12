"""Immutable runtime ownership contracts for the Scenario System.

This module deliberately contains no persistence or application-service code.
It freezes the identity that future runtime APIs must carry:

    Player -> GameInstance -> ScenarioVersion

The existing A/B runtime still uses ``player_id`` while the migration is staged;
that compatibility is intentionally outside this contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType, Protocol
from uuid import UUID

GameInstanceId = NewType("GameInstanceId", UUID)
PlayerId = NewType("PlayerId", UUID)
ScenarioVersionId = NewType("ScenarioVersionId", UUID)


class RuntimeScopeContractError(ValueError):
    """Fail-closed error raised when a runtime ownership contract is invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RuntimeOwner(StrEnum):
    """The aggregate that owns a piece of data."""

    PLAYER = "PLAYER"
    SCENARIO_VERSION = "SCENARIO_VERSION"
    GAME_INSTANCE = "GAME_INSTANCE"


@dataclass(frozen=True, slots=True)
class RuntimeOwnershipContract:
    """Explicit ownership manifest for the C0 runtime boundary.

    The field names are contract identifiers, not ORM column names. A new
    runtime concept must be classified here before it is added to a service or
    persistence model.
    """

    player_owned: frozenset[str]
    scenario_version_owned: frozenset[str]
    game_instance_owned: frozenset[str]

    def __post_init__(self) -> None:
        groups = (
            self.player_owned,
            self.scenario_version_owned,
            self.game_instance_owned,
        )
        if any(not isinstance(group, frozenset) for group in groups):
            raise RuntimeScopeContractError(
                "RUNTIME_OWNERSHIP_NOT_IMMUTABLE",
                "Ownership groups must be frozensets",
            )
        for left_index, left in enumerate(groups):
            for right in groups[left_index + 1 :]:
                overlap = left.intersection(right)
                if overlap:
                    raise RuntimeScopeContractError(
                        "RUNTIME_OWNERSHIP_OVERLAP",
                        f"Ownership fields cannot belong to multiple roots: {sorted(overlap)}",
                    )

    def owner_of(self, field: str) -> RuntimeOwner:
        """Return the declared owner or fail closed for an unknown field."""

        if field in self.player_owned:
            return RuntimeOwner.PLAYER
        if field in self.scenario_version_owned:
            return RuntimeOwner.SCENARIO_VERSION
        if field in self.game_instance_owned:
            return RuntimeOwner.GAME_INSTANCE
        raise RuntimeScopeContractError(
            "RUNTIME_OWNERSHIP_UNCLASSIFIED",
            f"The runtime ownership of {field!r} has not been declared",
        )


RUNTIME_OWNERSHIP = RuntimeOwnershipContract(
    player_owned=frozenset(
        {
            "player_identity",
            "long_term_account_information",
        }
    ),
    scenario_version_owned=frozenset(
        {
            "node_definition",
            "fact_definition",
            "relation_definition",
            "interaction_definition",
            "resource_definition",
            "objective_definition",
            "initial_state_definition",
            "behavior_binding",
        }
    ),
    game_instance_owned=frozenset(
        {
            "truth",
            "knowledge",
            "access",
            "resource_balances",
            "current_location",
            "current_state",
            "agent_task",
            "conversation_session",
            "world_operation",
            "decision",
            "memory",
        }
    ),
)


@dataclass(frozen=True, slots=True)
class RuntimeScope:
    """The complete immutable identity of one running game.

    ``scenario_version_id`` is deliberately required alongside the instance
    and player IDs. A runtime must never infer its version from a scenario key,
    a current-published pointer, or any other mutable lookup.
    """

    game_instance_id: GameInstanceId
    player_id: PlayerId
    scenario_version_id: ScenarioVersionId

    def __post_init__(self) -> None:
        for field_name, value in (
            ("game_instance_id", self.game_instance_id),
            ("player_id", self.player_id),
            ("scenario_version_id", self.scenario_version_id),
        ):
            if not isinstance(value, UUID) or value.int == 0:
                raise RuntimeScopeContractError(
                    "RUNTIME_SCOPE_ID_INVALID",
                    f"{field_name} must be a non-empty UUID",
                )

    def assert_compatible(self, other: RuntimeScope) -> None:
        """Reject mixing records from different runtime scopes.

        This is intended for future service boundaries and transaction guards.
        In particular, a changed ScenarioVersion is a version-drift error, not
        a request to resolve the latest published version.
        """

        if not isinstance(other, RuntimeScope):
            raise RuntimeScopeContractError(
                "RUNTIME_SCOPE_TYPE_INVALID",
                "Runtime scope comparisons require another RuntimeScope",
            )
        if self.game_instance_id != other.game_instance_id:
            raise RuntimeScopeContractError(
                "RUNTIME_SCOPE_INSTANCE_MISMATCH",
                "Runtime records belong to different GameInstances",
            )
        if self.player_id != other.player_id:
            raise RuntimeScopeContractError(
                "RUNTIME_SCOPE_PLAYER_MISMATCH",
                "The GameInstance is associated with a different Player",
            )
        if self.scenario_version_id != other.scenario_version_id:
            raise RuntimeScopeContractError(
                "RUNTIME_SCOPE_VERSION_DRIFT",
                "A GameInstance cannot drift to another ScenarioVersion",
            )


# Name the same contract in the terminology used by application services.
GameInstanceContext = RuntimeScope


class RuntimeScopeResolver(Protocol):
    """Resolve an explicit GameInstance into its frozen runtime scope.

    Implementations must raise when the instance or its bound version is
    missing. They must not fall back to a current-published ScenarioVersion.
    """

    def load(self, game_instance_id: GameInstanceId) -> RuntimeScope: ...


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
