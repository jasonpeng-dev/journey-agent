from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentError, GenericAgentService, GenericGoalResolver
from app.domain.enums import AgentPlanStatus, AgentTaskStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    AgentPlan,
    AgentStep,
    GameInstanceFactState,
    GameInstanceResourceState,
    Player,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService
from tests.unit.test_scenario_definition_v2 import _medical_scenario_document


def _definition(*, preflight: bool = False) -> ScenarioDefinitionV2:
    document = deepcopy(_medical_scenario_document())
    parameter = document["actions"][0]["parameters"][0]
    parameter["required"] = False
    parameter["default"] = 2
    if preflight:
        document["rules"].insert(
            0,
            {
                "key": "medicine_required",
                "phase": "PREFLIGHT",
                "action_key": "treat_patient",
                "priority": 100,
                "condition": {
                    "kind": "RESOURCE_COMPARE",
                    "resource_key": "medicine",
                    "operator": "LT",
                    "value": 1,
                },
                "effects": [
                    {
                        "kind": "EMIT_FAILURE",
                        "failure_code": "INSUFFICIENT_MEDICINE",
                        "message": "Find medicine before treatment.",
                        "retryable": True,
                    }
                ],
            },
        )
    return ScenarioDefinitionV2.model_validate(document)


def _agent(
    session: Session,
    *,
    preflight: bool = False,
) -> tuple[GenericAgentService, object]:
    definition = _definition(preflight=preflight)
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name="generic-agent")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="generic-agent",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return GenericAgentService(session, scope), runtime


def test_goal_resolver_uses_only_exact_version_candidates() -> None:
    definition = _definition()
    resolver = GenericGoalResolver(selector=lambda _goal, _items: "invented_objective")

    exact = resolver.resolve("stabilize the patient", definition)
    invented = resolver.resolve("conquer the galaxy", definition)

    assert exact.objective_key == "stabilize_patient"
    assert exact.source == "DETERMINISTIC"
    assert invented.status == "UNSUPPORTED"


def test_generic_agent_completes_goal_plan_action_and_backend_objective(
    session: Session,
) -> None:
    agent, runtime = _agent(session)

    task = agent.create_task(runtime.session, "stabilize the patient")
    step = agent.execute_next(task)

    assert step is not None and step.selected_tool_name == "execute_action"
    assert task.status == AgentTaskStatus.SUCCEEDED
    assert agent.evaluate(task).completed
    assert (
        task.objective_catalog_version == f"scenario-version:{runtime.instance.scenario_version_id}"
    )
    assert task.owner_actor_key == "doctor_lee"
    assert task.owner_actor_key == "doctor_lee"


def test_planner_uses_knowledge_projection_not_hidden_truth(session: Session) -> None:
    agent, runtime = _agent(session)
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "patient_one", "stable"),
    )
    assert fact is not None
    fact.truth_value = True
    fact.visibility = Visibility.HIDDEN
    session.flush()

    task = agent.create_task(runtime.session, "stabilize the patient")
    plan = session.scalar(
        select(AgentPlan).where(
            AgentPlan.task_id == task.id,
            AgentPlan.status == AgentPlanStatus.ACTIVE,
        )
    )
    assert plan is not None
    steps = session.scalars(select(AgentStep).where(AgentStep.plan_id == plan.id)).all()

    assert len(steps) == 1
    assert steps[0].action_intent == "treat_patient"


def test_retryable_rule_failure_creates_generic_replan_without_fixed_fallback(
    session: Session,
) -> None:
    agent, runtime = _agent(session, preflight=True)
    resource = session.get(
        GameInstanceResourceState,
        (runtime.instance.id, "medicine"),
    )
    assert resource is not None
    resource.value = 0
    session.flush()
    task = agent.create_task(runtime.session, "stabilize the patient")

    failed_step = agent.execute_next(task)

    assert failed_step is not None
    assert failed_step.failure_code == "INSUFFICIENT_MEDICINE"
    assert task.replan_count == 1
    assert task.last_error_code == "INSUFFICIENT_MEDICINE"
    active_plan = session.scalar(
        select(AgentPlan).where(
            AgentPlan.task_id == task.id,
            AgentPlan.status == AgentPlanStatus.ACTIVE,
        )
    )
    assert active_plan is not None
    assert active_plan.replan_reason == "INSUFFICIENT_MEDICINE"
    resource.value = 2
    session.flush()

    agent.execute_next(task)

    assert task.status == AgentTaskStatus.SUCCEEDED


def test_generic_agent_rejects_cross_instance_task(session: Session) -> None:
    agent, runtime = _agent(session)
    task = agent.create_task(runtime.session, "stabilize the patient")
    task.game_instance_id = None

    with pytest.raises(GenericAgentError) as caught:
        agent.execute_next(task)

    assert caught.value.code == "GENERIC_TASK_SCOPE_INVALID"
