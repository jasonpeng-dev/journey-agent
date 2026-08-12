from dataclasses import FrozenInstanceError
from uuid import UUID, uuid4

import pytest

from app.domain.runtime_scope import (
    RUNTIME_OWNERSHIP,
    GameInstanceContext,
    GameInstanceId,
    PlayerId,
    RuntimeOwner,
    RuntimeScope,
    RuntimeScopeContractError,
    ScenarioVersionId,
)


def _scope() -> RuntimeScope:
    return RuntimeScope(
        game_instance_id=GameInstanceId(uuid4()),
        player_id=PlayerId(uuid4()),
        scenario_version_id=ScenarioVersionId(uuid4()),
    )


def test_runtime_scope_requires_all_three_explicit_ids_and_is_immutable() -> None:
    scope = _scope()

    assert isinstance(scope.game_instance_id, UUID)
    assert isinstance(scope.player_id, UUID)
    assert isinstance(scope.scenario_version_id, UUID)
    assert GameInstanceContext is RuntimeScope

    with pytest.raises(FrozenInstanceError):
        scope.scenario_version_id = ScenarioVersionId(uuid4())  # type: ignore[misc]


@pytest.mark.parametrize("field_name", ["game_instance_id", "player_id", "scenario_version_id"])
def test_runtime_scope_rejects_missing_or_empty_ids(field_name: str) -> None:
    values = {
        "game_instance_id": GameInstanceId(uuid4()),
        "player_id": PlayerId(uuid4()),
        "scenario_version_id": ScenarioVersionId(uuid4()),
    }
    values[field_name] = None  # type: ignore[assignment]

    with pytest.raises(RuntimeScopeContractError) as caught:
        RuntimeScope(**values)  # type: ignore[arg-type]

    assert caught.value.code == "RUNTIME_SCOPE_ID_INVALID"


def test_runtime_scope_rejects_instance_player_and_version_mixing() -> None:
    scope = _scope()

    for changed_field, code in (
        ("game_instance_id", "RUNTIME_SCOPE_INSTANCE_MISMATCH"),
        ("player_id", "RUNTIME_SCOPE_PLAYER_MISMATCH"),
        ("scenario_version_id", "RUNTIME_SCOPE_VERSION_DRIFT"),
    ):
        values = {
            "game_instance_id": scope.game_instance_id,
            "player_id": scope.player_id,
            "scenario_version_id": scope.scenario_version_id,
        }
        values[changed_field] = uuid4()
        other = RuntimeScope(**values)  # type: ignore[arg-type]

        with pytest.raises(RuntimeScopeContractError) as caught:
            scope.assert_compatible(other)

        assert caught.value.code == code


def test_runtime_ownership_is_explicit_and_non_overlapping() -> None:
    assert RUNTIME_OWNERSHIP.owner_of("player_identity") == RuntimeOwner.PLAYER
    assert RUNTIME_OWNERSHIP.owner_of("node_definition") == RuntimeOwner.SCENARIO_VERSION
    assert RUNTIME_OWNERSHIP.owner_of("truth") == RuntimeOwner.GAME_INSTANCE

    all_groups = (
        RUNTIME_OWNERSHIP.player_owned,
        RUNTIME_OWNERSHIP.scenario_version_owned,
        RUNTIME_OWNERSHIP.game_instance_owned,
    )
    assert sum(len(group) for group in all_groups) == len(set().union(*all_groups))


def test_unclassified_runtime_ownership_fails_closed() -> None:
    with pytest.raises(RuntimeScopeContractError) as caught:
        RUNTIME_OWNERSHIP.owner_of("future_runtime_field")

    assert caught.value.code == "RUNTIME_OWNERSHIP_UNCLASSIFIED"
