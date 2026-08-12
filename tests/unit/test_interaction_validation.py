from types import MappingProxyType
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.types import ToolCall, ToolContext
from app.core.errors import AppError
from app.domain.enums import WorldOperationStatus
from app.domain.world import (
    AccessState,
    InteractionDefinition,
    NodeDefinition,
    Visibility,
    WorldDefinition,
    WorldNodeType,
)
from app.infrastructure.db.models import (
    AgentRun,
    ConversationSession,
    ToolExecution,
    WorldOperation,
)
from app.scenarios.registry import ScenarioWorldBinding
from app.services.game import GameService, seed_id
from app.services.interaction_targets import (
    InteractionTargetResolver,
    interaction_target_resolver,
)
from app.tools.base import InteractionRequirement
from app.tools.catalog import build_registry
from app.tools.executor import ToolExecutor
from app.tools.interaction_validation import (
    MILITARY_INTERACTION,
    RECON_INTERACTION,
    REPAIR_INTERACTION,
    TRADE_ROUTE_INTERACTION,
    VILLAGE_SUPPORT_INTERACTION,
    resolve_tool_interaction,
)


@pytest.mark.parametrize(
    ("raw_target", "interaction", "canonical_target"),
    [
        ("valley_entrance", "reconnaissance", "northern_valley"),
        ("ambush_valley", "clear_threat", "northern_valley"),
        ("enemy_north_supply_route", "disrupt_supply", "enemy_north_supply_route"),
        ("starfire_outpost", "repair", "starfire_outpost"),
        ("northern_trade_route", "test_trade_route", "northern_trade_route"),
    ],
)
def test_starfire_resolver_accepts_supported_interactions(
    raw_target: str,
    interaction: str,
    canonical_target: str,
) -> None:
    node = interaction_target_resolver.resolve_target(
        "starfire_command",
        raw_target,
        interaction,
    )

    assert node.key == canonical_target


@pytest.mark.parametrize(
    ("raw_target", "interaction", "error_code"),
    [
        ("north_village", "reconnaissance", "INTERACTION_NOT_SUPPORTED"),
        ("northern_valley", "repair", "INTERACTION_NOT_SUPPORTED"),
        ("starfire_outpost", "clear_threat", "INTERACTION_NOT_SUPPORTED"),
        ("unknown_node", "reconnaissance", "INTERACTION_TARGET_NOT_FOUND"),
        ("valley_entrance", "clear_threat", "LEGACY_TARGET_INTERACTION_INVALID"),
        ("ambush_valley", "reconnaissance", "LEGACY_TARGET_INTERACTION_INVALID"),
    ],
)
def test_starfire_resolver_rejects_invalid_targets_fail_closed(
    raw_target: str,
    interaction: str,
    error_code: str,
) -> None:
    with pytest.raises(AppError) as exc_info:
        interaction_target_resolver.resolve_target(
            "starfire_command",
            raw_target,
            interaction,
        )

    assert exc_info.value.code == error_code


def test_resolver_rejects_unknown_scenario_and_interaction() -> None:
    with pytest.raises(AppError) as scenario_error:
        interaction_target_resolver.resolve_target(
            "unknown_scenario",
            "northern_valley",
            "reconnaissance",
        )
    assert scenario_error.value.code == "SCENARIO_NOT_FOUND"

    with pytest.raises(AppError) as interaction_error:
        interaction_target_resolver.resolve_target(
            "starfire_command",
            "northern_valley",
            "unknown_interaction",
        )
    assert interaction_error.value.code == "INTERACTION_NOT_REGISTERED"


def test_unique_target_resolution_requires_exactly_one_candidate() -> None:
    repair = InteractionDefinition(key="repair", name="REPAIR")
    first = _node("first_facility", repair)
    second = _node("second_facility", repair)
    ambiguous_world = WorldDefinition(
        key="ambiguous_world",
        name="Ambiguous World",
        interactions=(repair,),
        nodes=(first, second),
        relations=(),
        resources=(),
    )
    resolver = InteractionTargetResolver(
        MappingProxyType({"ambiguous_world": _binding(ambiguous_world)})
    )

    with pytest.raises(AppError) as exc_info:
        resolver.resolve_unique_target("ambiguous_world", "repair")

    assert exc_info.value.code == "INTERACTION_TARGET_AMBIGUOUS"
    assert exc_info.value.details["candidate_keys"] == ["first_facility", "second_facility"]


def test_unique_target_resolution_rejects_zero_candidates() -> None:
    repair = InteractionDefinition(key="repair", name="REPAIR")
    world = WorldDefinition(
        key="empty_repair_world",
        name="Empty Repair World",
        interactions=(repair,),
        nodes=(),
        relations=(),
        resources=(),
    )
    resolver = InteractionTargetResolver(MappingProxyType({"empty_repair_world": _binding(world)}))

    with pytest.raises(AppError) as exc_info:
        resolver.resolve_unique_target("empty_repair_world", "repair")

    assert exc_info.value.code == "INTERACTION_TARGET_NOT_FOUND"


def test_tool_catalog_declares_interaction_requirements() -> None:
    registry = build_registry()
    expected = {
        "start_recon_operation": RECON_INTERACTION,
        "start_military_operation": MILITARY_INTERACTION,
        "negotiate_village_support": VILLAGE_SUPPORT_INTERACTION,
        "start_outpost_repair": REPAIR_INTERACTION,
        "start_trade_route_test": TRADE_ROUTE_INTERACTION,
        "inspect_command_state": None,
    }

    for tool_name, requirement in expected.items():
        tool = registry.get(tool_name)
        assert tool is not None
        assert tool.interaction_requirement == requirement


def test_military_operation_selects_required_interaction() -> None:
    clear_target = resolve_tool_interaction(
        "starfire_command",
        MILITARY_INTERACTION,
        {"target_key": "ambush_valley", "mission_type": "CLEAR_VALLEY"},
    )
    supply_target = resolve_tool_interaction(
        "starfire_command",
        MILITARY_INTERACTION,
        {
            "target_key": "enemy_north_supply_route",
            "mission_type": "DISRUPT_SUPPLY",
        },
    )

    assert clear_target.key == "northern_valley"
    assert supply_target.key == "enemy_north_supply_route"


def test_tool_requirement_rejects_unmapped_operation() -> None:
    requirement = InteractionRequirement(
        target_argument="target_key",
        operation_argument="operation_type",
        operation_interactions={"KNOWN": "reconnaissance"},
    )

    with pytest.raises(AppError) as exc_info:
        resolve_tool_interaction(
            "starfire_command",
            requirement,
            {"target_key": "valley_entrance", "operation_type": "UNKNOWN"},
        )

    assert exc_info.value.code == "TOOL_INTERACTION_OPERATION_INVALID"


def test_repair_uses_the_unique_scenario_target() -> None:
    target = resolve_tool_interaction(
        "starfire_command",
        REPAIR_INTERACTION,
        {},
    )

    assert target.key == "starfire_outpost"


def test_executor_preserves_raw_legacy_arguments_and_uses_canonical_target(
    session: Session,
) -> None:
    player = GameService(session).create_player("Interaction Audit Lord")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:han_lie"),
    )
    session.add(conversation)
    session.flush()
    run = AgentRun(
        request_id=uuid4(),
        session_id=conversation.id,
        model="unit-test",
        input_message="recon legacy target",
        max_rounds=1,
    )
    session.add(run)
    session.commit()
    arguments = {
        "target_key": "valley_entrance",
        "troop_count": 60,
        "approach": "CAUTIOUS",
        "idempotency_key": "interaction-audit-recon-0001",
    }

    result = ToolExecutor(session, build_registry()).execute(
        ToolContext(
            player_id=player.id,
            npc_id=conversation.npc_id,
            session_id=conversation.id,
            agent_run_id=run.id,
            message_id=uuid4(),
            scenario_key="starfire_command",
        ),
        ToolCall(
            id="interaction-audit-recon",
            name="start_recon_operation",
            arguments=arguments,
        ),
    )

    assert result.ok
    assert arguments["target_key"] == "valley_entrance"
    trace = session.scalar(
        select(ToolExecution).where(ToolExecution.tool_call_id == "interaction-audit-recon")
    )
    operation = session.scalar(select(WorldOperation))
    assert trace is not None and operation is not None
    assert trace.arguments["target_key"] == "valley_entrance"
    assert operation.target_key == "northern_valley"
    assert operation.status == WorldOperationStatus.PENDING


def test_executor_rejects_invalid_mission_target_before_world_mutation(
    session: Session,
) -> None:
    player = GameService(session).create_player("Invalid Interaction Lord")
    conversation = ConversationSession(
        player_id=player.id,
        npc_id=seed_id("npc:han_lie"),
    )
    session.add(conversation)
    session.flush()
    run = AgentRun(
        request_id=uuid4(),
        session_id=conversation.id,
        model="unit-test",
        input_message="invalid mission target",
        max_rounds=1,
    )
    session.add(run)
    session.commit()
    arguments = {
        "target_key": "enemy_north_supply_route",
        "troop_count": 60,
        "mission_type": "CLEAR_VALLEY",
        "strategy": "CAUTIOUS",
        "idempotency_key": "invalid-interaction-target-0001",
    }

    result = ToolExecutor(session, build_registry()).execute(
        ToolContext(
            player_id=player.id,
            npc_id=conversation.npc_id,
            session_id=conversation.id,
            agent_run_id=run.id,
            message_id=uuid4(),
            scenario_key="starfire_command",
        ),
        ToolCall(
            id="invalid-interaction-target",
            name="start_military_operation",
            arguments=arguments,
        ),
    )

    assert result.code == "INTERACTION_NOT_SUPPORTED"
    assert session.scalar(select(func.count()).select_from(WorldOperation)) == 0
    trace = session.scalar(
        select(ToolExecution).where(ToolExecution.tool_call_id == "invalid-interaction-target")
    )
    assert trace is not None
    assert trace.arguments == arguments
    assert trace.error_code == "INTERACTION_NOT_SUPPORTED"


def _node(key: str, interaction: InteractionDefinition) -> NodeDefinition:
    return NodeDefinition(
        key=key,
        name=key,
        description="",
        node_type=WorldNodeType.FACILITY,
        initial_access=AccessState.AVAILABLE,
        initial_visibility=Visibility.KNOWN,
        interactions=(interaction,),
    )


def _binding(world: WorldDefinition) -> ScenarioWorldBinding:
    return ScenarioWorldBinding(
        world=world,
        resolve_node_key=lambda key: key,
        raw_target_supports_interaction=lambda _target, _interaction: True,
    )
