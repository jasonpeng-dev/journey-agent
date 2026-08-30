from __future__ import annotations

import json
from pathlib import Path
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
from tests.scenario_fixtures import load_test_scenario

_ROOT = Path(__file__).resolve().parents[2]
_BASELINE_PATH = (
    _ROOT
    / "tests"
    / "fixtures"
    / "scenarios"
    / "linjiang_infrastructure_recovery_v2_0_task3_baseline.yaml"
)
_BASELINE = load_test_scenario(_BASELINE_PATH)
_CANONICAL = load_test_scenario(
    _ROOT / "app" / "scenarios" / "data" / "linjiang_infrastructure_recovery_v2_0.yaml"
)
_TASK3_KEY = "restore_east_emergency_power_network"
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


def _baseline_runtime(
    session: Session,
) -> tuple[ScenarioDefinitionV2, object, GenericAgentService]:
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(_BASELINE)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name="linjiang-task4-baseline")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="linjiang-task4-baseline",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return _BASELINE, runtime, GenericAgentService(session, scope)


def _truth_map(session: Session, game_instance_id: Any) -> dict[tuple[str, str], Any]:
    rows = session.scalars(
        select(GameInstanceFactState).where(
            GameInstanceFactState.game_instance_id == game_instance_id
        )
    )
    return {(row.node_key, row.fact_key): row.truth_value for row in rows}


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


def test_task4_baseline_has_only_the_seven_real_completion_requirements() -> None:
    definition = _BASELINE
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
    definition, runtime, agent = _baseline_runtime(session)
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
    definition, runtime, agent = _baseline_runtime(session)
    objective = _objective(definition, _TASK4_KEY)
    _set_requirements(session, runtime.instance.id, objective)

    task = agent.create_task(runtime.session, _TASK4_KEY, initialize_plan=False)

    assert task.status == AgentTaskStatus.SUCCEEDED
    assert agent.evaluate(task).completed
    assert session.scalar(select(AgentPlan).where(AgentPlan.task_id == task.id)) is None


def test_task2_completed_baseline_supports_task3_then_task4_on_one_runtime(
    session: Session,
) -> None:
    definition, runtime, agent = _baseline_runtime(session)
    truth = _truth_map(session, runtime.instance.id)
    assert all(
        truth[(requirement.node_key, requirement.fact_key)] in requirement.accepted_values
        for key in (
            "restore_central_communication_capability",
            "restore_north_basic_engineering_support",
        )
        for requirement in _objective(definition, key).completion_requirements
    )
    assert not all(
        truth[(requirement.node_key, requirement.fact_key)] in requirement.accepted_values
        for requirement in _objective(definition, _TASK3_KEY).completion_requirements
    )
    assert not all(
        truth[(requirement.node_key, requirement.fact_key)] in requirement.accepted_values
        for requirement in _objective(definition, _TASK4_KEY).completion_requirements
    )

    task3_objective = _objective(definition, _TASK3_KEY)
    _set_requirements(session, runtime.instance.id, task3_objective)
    task3 = agent.create_task(runtime.session, _TASK3_KEY, initialize_plan=False)
    assert task3.status == AgentTaskStatus.SUCCEEDED

    task4 = agent.create_task(runtime.session, _TASK4_KEY, initialize_plan=False)
    assert task4.status == AgentTaskStatus.ACTIVE
    assert not agent.evaluate(task4).completed

    _set_requirements(session, runtime.instance.id, _objective(definition, _TASK4_KEY))
    assert agent.execute_next(task4) is None
    assert task4.status == AgentTaskStatus.SUCCEEDED
    assert agent.evaluate(task4).completed


def test_task3_objective_and_common_recovery_contract_are_unchanged() -> None:
    assert _objective(_BASELINE, _TASK3_KEY) == _objective(_CANONICAL, _TASK3_KEY)

    canonical_actions = {item.key: item for item in _CANONICAL.actions}
    baseline_actions = {item.key: item for item in _BASELINE.actions}
    assert set(baseline_actions).issubset(canonical_actions)
    assert "activate_emergency_water_transfer" not in canonical_actions
    for key, action in baseline_actions.items():
        if key in {"supply_power", "repair_electrical", "repair_industrial_facility"}:
            baseline_document = action.model_dump(mode="json")
            canonical_document = canonical_actions[key].model_dump(mode="json")
            baseline_effects = baseline_document["planning"]["terminal_effects"]
            canonical_effects = canonical_document["planning"]["terminal_effects"]
            assert set(tuple(item.items()) for item in baseline_effects).issubset(
                set(tuple(item.items()) for item in canonical_effects)
            )
            baseline_document["planning"]["terminal_effects"] = []
            canonical_document["planning"]["terminal_effects"] = []
            assert baseline_document == canonical_document
        else:
            assert action == canonical_actions[key]

    canonical_rules = {item.key: item for item in _CANONICAL.rules}
    baseline_rules = {item.key: item for item in _BASELINE.rules}
    intentionally_changed_rules = {
        "repair_electrical_target_profile_required",
        "repair_industrial_facility_target_profile_required",
        "repair_industrial_facility_heavy_equipment_yard_resolution",
    }
    intentionally_removed_rules = {
        "cost_repair_electrical_0_electrical_repair_parts",
        "cost_repair_electrical_1_electrical_repair_parts",
        "repair_electrical_central_hospital_resolution",
        "repair_electrical_north_power_substation_resolution",
    }
    assert (set(baseline_rules) - intentionally_removed_rules).issubset(canonical_rules)
    assert intentionally_removed_rules.isdisjoint(canonical_rules)
    for key, rule in baseline_rules.items():
        if key not in intentionally_changed_rules | intentionally_removed_rules:
            assert rule == canonical_rules[key]
