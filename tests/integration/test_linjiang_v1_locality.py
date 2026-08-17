from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import (
    PLAN_INVALIDATED_BY_NEW_KNOWLEDGE,
    GenericAgentError,
    GenericAgentService,
)
from app.agent.planning_context import legal_candidate_id
from app.agent.provider import PlanProposal, PlanRequest, PlanStepProposal
from app.domain.enums import AgentPlanStatus, AgentStepStatus, AgentTaskStatus
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
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V1, require_builtin_v2_version
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionError, GenericActionService
from app.services.runtime_initialization import InitializedRuntime, RuntimeInitializationService


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
                        "inspect", "logistics_team_alpha", "west_freight_corridor"
                    ),
                    purpose="Inspect the West freight corridor before committing the route.",
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
            if self.suffix_stays_legal:
                steps = (
                    steps[0],
                    PlanStepProposal(
                        candidate_id=legal_candidate_id(
                            "clear_transport",
                            "municipal_repair_team_alpha",
                            "west_freight_corridor",
                        ),
                        purpose="Clear the known West corridor blockage.",
                    ),
                    PlanStepProposal(
                        candidate_id=legal_candidate_id(
                            "travel", "logistics_team_alpha", "west_logistics_district"
                        ),
                        purpose="Travel to the West logistics district after clearing it.",
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
                    steps[-1],
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
    version = require_builtin_v2_version(session, LINJIANG_INFRASTRUCTURE_RECOVERY_V1)
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
        diagnostic["code"] == "LOCALITY_INVALID"
        for diagnostic in provider.requests[1].repair_diagnostics
    )


def test_sequential_plan_accepts_travel_then_transport_to_central(session: Session) -> None:
    provider, _runtime_value, task = _create_with_fixed_plan(
        session,
        "linjiang-sequential-locality-valid",
        (_travel_to_west_step(), _transport_step("central_district"), _repair_step()),
    )
    assert task.current_plan_version == 1
    assert len(provider.requests) == 1


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
    assert any(
        diagnostic["code"] == "KNOWN_TRANSPORT_BLOCKED"
        for diagnostic in provider.requests[1].repair_diagnostics
    )


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
    assert actors["logistics_team_alpha"].current_node_key == "central_district"
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
    provider = KnowledgeRevalidationProvider(suffix_stays_legal=True)
    agent = GenericAgentService(session, scope, provider=provider)
    task = agent.create_task(
        runtime.session,
        "恢复中央医院应急供电",
    )

    first_step = agent.execute_next(task, replan_on_failure=False)
    assert first_step is not None
    assert first_step.action_intent == "inspect"
    assert first_step.status == AgentStepStatus.SUCCEEDED
    assert task.last_error_code is None
    assert not agent.has_pending_plan_invalidation(task)
    assert len(provider.requests) == 1
    plan = session.get(AgentPlan, first_step.plan_id)
    assert plan is not None and plan.status == AgentPlanStatus.ACTIVE
    next_step = session.scalar(
        select(AgentStep).where(
            AgentStep.plan_id == first_step.plan_id,
            AgentStep.status == AgentStepStatus.PENDING,
        ).order_by(AgentStep.sequence)
    )
    assert next_step is not None and next_step.action_intent == "clear_transport"


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

    inspect_step = agent.execute_next(task, replan_on_failure=False)
    assert inspect_step is not None
    assert inspect_step.action_intent == "inspect"
    assert inspect_step.status == AgentStepStatus.SUCCEEDED
    assert agent.has_pending_plan_invalidation(task)
    assert task.last_error_code == PLAN_INVALIDATED_BY_NEW_KNOWLEDGE
    assert len(provider.requests) == 1

    remaining = tuple(
        session.scalars(
            select(AgentStep)
            .where(AgentStep.plan_id == initial_plan.id)
            .order_by(AgentStep.sequence)
        )
    )
    assert [step.status for step in remaining] == [
        AgentStepStatus.SUCCEEDED,
        AgentStepStatus.SKIPPED,
        AgentStepStatus.SKIPPED,
        AgentStepStatus.SKIPPED,
    ]
    assert initial_plan.status == AgentPlanStatus.SUPERSEDED
    operations = tuple(
        session.scalars(
            select(WorldOperation)
            .where(WorldOperation.task_id == task.id)
            .order_by(WorldOperation.created_at)
        )
    )
    assert [operation.action_key for operation in operations] == ["inspect"]
    marker = task.objective_resolution_metadata["plan_invalidation"]
    assert marker["reason"] == PLAN_INVALIDATED_BY_NEW_KNOWLEDGE
    assert marker["diagnostics"][0]["code"] == "KNOWN_TRANSPORT_BLOCKED"
    assert marker["diagnostics"][0]["sequence"] == 2
    assert marker["diagnostics"][0]["action_key"] == "travel"
    assert marker["diagnostics"][0]["known_value"] is False

    replanned = agent.plan(task, reason=PLAN_INVALIDATED_BY_NEW_KNOWLEDGE)
    assert replanned.replan_reason == PLAN_INVALIDATED_BY_NEW_KNOWLEDGE
    assert [item.call_type for item in provider.requests] == ["INITIAL_PLAN", "REPLAN"]
    assert task.last_error_code is None
