from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.agent.provider import PlanProposal, PlanRequest, PlanStepProposal
from app.domain.enums import AgentTaskStatus
from app.domain.runtime_scope import GameInstanceId
from app.infrastructure.db.models import (
    GameInstanceActor,
    GameInstanceFactState,
    GameInstanceResourceState,
    Player,
    WorldOperation,
)
from app.scenarios.builtin import LINJIANG_INFRASTRUCTURE_RECOVERY_V1, require_builtin_v2_version
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionError, GenericActionService
from app.services.runtime_initialization import RuntimeInitializationService


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
                        "destination_region_key": "central_district",
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
                    purpose="改走北部联络通道",
                    action_key="travel",
                    actor_key="logistics_team_alpha",
                    target_key="north_industrial_district",
                ),
                PlanStepProposal(
                    purpose="将北部维修部件运输回中央城区",
                    action_key="transport_resource",
                    actor_key="logistics_team_alpha",
                    target_key="central_district",
                    parameters={
                        "resource_key": "electrical_repair_parts",
                        "amount": 10,
                        "destination_region_key": "central_district",
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
    assert any(
        item["current_known_state"]["current_region"] == "central_district"
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
    assert resources[("electrical_repair_parts", "west_logistics_district")] == 10
    assert resources[("electrical_repair_parts", "north_industrial_district")] == 0
    assert resources[("electrical_repair_parts", "central_district")] == 0

    passability = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "west_freight_corridor", "passable"),
    )
    hospital_power = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "central_hospital", "emergency_power_operational"),
    )
    assert passability is not None and passability.visibility.value == "KNOWN"
    assert passability.truth_value is False
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
            "destination_region_key": "west_logistics_district",
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
        "north_industrial_district": 10,
        "central_district": 0,
    }
