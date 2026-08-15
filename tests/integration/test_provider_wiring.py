import json
from collections import deque
from collections.abc import Iterable
from typing import Literal, NoReturn
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentError, GenericAgentService, proposal_signature
from app.agent.planning_context import legal_candidate_id
from app.agent.provider import (
    GenericProviderError,
    GoalSelection,
    GoalSelectionRequest,
    OpenAICompatibleGenericProvider,
    PlanProposal,
    PlanRequest,
    PlanStepProposal,
)
from app.core.config import Settings
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import (
    AgentPlan,
    AgentStep,
    GameInstance,
    GameInstanceNodeState,
    Player,
    WorldOperation,
)
from app.scenarios.builtin import (
    MEDICAL_EMERGENCY_V2,
    STARFIRE_V2,
    require_builtin_v2_version,
)
from app.services.composition import configured_play_orchestrator
from app.services.game_instances import GameInstanceService
from app.services.runtime_initialization import RuntimeInitializationService


class RecordingProvider:
    model_name = "fake-provider"

    def __init__(
        self,
        *,
        selected: tuple[str, ...] = (),
        proposals: Iterable[tuple[PlanStepProposal, ...]] = (),
    ) -> None:
        self.selected = selected
        self.proposals = deque(proposals)
        self.goal_requests: list[GoalSelectionRequest] = []
        self.plan_requests: list[PlanRequest] = []

    def select_objectives(self, request: GoalSelectionRequest) -> GoalSelection:
        self.goal_requests.append(request)
        return GoalSelection(objective_keys=self.selected)

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


def _runtime(session: Session, definition=STARFIRE_V2):  # type: ignore[no-untyped-def]
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


def _medical_plan() -> tuple[PlanStepProposal, ...]:
    return (
        _step("diagnose_patient", "patient_one", "doctor_lee"),
        _step("treat_patient", "patient_one", "doctor_lee", {"dosage": 2}),
    )


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
    runtime, _scope = _runtime(session, MEDICAL_EMERGENCY_V2)
    submission = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("mock")
    ).submit_goal("stabilize the patient", idempotency_key=str(uuid4()))

    assert submission.task is not None
    assert submission.resolution.source == "DETERMINISTIC"
    assert submission.task.planning_mode == "GENERIC"


def test_exact_goal_skips_provider_selection_but_initial_plan_uses_provider(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider(proposals=[_medical_plan()])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session, MEDICAL_EMERGENCY_V2)
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    submission = orchestrator.submit_goal("stabilize the patient", idempotency_key=str(uuid4()))

    assert submission.task is not None
    assert provider.goal_requests == []
    assert provider.plan_requests == []
    _start_initial_plan(orchestrator, submission.task)
    assert len(provider.plan_requests) == 1
    assert submission.task.planning_mode == "PROVIDER"
    catalog = {item.action_key: item for item in provider.plan_requests[0].planning_action_catalog}
    assert catalog["diagnose_patient"].currently_executable is True
    assert catalog["treat_patient"].currently_executable is False
    assert catalog["treat_patient"].known_blockers[0]["code"] == ("PUBLIC_PREREQUISITE_UNSATISFIED")


def test_fuzzy_goal_uses_provider_candidates_and_rejects_invented_objective(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _scope = _runtime(session)
    provider = RecordingProvider(
        selected=("gather_valley_intelligence",),
        proposals=[
            (
                _step(
                    "recon_valley",
                    "northern_valley",
                    "han_lie",
                    {"troop_count": 20, "approach": "CAUTIOUS"},
                ),
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    accepted = orchestrator.submit_goal(
        "find out what is happening beyond the northern pass",
        idempotency_key=str(uuid4()),
    )

    assert accepted.task is not None
    assert accepted.resolution.objective_keys == ("gather_valley_intelligence",)
    assert {item["key"] for item in provider.goal_requests[0].objective_candidates} == {
        objective.key for objective in STARFIRE_V2.objectives
    }

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


def test_provider_plan_is_validated_and_rejected_constraint_is_authoritative(
    session: Session,
) -> None:
    runtime, scope = _runtime(session, MEDICAL_EMERGENCY_V2)
    invalid_step = (_step("treat_patient", "patient_one", "invented_actor", {"dosage": 2}),)
    invalid = RecordingProvider(proposals=[invalid_step, invalid_step, invalid_step])
    with pytest.raises(GenericAgentError) as caught:
        GenericAgentService(session, scope, provider=invalid).create_task(
            runtime.session, "stabilize the patient"
        )
    assert caught.value.code == "MODEL_PLAN_REJECTED"
    session.rollback()

    runtime, scope = _runtime(session, MEDICAL_EMERGENCY_V2)
    repeated = RecordingProvider(
        proposals=[_medical_plan(), _medical_plan(), _medical_plan(), _medical_plan()]
    )
    agent = GenericAgentService(session, scope, provider=repeated)
    task = agent.create_task(runtime.session, "stabilize the patient")
    first = _medical_plan()[0]
    diagnose = next(
        item
        for item in repeated.plan_requests[0].planning_action_catalog
        if item.candidate_id == first.candidate_id
    )
    treatment_step = _medical_plan()[1]
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


def test_failure_knowledge_replan_uses_same_provider(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = (
        _step(
            "clear_valley",
            "northern_valley",
            "han_lie",
            {"troop_count": 80, "strategy": "STANDARD"},
        ),
    )
    recovery = (
        _step(
            "disrupt_supply",
            "enemy_north_supply_route",
            "han_lie",
            {"troop_count": 30, "strategy": "CAUTIOUS"},
        ),
        *initial,
    )
    provider = RecordingProvider(proposals=[initial, recovery])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session)
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    task = orchestrator.submit_goal("secure the northern valley", idempotency_key=str(uuid4())).task
    assert task is not None
    _start_initial_plan(orchestrator, task)
    checkpoint = orchestrator._ensure_checkpoint(task)
    orchestrator.acknowledge_action(expected_pacing_version=checkpoint.version)
    assert [item.call_type for item in provider.plan_requests] == ["INITIAL_PLAN"]
    orchestrator.replan(expected_pacing_version=checkpoint.version)

    assert len(provider.plan_requests) == 2
    assert provider.plan_requests[1].replan_reason == "ENCOUNTER_DEFEAT"
    assert (
        provider.plan_requests[1].known_world["facts"]["enemy_north_supply_route.supply_status"]
        == "ACTIVE"
    )


def test_starfire_catalog_evolves_from_failure_to_unlock_and_completion(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider(
        selected=("open_northern_trade_route",),
        proposals=[
            (
                _step(
                    "clear_valley",
                    "northern_valley",
                    "han_lie",
                    {"troop_count": 80, "strategy": "STANDARD"},
                ),
                _step(
                    "negotiate_support",
                    "north_village",
                    "lu_ning",
                    {"food_offer": 20, "requested_support": "GUIDE"},
                ),
                _step(
                    "repair_outpost",
                    "starfire_outpost",
                    "lu_ning",
                    {"repair_level": "FULL", "food_commitment": 30, "gold_commitment": 40},
                ),
                _step("test_trade_route", "northern_trade_route", "lu_ning"),
            ),
            (
                _step(
                    "disrupt_supply",
                    "enemy_north_supply_route",
                    "han_lie",
                    {"troop_count": 30, "strategy": "CAUTIOUS"},
                ),
                _step(
                    "clear_valley",
                    "northern_valley",
                    "han_lie",
                    {"troop_count": 80, "strategy": "STANDARD"},
                ),
                _step(
                    "negotiate_support",
                    "north_village",
                    "lu_ning",
                    {"food_offer": 20, "requested_support": "GUIDE"},
                ),
                _step(
                    "repair_outpost",
                    "starfire_outpost",
                    "lu_ning",
                    {"repair_level": "FULL", "food_commitment": 30, "gold_commitment": 40},
                ),
                _step("test_trade_route", "northern_trade_route", "lu_ning"),
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session)
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    submission = orchestrator.submit_goal(
        "让北方恢复贸易并重新稳定下来", idempotency_key=str(uuid4())
    )
    task = submission.task
    assert task is not None

    for _ in range(20):
        checkpoint = orchestrator._ensure_checkpoint(task)
        if checkpoint.phase == "COMPLETED":
            break
        if checkpoint.phase == "AWAITING_PLAN_START":
            orchestrator.start_initial_planning(expected_pacing_version=checkpoint.version)
        elif checkpoint.phase == "AWAITING_ACTION_ACK":
            orchestrator.acknowledge_action(expected_pacing_version=checkpoint.version)
        elif checkpoint.phase == "AWAITING_REPLAN_ACK":
            orchestrator.replan(expected_pacing_version=checkpoint.version)
        elif checkpoint.phase == "AWAITING_DEBRIEF_ACK":
            orchestrator.acknowledge_debrief(expected_pacing_version=checkpoint.version)
        else:
            raise AssertionError(f"Unexpected pacing phase {checkpoint.phase}")
    assert task.status.value == "SUCCEEDED"
    assert tuple(task.objective_scope_keys or ()) == ("open_northern_trade_route",)

    catalogs = [
        {(item.action_key, item.target_key) for item in request.planning_action_catalog}
        for request in provider.plan_requests
    ]
    assert ("repair_outpost", "starfire_outpost") in catalogs[0]
    assert ("test_trade_route", "northern_trade_route") in catalogs[0]
    assert ("disrupt_supply", "enemy_north_supply_route") not in catalogs[0]
    assert "enemy_north_supply_route" not in {
        item["key"] for item in provider.plan_requests[0].known_world["nodes"]
    }
    initial_provider_payload = json.dumps(
        provider.plan_requests[0].model_dump(mode="json"), ensure_ascii=False
    )
    assert "ambush_status" not in initial_provider_payload
    assert "enemy_north_supply_route" not in initial_provider_payload
    assert ("disrupt_supply", "enemy_north_supply_route") in catalogs[1]
    initial_by_action = {
        item.action_key: item for item in provider.plan_requests[0].planning_action_catalog
    }
    assert initial_by_action["clear_valley"].currently_executable is True
    assert initial_by_action["negotiate_support"].currently_executable is True
    assert initial_by_action["repair_outpost"].currently_executable is False
    assert initial_by_action["repair_outpost"].known_blockers[0]["code"] == (
        "TARGET_CURRENTLY_LOCKED"
    )
    assert initial_by_action["test_trade_route"].currently_executable is False
    assert [item.call_type for item in provider.plan_requests] == [
        "INITIAL_PLAN",
        "REPLAN",
    ]
    plans = tuple(
        session.scalars(
            select(AgentPlan).where(AgentPlan.task_id == task.id).order_by(AgentPlan.version)
        )
    )
    assert [
        [
            step.tool_arguments["action_key"]
            for step in session.scalars(
                select(AgentStep)
                .where(
                    AgentStep.plan_id == plan.id, AgentStep.selected_tool_name == "execute_action"
                )
                .order_by(AgentStep.sequence)
            )
        ]
        for plan in plans
    ] == [
        ["clear_valley", "negotiate_support", "repair_outpost", "test_trade_route"],
        [
            "disrupt_supply",
            "clear_valley",
            "negotiate_support",
            "repair_outpost",
            "test_trade_route",
        ],
    ]


def test_future_step_is_plan_valid_but_execution_guard_replans_if_still_locked(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    repair = _step(
        "repair_outpost",
        "starfire_outpost",
        "lu_ning",
        {"repair_level": "FULL", "food_commitment": 30, "gold_commitment": 40},
    )
    clear = _step(
        "clear_valley",
        "northern_valley",
        "han_lie",
        {"troop_count": 80, "strategy": "STANDARD"},
    )
    support = _step(
        "negotiate_support",
        "north_village",
        "lu_ning",
        {"food_offer": 20, "requested_support": "GUIDE"},
    )
    trade = _step("test_trade_route", "northern_trade_route", "lu_ning")
    provider = RecordingProvider(
        selected=("open_northern_trade_route",),
        proposals=[(repair, clear, support, trade), (clear, support, repair, trade)],
    )
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session)
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    task = orchestrator.submit_goal(
        "让北方恢复贸易并重新稳定下来", idempotency_key=str(uuid4())
    ).task
    assert task is not None

    _start_initial_plan(orchestrator, task)
    checkpoint = orchestrator._ensure_checkpoint(task)
    orchestrator.acknowledge_action(expected_pacing_version=checkpoint.version)
    orchestrator.replan(expected_pacing_version=checkpoint.version)

    assert [item.call_type for item in provider.plan_requests] == ["INITIAL_PLAN", "REPLAN"]
    assert provider.plan_requests[1].replan_reason == "ACTION_TARGET_UNAVAILABLE"
    assert (
        session.scalar(
            select(func.count())
            .select_from(WorldOperation)
            .where(WorldOperation.task_id == task.id)
        )
        == 0
    )
    assert task.status.value == "ACTIVE"


def test_provider_repair_uses_safe_diagnostics_and_stops_after_two_attempts(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown = (PlanStepProposal(candidate_id="candidate_invented"),)
    invalid_parameters = (_step("treat_patient", "patient_one", "doctor_lee", {"dosage": 99}),)
    provider = RecordingProvider(proposals=[unknown, invalid_parameters, _medical_plan()])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session, MEDICAL_EMERGENCY_V2)
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
    assert provider.plan_requests[1].repair_diagnostics == (
        {
            "code": "UNKNOWN_CANDIDATE",
            "step": 1,
            "candidate_id": "candidate_invented",
        },
    )
    assert provider.plan_requests[2].repair_diagnostics[0]["code"] == "PARAMETER_INVALID"
    diagnostic_text = str(provider.plan_requests[1].repair_diagnostics)
    assert "truth" not in diagnostic_text.casefold()
    assert "ambush" not in diagnostic_text.casefold()

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


def test_plan_order_repair_accepts_future_step_after_public_prerequisite(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    reversed_plan = tuple(reversed(_medical_plan()))
    provider = RecordingProvider(proposals=[reversed_plan, _medical_plan()])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session, MEDICAL_EMERGENCY_V2)

    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    submission = orchestrator.submit_goal("stabilize the patient", idempotency_key=str(uuid4()))

    assert submission.task is not None
    _start_initial_plan(orchestrator, submission.task)
    assert [item.call_type for item in provider.plan_requests] == ["INITIAL_PLAN", "REPAIR"]
    assert provider.plan_requests[1].repair_diagnostics[0]["code"] == "PLAN_ORDER_INVALID"


def test_empty_planning_catalog_is_unreachable_without_provider_fallback(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider(proposals=[])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    runtime, _scope = _runtime(session, MEDICAL_EMERGENCY_V2)
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


@pytest.mark.parametrize(
    ("definition", "goal", "proposal"),
    [
        (
            STARFIRE_V2,
            "gather valley intelligence",
            (
                _step(
                    "recon_valley",
                    "northern_valley",
                    "han_lie",
                    {"troop_count": 20, "approach": "CAUTIOUS"},
                ),
            ),
        ),
        (MEDICAL_EMERGENCY_V2, "stabilize the patient", _medical_plan()),
    ],
)
def test_starfire_and_medical_share_composition_wiring(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    definition,
    goal: str,
    proposal: tuple[PlanStepProposal, ...],  # type: ignore[no-untyped-def]
) -> None:
    runtime, _scope = _runtime(session, definition)
    provider = RecordingProvider(proposals=[proposal])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    orchestrator = configured_play_orchestrator(
        session, GameInstanceId(runtime.instance.id), _settings("openai_compatible")
    )
    submission = orchestrator.submit_goal(goal, idempotency_key=str(uuid4()))
    assert submission.task is not None
    _start_initial_plan(orchestrator, submission.task)
    assert len(provider.plan_requests) == 1


def test_draft_sandbox_uses_same_provider_composition_without_formal_game_row(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider(proposals=[_medical_plan()])
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    created = client.post(
        "/api/v1/scenarios",
        json={
            "mode": "EXAMPLE",
            "key": f"provider_sandbox_{uuid4().hex[:8]}",
            "name": "Provider Sandbox",
            "example_key": "medical_emergency",
        },
    )
    assert created.status_code == 201, created.text
    game_count = session.scalar(select(func.count()).select_from(GameInstance))

    response = client.post(
        f"/api/v1/scenarios/{created.json()['id']}/draft/sandbox",
        json={"expected_revision": 1, "goal": "stabilize the patient"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["goal_status"] == "SUCCEEDED"
    assert len(provider.plan_requests) == 1
    assert session.scalar(select(func.count()).select_from(GameInstance)) == game_count


def test_provider_timeout_and_malformed_json_are_explicit_and_secret_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings("openai_compatible")
    provider = OpenAICompatibleGenericProvider(settings)
    request = GoalSelectionRequest(goal="unclear", objective_candidates=({"key": "known"},))

    def timeout(*_args: object, **_kwargs: object) -> NoReturn:
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "post", timeout)
    with pytest.raises(GenericProviderError) as timed_out:
        provider.select_objectives(request)
    assert timed_out.value.code == "MODEL_PROVIDER_TIMEOUT"
    assert "not-a-real-key" not in str(timed_out.value)

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not-json"}}]},
            request=httpx.Request("POST", "https://provider.test/chat/completions"),
        ),
    )
    with pytest.raises(GenericProviderError) as malformed:
        provider.select_objectives(request)
    assert malformed.value.code == "MODEL_PROVIDER_RESPONSE_INVALID"

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"wrong":[]}'}}]},
            request=httpx.Request("POST", "https://provider.test/chat/completions"),
        ),
    )
    with pytest.raises(GenericProviderError) as wrong_schema:
        provider.select_objectives(request)
    assert wrong_schema.value.code == "MODEL_PROVIDER_RESPONSE_INVALID"

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_args, **_kwargs: httpx.Response(
            503,
            request=httpx.Request("POST", "https://provider.test/chat/completions"),
        ),
    )
    with pytest.raises(GenericProviderError) as http_error:
        provider.select_objectives(request)
    assert http_error.value.code == "MODEL_PROVIDER_HTTP_ERROR"


def test_provider_failure_returns_gateway_error_without_deterministic_fallback(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FailingPlanProvider(
        GenericProviderError("MODEL_PROVIDER_TIMEOUT", "provider timed out")
    )
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    version = require_builtin_v2_version(session, MEDICAL_EMERGENCY_V2)
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
    assert client.get(f"/api/v1/games/{game_id}/play").json()["current_task"] is not None


def test_replan_provider_failure_rolls_back_action_cycle_to_safe_pause(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FailOnReplanProvider(
        proposals=[
            (
                _step(
                    "clear_valley",
                    "northern_valley",
                    "han_lie",
                    {"troop_count": 80, "strategy": "STANDARD"},
                ),
            )
        ]
    )
    monkeypatch.setattr(
        "app.services.composition.build_generic_provider", lambda _settings: provider
    )
    scenario = next(
        item for item in client.get("/api/v1/scenarios").json() if item["key"] == "starfire_command"
    )
    game = client.post(
        "/api/v1/games",
        json={
            "scenario_version_id": scenario["current_published_version_id"],
            "idempotency_key": str(uuid4()),
        },
    ).json()
    goal = client.post(
        f"/api/v1/games/{game['id']}/goals",
        json={"goal": "secure the northern valley", "idempotency_key": str(uuid4())},
    )
    assert goal.status_code == 200, goal.text
    task = goal.json()["task"]
    start = client.post(
        f"/api/v1/games/{game['id']}/play/start-planning",
        json={"expected_pacing_version": task["pacing_version"]},
    )
    assert start.status_code == 200, start.text
    task = start.json()["current_task"]
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
    assert state["current_task"]["execution_phase"] == "AWAITING_REPLAN_ACK"
    assert session.scalar(select(func.count()).select_from(WorldOperation)) == before_operations + 1
