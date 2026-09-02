import json
from collections import deque
from collections.abc import Iterable
from copy import deepcopy
from time import sleep
from types import SimpleNamespace
from typing import Literal, NoReturn
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.agent.provider as provider_module
from app.agent.generic import GenericAgentError, GenericAgentService, proposal_signature
from app.agent.planning_context import PlanningContinuityBuilder, legal_candidate_id
from app.agent.provider import (
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    GenericProviderError,
    GoalSelection,
    GoalSelectionRequest,
    OpenAICompatibleGenericProvider,
    PlannerInput,
    PlanProposal,
    PlanRequest,
    PlanStepProposal,
)
from app.core.config import Settings
from app.domain.enums import NodeStatus, WorldOperationStatus
from app.domain.formal_goal import AdHocGoalRequirementCandidateV1
from app.domain.runtime_scope import GameInstanceId
from app.domain.scenario_v2 import ObjectiveRequirementKind, ScenarioDefinitionV2
from app.infrastructure.db.models import (
    AgentPlan,
    AgentStep,
    AgentTask,
    GameInstance,
    GameInstanceNodeState,
    PlanningAttempt,
    PlanningCycle,
    Player,
    WorldOperation,
)
from app.scenarios.builtin import require_builtin_v2_version
from app.services.composition import configured_play_orchestrator
from app.services.game_instances import GameInstanceService
from app.services.runtime_initialization import RuntimeInitializationService
from tests.scenario_fixtures import GENERIC_TEST, create_test_scenario


class RecordingProvider:
    model_name = "fake-provider"

    def __init__(
        self,
        *,
        selected: tuple[str, ...] = (),
        proposals: Iterable[tuple[PlanStepProposal, ...]] = (),
        dynamic_interpretation: DynamicGoalInterpretation | None = None,
    ) -> None:
        self.selected = selected
        self.proposals = deque(proposals)
        self.dynamic_interpretation = dynamic_interpretation
        self.goal_requests: list[GoalSelectionRequest] = []
        self.dynamic_requests: list[DynamicGoalInterpretationRequest] = []
        self.plan_requests: list[PlanRequest] = []

    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection:
        self.goal_requests.append(request)
        return GoalSelection(objective_keys=self.selected)

    def interpret_dynamic_goal(
        self,
        request: DynamicGoalInterpretationRequest,
    ) -> DynamicGoalInterpretation:
        self.dynamic_requests.append(request)
        return self.dynamic_interpretation or DynamicGoalInterpretation(status="UNSUPPORTED")

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.plan_requests.append(request)
        return PlanProposal(steps=self.proposals.popleft())


class FailingPlanProvider(RecordingProvider):
    def __init__(self, error: GenericProviderError) -> None:
        super().__init__()
        self.error = error

    def propose_plan(self, request: PlanRequest) -> NoReturn:
        self.plan_requests.append(request)
        raise self.error


class FailOnReplanProvider(RecordingProvider):
    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        if self.plan_requests:
            self.plan_requests.append(request)
            raise GenericProviderError("MODEL_PROVIDER_TIMEOUT", "provider timed out")
        return super().propose_plan(request)


def _settings(provider: Literal["mock", "openai_compatible"] = "mock") -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite+pysqlite:///:memory:",
        model_provider=provider,
        model_name="fake-provider",
        model_api_key=(SecretStr("not-a-real-key") if provider != "mock" else None),
    )


def _runtime(session: Session, definition=GENERIC_TEST):  # type: ignore[no-untyped-def]
    version = require_builtin_v2_version(session, definition)
    player = Player(name=f"provider-{definition.metadata.key}-{uuid4().hex[:8]}")
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=str(uuid4()),
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return runtime, scope


def _start_initial_plan(orchestrator, task):  # type: ignore[no-untyped-def]
    checkpoint = orchestrator._ensure_checkpoint(task)
    return orchestrator.start_initial_planning(expected_pacing_version=checkpoint.version)


def _generic_plan() -> tuple[PlanStepProposal, ...]:
    return (
        _step("diagnose_patient", "patient_one", "doctor_lee"),
        _step("treat_patient", "patient_one", "doctor_lee", {"dosage": 2}),
    )


def _plan_order_definition() -> ScenarioDefinitionV2:
    """Keep plan-order coverage independent from diagnosis preflight coverage."""

    document = deepcopy(GENERIC_TEST.model_dump(mode="json"))
    document["metadata"]["key"] = "generic_plan_order"
    document["metadata"]["name"] = "Generic Plan Order"
    document["world"]["key"] = "generic_plan_order"
    document["world"]["name"] = "Generic Plan Order"
    document["rules"] = [
        rule for rule in document["rules"] if rule["key"] != "treatment_needs_diagnosis"
    ]
    return ScenarioDefinitionV2.model_validate(document)


def _step(
    action_key: str,
    target_key: str,
    actor_key: str,
    parameters: dict[str, object] | None = None,
) -> PlanStepProposal:
    return PlanStepProposal(
        candidate_id=legal_candidate_id(action_key, actor_key, target_key),
        parameters=parameters or {},  # type: ignore[arg-type]
    )


def test_mock_composition_never_sends_model_http(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_http(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("mock mode must not issue model HTTP")

    monkeypatch.setattr(httpx, "post", fail_http)
    runtime, _scope = _runtime(session, GENERIC_TEST)
    submission = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("mock")
    ).submit_goal("stabilize the patient", idempotency_key=str(uuid4()))

    assert submission.task is not None
    assert submission.resolution.source == "DETERMINISTIC"
    assert submission.task.planning_mode == "GENERIC"


def test_exact_goal_skips_provider_selection_but_initial_plan_uses_provider(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider(proposals=[_generic_plan()])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session, GENERIC_TEST)
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    submission = orchestrator.submit_goal("stabilize the patient", idempotency_key=str(uuid4()))

    assert submission.task is not None
    assert provider.goal_requests == []
    assert provider.plan_requests == []
    original_propose_plan = provider.propose_plan
    observed_started_calls: list[dict[str, object]] = []

    def inspect_persistence_boundary(request: PlanRequest) -> PlanProposal:
        persisted = session.get(AgentTask, submission.task.id)
        assert persisted is not None
        calls = (persisted.objective_resolution_metadata or {}).get("provider_calls", [])
        observed_started_calls.append(dict(calls[-1]))
        cycle = session.scalar(select(PlanningCycle).where(PlanningCycle.task_id == persisted.id))
        assert cycle is not None and cycle.status == "RUNNING"
        assert cycle.started_at is not None
        attempt = session.scalar(
            select(PlanningAttempt).where(PlanningAttempt.cycle_id == cycle.id)
        )
        assert attempt is not None and attempt.status == "RUNNING"
        assert attempt.started_at is not None
        assert cycle.current_attempt == attempt.attempt_index
        return original_propose_plan(request)

    monkeypatch.setattr(provider, "propose_plan", inspect_persistence_boundary)
    _start_initial_plan(orchestrator, submission.task)
    assert len(provider.plan_requests) == 1
    assert observed_started_calls[0]["outcome"] == "RUNNING"
    assert observed_started_calls[0]["call_type"] == "INITIAL_PLAN"
    assert submission.task.planning_mode == "PROVIDER"
    calls = (submission.task.objective_resolution_metadata or {}).get("provider_calls", [])
    assert calls[-1]["outcome"] == "SUCCESS"
    assert calls[-1]["call_type"] == "INITIAL_PLAN"
    assert calls[-1]["started_at"]
    assert calls[-1]["finished_at"]
    assert calls[-1]["provider_payload"]["call_type"] == "INITIAL_PLAN"
    assert "planner_input" in calls[-1]["provider_payload"]
    assert calls[-1]["proposal_stop_reason"] == "OBJECTIVE_COMPLETION"
    assert calls[-1]["validator_violations"] == []
    catalog = {item.action_key: item for item in provider.plan_requests[0].planning_action_catalog}
    assert catalog["diagnose_patient"].currently_executable is True
    assert catalog["treat_patient"].currently_executable is False
    assert catalog["treat_patient"].known_blockers[0]["code"] == ("PUBLIC_PREREQUISITE_UNSATISFIED")


def test_rejected_formal_attempt_is_not_persisted_as_plan_or_runtime_operation(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider(
        proposals=[
            (_step("treat_patient", "patient_one", "doctor_lee", {"dosage": 99}),),
            (_step("treat_patient", "patient_one", "doctor_lee", {"dosage": 99}),),
            (_step("treat_patient", "patient_one", "doctor_lee", {"dosage": 99}),),
        ]
    )
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session, GENERIC_TEST)
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    submission = orchestrator.submit_goal("stabilize the patient", idempotency_key=str(uuid4()))
    assert submission.task is not None
    checkpoint = orchestrator._ensure_checkpoint(submission.task)

    task = orchestrator.start_initial_planning(expected_pacing_version=checkpoint.version)
    assert orchestrator._ensure_checkpoint(task).phase == "BLOCKED"
    assert task.status.value == "BLOCKED"
    assert task.last_error_code == "MODEL_PLAN_REJECTED"
    assert session.scalar(select(AgentPlan).where(AgentPlan.task_id == task.id)) is None
    assert session.scalar(select(WorldOperation).where(WorldOperation.task_id == task.id)) is None

    cycle = session.scalar(select(PlanningCycle).where(PlanningCycle.task_id == task.id))
    assert cycle is not None
    assert cycle.status == "REJECTED"
    attempt = session.scalar(select(PlanningAttempt).where(PlanningAttempt.cycle_id == cycle.id))
    assert attempt is not None
    assert attempt.status == "REJECTED"
    assert attempt.call_type == "INITIAL_PLAN"
    assert attempt.validator_violations
    assert (task.objective_resolution_metadata or {}).get("operation_durations")
    assert len(provider.plan_requests) == 3


def test_single_formal_request_runs_repair_and_persists_attempt_before_plan(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider(
        proposals=[
            (PlanStepProposal(candidate_id="candidate_invented"),),
            _generic_plan(),
        ]
    )
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session, GENERIC_TEST)
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    task = orchestrator.submit_goal("stabilize the patient", idempotency_key=str(uuid4())).task
    assert task is not None

    final_task = _start_initial_plan(orchestrator, task)
    assert final_task.status.value == "ACTIVE"
    assert orchestrator._ensure_checkpoint(final_task).phase == "AWAITING_ACTION_ACK"
    assert [request.call_type for request in provider.plan_requests] == [
        "INITIAL_PLAN",
        "REPAIR",
    ]
    cycle = session.scalar(select(PlanningCycle).where(PlanningCycle.task_id == task.id))
    assert cycle is not None and cycle.status == "ACCEPTED"
    assert cycle.started_at is not None
    assert cycle.finished_at is not None
    attempts = tuple(
        session.scalars(
            select(PlanningAttempt)
            .where(PlanningAttempt.cycle_id == cycle.id)
            .order_by(PlanningAttempt.attempt_index)
        )
    )
    assert [attempt.status for attempt in attempts] == ["REJECTED", "ACCEPTED"]
    plan = session.scalar(select(AgentPlan).where(AgentPlan.task_id == task.id))
    assert plan is not None and plan.planning_cycle_id == cycle.id
    assert (
        session.scalar(
            select(func.count()).select_from(AgentPlan).where(AgentPlan.task_id == task.id)
        )
        == 1
    )


def test_unmatched_goal_uses_dynamic_interpreter_and_rejects_unsupported_goal(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _scope = _runtime(session)
    dynamic_candidate = AdHocGoalRequirementCandidateV1(
        kind=ObjectiveRequirementKind.FACT,
        node_key="patient_one",
        fact_key="stable",
        accepted_values=(True,),
    )
    provider = RecordingProvider(
        selected=("stabilize_patient",),
        dynamic_interpretation=DynamicGoalInterpretation(requirements=(dynamic_candidate,)),
        proposals=[_generic_plan()],
    )
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    accepted = orchestrator.submit_goal(
        "make the patient better",
        idempotency_key=str(uuid4()),
    )

    assert accepted.task is not None
    assert accepted.resolution.objective_keys == ()
    assert accepted.resolution.dynamic_requirements == (dynamic_candidate,)
    assert accepted.resolution.source == "AD_HOC_DYNAMIC"
    assert provider.goal_requests == []
    assert len(provider.dynamic_requests) == 1
    assert accepted.task.formal_goal_source_kind == "AD_HOC_DYNAMIC"

    accepted.task.status = "SUCCEEDED"
    invented = RecordingProvider(selected=("invented_objective",))
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: invented
    )
    rejected = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    ).submit_goal("do something the scenario never defined", idempotency_key=str(uuid4()))
    assert rejected.task is None
    assert rejected.resolution.status == "UNSUPPORTED"
    assert invented.goal_requests == []
    assert len(invented.dynamic_requests) == 1


def test_provider_plan_is_validated_and_rejected_constraint_is_authoritative(
    session: Session,
) -> None:
    runtime, scope = _runtime(session, GENERIC_TEST)
    invalid_step = (_step("treat_patient", "patient_one", "invented_actor", {"dosage": 2}),)
    invalid = RecordingProvider(proposals=[invalid_step, invalid_step, invalid_step])
    with pytest.raises(GenericAgentError) as caught:
        GenericAgentService(session, scope, provider=invalid).create_task(
            runtime.session, "stabilize the patient"
        )
    assert caught.value.code == "MODEL_PLAN_REJECTED"
    session.rollback()

    runtime, scope = _runtime(session, GENERIC_TEST)
    repeated = RecordingProvider(
        proposals=[_generic_plan(), _generic_plan(), _generic_plan(), _generic_plan()]
    )
    agent = GenericAgentService(session, scope, provider=repeated)
    task = agent.create_task(runtime.session, "stabilize the patient")
    first = _generic_plan()[0]
    diagnose = next(
        item
        for item in repeated.plan_requests[0].planning_action_catalog
        if item.candidate_id == first.candidate_id
    )
    treatment_step = _generic_plan()[1]
    treatment = next(
        item
        for item in repeated.plan_requests[0].planning_action_catalog
        if item.candidate_id == treatment_step.candidate_id
    )
    task.rejected_proposal_signatures = [
        proposal_signature(
            diagnose.actor_key,
            diagnose.action_key,
            diagnose.target_key,
            first.parameters,
        ),
        proposal_signature(
            treatment.actor_key,
            treatment.action_key,
            treatment.target_key,
            treatment_step.parameters,
        ),
    ]
    with pytest.raises(GenericAgentError) as rejected:
        agent.plan(task, reason="PLAYER_REJECTED")
    assert rejected.value.code == "MODEL_PLAN_REJECTED"
    assert len(repeated.plan_requests) == 4


def test_provider_repair_uses_safe_diagnostics_and_stops_after_two_attempts(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown = (PlanStepProposal(candidate_id="candidate_invented"),)
    invalid_parameters = (_step("treat_patient", "patient_one", "doctor_lee", {"dosage": 99}),)
    provider = RecordingProvider(proposals=[unknown, invalid_parameters, _generic_plan()])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session, GENERIC_TEST)
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    submission = orchestrator.submit_goal("stabilize the patient", idempotency_key=str(uuid4()))

    assert submission.task is not None
    _start_initial_plan(orchestrator, submission.task)
    assert [item.call_type for item in provider.plan_requests] == [
        "INITIAL_PLAN",
        "REPAIR",
        "REPAIR",
    ]
    assert tuple(
        item.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        for item in provider.plan_requests[1].repair_diagnostics
    ) == (
        {
            "code": "UNKNOWN_CANDIDATE",
            "failure_code": "UNKNOWN_CANDIDATE",
            "dimension": "CANDIDATE_BINDING",
            "candidate_id": "candidate_invented",
            "step_id": unknown[0].step_id,
            "required": "KNOWN_CANDIDATE_OR_DIRECT_BINDING",
            "actual": "candidate_invented",
        },
    )
    assert provider.plan_requests[1].rejected_segment is not None
    assert provider.plan_requests[1].rejected_segment["steps"][0]["step_id"] == (unknown[0].step_id)
    assert provider.plan_requests[1].anti_regression_memory == ()
    parameter_diagnostic = provider.plan_requests[2].repair_diagnostics[0]
    assert parameter_diagnostic.code == "PARAMETER_INVALID"
    assert parameter_diagnostic.failure_code == "GENERIC_PLAN_PARAMETER_INVALID"
    assert parameter_diagnostic.dimension == "PARAMETER"
    assert parameter_diagnostic.step_id == invalid_parameters[0].step_id
    assert parameter_diagnostic.action_key == "treat_patient"
    assert parameter_diagnostic.actor_key == "doctor_lee"
    assert parameter_diagnostic.target_key == "patient_one"
    assert parameter_diagnostic.actual_parameters == {"dosage": 99}
    assert parameter_diagnostic.validation_error
    assert len(provider.plan_requests[2].anti_regression_memory) == 1
    historical = provider.plan_requests[2].anti_regression_memory[0]
    assert historical.code == "UNKNOWN_CANDIDATE"
    assert historical.step_id is None
    assert historical.first_seen_attempt == 0
    assert historical.last_seen_attempt == 0
    assert historical.seen_count == 1
    diagnostic_text = str(provider.plan_requests[1].repair_diagnostics)
    assert "truth" not in diagnostic_text.casefold()
    assert "ambush" not in diagnostic_text.casefold()

    cycle = session.scalar(
        select(PlanningCycle)
        .where(PlanningCycle.task_id == submission.task.id)
        .order_by(PlanningCycle.created_at.desc())
    )
    assert cycle is not None and cycle.status == "ACCEPTED"
    attempts = tuple(
        session.scalars(
            select(PlanningAttempt)
            .where(PlanningAttempt.cycle_id == cycle.id)
            .order_by(PlanningAttempt.attempt_index)
        )
    )
    assert [item.status for item in attempts] == ["REJECTED", "REJECTED", "ACCEPTED"]
    assert (
        len(
            {
                json.dumps(request.planner_input.model_dump(mode="json"), sort_keys=True)
                for request in provider.plan_requests
            }
        )
        == 1
    )
    assert (
        len(
            {
                json.dumps(request.objective_scope, sort_keys=True)
                for request in provider.plan_requests
            }
        )
        == 1
    )
    assert provider.plan_requests[1].rejected_segment is not None
    assert provider.plan_requests[2].rejected_segment is not None
    assert (
        session.scalar(
            select(func.count())
            .select_from(AgentPlan)
            .where(AgentPlan.task_id == submission.task.id)
        )
        == 1
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(WorldOperation)
            .where(WorldOperation.task_id == submission.task.id)
        )
        == 0
    )
    rejected_provider = RecordingProvider(proposals=[unknown, unknown, unknown])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: rejected_provider
    )
    submission.task.status = "SUCCEEDED"
    rejected = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    ).submit_goal("diagnose the patient", idempotency_key=str(uuid4()))
    assert rejected.task is not None
    _start_initial_plan(
        configured_play_orchestrator(
            session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
        ),
        rejected.task,
    )
    assert rejected.task.status.value == "BLOCKED"
    assert rejected.task.last_error_code == "MODEL_PLAN_REJECTED"
    assert len(rejected_provider.plan_requests) == 3
    assert rejected_provider.plan_requests[1].anti_regression_memory == ()
    rejected_cycle = session.scalar(
        select(PlanningCycle)
        .where(PlanningCycle.task_id == rejected.task.id)
        .order_by(PlanningCycle.created_at.desc())
    )
    assert rejected_cycle is not None and rejected_cycle.status == "REJECTED"
    assert (
        session.scalar(
            select(func.count()).select_from(AgentPlan).where(AgentPlan.task_id == rejected.task.id)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(WorldOperation)
            .where(WorldOperation.task_id == rejected.task.id)
        )
        == 0
    )


def test_provider_repair_attempt_limit_comes_from_settings(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert Settings(_env_file=None).model_max_repair_attempts_per_cycle == 2
    unknown = (PlanStepProposal(candidate_id="candidate_invented"),)
    provider = RecordingProvider(proposals=[unknown, unknown, unknown, unknown, _generic_plan()])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session, GENERIC_TEST)
    settings = _settings("openai_compatible").model_copy(
        update={"model_max_repair_attempts_per_cycle": 4}
    )
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), settings
    )

    submission = orchestrator.submit_goal("stabilize the patient", idempotency_key=str(uuid4()))

    assert submission.task is not None
    _start_initial_plan(orchestrator, submission.task)
    assert [request.call_type for request in provider.plan_requests] == [
        "INITIAL_PLAN",
        "REPAIR",
        "REPAIR",
        "REPAIR",
        "REPAIR",
    ]
    assert submission.task.status.value == "ACTIVE"


def test_plan_order_repair_accepts_future_step_after_public_prerequisite(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    reversed_plan = tuple(reversed(_generic_plan()))
    provider = RecordingProvider(proposals=[reversed_plan, _generic_plan()])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session, _plan_order_definition())

    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    submission = orchestrator.submit_goal("stabilize the patient", idempotency_key=str(uuid4()))

    assert submission.task is not None
    _start_initial_plan(orchestrator, submission.task)
    assert [item.call_type for item in provider.plan_requests] == ["INITIAL_PLAN", "REPAIR"]
    diagnostic = provider.plan_requests[1].repair_diagnostics[0]
    assert diagnostic.code == "PLAN_ORDER_INVALID"
    assert diagnostic.failure_code == "PLAN_ORDER_INVALID"
    assert diagnostic.dimension == "PLAN_ORDER"
    assert diagnostic.required == "PUBLIC_PREREQUISITES_BEFORE_TERMINAL_EFFECT"
    assert diagnostic.actual == list(diagnostic.missing_prior_public_requirements)


def test_empty_planning_catalog_is_unreachable_without_provider_fallback(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider(proposals=[])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session, GENERIC_TEST)
    patient = session.get(
        GameInstanceNodeState,
        (runtime.instance.id, "patient_one"),
    )
    assert patient is not None
    patient.visibility = "HIDDEN"
    session.flush()

    submission = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    ).submit_goal("stabilize the patient", idempotency_key=str(uuid4()))

    assert submission.task is not None
    _start_initial_plan(
        configured_play_orchestrator(
            session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
        ),
        submission.task,
    )
    assert submission.task.status.value == "BLOCKED"
    assert submission.task.last_error_code == "UNREACHABLE_IN_CURRENT_STATE"
    assert provider.plan_requests == []


def test_generic_composition_uses_the_same_provider_wiring(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _scope = _runtime(session, GENERIC_TEST)
    provider = RecordingProvider(proposals=[_generic_plan()])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    submission = orchestrator.submit_goal("stabilize the patient", idempotency_key=str(uuid4()))
    assert submission.task is not None
    _start_initial_plan(orchestrator, submission.task)
    assert len(provider.plan_requests) == 1


def test_draft_sandbox_uses_same_provider_composition_without_formal_game_row(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider(proposals=[_generic_plan()])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    created = create_test_scenario(
        session,
        GENERIC_TEST,
        key=f"provider_sandbox_{uuid4().hex[:8]}",
        name="Provider Sandbox",
    )
    game_count = session.scalar(select(func.count()).select_from(GameInstance))

    response = client.post(
        f"/api/v1/scenarios/{created.id}/draft/sandbox",
        json={"expected_revision": 1, "goal": "stabilize the patient"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["goal_status"] == "SUCCEEDED"
    assert len(provider.plan_requests) == 1
    assert session.scalar(select(func.count()).select_from(GameInstance)) == game_count


def test_provider_timeout_and_malformed_json_are_explicit_and_secret_safe() -> None:
    settings = _settings("openai_compatible")
    request = GoalSelectionRequest(goal="unclear", objective_candidates=({"key": "known"},))

    def timeout(_request: httpx.Request) -> NoReturn:
        raise httpx.ReadTimeout("timed out")

    provider = OpenAICompatibleGenericProvider(settings, transport=httpx.MockTransport(timeout))
    with pytest.raises(GenericProviderError) as timed_out:
        provider.select_objectives(request)
    assert timed_out.value.code == "MODEL_PROVIDER_TIMEOUT"
    assert "not-a-real-key" not in str(timed_out.value)
    assert provider.last_call_metadata is not None
    assert provider.last_call_metadata.timeout_subtype == "ReadTimeout"
    assert provider.last_call_metadata.response_headers_received_at is None

    provider = OpenAICompatibleGenericProvider(
        settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not-json"}}]},
                request=request,
            )
        ),
    )
    with pytest.raises(GenericProviderError) as malformed:
        provider.select_objectives(request)
    assert malformed.value.code == "MODEL_PROVIDER_RESPONSE_INVALID"

    provider = OpenAICompatibleGenericProvider(
        settings,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"wrong":[]}'}}]},
                request=request,
            )
        ),
    )
    with pytest.raises(GenericProviderError) as wrong_schema:
        provider.select_objectives(request)
    assert wrong_schema.value.code == "MODEL_PROVIDER_RESPONSE_INVALID"

    provider = OpenAICompatibleGenericProvider(
        settings,
        transport=httpx.MockTransport(lambda request: httpx.Response(503, request=request)),
    )
    with pytest.raises(GenericProviderError) as http_error:
        provider.select_objectives(request)
    assert http_error.value.code == "MODEL_PROVIDER_HTTP_ERROR"


def test_provider_total_deadline_bounds_a_slow_sync_provider_call() -> None:
    settings = _settings("openai_compatible").model_copy(
        update={"model_timeout_seconds": 5, "model_total_timeout_seconds": 0.02}
    )
    request = GoalSelectionRequest(goal="unclear", objective_candidates=({"key": "known"},))

    def slow_post(_request: httpx.Request) -> httpx.Response:
        sleep(0.15)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}}]},
            request=_request,
        )

    provider = OpenAICompatibleGenericProvider(settings, transport=httpx.MockTransport(slow_post))
    with pytest.raises(GenericProviderError) as timed_out:
        provider.select_objectives(request)

    assert timed_out.value.code == "MODEL_PROVIDER_TIMEOUT"
    assert provider.last_call_metadata is not None
    assert provider.last_call_metadata.outcome == "TIMEOUT"
    assert provider.last_call_metadata.total_deadline_seconds == 0.02
    assert provider.last_call_metadata.wall_clock_latency_ms is not None
    assert provider.last_call_metadata.wall_clock_latency_ms < 120


def test_provider_http_error_logs_bounded_safe_upstream_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings("openai_compatible")
    request = GoalSelectionRequest(goal="unclear", objective_candidates=({"key": "known"},))
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        provider_module,
        "log",
        SimpleNamespace(
            error=lambda event, **fields: events.append((event, fields)),
        ),
    )

    def http_error_response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "error": {
                    "type": "upstream_error",
                    "code": "temporarily_unavailable",
                    "message": "token=sk-test-secret upstream unavailable",
                }
            },
            headers={"x-request-id": "provider-request-123"},
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(
        settings, transport=httpx.MockTransport(http_error_response)
    )

    with pytest.raises(GenericProviderError) as http_error:
        provider.select_objectives(request)

    assert http_error.value.code == "MODEL_PROVIDER_HTTP_ERROR"
    assert len(events) == 1
    event, fields = events[0]
    assert event == "model_provider_upstream_error"
    assert fields["model"] == "fake-provider"
    assert fields["error_type"] == "HTTPStatusError"
    assert fields["upstream_status_code"] == 503
    assert fields["request_size_bytes"] > 0
    assert fields["provider_request_id"] == "provider-request-123"
    assert "planning_context" not in fields
    assert "prompt" not in fields
    assert "sk-test-secret" not in json.dumps(fields)
    assert "upstream unavailable" in json.dumps(fields)


def test_generic_planner_prompt_requires_known_concrete_purpose() -> None:
    settings = _settings("openai_compatible")
    system_prompts: list[str] = []

    def complete(request: httpx.Request) -> httpx.Response:
        request_body = json.loads(request.content)
        assert isinstance(request_body, dict)
        messages = request_body["messages"]
        assert isinstance(messages, list)
        system_message = messages[0]
        assert isinstance(system_message, dict)
        system_prompts.append(str(system_message["content"]))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"plan_summary":"complete","steps":['
                                '{"purpose":"known task step",'
                                '"action_key":"known_action",'
                                '"actor_key":"known_actor",'
                                '"target_key":"known_target",'
                                '"parameters":{}}]}'
                            )
                        }
                    }
                ]
            },
            request=request,
        )

    provider = OpenAICompatibleGenericProvider(settings, transport=httpx.MockTransport(complete))
    planner_input = PlannerInput(
        objective={"objective_keys": ["known_objective"]},
        known_world={"facts": {}},
    )
    for call_type in ("INITIAL_PLAN", "REPLAN"):
        provider.propose_plan(
            PlanRequest(
                call_type=call_type,
                goal="known goal",
                objective_keys=("known_objective",),
                planner_input=planner_input,
            )
        )

    assert len(system_prompts) == 2
    for prompt in system_prompts:
        assert "concrete purpose supported by the current Knowledge and task state" in prompt
        assert "Do not add speculative or preventive corrective actions" in prompt
        assert "currently known failure, blockage, unmet prerequisite" in prompt


def test_provider_failure_returns_gateway_error_without_deterministic_fallback(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FailingPlanProvider(
        GenericProviderError("MODEL_PROVIDER_TIMEOUT", "provider timed out")
    )
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    version = require_builtin_v2_version(session, GENERIC_TEST)
    session.commit()
    response = client.post(
        "/api/v1/games",
        json={"scenario_version_id": str(version.id), "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 201, response.text
    game_id = str(response.json()["id"])
    before_operations = session.scalar(select(func.count()).select_from(WorldOperation))

    response = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 200
    task = response.json()["task"]
    response = client.post(
        f"/api/v1/games/{game_id}/play/start-planning",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "MODEL_PROVIDER_TIMEOUT"
    assert session.scalar(select(func.count()).select_from(WorldOperation)) == before_operations
    state = client.get(f"/api/v1/games/{game_id}/play").json()
    assert state["current_task"]["status"] == "MODEL_PROVIDER_TIMEOUT"
    assert state["current_task"]["execution_phase"] == "BLOCKED"
    assert state["current_task"]["explanation"] == "模型调用超时"
    terminal = next(
        event for event in state["current_task"]["timeline"] if event["kind"] == "TASK_BLOCKED"
    )
    assert terminal["title"] == "规划失败"
    assert terminal["detail"] == "模型调用超时"
    persisted = session.get(AgentTask, UUID(task["id"]))
    assert persisted is not None
    assert persisted.last_error_code == "MODEL_PROVIDER_TIMEOUT"
    assert persisted.last_error_detail == "模型调用超时"
    calls = (persisted.objective_resolution_metadata or {}).get("provider_calls", [])
    assert calls[-1]["outcome"] == "TIMEOUT"
    assert calls[-1]["call_type"] == "INITIAL_PLAN"
    assert calls[-1]["started_at"]
    assert calls[-1]["finished_at"]
    assert calls[-1]["context_bytes"] is not None
    assert calls[-1]["request_size_bytes"] is not None
    assert calls[-1]["latency_ms"] >= 0
    cycle = session.scalar(
        select(PlanningCycle)
        .where(PlanningCycle.task_id == persisted.id)
        .order_by(PlanningCycle.created_at.desc())
    )
    assert cycle is not None and cycle.status == "ERROR"
    assert cycle.started_at is not None
    assert cycle.finished_at is not None
    attempt = session.scalar(select(PlanningAttempt).where(PlanningAttempt.cycle_id == cycle.id))
    assert attempt is not None
    assert attempt.status == "TIMEOUT"


def test_formal_planning_repair_loop_is_one_http_and_returns_final_failure(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    rejected = (PlanStepProposal(candidate_id="candidate_invented"),)
    provider = RecordingProvider(proposals=[rejected, rejected, rejected])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    version = require_builtin_v2_version(session, GENERIC_TEST)
    session.commit()
    game = client.post(
        "/api/v1/games",
        json={"scenario_version_id": str(version.id), "idempotency_key": str(uuid4())},
    )
    assert game.status_code == 201, game.text
    game_id = str(game.json()["id"])
    goal = client.post(
        f"/api/v1/games/{game_id}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    )
    assert goal.status_code == 200, goal.text
    task = goal.json()["task"]

    response = client.post(
        f"/api/v1/games/{game_id}/play/start-planning",
        json={"expected_pacing_version": task["pacing_version"]},
    )

    assert response.status_code == 200, response.text
    final_task = response.json()["current_task"]
    assert final_task["status"] == "MODEL_PLAN_REJECTED"
    assert final_task["execution_phase"] == "BLOCKED"
    assert final_task["plan_history"] == []
    assert "planning_cycle" not in final_task
    assert len(provider.plan_requests) == 3
    task_row = session.get(AgentTask, UUID(final_task["id"]))
    assert task_row is not None
    assert session.scalar(select(AgentPlan).where(AgentPlan.task_id == task_row.id)) is None
    assert (
        session.scalar(select(WorldOperation).where(WorldOperation.task_id == task_row.id)) is None
    )


def test_replan_provider_failure_persists_failure_and_action_history(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FailOnReplanProvider(proposals=[_generic_plan()])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    version = require_builtin_v2_version(session, GENERIC_TEST)
    session.commit()
    game = client.post(
        "/api/v1/games",
        json={
            "scenario_version_id": str(version.id),
            "idempotency_key": str(uuid4()),
        },
    ).json()
    goal = client.post(
        f"/api/v1/games/{game['id']}/goals",
        json={"goal": "stabilize the patient", "idempotency_key": str(uuid4())},
    )
    assert goal.status_code == 200, goal.text
    task = goal.json()["task"]
    start = client.post(
        f"/api/v1/games/{game['id']}/play/start-planning",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert start.status_code == 200, start.text
    task = start.json()["current_task"]
    patient = session.get(GameInstanceNodeState, (UUID(game["id"]), "patient_one"))
    assert patient is not None
    patient.status = NodeStatus.LOCKED
    session.flush()
    before_operations = session.scalar(select(func.count()).select_from(WorldOperation))

    failed = client.post(
        f"/api/v1/games/{game['id']}/play/acknowledge-action",
        json={"expected_pacing_version": task["pacing_version"]},
    )

    assert failed.status_code == 200
    replanned = client.post(
        f"/api/v1/games/{game['id']}/play/replan",
        json={"expected_pacing_version": failed.json()["current_task"]["pacing_version"]},
    )
    assert replanned.status_code == 504
    assert replanned.json()["error"]["code"] == "MODEL_PROVIDER_TIMEOUT"
    state = client.get(f"/api/v1/games/{game['id']}/play").json()
    assert state["current_task"]["status"] == "MODEL_PROVIDER_TIMEOUT"
    assert state["current_task"]["execution_phase"] == "BLOCKED"
    assert state["current_task"]["explanation"] == "模型调用超时"
    assert session.scalar(select(func.count()).select_from(WorldOperation)) == before_operations
    terminal = next(
        event for event in state["current_task"]["timeline"] if event["kind"] == "TASK_BLOCKED"
    )
    assert terminal["title"] == "规划失败"
    assert terminal["detail"] == "模型调用超时"
    persisted = session.get(AgentTask, UUID(task["id"]))
    assert persisted is not None
    calls = (persisted.objective_resolution_metadata or {}).get("provider_calls", [])
    assert calls[-1]["outcome"] == "TIMEOUT"
    assert calls[-1]["call_type"] == "REPLAN"
    assert calls[-1]["latency_ms"] >= 0
    cycle = session.scalar(
        select(PlanningCycle)
        .where(PlanningCycle.task_id == persisted.id)
        .order_by(PlanningCycle.created_at.desc())
    )
    assert cycle is not None and cycle.status == "ERROR"


def test_continuity_trigger_without_knowledge_does_not_reuse_historical_delta(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider(proposals=[_generic_plan()])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, scope = _runtime(session, GENERIC_TEST)
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    task = orchestrator.submit_goal("stabilize the patient", idempotency_key=str(uuid4())).task
    assert task is not None
    _start_initial_plan(orchestrator, task)
    plan = session.scalar(
        select(AgentPlan).where(AgentPlan.task_id == task.id).order_by(AgentPlan.version.desc())
    )
    assert plan is not None
    steps = tuple(
        session.scalars(
            select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
        )
    )
    assert len(steps) >= 2
    session.add_all(
        [
            WorldOperation(
                player_id=scope.player_id,
                game_instance_id=scope.game_instance_id,
                task_id=task.id,
                source_step_id=steps[0].id,
                actor_key=steps[0].assigned_actor_key,
                action_key=steps[0].action_intent or "",
                execution_mode="SYNC",
                target_key=str(steps[0].tool_arguments.get("target_key", "")),
                parameters={},
                status=WorldOperationStatus.RESOLVED,
                outcome={"knowledge_changes": [{"key": "historical_public_fact", "value": True}]},
                idempotency_key=f"historical-knowledge-{uuid4()}",
            ),
            WorldOperation(
                player_id=scope.player_id,
                game_instance_id=scope.game_instance_id,
                task_id=task.id,
                source_step_id=steps[1].id,
                actor_key=steps[1].assigned_actor_key,
                action_key=steps[1].action_intent or "",
                execution_mode="SYNC",
                target_key=str(steps[1].tool_arguments.get("target_key", "")),
                parameters={},
                status=WorldOperationStatus.RESOLVED,
                outcome={"outcome_code": "NO_PUBLIC_KNOWLEDGE"},
                idempotency_key=f"current-no-knowledge-{uuid4()}",
            ),
        ]
    )
    session.flush()

    continuity = PlanningContinuityBuilder(session, scope).build(
        task,
        replan_reason="ACTION_FAILED",
        trigger_step_id=steps[1].id,
    )

    assert continuity is not None
    assert continuity.latest_new_knowledge == ()
    assert continuity.prior_plans[0].steps[0].knowledge_changes == (
        {"key": "historical_public_fact", "value": True},
    )


def test_replan_continuity_is_frozen_and_keeps_only_latest_three_formal_plans(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = _generic_plan()
    invalid = (_step("treat_patient", "patient_one", "doctor_lee", {"dosage": 99}),)
    provider = RecordingProvider(proposals=[initial, invalid, initial, initial, initial, initial])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session, GENERIC_TEST)
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    task = orchestrator.submit_goal("stabilize the patient", idempotency_key=str(uuid4())).task
    assert task is not None
    _start_initial_plan(orchestrator, task)
    assert "planning_continuity" not in provider.plan_requests[0].provider_payload()
    builder = PlanningContinuityBuilder(session, orchestrator.scope)
    continuity = builder.build(task, replan_reason="TEST_REPLAN")
    assert continuity is not None
    assert len(continuity.prior_plans) == 1
    assert continuity.latest_replan_trigger == "TEST_REPLAN"

    orchestrator.agent.plan(
        task,
        reason="TEST_REPLAN",
        planning_continuity=continuity,
    )
    replan_requests = provider.plan_requests[1:]
    assert [request.call_type for request in replan_requests] == ["REPLAN", "REPAIR"]
    assert replan_requests[0].planning_continuity == replan_requests[1].planning_continuity
    assert replan_requests[0].planning_continuity is not None
    assert len(replan_requests[0].planning_continuity.prior_plans) == 1
    assert replan_requests[1].planning_continuity is not None
    assert replan_requests[1].planning_continuity.prior_plans[0].steps[0].purpose
    repair_payload = replan_requests[1].provider_payload()
    assert "planning_continuity" in repair_payload
    assert "planning_context" not in repair_payload
    assert "candidate_catalog" not in json.dumps(repair_payload)

    for index in range(3):
        next_continuity = builder.build(task, replan_reason=f"TEST_REPLAN_{index}")
        assert next_continuity is not None
        orchestrator.agent.plan(
            task,
            reason=f"TEST_REPLAN_{index}",
            planning_continuity=next_continuity,
        )

    latest = builder.build(task, replan_reason="TEST_REPLAN_FINAL")
    assert latest is not None
    assert [plan.plan_summary for plan in latest.prior_plans] == [
        plan.strategy_summary
        for plan in session.scalars(
            select(AgentPlan)
            .where(AgentPlan.task_id == task.id)
            .order_by(AgentPlan.version.desc())
            .limit(3)
        )
    ][::-1]
    assert len(latest.prior_plans) == 3
    attempts = tuple(
        session.scalars(select(PlanningAttempt).where(PlanningAttempt.task_id == task.id))
    )
    assert any(attempt.status == "REJECTED" for attempt in attempts)
