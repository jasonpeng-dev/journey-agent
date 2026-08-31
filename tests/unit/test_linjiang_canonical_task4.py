from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.enums import AgentTaskStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ObjectiveDefinitionV2, ScenarioDefinitionV2
from app.infrastructure.db.models import AgentPlan, GameInstanceFactState, Player
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService
from tests.scenario_fixtures import LINJIANG_V2_TEST

_CANONICAL = LINJIANG_V2_TEST
_TASK4_KEY = "restore_east_emergency_water_supply"
_TASK4_REQUIREMENT_KEYS = (
    "water_treatment_plant_operational",
    "water_treatment_plant_power_supply",
    "south_pump_station_operational",
    "south_pump_station_power_supply",
    "east_water_pump_station_operational",
    "east_water_pump_station_power_supply",
    "south_communication_core_operational",
)


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


def _set_requirements(
    session: Session,
    game_instance_id: Any,
    objective: ObjectiveDefinitionV2,
) -> None:
    for requirement in objective.completion_requirements:
        row = session.get(
            GameInstanceFactState,
            (game_instance_id, requirement.node_key, requirement.fact_key),
        )
        assert row is not None
        row.truth_value = requirement.accepted_values[0]
    session.flush()


def test_canonical_task4_has_only_the_seven_real_completion_requirements() -> None:
    definition = _CANONICAL
    objective = _objective(definition, _TASK4_KEY)

    assert tuple(item.key for item in objective.completion_requirements) == _TASK4_REQUIREMENT_KEYS
    assert len(objective.completion_requirements) == 7

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


@pytest.mark.parametrize("missing_key", _TASK4_REQUIREMENT_KEYS)
def test_task4_requires_each_completion_requirement(
    session: Session,
    missing_key: str,
) -> None:
    definition, runtime, agent = _canonical_runtime(session)
    objective = _objective(definition, _TASK4_KEY)
    for requirement in objective.completion_requirements:
        if requirement.key == missing_key:
            continue
        row = session.get(
            GameInstanceFactState,
            (runtime.instance.id, requirement.node_key, requirement.fact_key),
        )
        assert row is not None
        row.truth_value = requirement.accepted_values[0]
    session.flush()

    task = agent.create_task(
        runtime.session,
        _TASK4_KEY,
        initialize_plan=False,
    )

    assert task.status == AgentTaskStatus.ACTIVE
    assert not agent.evaluate(task).completed


def test_task4_completes_directly_when_all_seven_facts_are_satisfied(
    session: Session,
) -> None:
    definition, runtime, agent = _canonical_runtime(session)
    objective = _objective(definition, _TASK4_KEY)
    _set_requirements(session, runtime.instance.id, objective)

    task = agent.create_task(runtime.session, _TASK4_KEY, initialize_plan=False)

    assert task.status == AgentTaskStatus.SUCCEEDED
    assert agent.evaluate(task).completed
    assert session.scalar(select(AgentPlan).where(AgentPlan.task_id == task.id)) is None
