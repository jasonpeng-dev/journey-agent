from __future__ import annotations

import json
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import (
    GenericAgentError,
    GenericAgentService,
)
from app.agent.planning_context import PlanningContextBuilder, legal_candidate_id
from app.agent.provider import PlanProposal, PlanRequest, PlanStepProposal
from app.domain.enums import AgentPlanStatus, AgentStepStatus, AgentTaskStatus, WorldOperationStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    AgentPlan,
    AgentStep,
    AgentTask,
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceResourceState,
    Player,
    WorldOperation,
)
from app.scenarios.builtin import require_builtin_v2_version
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionError, GenericActionService
from app.services.runtime_initialization import InitializedRuntime, RuntimeInitializationService
from tests.scenario_fixtures import LINJIANG_V1_TEST


class LinjiangProvider:
    model_name = "linjiang-test-provider"

    def __init__(self) -> None:
        self.requests: list[PlanRequest] = []

    def select_objectives(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("The exact Linjiang goal alias should resolve deterministically")

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.requests.append(request)
        if request.call_type == "INITIAL_PLAN":
            steps = (
                PlanStepProposal(
                    purpose="先尝试前往西部物流区",
                    action_key="travel",
                    actor_key="logistics_team_alpha",
                    target_key="west_logistics_district",
                ),
                PlanStepProposal(
                    purpose="将西部维修部件运输回中央城区",
                    action_key="transport_resource",
                    actor_key="logistics_team_alpha",
                    target_key="central_district",
                    parameters={
                        "resource_key": "electrical_repair_parts",
                        "amount": 10,
                    },
                ),
                PlanStepProposal(
                    purpose="恢复中央医院应急供电",
                    action_key="repair_electrical",
                    actor_key="electrical_team_beta",
                    target_key="central_hospital",
                ),
            )
        else:
            steps = (
                PlanStepProposal(
                    purpose="清理已知的西部货运走廊阻塞",
                    action_key="clear_transport",
                    actor_key="municipal_repair_team_alpha",
                    target_key="west_freight_corridor",
                ),
                PlanStepProposal(
                    purpose="前往西部物流区",
                    action_key="travel",
                    actor_key="logistics_team_alpha",
                    target_key="west_logistics_district",
                ),
                PlanStepProposal(
                    purpose="将西部维修部件运输回中央城区",
                    action_key="transport_resource",
                    actor_key="logistics_team_alpha",
                    target_key="central_district",
                    parameters={
                        "resource_key": "electrical_repair_parts",
                        "amount": 10,
                    },
                ),
                PlanStepProposal(
                    purpose="恢复中央医院应急供电",
                    action_key="repair_electrical",
                    actor_key="electrical_team_beta",
                    target_key="central_hospital",
                ),
            )
        return PlanProposal(plan_summary=request.call_type, steps=steps)


class KnowledgeRevalidationProvider:
    model_name = "linjiang-knowledge-revalidation-provider"

    def __init__(self, *, suffix_stays_legal: bool = False) -> None:
        self.requests: list[PlanRequest] = []
        self.suffix_stays_legal = suffix_stays_legal

    def select_objectives(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("The exact Linjiang goal alias should resolve deterministically")

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.requests.append(request)
        if request.call_type == "INITIAL_PLAN":
            steps = (
                PlanStepProposal(
                    candidate_id=legal_candidate_id(
                        "travel", "logistics_team_alpha", "west_logistics_district"
                    ),
                    purpose="Travel to the West logistics district.",
                ),
                PlanStepProposal(
                    candidate_id=legal_candidate_id(
                        "transport_resource", "logistics_team_alpha", "central_district"
                    ),
                    purpose="Transport ten electrical repair parts to Central.",
                    parameters={
                        "resource_key": "electrical_repair_parts",
                        "amount": 10,
                    },
                ),
                PlanStepProposal(
                    candidate_id=legal_candidate_id(
                        "repair_electrical", "electrical_team_beta", "central_hospital"
                    ),
                    purpose="Restore Central Hospital emergency power.",
                ),
            )
        else:
            steps = (
                PlanStepProposal(
                    candidate_id=legal_candidate_id(
                        "clear_transport", "municipal_repair_team_alpha", "west_freight_corridor"
                    ),
                    purpose="Clear the known West corridor blockage.",
                ),
                PlanStepProposal(
                    candidate_id=legal_candidate_id(
                        "travel", "logistics_team_alpha", "west_logistics_district"
                    ),
                    purpose="Travel to the West logistics district.",
                ),
                PlanStepProposal(
                    candidate_id=legal_candidate_id(
                        "transport_resource", "logistics_team_alpha", "central_district"
                    ),
                    purpose="Transport ten electrical repair parts to Central.",
                    parameters={
                        "resource_key": "electrical_repair_parts",
                        "amount": 10,
                    },
                ),
                PlanStepProposal(
                    candidate_id=legal_candidate_id(
                        "repair_electrical", "electrical_team_beta", "central_hospital"
                    ),
                    purpose="Restore Central Hospital emergency power.",
                ),
            )
        return PlanProposal(plan_summary=request.call_type, steps=steps)


class FixedPlanProvider:
    model_name = "linjiang-fixed-plan-provider"

    def __init__(self, steps: tuple[PlanStepProposal, ...]) -> None:
        self.steps = steps
        self.requests: list[PlanRequest] = []

    def select_objectives(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("The exact Linjiang goal alias should resolve deterministically")

    def propose_plan(self, request: PlanRequest) -> PlanProposal:
        self.requests.append(request)
        return PlanProposal(plan_summary=request.call_type, steps=self.steps)


def _travel_to_west_step() -> PlanStepProposal:
    return PlanStepProposal(
        purpose="Travel to the West logistics district.",
        action_key="travel",
        actor_key="logistics_team_alpha",
        target_key="west_logistics_district",
    )


def _travel_to_central_step() -> PlanStepProposal:
    return PlanStepProposal(
        purpose="Travel back to the Central district.",
        action_key="travel",
        actor_key="logistics_team_alpha",
        target_key="central_district",
    )


def _transport_step(target_key: str) -> PlanStepProposal:
    return PlanStepProposal(
        purpose="Transport ten electrical repair parts.",
        action_key="transport_resource",
        actor_key="logistics_team_alpha",
        target_key=target_key,
        parameters={
            "resource_key": "electrical_repair_parts",
            "amount": 10,
        },
    )


def _repair_step() -> PlanStepProposal:
    return PlanStepProposal(
        purpose="Restore Central Hospital emergency power.",
        action_key="repair_electrical",
        actor_key="electrical_team_beta",
        target_key="central_hospital",
    )


def _runtime(session: Session, key: str):  # type: ignore[no-untyped-def]
    version = require_builtin_v2_version(session, LINJIANG_V1_TEST)
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


def _set_actor_location(
    session: Session, game_instance_id: UUID, actor_key: str, node_key: str
) -> None:
    actor = session.get(GameInstanceActor, (game_instance_id, actor_key))
    assert actor is not None
    actor.current_node_key = node_key
    session.flush()


def _create_with_fixed_plan(
    session: Session,
    key: str,
    steps: tuple[PlanStepProposal, ...],
    *,
    logistics_location: str | None = None,
) -> tuple[FixedPlanProvider, InitializedRuntime, AgentTask]:
    runtime, scope = _runtime(session, key)
    if logistics_location is not None:
        _set_actor_location(
            session,
            runtime.instance.id,
            "logistics_team_alpha",
            logistics_location,
        )
    provider = FixedPlanProvider(steps)
    task = GenericAgentService(session, scope, provider=provider).create_task(
        runtime.session,
        "restore central hospital emergency power",
    )
    return provider, runtime, task


def test_transport_target_is_the_only_destination_and_accepts_projected_west_source(
    session: Session,
) -> None:
    provider, _runtime_value, task = _create_with_fixed_plan(
        session,
        "linjiang-transport-contract-accept",
        (_transport_step("central_district"), _repair_step()),
        logistics_location="west_logistics_district",
    )
    assert task.current_plan_version == 1
    assert len(provider.requests) == 1
    plan = session.scalar(select(AgentPlan).where(AgentPlan.task_id == task.id))
    assert plan is not None
    transport = session.scalar(
        select(AgentStep).where(
            AgentStep.plan_id == plan.id,
            AgentStep.action_intent == "transport_resource",
        )
    )
    assert transport is not None
    assert transport.tool_arguments["target_key"] == "central_district"
    assert transport.tool_arguments["parameters"] == {
        "resource_key": "electrical_repair_parts",
        "amount": 10,
    }


def test_sequential_plan_locality_uses_projected_location_and_rejects_same_region(
    session: Session,
) -> None:
    runtime, scope = _runtime(session, "linjiang-sequential-locality")
    provider = FixedPlanProvider(
        (_travel_to_west_step(), _transport_step("west_logistics_district"), _repair_step())
    )
    with pytest.raises(GenericAgentError, match="backend-valid current Plan"):
        GenericAgentService(session, scope, provider=provider).create_task(
            runtime.session,
            "restore central hospital emergency power",
        )
    assert len(provider.requests) == 3
    assert provider.requests[1].call_type == "REPAIR"
    assert any(
        diagnostic.code == "LOCALITY_INVALID"
        for diagnostic in provider.requests[1].repair_diagnostics
    )
    repair_request = provider.requests[1]
    assert repair_request.rejected_segment is not None
    rejected_steps = repair_request.rejected_segment["steps"]
    assert all(step["step_id"] for step in rejected_steps)
    rejected_ids = {step["step_id"] for step in rejected_steps}
    assert all(
        violation.step_id in rejected_ids
        for violation in repair_request.repair_diagnostics
        if violation.step_id is not None
    )
    locality = next(
        item for item in repair_request.repair_diagnostics if item.code == "LOCALITY_INVALID"
    )
    assert locality.failure_code == "LOCALITY_TRAVEL_SAME_REGION"
    assert locality.dimension == "LOCALITY"
    assert locality.required == "DIFFERENT_REGION"
    assert isinstance(locality.actual, dict)
    assert locality.actual["actor_region"] == locality.actual["target_region"]
    assert "known_recovery_effects" not in json.dumps(
        repair_request.provider_payload(), ensure_ascii=False
    )


def test_sequential_plan_accepts_travel_then_transport_to_central(session: Session) -> None:
    provider, _runtime_value, task = _create_with_fixed_plan(
        session,
        "linjiang-sequential-locality-valid",
        (_travel_to_west_step(), _transport_step("central_district"), _repair_step()),
    )
    assert task.current_plan_version == 1
    assert len(provider.requests) == 1


def test_historical_travel_signature_uses_sequential_projected_location(
    session: Session,
) -> None:
    runtime, scope = _runtime(session, "linjiang-historical-travel-projected-location")
    _set_actor_location(
        session,
        runtime.instance.id,
        "logistics_team_alpha",
        "west_logistics_district",
    )
    provider = FixedPlanProvider(
        (
            _travel_to_central_step(),
            _travel_to_west_step(),
            _transport_step("central_district"),
            _repair_step(),
        )
    )
    agent = GenericAgentService(session, scope, provider=provider)
    task = agent.create_task(
        runtime.session,
        "restore central hospital emergency power",
        initialize_plan=False,
    )
    session.add(
        WorldOperation(
            player_id=scope.player_id,
            game_instance_id=scope.game_instance_id,
            task_id=task.id,
            source_step_id=None,
            actor_key="logistics_team_alpha",
            action_key="travel",
            execution_mode="SYNC",
            target_key="west_logistics_district",
            parameters={},
            status=WorldOperationStatus.RESOLVED,
            outcome={"success": True},
            idempotency_key="historical-travel-west-success",
        )
    )
    session.flush()

    plan = agent.plan(task)

    assert plan.version == 1
    assert len(provider.requests) == 1
    steps = tuple(
        session.scalars(
            select(AgentStep).where(AgentStep.plan_id == plan.id).order_by(AgentStep.sequence)
        )
    )
    assert [(step.action_intent, step.tool_arguments["target_key"]) for step in steps[:2]] == [
        ("travel", "central_district"),
        ("travel", "west_logistics_district"),
    ]


def test_known_blocked_connector_is_rejected_without_reading_hidden_truth(session: Session) -> None:
    runtime, scope = _runtime(session, "linjiang-known-blocked-plan")
    fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "west_freight_corridor", "passable"),
    )
    assert fact is not None
    fact.visibility = Visibility.KNOWN
    fact.truth_value = False
    session.flush()

    provider = FixedPlanProvider((_transport_step("west_logistics_district"), _repair_step()))
    with pytest.raises(GenericAgentError, match="backend-valid current Plan"):
        GenericAgentService(session, scope, provider=provider).create_task(
            runtime.session,
            "restore central hospital emergency power",
        )
    diagnostic = next(
        item
        for item in provider.requests[1].repair_diagnostics
        if item.code == "KNOWN_TRANSPORT_BLOCKED"
    )
    assert diagnostic.failure_code == "KNOWN_TRANSPORT_BLOCKED"
    assert diagnostic.dimension == "TRANSPORT_PASSABILITY"
    assert diagnostic.step_id
    assert diagnostic.action_key == "transport_resource"
    assert diagnostic.actor_key == "logistics_team_alpha"
    assert diagnostic.target_key == "west_logistics_district"
    assert diagnostic.transport_key == "west_freight_corridor"
    assert diagnostic.source_region == "central_district"
    assert diagnostic.target_region == "west_logistics_district"
    assert diagnostic.required == "PASSABLE"
    assert diagnostic.actual == "BLOCKED"


def test_unknown_transport_resource_source_diagnostic_is_actionable(session: Session) -> None:
    runtime, scope = _runtime(session, "linjiang-unknown-resource-plan")
    provider = FixedPlanProvider((_transport_step("west_logistics_district"), _repair_step()))
    with pytest.raises(GenericAgentError, match="backend-valid current Plan"):
        GenericAgentService(session, scope, provider=provider).create_task(
            runtime.session,
            "restore central hospital emergency power",
        )

    diagnostic = provider.requests[1].repair_diagnostics[0]
    assert diagnostic.code == "KNOWN_RESOURCE_INSUFFICIENT"
    assert diagnostic.failure_code == "KNOWN_RESOURCE_INSUFFICIENT"
    assert diagnostic.dimension == "RESOURCE_QUANTITY"
    assert diagnostic.step_id
    assert diagnostic.action_key == "transport_resource"
    assert diagnostic.actor_key == "logistics_team_alpha"
    assert diagnostic.target_key == "west_logistics_district"
    assert diagnostic.resource_key == "electrical_repair_parts"
    assert diagnostic.scope_region == "central_district"
    assert diagnostic.required_amount == 10
    assert diagnostic.projected_known_available_amount == 0
    assert diagnostic.deficit == 10


def test_unknown_route_is_may_attempt_without_inspect_or_clear_closure_actions(
    session: Session,
) -> None:
    runtime, scope = _runtime(session, "linjiang-unknown-route-may-attempt")
    agent = GenericAgentService(session, scope)
    task = agent.create_task(
        runtime.session,
        "restore central hospital emergency power",
        initialize_plan=False,
    )
    definition = agent._definition()
    closure = PlanningContextBuilder(session, scope).build_v2_closure(
        definition,
        agent._objectives(task, definition),
        task=task,
        replan_reason=None,
    )
    action_keys = {item.action_key for item in closure.planner_input.action_contracts}
    assert "travel" in action_keys
    assert "inspect" not in action_keys
    assert "clear_transport" not in action_keys
    route_dependencies = [
        item
        for item in closure.planner_input.known_world.unknown_dependencies
        if item.get("dimension") == "TRANSPORT_PASSABILITY"
    ]
    assert route_dependencies
    assert all(item.get("attempt_policy") == "MAY_ATTEMPT" for item in route_dependencies)
    assert all(item.get("status") == "UNKNOWN" for item in route_dependencies)


def test_linjiang_vertical_slice_hidden_block_reveals_and_replans(session: Session) -> None:
    runtime, scope = _runtime(session, "linjiang-v1-e2e")
    provider = LinjiangProvider()
    agent = GenericAgentService(session, scope, provider=provider)
    task = agent.create_task(
        runtime.session,
        "恢复中央医院应急供电",
    )

    initial_context = provider.requests[0].planning_context
    assert initial_context is not None
    assert "west_freight_corridor.passable" not in initial_context.current_knowledge["facts"]
    assert (
        initial_context.current_knowledge["resources"]["electrical_repair_parts"]["scopes"][
            "west_logistics_district"
        ]["value"]
        == 10
    )
    assert {
        (item.resource_key, item.scope_node_key)
        for item in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == runtime.instance.id
            )
        )
    } == {("electrical_repair_parts", "west_logistics_district")}
    assert any(
        item["current_known_state"]["current_node_key"] == "central_district"
        for item in initial_context.relevant_actors
    )
    assert any(item["action_key"] == "travel" for item in initial_context.relevant_actions)

    for _ in range(12):
        if task.status in {
            AgentTaskStatus.SUCCEEDED,
            AgentTaskStatus.BLOCKED,
            AgentTaskStatus.ABORTED,
        }:
            break
        agent.execute_next(task)
    assert task.status == AgentTaskStatus.SUCCEEDED
    assert task.replan_count == 1
    assert len(provider.requests) == 2

    actors = {
        item.actor_key: item
        for item in session.scalars(
            select(GameInstanceActor).where(
                GameInstanceActor.game_instance_id == runtime.instance.id
            )
        )
    }
    # Transport transfers resources but does not move the logistics Actor;
    # only the preceding Travel changes its location.
    assert actors["logistics_team_alpha"].current_node_key == "west_logistics_district"
    assert actors["electrical_team_beta"].current_node_key == "central_district"

    resources = {
        (item.resource_key, item.scope_node_key): item.value
        for item in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == runtime.instance.id
            )
        )
    }
    assert resources[("electrical_repair_parts", "west_logistics_district")] == 0
    assert resources[("electrical_repair_parts", "central_district")] == 0
    assert ("electrical_repair_parts", "north_industrial_district") not in resources

    replayed = RuntimeInitializationService(session).create(
        player_id=runtime.instance.player_id,
        scenario_version_id=runtime.instance.scenario_version_id,
        creation_key="linjiang-v1-e2e",
    )
    assert replayed.created is False

    passability = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "west_freight_corridor", "passable"),
    )
    hospital_power = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "central_hospital", "emergency_power_operational"),
    )
    assert passability is not None and passability.visibility.value == "KNOWN"
    assert passability.truth_value is True
    assert hospital_power is not None and hospital_power.truth_value is True

    operations = session.scalars(
        select(WorldOperation)
        .where(WorldOperation.game_instance_id == runtime.instance.id)
        .order_by(WorldOperation.created_at)
    ).all()
    failed_travel = next(item for item in operations if item.action_key == "travel")
    assert failed_travel.outcome["failure"]["code"] == "TRAVEL_BLOCKED"
    assert any(
        item["key"] == "west_freight_corridor.passable"
        for item in failed_travel.outcome["knowledge_changes"]
    )


def test_linjiang_locality_and_atomic_transport_guards(session: Session) -> None:
    runtime, scope = _runtime(session, "linjiang-v1-guards")
    actions = GenericActionService(session, scope)

    electrical = session.get(
        GameInstanceActor,
        (runtime.instance.id, "electrical_team_beta"),
    )
    assert electrical is not None
    electrical.current_node_key = "north_industrial_district"
    with pytest.raises(GenericActionError, match="Facility"):
        actions.execute_action(
            actor_key="electrical_team_beta",
            action_key="repair_electrical",
            target_key="central_hospital",
            parameters={},
            idempotency_key="linjiang-out-of-region",
        )
    electrical.current_node_key = "central_district"

    with pytest.raises(GenericActionError):
        actions.execute_action(
            actor_key="electrical_team_beta",
            action_key="travel",
            target_key="east_residential_district",
            parameters={},
            idempotency_key="linjiang-non-adjacent",
        )

    blocked = actions.execute_action(
        actor_key="logistics_team_alpha",
        action_key="transport_resource",
        target_key="west_logistics_district",
        parameters={
            "resource_key": "electrical_repair_parts",
            "amount": 10,
        },
        idempotency_key="linjiang-blocked-transport",
    )
    assert blocked.applied is not None
    assert blocked.applied.outcome.failure is not None
    assert blocked.applied.outcome.failure.code == "TRANSPORT_BLOCKED"
    assert blocked.applied.outcome.failure.retryable
    values = {
        item.scope_node_key: item.value
        for item in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == runtime.instance.id
            )
        )
    }
    assert values == {
        "west_logistics_district": 10,
    }


def test_knowledge_change_does_not_replan_when_remaining_suffix_is_legal(
    session: Session,
) -> None:
    runtime, scope = _runtime(session, "linjiang-v1-knowledge-no-replan")
    passability = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "west_freight_corridor", "passable"),
    )
    assert passability is not None
    passability.truth_value = True
    session.flush()
    provider = KnowledgeRevalidationProvider(suffix_stays_legal=True)
    agent = GenericAgentService(session, scope, provider=provider)
    task = agent.create_task(
        runtime.session,
        "恢复中央医院应急供电",
    )

    first_step = agent.execute_next(task, replan_on_failure=False)
    assert first_step is not None
    assert first_step.action_intent == "travel"
    assert first_step.status == AgentStepStatus.SUCCEEDED
    assert task.last_error_code is None
    assert not agent.has_pending_plan_invalidation(task)
    assert len(provider.requests) == 1
    plan = session.get(AgentPlan, first_step.plan_id)
    assert plan is not None and plan.status == AgentPlanStatus.ACTIVE
    next_step = session.scalar(
        select(AgentStep)
        .where(
            AgentStep.plan_id == first_step.plan_id,
            AgentStep.status == AgentStepStatus.PENDING,
        )
        .order_by(AgentStep.sequence)
    )
    assert next_step is not None and next_step.action_intent == "transport_resource"


def test_known_knowledge_invalidates_only_remaining_plan_and_enters_replan(
    session: Session,
) -> None:
    runtime, scope = _runtime(session, "linjiang-v1-knowledge-replan")
    provider = KnowledgeRevalidationProvider()
    agent = GenericAgentService(session, scope, provider=provider)
    task = agent.create_task(
        runtime.session,
        "恢复中央医院应急供电",
    )
    initial_plan = session.scalar(select(AgentPlan).where(AgentPlan.task_id == task.id))
    assert initial_plan is not None
    assert initial_plan.validation_status == "PASSED"
    initial_context = provider.requests[0].planning_context
    assert initial_context is not None
    assert "west_freight_corridor.passable" not in initial_context.current_knowledge["facts"]

    failed_step = agent.execute_next(task, replan_on_failure=False)
    assert failed_step is not None
    assert failed_step.action_intent == "travel"
    assert failed_step.status == AgentStepStatus.FAILED
    assert task.last_error_code == "TRAVEL_BLOCKED"
    assert len(provider.requests) == 1

    remaining = tuple(
        session.scalars(
            select(AgentStep)
            .where(AgentStep.plan_id == initial_plan.id)
            .order_by(AgentStep.sequence)
        )
    )
    assert [step.status for step in remaining] == [
        AgentStepStatus.FAILED,
        AgentStepStatus.PENDING,
        AgentStepStatus.PENDING,
    ]
    assert initial_plan.status == AgentPlanStatus.ACTIVE
    operations = tuple(
        session.scalars(
            select(WorldOperation)
            .where(WorldOperation.task_id == task.id)
            .order_by(WorldOperation.created_at)
        )
    )
    assert [operation.action_key for operation in operations] == ["travel"]
    outcome = operations[0].outcome
    assert outcome["failure"]["code"] == "TRAVEL_BLOCKED"
    assert outcome["failure"]["retryable"] is True
    assert any(
        item.get("kind") == "FACT_REVEALED"
        and item.get("key") == "west_freight_corridor.passable"
        and item.get("value") is False
        for item in outcome["knowledge_changes"]
    )

    replanned = agent.plan(task, reason="TRAVEL_BLOCKED")
    assert replanned.replan_reason == "TRAVEL_BLOCKED"
    assert [item.call_type for item in provider.requests] == ["INITIAL_PLAN", "REPLAN"]
    assert task.last_error_code == "TRAVEL_BLOCKED"
    first_replanned_step = session.scalar(
        select(AgentStep).where(AgentStep.plan_id == replanned.id).order_by(AgentStep.sequence)
    )
    assert first_replanned_step is not None
    assert first_replanned_step.action_intent == "clear_transport"
