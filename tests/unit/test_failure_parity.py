from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.provider import (
    GenericProviderError,
    PlannerInput,
    PlanProposal,
    PlanRequest,
    PlanViolation,
)
from app.domain.enums import AgentTaskStatus, CommandReachability
from app.domain.failures import (
    ActorEvidence,
    FactEvidence,
    FailureDomain,
    FailureEvent,
    FailureEvidence,
    FailureKind,
    FailurePhase,
    LocalityEvidence,
    ResourceEvidence,
    TransportEvidence,
    normalize_legacy_failure,
)
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceRegionResourceKnowledge,
    GameInstanceResourceState,
    PlanningCycle,
)
from app.services.generic_actions import GenericActionError
from app.services.generic_game import GenericGameError, GenericGameService
from tests.integration.test_linjiang_v2_0_v3_knowledge import (
    V2_0,
    _definition_with_pool,
    _set_actor,
)
from tests.integration.test_linjiang_v2_0_v3_knowledge import (
    _fact as _linjiang_fact,
)
from tests.integration.test_linjiang_v2_0_v3_knowledge import (
    _runtime as _linjiang_runtime,
)
from tests.unit.test_generic_agent import (
    _accepted_proposal,
    _agent,
    _fact_precondition_definition,
)
from tests.unit.test_transport_resource import (
    _definition as _transport_definition,
)
from tests.unit.test_transport_resource import (
    _pool,
    _transport,
)
from tests.unit.test_transport_resource import (
    _runtime as _transport_runtime,
)


def _event(
    failure: object,
    *,
    phase: FailurePhase,
    evidence: FailureEvidence | Mapping[str, object],
    **metadata: object,
) -> FailureEvent:
    return normalize_legacy_failure(
        failure,
        phase=phase,
        evidence=evidence,
        metadata=metadata,
    )


def test_travel_blocked_preserves_reveal_and_actor_position(session: Session) -> None:
    definition = _definition_with_pool(
        "phase_a_travel_blocked",
        pool_key="phase_a_travel_pool",
        resource_key="general_engineering_parts",
        region_key="central_district",
        quantity=1,
        blocked_transport_keys=("central_river_tunnel",),
    )
    runtime, scope = cast(
        tuple[Any, Any], _linjiang_runtime(session, definition, "phase-a-travel-blocked")
    )
    _set_actor(session, runtime.instance.id, "logistics_team_alpha", "central_district")
    game = GenericGameService(session, scope)

    result = game.execute(
        actor_key="logistics_team_alpha",
        action_key="travel",
        target_node_key="east_residential_district",
        parameters={},
    )

    assert result.outcome.failure is not None
    failure = result.outcome.failure
    assert failure.code == "TRAVEL_BLOCKED"
    assert failure.message == "The one-hop Transport is currently blocked"
    assert failure.retryable is True
    actor = session.get(GameInstanceActor, (runtime.instance.id, "logistics_team_alpha"))
    passability = _linjiang_fact(
        session, runtime.instance.id, "central_river_tunnel", "passable"
    )
    assert actor is not None and actor.current_node_key == "central_district"
    assert passability.visibility == Visibility.KNOWN and passability.truth_value is False
    assert any(item.key == "central_river_tunnel.passable" for item in result.knowledge_changes)
    event = _event(
        failure,
        phase=FailurePhase.RESOLVE,
        evidence=TransportEvidence(
            transport_key="central_river_tunnel",
            source_region="central_district",
            target_region="east_residential_district",
            passable=False,
            knowledge_status="KNOWN",
        ),
        actor_location_before="central_district",
        actor_location_after=actor.current_node_key,
    )
    assert event.kind is FailureKind.TRAVEL_BLOCKED
    assert event.legacy_retryable is True
    assert event.gameplay_recovery_eligible
    assert event.metadata["actor_location_after"] == "central_district"


def test_transport_blocked_preserves_world_operation_and_reveal(session: Session) -> None:
    document = _transport_definition().model_dump(mode="json")
    edge = next(node for node in document["world"]["nodes"] if node["key"] == "edge_ab")
    edge["facts"][0]["initial_value"] = False
    definition = _transport_definition().model_validate(document)
    runtime, scope = _transport_runtime(session, definition, "phase-a-transport-blocked")

    result = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={"resource_key": "cargo_alpha", "amount": 1},
        key="phase-a-transport-blocked-1",
    )

    assert result.applied is not None and result.applied.outcome.failure is not None
    failure = result.applied.outcome.failure
    assert failure.code == "TRANSPORT_BLOCKED"
    assert failure.retryable is True
    assert result.operation.outcome is not None
    assert result.operation.outcome["failure"] == asdict(failure)
    actor = session.get(GameInstanceActor, (runtime.instance.id, "carrier"))
    fact = session.get(GameInstanceFactState, (runtime.instance.id, "edge_ab", "passable"))
    assert actor is not None and actor.current_node_key == "region_a"
    assert fact is not None and fact.visibility == Visibility.KNOWN and fact.truth_value is False
    event = _event(
        failure,
        phase=FailurePhase.RESOLVE,
        evidence=TransportEvidence(
            transport_key="edge_ab",
            source_region="region_a",
            target_region="region_b",
            passable=False,
            knowledge_status="KNOWN",
        ),
        world_operation_failure=result.operation.outcome["failure"],
    )
    assert event.to_legacy().code == "TRANSPORT_BLOCKED"
    assert event.to_legacy().retryable is True


def test_transport_resource_insufficient_is_atomic_and_retryable(session: Session) -> None:
    definition = _transport_definition()
    runtime, scope = _transport_runtime(session, definition, "phase-a-resource-shortfall")

    result = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={"resource_key": "cargo_alpha", "amount": 11},
        key="phase-a-resource-shortfall-1",
    )

    assert result.applied is not None and result.applied.outcome.failure is not None
    failure = result.applied.outcome.failure
    assert failure.code == "TRANSPORT_RESOURCE_INSUFFICIENT"
    assert failure.retryable is True
    source = _pool(session, runtime.instance.id, "cargo_alpha", "region_a", "source_alpha")
    inflow = _pool(
        session, runtime.instance.id, "cargo_alpha", "region_b", "__runtime_known_inflow__"
    )
    actor = session.get(GameInstanceActor, (runtime.instance.id, "carrier"))
    assert source is not None and source.value == 10
    assert inflow is None
    assert actor is not None and actor.current_node_key == "region_a"
    assert result.operation.outcome is not None
    event = _event(
        failure,
        phase=FailurePhase.RESOLVE,
        evidence=ResourceEvidence(
            resource_key="cargo_alpha",
            required=11,
            available=10,
            deficit=1,
            knowledge_status="KNOWN",
            scope_key="region_a",
        ),
    )
    assert event.kind is FailureKind.RESOURCE_INSUFFICIENT
    assert event.legacy_retryable is True


def test_transport_resource_unknown_knowledge_preserves_completed_first_hop(
    session: Session,
) -> None:
    definition = _transport_definition()
    runtime, scope = _transport_runtime(session, definition, "phase-a-resource-unknown")
    first = _transport(
        session,
        scope,
        target_key="region_b",
        parameters={"resource_key": "cargo_alpha", "amount": 10},
        key="phase-a-resource-unknown-1",
    )
    second = _transport(
        session,
        scope,
        target_key="region_c",
        parameters={"resource_key": "cargo_alpha", "amount": 15},
        key="phase-a-resource-unknown-2",
    )

    assert first.applied is not None and first.applied.outcome.failure is None
    assert second.applied is not None and second.applied.outcome.failure is not None
    failure = second.applied.outcome.failure
    assert failure.code == "TRANSPORT_RESOURCE_KNOWLEDGE_UNKNOWN"
    assert failure.retryable is True
    b_inflow = _pool(
        session, runtime.instance.id, "cargo_alpha", "region_b", "__runtime_known_inflow__"
    )
    c_inflow = _pool(
        session, runtime.instance.id, "cargo_alpha", "region_c", "__runtime_known_inflow__"
    )
    actor = session.get(GameInstanceActor, (runtime.instance.id, "carrier"))
    assert b_inflow is not None and b_inflow.value == 10
    assert c_inflow is None
    assert actor is not None and actor.current_node_key == "region_b"
    event = _event(
        failure,
        phase=FailurePhase.RESOLVE,
        evidence=ResourceEvidence(
            resource_key="cargo_alpha",
            required=15,
            available=10,
            knowledge_status="UNKNOWN",
            scope_key="region_b",
        ),
    )
    assert event.kind is FailureKind.KNOWLEDGE_UNKNOWN


def test_resource_survey_already_completed_is_non_retryable_and_noop(session: Session) -> None:
    definition = _definition_with_pool(
        "phase_a_survey_completed",
        pool_key="phase_a_survey_pool",
        resource_key="general_engineering_parts",
        region_key="north_industrial_district",
        quantity=1,
    )
    runtime, scope = cast(
        tuple[Any, Any], _linjiang_runtime(session, definition, "phase-a-survey-completed")
    )
    _set_actor(session, runtime.instance.id, "logistics_team_alpha", "north_industrial_district")
    game = GenericGameService(session, scope)
    first = game.execute(
        actor_key="logistics_team_alpha",
        action_key="survey_resources",
        target_node_key="north_industrial_district",
        parameters={},
    )
    second = game.execute(
        actor_key="logistics_team_alpha",
        action_key="survey_resources",
        target_node_key="north_industrial_district",
        parameters={},
    )

    assert first.outcome.failure is None
    assert second.outcome.failure is not None
    failure = second.outcome.failure
    assert failure.code == "RESOURCE_SURVEY_ALREADY_COMPLETED"
    assert failure.retryable is False
    assert second.knowledge_changes == ()
    knowledge = session.get(
        GameInstanceRegionResourceKnowledge,
        (runtime.instance.id, "north_industrial_district"),
    )
    assert knowledge is not None
    assert knowledge.resource_survey_completed is True
    event = _event(
        failure,
        phase=FailurePhase.EXECUTION,
        evidence=ResourceEvidence(
            resource_key="general_engineering_parts",
            knowledge_status="KNOWN",
            scope_key="north_industrial_district",
        ),
    )
    assert event.kind is FailureKind.ALREADY_COMPLETED
    assert event.legacy_retryable is False


def test_locality_invalid_is_an_action_failure_without_world_mutation(session: Session) -> None:
    runtime, scope = cast(
        tuple[Any, Any], _linjiang_runtime(session, V2_0, "phase-a-locality-invalid")
    )
    _set_actor(session, runtime.instance.id, "logistics_team_alpha", "central_district")
    actor_before = session.get(GameInstanceActor, (runtime.instance.id, "logistics_team_alpha"))
    assert actor_before is not None

    with pytest.raises(GenericGameError) as caught:
        GenericGameService(session, scope).execute(
            actor_key="logistics_team_alpha",
            action_key="travel",
            target_node_key="central_district",
            parameters={},
        )

    assert caught.value.code == "LOCALITY_TRAVEL_SAME_REGION"
    actor_after = session.get(GameInstanceActor, (runtime.instance.id, "logistics_team_alpha"))
    assert actor_after is not None and actor_after.current_node_key == actor_before.current_node_key
    event = _event(
        caught.value,
        phase=FailurePhase.EXECUTION,
        evidence=LocalityEvidence(
            actor_region="central_district",
            target_region="central_district",
            required_locality="DIFFERENT_REGION",
        ),
    )
    assert event.kind is FailureKind.LOCALITY_INVALID
    assert event.gameplay_recovery_eligible


def test_disconnected_actor_is_reachability_blocker_without_action_mutation(
    session: Session,
) -> None:
    runtime, scope = cast(
        tuple[Any, Any], _linjiang_runtime(session, V2_0, "phase-a-actor-disconnected")
    )
    _set_actor(session, runtime.instance.id, "logistics_team_alpha", "central_district")
    actor = session.get(GameInstanceActor, (runtime.instance.id, "logistics_team_alpha"))
    assert actor is not None
    actor.command_reachability = CommandReachability.DISCONNECTED.value
    session.flush()

    with pytest.raises(GenericGameError) as caught:
        GenericGameService(session, scope).execute(
            actor_key="logistics_team_alpha",
            action_key="travel",
            target_node_key="east_residential_district",
            parameters={},
        )

    assert caught.value.code == "ACTOR_COMMAND_DISCONNECTED"
    assert caught.value.retryable is True
    unchanged = session.get(GameInstanceActor, (runtime.instance.id, "logistics_team_alpha"))
    assert unchanged is not None and unchanged.current_node_key == "central_district"
    event = _event(
        caught.value,
        phase=FailurePhase.EXECUTION,
        evidence=ActorEvidence(
            actor_key="logistics_team_alpha",
            required="ONLINE",
            actual="DISCONNECTED",
            reachability="DISCONNECTED",
        ),
    )
    assert event.kind is FailureKind.ACTOR_UNAVAILABLE
    assert event.legacy_retryable is True


def test_authored_resource_compare_preserves_preflight_replan_and_completion(
    session: Session,
) -> None:
    provider = _RecordingProvider(_accepted_proposal())
    agent, runtime = cast(
        tuple[Any, Any], _agent(session, preflight=True, provider=provider)
    )
    resource = session.get(GameInstanceResourceState, (runtime.instance.id, "medicine"))
    assert resource is not None
    resource.value = 0
    session.flush()
    game = GenericGameService(session, agent.scope)
    preflight = game.preflight(
        actor_key="doctor_lee",
        action_key="treat_patient",
        target_node_key="patient_one",
        parameters={"dosage": 2},
    )
    assert preflight is not None and preflight.failure is not None
    assert preflight.failure.code == "INSUFFICIENT_MEDICINE"
    event = _event(
        preflight.failure,
        phase=FailurePhase.PREFLIGHT,
        evidence=ResourceEvidence(
            resource_key="medicine",
            required=1,
            available=0,
            deficit=1,
            knowledge_status="KNOWN",
        ),
    )
    assert event.kind is FailureKind.RESOURCE_INSUFFICIENT
    assert event.to_legacy().message == "Find medicine before treatment."

    task = agent.create_task(runtime.session, "stabilize the patient")
    failed_step = agent.execute_next(task)
    assert failed_step is not None
    assert failed_step.failure_code == "INSUFFICIENT_MEDICINE"
    assert task.last_error_code == "INSUFFICIENT_MEDICINE"
    assert task.replan_count == 1
    assert task.status == AgentTaskStatus.ACTIVE
    assert provider.plan_requests
    latest_cycle = session.scalar(
        select(PlanningCycle)
        .where(PlanningCycle.task_id == task.id)
        .order_by(PlanningCycle.created_at.desc())
    )
    assert latest_cycle is not None
    planner_input = PlannerInput.model_validate(latest_cycle.planner_input)
    assert isinstance(planner_input.execution_context, dict)
    assert any(
        "INSUFFICIENT_MEDICINE" in str(value)
        for value in planner_input.execution_context.values()
    )
    assert resource.value == 0
    stable = session.get(GameInstanceFactState, (runtime.instance.id, "patient_one", "stable"))
    assert stable is not None and stable.truth_value is False
    resource.value = 2
    session.flush()
    completed = agent.execute_next(task)
    assert completed is not None
    assert task.status == AgentTaskStatus.SUCCEEDED
    assert stable.truth_value is True


def test_authored_fact_not_equals_is_preflight_and_not_validator_rejection(
    session: Session,
) -> None:
    definition = _fact_precondition_definition()
    agent, runtime = cast(tuple[Any, Any], _agent(session, definition=definition))
    fact = session.get(GameInstanceFactState, (runtime.instance.id, "triage_room", "ready"))
    assert fact is not None
    fact.truth_value = False
    fact.visibility = Visibility.KNOWN
    session.flush()
    preflight = GenericGameService(session, agent.scope).preflight(
        actor_key="doctor_lee",
        action_key="treat_patient",
        target_node_key="patient_one",
        parameters={"dosage": 2},
    )
    assert preflight is not None and preflight.failure is not None
    failure = preflight.failure
    assert failure.code == "READY_REQUIRED"
    assert failure.retryable is True
    event = _event(
        failure,
        phase=FailurePhase.PREFLIGHT,
        evidence=FactEvidence(
            node_key="triage_room",
            fact_key="ready",
            required=True,
            accepted_values=(True,),
            actual=False,
            knowledge_status="KNOWN",
        ),
    )
    assert event.domain is FailureDomain.ACTION_RUNTIME
    assert event.kind is FailureKind.PRECONDITION_UNMET
    assert fact.truth_value is False
    assert fact.visibility is Visibility.KNOWN


def test_generic_parameter_and_target_failures_project_legacy_fields(session: Session) -> None:
    definition = _transport_definition()
    _runtime, scope = _transport_runtime(session, definition, "phase-a-generic-errors")
    with pytest.raises(GenericActionError) as parameter_error:
        _transport(
            session,
            scope,
            target_key="region_b",
            parameters={"resource_key": "cargo_alpha", "amount": 0},
            key="phase-a-generic-errors-parameter",
        )
    assert parameter_error.value.code == "ACTION_PARAMETERS_INVALID"
    parameter_event = _event(
        parameter_error.value,
        phase=FailurePhase.EXECUTION,
        evidence={
            "family": "PARAMETER",
            "parameter_key": "amount",
            "validation_error": "positive integer",
        },
    )
    assert parameter_event.kind is FailureKind.PARAMETER_INVALID
    assert parameter_event.to_legacy().code == "ACTION_PARAMETERS_INVALID"

    with pytest.raises(GenericGameError) as target_error:
        GenericGameService(session, scope).execute(
            actor_key="carrier",
            action_key="transport_resource",
            target_node_key="does_not_exist",
            parameters={"resource_key": "cargo_alpha", "amount": 1},
        )
    assert target_error.value.code == "ACTION_TARGET_INVALID"
    target_event = _event(
        target_error.value,
        phase=FailurePhase.EXECUTION,
        evidence={
            "family": "TARGET",
            "target_key": "does_not_exist",
            "required": "transport_destination",
            "actual": "NOT_FOUND",
        },
    )
    assert target_event.kind is FailureKind.TARGET_INVALID


def test_validator_rejection_stays_out_of_gameplay_action_recovery() -> None:
    violation = PlanViolation(
        code="TARGET_INTERACTION_INVALID",
        dimension="TARGET_INTERACTION",
        action_key="repair",
        actor_key="team",
        target_key="facility",
        required_interaction_key="repairable",
        actual_interactions=("inspectable",),
    )
    event = normalize_legacy_failure(violation)
    assert event.domain is FailureDomain.PLAN_VALIDATION
    assert event.kind is FailureKind.VALIDATOR_REJECTED
    assert event.gameplay_recovery_eligible is False
    assert event.phase is FailurePhase.PREFLIGHT
    assert event.evidence is not None and event.evidence.family == "TARGET"


def test_provider_transport_failure_stays_out_of_gameplay_action_recovery() -> None:
    error = GenericProviderError(
        "MODEL_PROVIDER_TRANSPORT_ERROR",
        "The provider transport failed",
        validation_diagnostics=({"code": "NETWORK_UNAVAILABLE"},),
    )
    event = normalize_legacy_failure(error, provider_retryable=True)
    assert event.domain is FailureDomain.PROVIDER
    assert event.kind is FailureKind.PROVIDER_ERROR
    assert event.phase is FailurePhase.PROVIDER_CALL
    assert event.gameplay_recovery_eligible is False
    assert event.provider_retryable is True
    assert event.legacy_retryable is None
    details = event.metadata["legacy_details"]
    assert isinstance(details, dict)
    assert details["validation_diagnostics"] == [{"code": "NETWORK_UNAVAILABLE"}]


class _RecordingProvider:
    model_name = "phase-a-parity-provider"

    def __init__(self, proposal: PlanProposal) -> None:
        self.proposal = proposal
        self.plan_requests: list[PlanRequest] = []

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.plan_requests.append(request)
        return self.proposal
