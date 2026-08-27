from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import (
    GenericAgentError,
    GenericAgentService,
    GenericGoalResolver,
    normalize_objective_keys,
)
from app.agent.provider import (
    GenericModelProvider,
    GoalSelection,
    GoalSelectionRequest,
    PlanProposal,
    PlanRequest,
    PlanStepProposal,
)
from app.domain.enums import AgentPlanStatus, AgentStepStatus, AgentTaskStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ScenarioDefinitionV2
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    AgentPlan,
    AgentStep,
    AgentTask,
    GameInstanceFactState,
    GameInstanceResourceState,
    PlanningAttempt,
    PlanningCycle,
    Player,
    PlayerExecutionCheckpoint,
)
from app.scenarios.persistence import ScenarioDefinitionRepository
from app.services.game_instances import GameInstanceService
from app.services.play import PlayError, PlayOrchestrator
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.scenarios import ScenarioService
from tests.unit.test_scenario_definition_v2 import _contract_scenario_document


def _definition(*, preflight: bool = False) -> ScenarioDefinitionV2:
    document = deepcopy(_contract_scenario_document())
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


class _RecordingProvider:
    def __init__(self, proposal: PlanProposal) -> None:
        self.proposal = proposal
        self.plan_requests: list[PlanRequest] = []

    @property
    def model_name(self) -> str:
        return "synthetic-provider"

    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection:
        raise AssertionError(f"Unexpected fuzzy goal selection: {request.goal}")

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.plan_requests.append(request)
        return self.proposal


def _accepted_proposal(parameters: dict[str, int] | None = None) -> PlanProposal:
    return PlanProposal(
        steps=(
            PlanStepProposal(
                action_key="treat_patient",
                actor_key="doctor_lee",
                target_key="patient_one",
                parameters=parameters or {"dosage": 2},
            ),
        )
    )


def _planner_owned_definition() -> ScenarioDefinitionV2:
    document = deepcopy(_contract_scenario_document())
    parameter = document["actions"][0]["parameters"][0]
    parameter["required"] = True
    parameter.pop("default", None)
    return ScenarioDefinitionV2.model_validate(document)


def _known_impossible_definition() -> ScenarioDefinitionV2:
    document = deepcopy(_contract_scenario_document())
    document["world"]["nodes"][0]["interaction_keys"] = []
    return ScenarioDefinitionV2.model_validate(document)


def _subsumption_definition() -> ScenarioDefinitionV2:
    document = deepcopy(_contract_scenario_document())
    base_objective = document["objectives"][0]
    document["objectives"].append(
        {
            **base_objective,
            "key": "complete_contract",
            "name": "Complete Contract",
            "description": "Complete the generic contract.",
            "subsumes": ["stabilize_patient"],
            "goal_aliases": [],
            "goal_examples": [],
        }
    )
    return ScenarioDefinitionV2.model_validate(document)


def _agent(
    session: Session,
    *,
    preflight: bool = False,
    definition: ScenarioDefinitionV2 | None = None,
    provider: GenericModelProvider | None = None,
) -> tuple[GenericAgentService, object]:
    actual_definition = definition if definition is not None else _definition(preflight=preflight)
    scenario = ScenarioDefinitionRepository(session).persist_initial_draft(actual_definition)
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
    return GenericAgentService(session, scope, provider=provider), runtime


def test_goal_resolver_uses_only_exact_version_candidates() -> None:
    definition = _definition()
    resolver = GenericGoalResolver(selector=lambda _goal, _items: "invented_objective")

    exact = resolver.resolve("stabilize the patient", definition)
    invented = resolver.resolve("conquer the galaxy", definition)

    assert exact.objective_key == "stabilize_patient"
    assert exact.source == "DETERMINISTIC"
    assert invented.status == "UNSUPPORTED"


def test_objective_subsumption_is_normalized_before_scope_freeze() -> None:
    definition = _subsumption_definition()
    assert normalize_objective_keys(
        definition,
        ("complete_contract", "stabilize_patient"),
    ) == ("complete_contract",)


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
    fact.truth_value = False
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


def test_generic_replan_hard_limit_remains_enforced(session: Session) -> None:
    agent, runtime = _agent(session)
    task = agent.create_task(runtime.session, "stabilize the patient")
    task.replan_count = agent.MAX_REPLANS

    with pytest.raises(GenericAgentError) as caught:
        agent.plan(task, reason="TEST_REPLAN")

    assert caught.value.code == "GENERIC_REPLAN_LIMIT"


def test_formal_play_transition_hard_limit_remains_enforced(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, runtime = _agent(session)
    task = agent.create_task(runtime.session, "stabilize the patient")
    orchestrator = PlayOrchestrator(session, GameInstanceId(runtime.instance.id))
    monkeypatch.setattr(orchestrator.agent, "execute_next", lambda _task, **_kwargs: None)
    orchestrator.MAX_TRANSITIONS = 2

    with pytest.raises(PlayError) as caught:
        orchestrator.advance_sandbox_until_pause(task)

    assert caught.value.code == "PLAY_TRANSITION_LIMIT"


def test_player_pacing_recovers_missing_checkpoint_and_enforces_phase(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _agent_service, runtime = _agent(session)
    orchestrator = PlayOrchestrator(session, GameInstanceId(runtime.instance.id))
    submission = orchestrator.submit_goal(
        "stabilize the patient", idempotency_key="pacing-recovery"
    )
    assert submission.task is not None
    checkpoint = session.get(PlayerExecutionCheckpoint, submission.task.id)
    assert checkpoint is not None
    session.delete(checkpoint)
    session.flush()
    monkeypatch.setattr(orchestrator.agent, "execute_next", lambda _task, **_kwargs: None)

    orchestrator.start_initial_planning(expected_pacing_version=1)
    orchestrator.acknowledge_action(expected_pacing_version=2)
    recovered = session.get(PlayerExecutionCheckpoint, submission.task.id)
    assert recovered is not None
    assert recovered.phase == "AWAITING_DEBRIEF_ACK"
    with pytest.raises(PlayError) as caught:
        orchestrator.acknowledge_action(expected_pacing_version=recovered.version)
    assert caught.value.code == "PLAYER_PACING_PHASE_INVALID"
    submission.task.status = AgentTaskStatus.ABORTED
    assert orchestrator._phase_after_cycle(submission.task).value == "ABORTED"


def test_player_pacing_blocks_when_plan_has_no_action(session: Session) -> None:
    _agent_service, runtime = _agent(session)
    orchestrator = PlayOrchestrator(session, GameInstanceId(runtime.instance.id))
    submission = orchestrator.submit_goal(
        "stabilize the patient", idempotency_key="pacing-no-action"
    )
    assert submission.task is not None
    orchestrator.start_initial_planning(expected_pacing_version=1)
    plan = session.scalar(
        select(AgentPlan).where(
            AgentPlan.task_id == submission.task.id,
            AgentPlan.status == AgentPlanStatus.ACTIVE,
        )
    )
    assert plan is not None
    for step in session.scalars(select(AgentStep).where(AgentStep.plan_id == plan.id)):
        step.status = AgentStepStatus.SKIPPED
    session.flush()

    blocked = orchestrator.acknowledge_action(expected_pacing_version=2)
    assert blocked.status == AgentTaskStatus.BLOCKED
    checkpoint = session.get(PlayerExecutionCheckpoint, blocked.id)
    assert checkpoint is not None
    assert checkpoint.phase == "BLOCKED"


def test_play_rejects_blank_idempotency_key(session: Session) -> None:
    _agent_service, runtime = _agent(session)
    orchestrator = PlayOrchestrator(session, GameInstanceId(runtime.instance.id))
    with pytest.raises(PlayError) as caught:
        orchestrator.submit_goal("stabilize the patient", idempotency_key=" ")
    assert caught.value.code == "GOAL_IDEMPOTENCY_KEY_REQUIRED"


def test_generic_agent_rejects_cross_instance_task(session: Session) -> None:
    agent, runtime = _agent(session)
    task = agent.create_task(runtime.session, "stabilize the patient")
    task.game_instance_id = None

    with pytest.raises(GenericAgentError) as caught:
        agent.execute_next(task)

    assert caught.value.code == "GENERIC_TASK_SCOPE_INVALID"


def test_planner_owned_required_parameter_reaches_provider_without_prefill(
    session: Session,
) -> None:
    provider = _RecordingProvider(_accepted_proposal())
    agent, runtime = _agent(
        session,
        definition=_planner_owned_definition(),
        provider=provider,
    )
    task = agent.create_task(
        runtime.session,
        "stabilize the patient",
        initialize_plan=False,
    )

    definition = agent._definition()
    objectives = agent._objectives(task, definition)
    frontier = agent._candidate_steps(
        definition,
        objectives,
        task=task,
        reason=None,
        plan_version=1,
    )
    assert frontier == []

    plan = agent.plan(task)

    assert task.status == AgentTaskStatus.ACTIVE
    assert task.last_error_code is None
    assert len(provider.plan_requests) == 1
    assert plan.version == 1
    assert provider.plan_requests[0].planner_input is not None
    planner_parameters = provider.plan_requests[0].planner_input.action_contracts[0].parameters
    assert planner_parameters[0]["required"] is True
    assert planner_parameters[0]["default"] is None
    steps = session.scalars(select(AgentStep).where(AgentStep.plan_id == plan.id)).all()
    assert len(steps) == 1
    assert steps[0].tool_arguments["parameters"] == {"dosage": 2}


def test_known_impossible_action_still_has_no_deterministic_plan(
    session: Session,
) -> None:
    agent, runtime = _agent(session, definition=_known_impossible_definition())

    with pytest.raises(GenericAgentError) as caught:
        agent.create_task(runtime.session, "stabilize the patient")

    assert caught.value.code == "GENERIC_PLAN_NOT_FOUND"


def test_satisfied_objective_short_circuits_all_planning_and_execution(
    session: Session,
) -> None:
    provider = _RecordingProvider(_accepted_proposal())
    _agent_service, runtime = _agent(session, provider=provider)
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "patient_one", "stable"),
    )
    resource = session.get(
        GameInstanceResourceState,
        (runtime.instance.id, "medicine"),
    )
    assert fact is not None and resource is not None
    fact.truth_value = True
    resource_before = resource.value
    session.flush()

    orchestrator = PlayOrchestrator(
        session,
        GameInstanceId(runtime.instance.id),
        provider=provider,
    )
    submission = orchestrator.submit_goal(
        "stabilize the patient",
        idempotency_key="already-complete",
    )
    task = submission.task
    assert task is not None

    assert task.status == AgentTaskStatus.SUCCEEDED
    checkpoint = session.get(PlayerExecutionCheckpoint, task.id)
    assert checkpoint is not None
    assert checkpoint.phase == "COMPLETED"
    assert provider.plan_requests == []
    assert session.scalar(select(PlanningCycle).where(PlanningCycle.task_id == task.id)) is None
    assert (
        session.scalar(select(PlanningAttempt).where(PlanningAttempt.cycle_id.is_not(None))) is None
    )
    assert session.scalar(select(AgentPlan).where(AgentPlan.task_id == task.id)) is None
    assert session.scalar(select(AgentStep).where(AgentStep.plan_id.is_not(None))) is None
    assert resource.value == resource_before


def test_repeated_completed_objective_creates_new_terminal_task_without_planning(
    session: Session,
) -> None:
    provider = _RecordingProvider(_accepted_proposal())
    agent, runtime = _agent(session, provider=provider)
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "patient_one", "stable"),
    )
    assert fact is not None
    fact.truth_value = True
    session.flush()

    first = agent.create_task(runtime.session, "stabilize the patient")
    second = agent.create_task(runtime.session, "stabilize the patient")

    assert first.id != second.id
    assert first.status == AgentTaskStatus.SUCCEEDED
    assert second.status == AgentTaskStatus.SUCCEEDED
    assert provider.plan_requests == []
    tasks = session.scalars(
        select(AgentTask).where(AgentTask.game_instance_id == runtime.instance.id)
    ).all()
    assert len(tasks) == 2


def test_duplicate_active_objective_keeps_one_task_and_one_provider_plan(
    session: Session,
) -> None:
    provider = _RecordingProvider(_accepted_proposal())
    agent, runtime = _agent(session, provider=provider)

    first = agent.create_task(runtime.session, "stabilize the patient")

    assert first.status == AgentTaskStatus.ACTIVE
    assert len(provider.plan_requests) == 1
    with pytest.raises(GenericAgentError) as caught:
        agent.create_task(runtime.session, "stabilize the patient")

    assert caught.value.code == "AGENT_TASK_ALREADY_ACTIVE"
    assert len(provider.plan_requests) == 1
    tasks = session.scalars(
        select(AgentTask).where(AgentTask.game_instance_id == runtime.instance.id)
    ).all()
    assert len(tasks) == 1


def test_incomplete_objective_keeps_normal_provider_planning_path(
    session: Session,
) -> None:
    provider = _RecordingProvider(_accepted_proposal())
    agent, runtime = _agent(session, provider=provider)

    task = agent.create_task(runtime.session, "stabilize the patient")

    assert task.status == AgentTaskStatus.ACTIVE
    assert len(provider.plan_requests) == 1
    plan = session.scalar(select(AgentPlan).where(AgentPlan.task_id == task.id))
    assert plan is not None
    steps = session.scalars(select(AgentStep).where(AgentStep.plan_id == plan.id)).all()
    assert len(steps) == 1
