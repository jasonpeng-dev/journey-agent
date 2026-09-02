from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.enums import AgentTaskStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import (
    DerivedDependencyKind,
    ObjectiveDefinitionV2,
    ObjectiveRequirementKind,
    ScenarioDefinitionV2,
)
from app.domain.world import Visibility
from app.infrastructure.db.models import AgentPlan, GameInstanceFactState, Player
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService
from tests.scenario_fixtures import LINJIANG_V2_TEST, predefined_goal_resolution

_CANONICAL = LINJIANG_V2_TEST
_TASK4_KEY = "restore_east_emergency_water_supply"
_TASK4_DERIVED_KEY = "east_emergency_water_supply"
_TASK4_GOAL = "恢复东部应急供水"


def _objective(definition: ScenarioDefinitionV2, key: str) -> ObjectiveDefinitionV2:
    objective = next(item for item in definition.objectives if item.key == key)
    return objective


def _canonical_runtime(
    session: Session,
) -> tuple[ScenarioDefinitionV2, object, GenericAgentService]:
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(_CANONICAL)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name="linjiang-task4-canonical")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="linjiang-task4-canonical",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return _CANONICAL, runtime, GenericAgentService(session, scope)


def _set_derived_dependencies(
    session: Session,
    game_instance_id: Any,
    definition: ScenarioDefinitionV2,
    *,
    missing: tuple[str, str] | None = None,
) -> None:
    derived = definition.derived_state_definitions[_TASK4_DERIVED_KEY]
    for dependency in derived.dependencies:
        if dependency.kind != DerivedDependencyKind.FACT:
            continue
        assert dependency.node_key is not None and dependency.fact_key is not None
        row = session.get(
            GameInstanceFactState,
            (game_instance_id, dependency.node_key, dependency.fact_key),
        )
        assert row is not None
        row.truth_value = (
            False
            if dependency.accepted_values[0] is True
            else "UNAVAILABLE"
            if dependency.accepted_values[0] == "AVAILABLE"
            else dependency.accepted_values[0]
        )
        if missing != (dependency.node_key, dependency.fact_key):
            row.truth_value = dependency.accepted_values[0]
        row.visibility = Visibility.KNOWN
    session.flush()


def test_canonical_task4_has_one_derived_capability_requirement() -> None:
    definition = _CANONICAL
    objective = _objective(definition, _TASK4_KEY)

    assert len(objective.completion_requirements) == 1
    requirement = objective.completion_requirements[0]
    assert requirement.key == _TASK4_DERIVED_KEY
    assert requirement.kind == ObjectiveRequirementKind.DERIVED_STATE
    assert requirement.derived_key == _TASK4_DERIVED_KEY
    derived = definition.derived_state_definitions[_TASK4_DERIVED_KEY]
    assert {
        (item.node_key, item.fact_key, item.accepted_values)
        for item in derived.dependencies
        if item.kind == DerivedDependencyKind.FACT
    } == {
        ("water_treatment_plant", "operational", (True,)),
        ("water_treatment_plant", "power_supply", ("AVAILABLE",)),
        ("south_pump_station", "operational", (True,)),
        ("south_pump_station", "power_supply", ("AVAILABLE",)),
        ("east_water_pump_station", "operational", (True,)),
        ("east_water_pump_station", "power_supply", ("AVAILABLE",)),
        ("south_communication_core", "operational", (True,)),
    }

    document = definition.model_dump(mode="json")
    serialized = json.dumps(document, ensure_ascii=False)
    assert "activate_emergency_water_transfer" not in serialized
    assert all(
        fact.key != "east_emergency_water_supply"
        for node in definition.world.nodes
        for fact in node.facts
    )
    assert "water_transfer_target" not in serialized

    action_keys = {item.key for item in definition.actions}
    interaction_keys = {item.key for item in definition.interactions}
    assert "activate_emergency_water_transfer" not in action_keys
    assert "water_transfer_target" not in interaction_keys
    assert all(item.action_key in action_keys for item in definition.rules)
    assert all(item.required_interaction_key in interaction_keys for item in definition.actions)


@pytest.mark.parametrize(
    "missing",
    [
        ("water_treatment_plant", "operational"),
        ("water_treatment_plant", "power_supply"),
        ("south_pump_station", "operational"),
        ("south_pump_station", "power_supply"),
        ("east_water_pump_station", "operational"),
        ("east_water_pump_station", "power_supply"),
        ("south_communication_core", "operational"),
    ],
)
def test_task4_requires_each_derived_dependency(
    session: Session,
    missing: tuple[str, str],
) -> None:
    definition, runtime, agent = _canonical_runtime(session)
    _set_derived_dependencies(session, runtime.instance.id, definition, missing=missing)

    task = agent.create_task(
        runtime.session,
        _TASK4_GOAL,
        resolved_goal=predefined_goal_resolution(_TASK4_KEY),
        initialize_plan=False,
    )

    assert task.status == AgentTaskStatus.ACTIVE
    assert not agent.evaluate(task).completed


def test_task4_completes_directly_when_all_derived_dependencies_are_satisfied(
    session: Session,
) -> None:
    definition, runtime, agent = _canonical_runtime(session)
    _set_derived_dependencies(session, runtime.instance.id, definition)

    task = agent.create_task(
        runtime.session,
        _TASK4_GOAL,
        resolved_goal=predefined_goal_resolution(_TASK4_KEY),
        initialize_plan=False,
    )

    assert task.status == AgentTaskStatus.SUCCEEDED
    assert agent.evaluate(task).completed
    assert session.scalar(select(AgentPlan).where(AgentPlan.task_id == task.id)) is None
