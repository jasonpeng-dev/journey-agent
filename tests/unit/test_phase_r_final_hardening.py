from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentError, GenericAgentService, GenericGoalResolver
from app.agent.planning_context import legal_candidate_id
from app.agent.provider import (
    GoalSelection,
    GoalSelectionRequest,
    PlanProposal,
    PlanRequest,
    PlanStepProposal,
)
from app.domain.enums import AgentTaskStatus, DecisionStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.infrastructure.db.models import (
    ActionDecisionRequest,
    AgentPlan,
    AgentStep,
    GameInstanceActor,
    ObjectiveScopeImmutableError,
    Player,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import (
    GenericActionError,
    GenericActionService,
    GenericApprovalRequired,
)
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService
from tests.unit.test_scenario_definition_v2 import _contract_scenario_document


class FakeProvider:
    model_name = "fake-structured-model"

    def __init__(self, *, selected: tuple[str, ...], proposed: tuple[PlanStepProposal, ...] = ()):
        self.selected = selected
        self.proposed = proposed
        self.goal_request: GoalSelectionRequest | None = None
        self.plan_request: PlanRequest | None = None

    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection:
        self.goal_request = request
        return GoalSelection(objective_keys=self.selected)

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.plan_request = request
        return PlanProposal(steps=self.proposed)


def _provider_step(
    action_key: str,
    target_key: str,
    actor_key: str,
    parameters: dict[str, object],
) -> PlanStepProposal:
    return PlanStepProposal(
        candidate_id=legal_candidate_id(action_key, actor_key, target_key),
        parameters=parameters,  # type: ignore[arg-type]
    )


def _runtime(session: Session, document: dict | None = None):  # type: ignore[type-arg,no-untyped-def]
    payload = deepcopy(document or _contract_scenario_document())
    parameter = payload["actions"][0]["parameters"][0]
    parameter["required"] = False
    parameter["default"] = 2
    definition = ScenarioDefinitionV2.model_validate(payload)
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(definition)
    version = ScenarioService(session).publish_draft(scenario.id, expected_revision=1).version
    player = Player(name=f"hardening-{scenario.key}")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=f"hardening-{scenario.id}",
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return definition, runtime, scope


def test_nonempty_multi_objective_scope_is_frozen_and_uses_and_completion(
    session: Session,
) -> None:
    document = _contract_scenario_document()
    document["world"]["nodes"][0]["facts"].append(
        {
            "key": "conscious",
            "name": "Conscious",
            "value_type": "BOOLEAN",
            "initial_value": False,
            "initial_visibility": "KNOWN",
        }
    )
    document["rules"][0]["effects"].insert(
        1,
        {
            "kind": "SET_FACT",
            "node": {"kind": "EXPLICIT", "node_key": "patient_one"},
            "fact_key": "conscious",
            "value": {"source": "LITERAL", "literal": True},
        },
    )
    document["objectives"].append(
        {
            "key": "restore_consciousness",
            "name": "Restore Consciousness",
            "description": "Make Patient One conscious.",
            "completion_requirements": [
                {
                    "key": "patient_is_conscious",
                    "node_key": "patient_one",
                    "fact_key": "conscious",
                    "accepted_values": [True],
                    "description": "Patient One is conscious.",
                }
            ],
        }
    )
    _definition, runtime, scope = _runtime(session, document)
    provider = FakeProvider(selected=("stabilize_patient", "restore_consciousness"))
    resolver = GenericGoalResolver(provider=provider)
    agent = GenericAgentService(session, scope, goal_resolver=resolver)

    task = agent.create_task(runtime.session, "help this patient")

    assert task.objective_scope_keys == ["restore_consciousness", "stabilize_patient"]
    assert task.objective_scope_hash
    frozen = (list(task.objective_scope_keys), task.objective_scope_hash)
    agent.plan(task, reason="TEST_REPLAN")
    assert (task.objective_scope_keys, task.objective_scope_hash) == frozen
    assert not agent.evaluate(task).completed
    agent.execute_next(task)
    assert agent.evaluate(task).completed

    session.commit()
    task.objective_scope_keys = ["stabilize_patient"]
    with pytest.raises(ObjectiveScopeImmutableError):
        session.flush()
    session.rollback()


def test_planner_delegates_by_exact_actor_capability_and_execution_rechecks(
    session: Session,
) -> None:
    document = _contract_scenario_document()
    document["actors"]["actor_profiles"].append(
        {
            "key": "nurse_ana",
            "name": "Nurse Ana",
            "role_key": "clinician",
            "persona": "A careful nurse.",
            "initial_node_key": "triage_room",
            "allowed_action_keys": ["treat_patient"],
        }
    )
    _definition, runtime, scope = _runtime(session, document)
    agent = GenericAgentService(session, scope)
    task = agent.create_task(runtime.session, "stabilize the patient")
    plan = session.scalar(select(AgentPlan).where(AgentPlan.task_id == task.id))
    assert plan is not None
    steps = session.scalars(select(AgentStep).where(AgentStep.plan_id == plan.id)).all()

    assert [(step.action_intent, step.assigned_actor_key) for step in steps] == [
        ("treat_patient", "nurse_ana")
    ]
    nurse = session.get(GameInstanceActor, (runtime.instance.id, "nurse_ana"))
    assert nurse is not None
    nurse.capabilities = []
    with pytest.raises(GenericActionError) as caught:
        GenericActionService(session, scope).execute_action(
            actor_key="nurse_ana",
            action_key="treat_patient",
            target_key="patient_one",
            parameters={"dosage": 2},
            idempotency_key="capability-recheck",
        )
    assert caught.value.code == "RUNTIME_ACTOR_BINDING_INVALID"


def test_generic_approval_gate_pauses_and_consumes_exact_decision(session: Session) -> None:
    document = deepcopy(_contract_scenario_document())
    document["actions"][0]["authority_policy"] = {
        "autonomous_limits": [{"parameter_key": "dosage", "maximum": 1}]
    }
    _definition, _runtime_record, scope = _runtime(session, document)
    actions = GenericActionService(session, scope)

    with pytest.raises(GenericApprovalRequired) as caught:
        actions.execute_action(
            actor_key="doctor_lee",
            action_key="treat_patient",
            target_key="patient_one",
            parameters={"dosage": 2},
            idempotency_key="approval-action",
        )
    decision = caught.value.decision
    assert decision.status == DecisionStatus.PENDING
    actions.decide(decision.id, approve=True)
    result = actions.execute_action(
        actor_key="doctor_lee",
        action_key="treat_patient",
        target_key="patient_one",
        parameters={"dosage": 2},
        idempotency_key="approval-action",
        decision_id=decision.id,
    )
    assert result.operation.status.value == "RESOLVED"
    assert session.get(ActionDecisionRequest, decision.id).status == DecisionStatus.CONSUMED


def test_agent_pauses_for_approval_and_resumes_without_replanning(session: Session) -> None:
    document = deepcopy(_contract_scenario_document())
    document["actions"][0]["authority_policy"] = {
        "autonomous_limits": [{"parameter_key": "dosage", "maximum": 1}]
    }
    _definition, runtime, scope = _runtime(session, document)
    agent = GenericAgentService(session, scope)
    task = agent.create_task(runtime.session, "stabilize the patient")

    paused = agent.execute_next(task)
    assert paused is not None
    assert paused.status.value == "REQUIRES_PLAYER_DECISION"
    assert task.status == AgentTaskStatus.REQUIRES_PLAYER_DECISION
    decision = session.scalar(
        select(ActionDecisionRequest).where(ActionDecisionRequest.source_step_id == paused.id)
    )
    assert decision is not None and decision.status == DecisionStatus.PENDING
    GenericActionService(session, scope).decide(decision.id, approve=True)

    resumed = agent.execute_next(task)
    assert resumed is not None and resumed.status.value == "SUCCEEDED"
    assert task.status == AgentTaskStatus.SUCCEEDED
    assert task.replan_count == 0


def test_provider_goal_and_plan_are_structured_exact_version_and_validated(
    session: Session,
) -> None:
    _definition, runtime, scope = _runtime(session)
    provider = FakeProvider(
        selected=("stabilize_patient",),
        proposed=(_provider_step("treat_patient", "patient_one", "doctor_lee", {"dosage": 2}),),
    )
    agent = GenericAgentService(session, scope, provider=provider)
    task = agent.create_task(runtime.session, "work out what is wrong")

    assert provider.goal_request is not None
    assert {item["key"] for item in provider.goal_request.objective_candidates} == {
        "stabilize_patient",
    }
    assert provider.plan_request is not None
    assert provider.plan_request.objective_keys == ("stabilize_patient",)
    assert "patient_one.stable" in provider.plan_request.known_world["facts"]
    assert task.status == AgentTaskStatus.ACTIVE
    # Phase D permits only one non-terminal Task per GameInstance. Complete this
    # task before exercising a second provider proposal in the same Runtime.
    task.status = AgentTaskStatus.SUCCEEDED
    session.flush()

    bad = FakeProvider(
        selected=("stabilize_patient",),
        proposed=(_provider_step("treat_patient", "patient_one", "invented_actor", {"dosage": 2}),),
    )
    with pytest.raises(GenericAgentError) as caught:
        GenericAgentService(session, scope, provider=bad).create_task(
            runtime.session, "another unclear diagnosis request"
        )
    assert caught.value.code == "MODEL_PLAN_REJECTED"
