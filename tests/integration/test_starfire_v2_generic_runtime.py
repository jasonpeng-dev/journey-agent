from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.generic import GenericAgentService
from app.domain.enums import AgentTaskStatus, NodeStatus, WorldOperationStatus
from app.domain.runtime_scope import GameInstanceId
from app.domain.world import Visibility
from app.infrastructure.db.models import (
    GameInstanceFactState,
    GameInstanceNodeState,
    GameInstanceResourceState,
    Player,
    WorldOperation,
)
from app.scenarios.builtin import STARFIRE_V2, require_builtin_v2_version
from app.services.game_instances import GameInstanceService
from app.services.generic_actions import GenericActionService
from app.services.runtime_initialization import RuntimeInitializationService


def _runtime(session: Session, key: str):  # type: ignore[no-untyped-def]
    version = require_builtin_v2_version(session, STARFIRE_V2)
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


def _run_action(
    session: Session,
    service: GenericActionService,
    *,
    action: str,
    target: str,
    parameters: dict[str, str | int | bool],
    key: str,
):  # type: ignore[no-untyped-def]
    started = service.execute_action(
        actor_key="shen_ce",
        action_key=action,
        target_key=target,
        parameters=parameters,
        idempotency_key=key,
    )
    if started.operation.status == WorldOperationStatus.PENDING:
        return service.resolve_operation(started.operation.id, resolution_key=f"resolve-{key}")
    return started


def _resources(session: Session, instance_id) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {
        row.resource_key: row.value
        for row in session.scalars(
            select(GameInstanceResourceState).where(
                GameInstanceResourceState.game_instance_id == instance_id
            )
        ).all()
    }


def test_starfire_v2_defeat_reveal_disrupt_and_second_clear(session: Session) -> None:
    runtime, scope = _runtime(session, "starfire-v2-defeat")
    actions = GenericActionService(session, scope)

    defeat = _run_action(
        session,
        actions,
        action="clear_valley",
        target="northern_valley",
        parameters={"troop_count": 80, "strategy": "STANDARD"},
        key="clear-first",
    )

    assert defeat.applied is not None and defeat.applied.outcome.failure is not None
    assert defeat.applied.outcome.failure.code == "ENCOUNTER_DEFEAT"
    supply_node = session.get(
        GameInstanceNodeState,
        (runtime.instance.id, "enemy_north_supply_route"),
    )
    supply_fact = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "enemy_north_supply_route", "supply_status"),
    )
    assert supply_node is not None and supply_node.visibility.value == "KNOWN"
    assert supply_fact is not None and supply_fact.visibility.value == "KNOWN"
    assert _resources(session, runtime.instance.id)["soldiers"] == 282

    _run_action(
        session,
        actions,
        action="disrupt_supply",
        target="enemy_north_supply_route",
        parameters={"troop_count": 30, "strategy": "CAUTIOUS"},
        key="disrupt-no-guide",
    )
    victory = _run_action(
        session,
        actions,
        action="clear_valley",
        target="northern_valley",
        parameters={"troop_count": 80, "strategy": "STANDARD"},
        key="clear-second",
    )

    assert victory.applied is not None and victory.applied.outcome.outcome_code == "VICTORY"
    assert _resources(session, runtime.instance.id)["soldiers"] == 272
    valley = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "northern_valley", "valley_security"),
    )
    assert valley is not None and valley.truth_value == "SAFE"


def test_starfire_v2_guide_reduces_both_military_losses(session: Session) -> None:
    runtime, scope = _runtime(session, "starfire-v2-guide")
    actions = GenericActionService(session, scope)
    _run_action(
        session,
        actions,
        action="negotiate_support",
        target="north_village",
        parameters={"food_offer": 20, "requested_support": "GUIDE"},
        key="gain-guide",
    )
    supply_node = session.get(
        GameInstanceNodeState,
        (runtime.instance.id, "enemy_north_supply_route"),
    )
    assert supply_node is not None
    supply_node.status = NodeStatus.AVAILABLE
    supply_node.visibility = Visibility.KNOWN
    _run_action(
        session,
        actions,
        action="disrupt_supply",
        target="enemy_north_supply_route",
        parameters={"troop_count": 30, "strategy": "CAUTIOUS"},
        key="guided-disrupt",
    )
    _run_action(
        session,
        actions,
        action="clear_valley",
        target="northern_valley",
        parameters={"troop_count": 80, "strategy": "STANDARD"},
        key="guided-clear",
    )

    assert _resources(session, runtime.instance.id) == {
        "soldiers": 295,
        "food": 80,
        "gold": 80,
        "morale": 68,
    }


def test_starfire_v2_generic_agent_failure_knowledge_replan_completion(
    session: Session,
) -> None:
    runtime, scope = _runtime(session, "starfire-v2-agent")
    agent = GenericAgentService(session, scope)
    task = agent.create_task(runtime.session, "secure the northern valley")
    actions = GenericActionService(session, scope)

    for index in range(20):
        agent.execute_next(task)
        pending = session.scalar(
            select(WorldOperation)
            .where(
                WorldOperation.game_instance_id == runtime.instance.id,
                WorldOperation.status == WorldOperationStatus.PENDING,
            )
            .order_by(WorldOperation.created_at.desc())
        )
        if pending is not None:
            actions.resolve_operation(pending.id, resolution_key=f"event-{index}")
        if task.status == AgentTaskStatus.SUCCEEDED:
            break

    assert task.status == AgentTaskStatus.SUCCEEDED
    assert task.replan_count >= 1
    assert agent.evaluate(task).completed
    assert any(
        operation.action_key == "disrupt_supply"
        for operation in session.scalars(
            select(WorldOperation).where(WorldOperation.game_instance_id == runtime.instance.id)
        ).all()
    )


def test_starfire_v2_repair_and_trade_are_versioned_rules(session: Session) -> None:
    runtime, scope = _runtime(session, "starfire-v2-repair-trade")
    actions = GenericActionService(session, scope)
    _run_action(
        session,
        actions,
        action="negotiate_support",
        target="north_village",
        parameters={"food_offer": 20, "requested_support": "GUIDE"},
        key="trade-guide",
    )
    valley = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "northern_valley", "valley_security"),
    )
    outpost_node = session.get(
        GameInstanceNodeState,
        (runtime.instance.id, "starfire_outpost"),
    )
    assert valley is not None and outpost_node is not None
    valley.truth_value = "SAFE"
    outpost_node.status = NodeStatus.AVAILABLE
    session.flush()

    _run_action(
        session,
        actions,
        action="repair_outpost",
        target="starfire_outpost",
        parameters={
            "repair_level": "FULL",
            "food_commitment": 30,
            "gold_commitment": 40,
        },
        key="repair-full",
    )
    _run_action(
        session,
        actions,
        action="test_trade_route",
        target="northern_trade_route",
        parameters={},
        key="open-trade",
    )

    outpost = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "starfire_outpost", "outpost_status"),
    )
    trade = session.get(
        GameInstanceFactState,
        (runtime.instance.id, "northern_trade_route", "trade_route_status"),
    )
    assert outpost is not None and outpost.truth_value == "RESTORED"
    assert trade is not None and trade.truth_value == "OPEN"
    assert _resources(session, runtime.instance.id) == {
        "soldiers": 300,
        "food": 50,
        "gold": 40,
        "morale": 60,
    }
