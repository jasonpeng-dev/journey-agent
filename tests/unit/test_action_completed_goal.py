from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import (
    GenericAgentService,
    GenericGoalResolution,
    GenericGoalResolver,
    _DynamicGoalGrounding,
    _validate_dynamic_goal_lossless_operation_semantics,
)
from app.agent.provider import (
    DynamicGoalCandidateReference,
    DynamicGoalEntityGrounding,
    DynamicGoalEntityGroundingRequest,
    DynamicGoalInterpretation,
    DynamicGoalInterpretationRequest,
    PlanProposal,
    PlanRequest,
    PlanStepProposal,
)
from app.domain.action_invocation import ActionInvocationBinding
from app.domain.completion import OperationMatchConstraint
from app.domain.enums import AgentTaskStatus, WorldOperationStatus
from app.domain.formal_goal import (
    AdHocActionCompletedRequirementCandidateV1,
    AdHocFactRequirementCandidateV1,
    AdHocGoalCandidateSetV2,
    FormalGoalError,
    compile_ad_hoc_dynamic_goal_v2,
)
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import AgentStep, AgentTask, Player, WorldOperation
from app.scenarios.builtin import require_builtin_v2_version
from app.scenarios.versions import ScenarioVersionRepository
from app.services.completion_kernel import (
    CompletionKernelError,
    evaluate_operation_requirement,
)
from app.services.formal_goal import FormalGoalCompletionEvaluator, load_formal_goal_for_task
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionService
from app.services.player_projection import PlayerProjectionService
from app.services.runtime_initialization import RuntimeInitializationService
from tests.scenario_fixtures import GENERIC_TEST
from tests.unit.test_transport_resource import _definition as transport_definition
from tests.unit.test_transport_resource import _runtime as transport_runtime


class _RecordingProvider:
    def __init__(self, proposal: PlanProposal) -> None:
        self.proposal = proposal
        self.requests: list[PlanRequest] = []

    @property
    def model_name(self) -> str:
        return "action-completed-test-provider"

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.requests.append(request)
        return self.proposal


class _OperationGoalResolverProvider(_RecordingProvider):
    def ground_dynamic_goal_entities(
        self,
        _request: DynamicGoalEntityGroundingRequest,
    ) -> DynamicGoalEntityGrounding:
        return DynamicGoalEntityGrounding(
            candidate_refs=(
                DynamicGoalCandidateReference(ref_type="ACTION", key="diagnose_patient"),
                DynamicGoalCandidateReference(ref_type="NODE", key="patient_one"),
            )
        )

    def interpret_dynamic_goal(
        self,
        _request: DynamicGoalInterpretationRequest,
    ) -> DynamicGoalInterpretation:
        return DynamicGoalInterpretation(requirements=(_inspect_candidate(),))


def _generic_runtime(session: Session, key: str) -> tuple[Any, Any]:
    version = require_builtin_v2_version(session, GENERIC_TEST)
    player = Player(name=key)
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=key,
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    return runtime, scope


def _dynamic_task(
    session: Session,
    runtime: Any,
    scope: Any,
    candidate: AdHocActionCompletedRequirementCandidateV1,
    *,
    provider: _RecordingProvider | None = None,
    goal: str = "complete the requested operation",
) -> AgentTask:
    resolution = GenericGoalResolution(
        status="RESOLVED",
        source="TEST",
        dynamic_requirements=(candidate,),
    )
    return GenericAgentService(session, scope, provider=provider).create_task(
        runtime.session,
        goal,
        resolved_goal=resolution,
        initialize_plan=False,
    )


def _proposal(
    action_key: str,
    actor_key: str,
    target_key: str,
    *,
    parameters: dict[str, object] | None = None,
    stop_reason: str = "OBJECTIVE_COMPLETION",
) -> PlanProposal:
    return PlanProposal(
        segment_goal="execute the frozen operation goal",
        goal_link="advances the frozen operation requirement",
        continuation_intent="continue after the operation is authoritative",
        stop_reason=stop_reason,  # type: ignore[arg-type]
        steps=(
            PlanStepProposal(
                action_key=action_key,
                actor_key=actor_key,
                target_key=target_key,
                parameters=parameters or {},
            ),
        ),
    )


def _operation(
    session: Session,
    task: AgentTask,
    *,
    action_key: str,
    actor_key: str,
    target_key: str,
    status: WorldOperationStatus,
    parameters: dict[str, object] | None = None,
    outcome: dict[str, object] | None = None,
    idempotency_key: str,
) -> WorldOperation:
    operation = WorldOperation(
        player_id=task.player_id,
        game_instance_id=task.game_instance_id,
        task_id=task.id,
        actor_key=actor_key,
        action_key=action_key,
        execution_mode="IMMEDIATE",
        target_key=target_key,
        status=status,
        parameters=parameters or {},
        outcome=outcome,
        idempotency_key=idempotency_key,
    )
    session.add(operation)
    session.flush()
    return operation


def _inspect_candidate(
    *,
    actor_key: str | None = None,
) -> AdHocActionCompletedRequirementCandidateV1:
    return AdHocActionCompletedRequirementCandidateV1(
        kind="ACTION_COMPLETED",
        action_key="diagnose_patient",
        actor_key=actor_key,
        target_key="patient_one",
    )


def _transport_candidate(
    *,
    actor_key: str | None = None,
    source_region: str = "region_a",
    destination_region: str = "region_b",
    parameters: dict[str, object] | None = None,
) -> AdHocActionCompletedRequirementCandidateV1:
    return AdHocActionCompletedRequirementCandidateV1(
        kind="ACTION_COMPLETED",
        action_key="transport_resource",
        actor_key=actor_key,
        target_key=destination_region,
        binding_constraints=(
            ActionInvocationBinding(role="destination_region", value=destination_region),
            ActionInvocationBinding(role="source_region", value=source_region),
        ),
        parameter_constraints=parameters or {"resource_key": "cargo_alpha", "amount": 10},
    )


def test_resolver_preserves_public_action_goal_as_action_completed(session: Session) -> None:
    provider = _OperationGoalResolverProvider(
        _proposal("diagnose_patient", "doctor_lee", "patient_one")
    )

    resolution = GenericGoalResolver(provider=provider).resolve(
        "diagnose patient one",
        GENERIC_TEST,
    )

    assert resolution.status == "RESOLVED"
    assert len(resolution.dynamic_requirements) == 1
    requirement = resolution.dynamic_requirements[0]
    assert isinstance(requirement, AdHocActionCompletedRequirementCandidateV1)
    assert requirement.kind == "ACTION_COMPLETED"
    assert requirement.action_key == "diagnose_patient"
    assert requirement.target_key == "patient_one"


def test_matching_inspect_action_is_a_valid_operation_goal_plan(session: Session) -> None:
    runtime, scope = _generic_runtime(session, "action-goal-inspect-plan")
    provider = _RecordingProvider(_proposal("diagnose_patient", "doctor_lee", "patient_one"))
    task = _dynamic_task(
        session,
        runtime,
        scope,
        _inspect_candidate(),
        provider=provider,
        goal="inspect patient one",
    )

    plan = GenericAgentService(session, scope, provider=provider).plan(task)

    assert plan.stop_reason == "OBJECTIVE_COMPLETION"
    assert len(session.scalars(select(AgentStep).where(AgentStep.plan_id == plan.id)).all()) == 1
    assert provider.requests[0].planner_input is not None
    operation_goal = provider.requests[0].planner_input.operation_goal
    assert operation_goal is not None
    assert operation_goal.action_key == "diagnose_patient"
    assert operation_goal.actor_key is None
    assert operation_goal.target_key == "patient_one"


def test_segment_complete_does_not_require_operation_completion(session: Session) -> None:
    runtime, scope = _generic_runtime(session, "action-goal-segment-complete")
    provider = _RecordingProvider(
        _proposal(
            "diagnose_patient",
            "doctor_lee",
            "patient_one",
            stop_reason="SEGMENT_COMPLETE",
        )
    )
    task = _dynamic_task(session, runtime, scope, _inspect_candidate(), provider=provider)

    plan = GenericAgentService(session, scope, provider=provider).plan(task)

    assert plan.stop_reason == "SEGMENT_COMPLETE"
    assert task.status == AgentTaskStatus.ACTIVE


def test_authoritative_inspect_operation_is_required_for_completion(session: Session) -> None:
    runtime, scope = _generic_runtime(session, "action-goal-inspect-completion")
    task = _dynamic_task(session, runtime, scope, _inspect_candidate(actor_key="doctor_lee"))
    service = GenericActionService(session, scope)

    pending = _operation(
        session,
        task,
        action_key="diagnose_patient",
        actor_key="doctor_lee",
        target_key="patient_one",
        status=WorldOperationStatus.PENDING,
        idempotency_key="inspect-pending",
    )
    agent = GenericAgentService(session, scope)
    assert agent.evaluate(task).completed is False

    pending.status = WorldOperationStatus.RESOLVED
    pending.outcome = {"outcome_code": "DIAGNOSED", "failure": {"code": "FAILED"}}
    session.flush()
    assert agent.evaluate(task).completed is False

    result = service.execute_action(
        actor_key="doctor_lee",
        action_key="diagnose_patient",
        target_key="patient_one",
        parameters={},
        idempotency_key="inspect-success",
        task_id=task.id,
    )
    assert result.applied is not None and result.applied.outcome.failure is None
    evaluation = agent.evaluate(task)
    assert evaluation.completed is True
    assert evaluation.requirements[0][2] is True


def test_operation_goal_uses_shared_completion_and_public_player_projection(
    session: Session,
) -> None:
    runtime, scope = _generic_runtime(session, "action-goal-player-projection")
    task = _dynamic_task(session, runtime, scope, _inspect_candidate())
    contract = load_formal_goal_for_task(session, scope, task)
    operation_evaluation = FormalGoalCompletionEvaluator(session, scope).evaluate(
        contract,
        definition=GENERIC_TEST,
        task=task,
    )
    projected = PlayerProjectionService(session).task(
        task,
        GENERIC_TEST,
        known_facts={},
    )

    assert operation_evaluation.completed is False
    assert operation_evaluation.requirements[0].kind == "OPERATION"
    assert len(projected.goal_requirements) == 1
    requirement = projected.goal_requirements[0]
    assert requirement.kind == "ACTION_COMPLETED"
    assert requirement.action_key == "diagnose_patient"
    assert requirement.target_key == "patient_one"
    assert "patient_one" not in requirement.description


def test_transport_operation_matches_actor_source_target_resource_and_amount(
    session: Session,
) -> None:
    definition = transport_definition()
    runtime, scope = transport_runtime(session, definition, "action-goal-transport")
    task = _dynamic_task(
        session,
        runtime,
        scope,
        _transport_candidate(actor_key="carrier"),
        goal="transport cargo alpha from region a to region b",
    )
    result = GenericActionService(session, scope).execute_action(
        actor_key="carrier",
        action_key="transport_resource",
        target_key="region_b",
        parameters={"resource_key": "cargo_alpha", "amount": 10},
        idempotency_key="action-goal-transport-success",
        task_id=task.id,
    )
    assert result.applied is not None and result.applied.outcome.failure is None
    scope = GameInstanceService(session).load(GameInstanceId(task.game_instance_id))
    definition = ScenarioVersionRepository(session).load(scope.scenario_version_id).definition

    def evaluate(constraint: OperationMatchConstraint) -> bool:
        return evaluate_operation_requirement(
            session,
            scope,
            task,
            definition,
            requirement_identity="operation/transport",
            constraint=constraint,
        ).satisfied

    base = OperationMatchConstraint(
        action_key="transport_resource",
        actor_key="carrier",
        target_key="region_b",
        bindings=(
            ActionInvocationBinding(role="source_region", value="region_a"),
            ActionInvocationBinding(role="destination_region", value="region_b"),
        ),
        parameters={"resource_key": "cargo_alpha", "amount": 10},
    )
    assert evaluate(base) is True
    assert evaluate(base.model_copy(update={"actor_key": "not_carrier"})) is False
    with pytest.raises(CompletionKernelError):
        evaluate(base.model_copy(update={"action_key": "not_an_action"}))
    assert (
        evaluate(
            base.model_copy(
                update={
                    "bindings": (
                        ActionInvocationBinding(role="source_region", value="region_c"),
                        ActionInvocationBinding(role="destination_region", value="region_b"),
                    )
                }
            )
        )
        is False
    )
    assert evaluate(base.model_copy(update={"target_key": "region_c"})) is False
    assert (
        evaluate(
            base.model_copy(update={"parameters": {"resource_key": "cargo_beta", "amount": 10}})
        )
        is False
    )
    assert (
        evaluate(
            base.model_copy(update={"parameters": {"resource_key": "cargo_alpha", "amount": 11}})
        )
        is False
    )


def test_operation_completion_is_task_and_game_instance_owned(session: Session) -> None:
    runtime_one, scope_one = _generic_runtime(session, "action-goal-boundary-one")
    old_task = _dynamic_task(session, runtime_one, scope_one, _inspect_candidate())
    old_operation = _operation(
        session,
        old_task,
        action_key="diagnose_patient",
        actor_key="doctor_lee",
        target_key="patient_one",
        status=WorldOperationStatus.RESOLVED,
        outcome={"outcome_code": "DIAGNOSED", "failure": None},
        idempotency_key="boundary-success",
    )
    old_task.status = AgentTaskStatus.SUCCEEDED
    session.flush()

    new_task = _dynamic_task(session, runtime_one, scope_one, _inspect_candidate())
    assert GenericAgentService(session, scope_one).evaluate(new_task).completed is False

    runtime_two, scope_two = _generic_runtime(session, "action-goal-boundary-two")
    other_task = _dynamic_task(session, runtime_two, scope_two, _inspect_candidate())
    assert GenericAgentService(session, scope_two).evaluate(other_task).completed is False
    assert old_operation.game_instance_id != other_task.game_instance_id


def test_action_completed_hash_is_stable_across_binding_and_transport_wire_order(
    session: Session,
) -> None:
    definition = transport_definition()
    _runtime, scope = transport_runtime(session, definition, "action-goal-hash")
    snapshot = ScenarioVersionRepository(session).load(scope.scenario_version_id)
    first = compile_ad_hoc_dynamic_goal_v2(
        snapshot,
        AdHocGoalCandidateSetV2(
            requirements=(
                _transport_candidate(parameters={"resource_key": "cargo_alpha", "amount": 10}),
            )
        ),
    )
    second = compile_ad_hoc_dynamic_goal_v2(
        snapshot,
        AdHocGoalCandidateSetV2(
            requirements=(
                _transport_candidate(
                    parameters={"resources": [{"amount": 10, "resource_key": "cargo_alpha"}]}
                ),
            )
        ),
    )

    assert first.content_hash == second.content_hash
    assert first.canonical_json() == second.canonical_json()


def test_explicit_operation_language_cannot_downgrade_to_state_requirement() -> None:
    definition = transport_definition()
    grounding = _DynamicGoalGrounding(
        status="RESOLVED",
        candidate_refs=(
            DynamicGoalCandidateReference(ref_type="ACTION", key="transport_resource"),
            DynamicGoalCandidateReference(ref_type="REGION", key="region_a"),
            DynamicGoalCandidateReference(ref_type="REGION", key="region_b"),
            DynamicGoalCandidateReference(ref_type="RESOURCE", key="cargo_alpha"),
        ),
    )
    state_candidate = AdHocFactRequirementCandidateV1(
        kind="FACT",
        node_key="region_b",
        fact_key="delivered",
        accepted_values=(True,),
    )

    with pytest.raises(FormalGoalError) as error:
        _validate_dynamic_goal_lossless_operation_semantics(
            "transport cargo alpha 10 from region a to region b",
            definition,
            grounding,
            AdHocGoalCandidateSetV2(requirements=(state_candidate,)),
        )

    assert error.value.code == "FORMAL_GOAL_OPERATION_SEMANTICS_LOST"
