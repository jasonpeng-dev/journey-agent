from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.api.schemas.phase_d import PublicTaskStatus
from app.domain.enums import AgentPlanStatus, AgentTaskStatus, StepExecutionType
from app.domain.runtime_scope import GameInstanceId
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    AgentPlan,
    AgentStep,
    GameInstanceActor,
    GameInstanceFactState,
    PlanningAttempt,
    PlanningCycle,
    Player,
)
from app.scenarios.builtin import (
    LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    load_builtin_scenario,
    require_builtin_v2_version,
)
from app.services.game_instances import GameInstanceService
from app.services.game_lifecycle import GameLifecycleService
from app.services.player_projection import PlayerProjectionService, _task_explanation, _task_status
from app.services.runtime_initialization import RuntimeInitializationService
from app.services.spatial_projection import SpatialDisplayProjector


def _runtime_task(
    session: Session,
    key: str,
    definition=LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
    *,
    goal: str = "restore central communications",
):  # type: ignore[no-untyped-def]
    version = require_builtin_v2_version(session, definition)
    player = Player(name=key)
    session.add(player)
    session.flush()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key=key,
    )
    scope = GameInstanceService(session).load(GameInstanceId(runtime.instance.id))
    task = GenericAgentService(session, scope).create_task(
        runtime.session,
        goal,
        initialize_plan=False,
    )
    return runtime, task


def _cycle(
    task_id,
    game_instance_id,
    region: str,
    created_at: datetime,
    *,
    actor_positions: dict[str, str] | None = None,
) -> PlanningCycle:  # type: ignore[no-untyped-def]
    positions = actor_positions or {"logistics_team_alpha": region}
    planner_input = {
        "actors": [
            {"actor_key": actor_key, "current_region": current_region}
            for actor_key, current_region in positions.items()
        ]
    }
    canonical = json.dumps(planner_input, sort_keys=True, separators=(",", ":"))
    return PlanningCycle(
        task_id=task_id,
        game_instance_id=game_instance_id,
        base_call_type="INITIAL_PLAN",
        frozen_objective_scope=[],
        planner_input=planner_input,
        planner_input_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        status="ACCEPTED",
        current_attempt=1,
        created_at=created_at,
    )


def _plan(task_id, version: int, created_at: datetime, *, supersedes_plan_id=None) -> AgentPlan:  # type: ignore[no-untyped-def]
    return AgentPlan(
        task_id=task_id,
        version=version,
        status=AgentPlanStatus.ACTIVE,
        strategy_summary="Projection regression plan",
        supersedes_plan_id=supersedes_plan_id,
        created_by_actor_key="logistics_team_alpha",
        source="PROVIDER",
        validation_status="PASSED",
        validation_errors=[],
        stop_reason="OBJECTIVE_COMPLETION",
        created_at=created_at,
    )


def _step(
    plan_id: UUID,
    sequence: int,
    action: str,
    target: str,
    parameters: dict[str, object],
    *,
    actor_key: str = "logistics_team_alpha",
) -> AgentStep:
    return AgentStep(
        plan_id=plan_id,
        sequence=sequence,
        description=f"{action} to {target}",
        execution_type=StepExecutionType.TOOL,
        assigned_actor_key=actor_key,
        action_intent=action,
        allowed_tool_names=["execute_action"],
        selected_tool_name="execute_action",
        tool_arguments={
            "action_key": action,
            "target_key": target,
            "parameters": parameters,
        },
        expected_outcome={},
    )


def _locations(session: Session, task, plans, steps_by_plan):  # type: ignore[no-untyped-def]
    service = PlayerProjectionService(session)
    spatial = SpatialDisplayProjector(LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0)
    return service._locations_by_step(
        task,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        spatial,
        tuple(plans),
        steps_by_plan,
    ), spatial


def _summary(spatial: SpatialDisplayProjector, source: str, target: str) -> str:
    source_projection = spatial.node(source)
    target_projection = spatial.node(target)
    assert source_projection is not None and target_projection is not None
    return f"{source_projection.name} → {target_projection.name}"


def test_plan_projection_starts_from_frozen_planner_input_position(session: Session) -> None:
    runtime, task = _runtime_task(session, "player-projection-plan-time")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cycle = _cycle(task.id, runtime.instance.id, "east_residential_district", base)
    session.add(cycle)
    session.flush()
    plan = _plan(task.id, 1, base + timedelta(seconds=1))
    session.add(plan)
    session.flush()
    steps = (
        _step(
            plan.id,
            1,
            "transport_resource",
            "central_district",
            {
                "resource_key": "communication_equipment",
                "amount": 10,
            },
        ),
        _step(plan.id, 2, "travel", "central_district", {}),
        _step(plan.id, 3, "travel", "west_logistics_district", {}),
    )
    session.add_all(steps)
    session.flush()

    locations, spatial = _locations(session, task, (plan,), {plan.id: steps})

    assert [locations[step.id].summary for step in steps] == [
        _summary(spatial, "east_residential_district", "central_district"),
        _summary(spatial, "east_residential_district", "central_district"),
        _summary(spatial, "central_district", "west_logistics_district"),
    ]


def test_runtime_action_failure_is_not_projected_as_no_legal_action(session: Session) -> None:
    _runtime, task = _runtime_task(session, "player-projection-action-failure")
    task.status = AgentTaskStatus.BLOCKED
    task.last_error_code = "ACTION_IDEMPOTENCY_CONFLICT"

    assert (
        _task_status(task.status, task.last_error_code) == PublicTaskStatus.ACTION_EXECUTION_FAILED
    )
    assert _task_explanation(task) == "行动执行失败 - 未完成世界状态更新"


def test_unreachable_projection_remains_reserved_for_feasibility_errors(session: Session) -> None:
    _runtime, task = _runtime_task(session, "player-projection-unreachable")
    task.status = AgentTaskStatus.BLOCKED
    task.last_error_code = "UNREACHABLE_IN_CURRENT_STATE"

    assert (
        _task_status(task.status, task.last_error_code)
        == PublicTaskStatus.UNREACHABLE_IN_CURRENT_STATE
    )
    assert _task_explanation(task) == "当前世界状态下没有可继续执行的合法行动"


def test_replan_projection_uses_its_own_frozen_position_not_scenario_initial(
    session: Session,
) -> None:
    runtime, task = _runtime_task(session, "player-projection-replan-time")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    first_cycle = _cycle(task.id, runtime.instance.id, "central_district", base)
    session.add(first_cycle)
    session.flush()
    first_plan = _plan(task.id, 1, base + timedelta(seconds=1))
    session.add(first_plan)
    session.flush()
    first_step = _step(first_plan.id, 1, "travel", "east_residential_district", {})
    session.add(first_step)
    session.flush()

    second_cycle = _cycle(
        task.id,
        runtime.instance.id,
        "west_logistics_district",
        base + timedelta(seconds=2),
    )
    second_cycle.base_call_type = "REPLAN"
    session.add(second_cycle)
    session.flush()
    second_plan = _plan(
        task.id,
        2,
        base + timedelta(seconds=3),
        supersedes_plan_id=first_plan.id,
    )
    session.add(second_plan)
    session.flush()
    second_step = _step(second_plan.id, 1, "travel", "central_district", {})
    session.add(second_step)
    session.flush()

    locations, spatial = _locations(
        session,
        task,
        (first_plan, second_plan),
        {first_plan.id: (first_step,), second_plan.id: (second_step,)},
    )

    assert locations[first_step.id].summary == _summary(
        spatial,
        "central_district",
        "east_residential_district",
    )
    assert locations[second_step.id].summary == _summary(
        spatial,
        "west_logistics_district",
        "central_district",
    )


def test_planning_process_projects_cycle_wall_time_and_safe_attempt_summaries(
    session: Session,
) -> None:
    runtime, task = _runtime_task(session, "player-planning-process")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cycle = _cycle(task.id, runtime.instance.id, "central_district", base)
    cycle.base_call_type = "REPLAN"
    cycle.status = "ERROR"
    cycle.current_attempt = 1
    session.add(cycle)
    session.flush()
    first = PlanningAttempt(
        cycle_id=cycle.id,
        task_id=task.id,
        attempt_index=0,
        call_type="REPLAN",
        status="REJECTED",
        started_at=base,
        finished_at=base + timedelta(seconds=194),
        latency_ms=194000,
        proposal={"steps": [{"action_key": "hidden_from_player"}]},
        validator_violations=[
            {
                "code": "RESOURCE_INVENTORY_UNKNOWN",
                "dimension": "RESOURCE",
                "step_id": "step-1",
                "action_key": "transport_resource",
                "actual": "must not be projected",
            }
        ],
    )
    second = PlanningAttempt(
        cycle_id=cycle.id,
        task_id=task.id,
        attempt_index=1,
        call_type="REPAIR",
        status="ERROR",
        started_at=base + timedelta(seconds=194),
        finished_at=base + timedelta(seconds=435),
        latency_ms=241000,
    )
    session.add_all((first, second))
    task.objective_resolution_metadata = {
        "provider_calls": [
            {
                "call_type": "REPLAN",
                "repair_attempt": 0,
                "outcome": "SUCCESS",
                "wall_clock_latency_ms": 194000,
            },
            {
                "call_type": "REPAIR",
                "repair_attempt": 1,
                "outcome": "ERROR",
                "error_code": "MODEL_PROVIDER_HTTP_ERROR",
                "error_category": "RemoteProtocolError",
                "wall_clock_latency_ms": 241000,
            },
        ]
    }
    session.flush()

    response = PlayerProjectionService(session).task(
        task,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        known_facts={},
    )

    assert response.plan_history == []
    assert len(response.planning_process) == 1
    projected_cycle = response.planning_process[0]
    assert projected_cycle.cycle_type == "REPLAN"
    assert projected_cycle.status == "ERROR"
    assert projected_cycle.wall_clock_duration_ms == 435000
    assert projected_cycle.attempt_count == 2
    assert projected_cycle.final_outcome == "ERROR"
    assert [item.status for item in projected_cycle.attempts] == ["REJECTED", "ERROR"]
    assert [item.duration_ms for item in projected_cycle.attempts] == [194000, 241000]
    assert projected_cycle.attempts[0].provider_outcome == "SUCCESS"
    assert projected_cycle.attempts[0].validator_summary == [
        {
            "code": "RESOURCE_INVENTORY_UNKNOWN",
            "dimension": "RESOURCE",
            "step_id": "step-1",
            "action_key": "transport_resource",
        }
    ]
    assert projected_cycle.attempts[1].provider_error_code == "MODEL_PROVIDER_HTTP_ERROR"
    assert projected_cycle.attempts[1].provider_error_category == "RemoteProtocolError"
    serialized = response.model_dump(mode="json")
    assert "provider_payload" not in json.dumps(serialized)
    assert "hidden_from_player" not in json.dumps(serialized)
    assert "must not be projected" not in json.dumps(serialized)


def test_planning_process_reports_accepted_step_count(session: Session) -> None:
    runtime, task = _runtime_task(session, "player-planning-process-accepted")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cycle = _cycle(task.id, runtime.instance.id, "central_district", base)
    cycle.status = "ACCEPTED"
    cycle.current_attempt = 0
    session.add(cycle)
    session.flush()
    session.add(
        PlanningAttempt(
            cycle_id=cycle.id,
            task_id=task.id,
            attempt_index=0,
            call_type="INITIAL_PLAN",
            status="ACCEPTED",
            started_at=base,
            finished_at=base + timedelta(seconds=5),
            latency_ms=5000,
            proposal={"steps": [{"action_key": "one"}, {"action_key": "two"}]},
        )
    )
    session.flush()

    response = PlayerProjectionService(session).task(
        task,
        LINJIANG_INFRASTRUCTURE_RECOVERY_V2_0,
        known_facts={},
    )

    projected_attempt = response.planning_process[0].attempts[0]
    assert projected_attempt.status == "ACCEPTED"
    assert projected_attempt.accepted_step_count == 2


def test_relay_projection_uses_target_actor_plan_time_region_and_name(
    session: Session,
) -> None:
    definition = load_builtin_scenario("linjiang_infrastructure_recovery_v2_0.yaml")
    runtime, task = _runtime_task(
        session,
        "player-projection-relay-subtitle",
        definition,
        goal=definition.objectives[0].key,
    )
    target_actor = session.get(
        GameInstanceActor,
        (runtime.instance.id, "industrial_repair_team_alpha"),
    )
    assert target_actor is not None
    base = datetime(2026, 1, 1, tzinfo=UTC)
    cycle = _cycle(
        task.id,
        runtime.instance.id,
        "central_district",
        base,
        actor_positions={
            "communications_repair_team_alpha": "central_district",
            "industrial_repair_team_alpha": "central_district",
            "logistics_team_alpha": "central_district",
        },
    )
    session.add(cycle)
    session.flush()
    plan = _plan(task.id, 1, base + timedelta(seconds=1))
    session.add(plan)
    session.flush()
    target_travel, relay = (
        _step(
            plan.id,
            1,
            "travel",
            "east_residential_district",
            {},
            actor_key="industrial_repair_team_alpha",
        ),
        _step(
            plan.id,
            2,
            "relay_message",
            "industrial_repair_team_alpha",
            {},
            actor_key="communications_repair_team_alpha",
        ),
    )
    session.add_all((target_travel, relay))
    session.flush()

    response = PlayerProjectionService(session).task(
        task,
        definition,
        known_facts={},
    )
    relay_step = next(step for step in response.plan_history[0].steps if step.id == relay.id)
    spatial = SpatialDisplayProjector(definition)
    target_region = spatial.node("east_residential_district")
    assert target_region is not None and target_region.region_name is not None
    assert relay_step.subtitle == f"{target_region.region_name} · {target_actor.name}"


def test_player_projection_exposes_known_target_contracts_without_hidden_targets(
    session: Session,
) -> None:
    definition = load_builtin_scenario("linjiang_infrastructure_recovery_v2_0.yaml")
    version = require_builtin_v2_version(session, definition)
    player = GameLifecycleService(session).platform_player()
    runtime = RuntimeInitializationService(session).create(
        player_id=player.id,
        scenario_version_id=version.id,
        creation_key="player-projection-known-target-contracts",
    )
    projection = PlayerProjectionService(session)
    state = projection.game_state(GameInstanceId(runtime.instance.id))
    contracts = {
        (item.target_key, item.action_key): item for item in state.known_target_action_contracts
    }
    assert ("utility_service_depot", "repair_industrial_facility") not in contracts
    repair_profile = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "utility_service_depot", "repair_profile"),
    )
    assert repair_profile is not None
    assert repair_profile.visibility == Visibility.HIDDEN
    utility_node = definition.world.node("utility_service_depot")
    assert utility_node is not None
    for fact in utility_node.facts:
        state_fact = session.get(
            GameInstanceFactState,
            (runtime.instance.id, "utility_service_depot", fact.key),
        )
        assert state_fact is not None
        state_fact.visibility = Visibility.KNOWN
    session.flush()
    known_state = projection.game_state(GameInstanceId(runtime.instance.id))
    known_contracts = {
        (item.target_key, item.action_key): item
        for item in known_state.known_target_action_contracts
    }
    utility = known_contracts[("utility_service_depot", "repair_industrial_facility")]
    assert utility.cost == {
        "general_engineering_parts": 5,
        "municipal_repair_materials": 20,
    }
    assert any(
        effect.get("fact_key") == "operational" and effect.get("value") is True
        for effect in utility.effects
    )
    repair_profile.visibility = Visibility.HIDDEN
    session.flush()
    hidden_state = projection.game_state(GameInstanceId(runtime.instance.id))
    assert all(
        item.target_key != "utility_service_depot"
        for item in hidden_state.known_target_action_contracts
    )
