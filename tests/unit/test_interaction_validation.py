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
from app.tools.handlers import OutpostRepairArgs, TradeRouteTestArgs
from app.tools.interaction_validation import (
    MILITARY_INTERACTION,
    RECON_INTERACTION,
    REPAIR_INTERACTION,
    TRADE_ROUTE_INTERACTION,
    VILLAGE_SUPPORT_INTERACTION,
    interaction_target_guidance,
    resolve_tool_interaction,
)


@pytest.mark.parametrize(
    ("raw_target", "interaction", "canonical_target"),
    [
        ("valley_entrance", "reconnaissance", "northern_valley"),
        ("ambush_valley", "clear_threat", "northern_valley"),
        ("northern_valley", "reconnaissance", "northern_valley"),
        ("northern_valley", "clear_threat", "northern_valley"),
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


def test_public_tool_schemas_use_generic_canonical_target_key() -> None:
    definitions = {item.name: item for item in build_registry().definitions()}

    for tool_name in {
        "start_recon_operation",
        "start_military_operation",
        "start_outpost_repair",
        "start_trade_route_test",
    }:
        schema = definitions[tool_name].parameters
        target = schema["properties"]["target_key"]
        assert target["type"] == "string"
        assert "enum" not in target
        assert "target_key" in schema["required"]

    trade_schema = definitions["start_trade_route_test"].parameters
    assert "route_key" not in trade_schema["properties"]


def test_tool_descriptions_get_canonical_targets_from_scenario_definition() -> None:
    definitions = {item.name: item for item in build_registry().definitions("starfire_command")}

    assert "reconnaissance: northern_valley" in definitions["start_recon_operation"].description
    military = definitions["start_military_operation"].description
    assert "clear_threat: northern_valley" in military
    assert "disrupt_supply: enemy_north_supply_route" in military
    assert "valley_entrance" not in military
    assert "ambush_valley" not in military
    assert "repair: starfire_outpost" in definitions["start_outpost_repair"].description
    assert (
        "test_trade_route: northern_trade_route"
        in definitions["start_trade_route_test"].description
    )


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


@pytest.mark.parametrize(
    ("requirement", "arguments", "canonical_target"),
    [
        (RECON_INTERACTION, {"target_key": "northern_valley"}, "northern_valley"),
        (
            MILITARY_INTERACTION,
            {"target_key": "northern_valley", "mission_type": "CLEAR_VALLEY"},
            "northern_valley",
        ),
        (
            MILITARY_INTERACTION,
            {
                "target_key": "enemy_north_supply_route",
                "mission_type": "DISRUPT_SUPPLY",
            },
            "enemy_north_supply_route",
        ),
        (REPAIR_INTERACTION, {"target_key": "starfire_outpost"}, "starfire_outpost"),
        (
            TRADE_ROUTE_INTERACTION,
            {"target_key": "northern_trade_route"},
            "northern_trade_route",
        ),
    ],
)
def test_canonical_tool_targets_resolve_from_interactions(
    requirement: InteractionRequirement,
    arguments: dict[str, str],
    canonical_target: str,
) -> None:
    target = resolve_tool_interaction("starfire_command", requirement, arguments)

    assert target.key == canonical_target


@pytest.mark.parametrize(
    ("requirement", "arguments"),
    [
        (RECON_INTERACTION, {"target_key": "unknown_node"}),
        (RECON_INTERACTION, {"target_key": "north_village"}),
        (REPAIR_INTERACTION, {"target_key": "northern_valley"}),
        (
            MILITARY_INTERACTION,
            {"target_key": "starfire_outpost", "mission_type": "CLEAR_VALLEY"},
        ),
        (
            MILITARY_INTERACTION,
            {
                "target_key": "enemy_north_supply_route",
                "mission_type": "CLEAR_VALLEY",
            },
        ),
    ],
)
def test_generic_string_targets_still_fail_closed(
    requirement: InteractionRequirement,
    arguments: dict[str, str],
) -> None:
    with pytest.raises(AppError):
        resolve_tool_interaction("starfire_command", requirement, arguments)


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


def test_trade_accepts_preferred_and_legacy_target_inputs() -> None:
    preferred = TradeRouteTestArgs.model_validate(
        {"target_key": "northern_trade_route", "idempotency_key": "trade-target-0001"}
    )
    legacy = TradeRouteTestArgs.model_validate(
        {"route_key": "northern_trade_route", "idempotency_key": "trade-target-0002"}
    )
    both = TradeRouteTestArgs.model_validate(
        {
            "target_key": "northern_trade_route",
            "route_key": "northern_trade_route",
            "idempotency_key": "trade-target-0003",
        }
    )

    assert (
        resolve_tool_interaction("starfire_command", TRADE_ROUTE_INTERACTION, preferred).key
        == "northern_trade_route"
    )
    assert (
        resolve_tool_interaction("starfire_command", TRADE_ROUTE_INTERACTION, legacy).key
        == "northern_trade_route"
    )
    assert (
        resolve_tool_interaction("starfire_command", TRADE_ROUTE_INTERACTION, both).key
        == "northern_trade_route"
    )


def test_conflicting_preferred_and_legacy_targets_fail_closed() -> None:
    trade = InteractionDefinition(key="test_trade_route", name="TEST TRADE ROUTE")
    world = WorldDefinition(
        key="two_routes",
        name="Two Routes",
        interactions=(trade,),
        nodes=(_node("first_route", trade), _node("second_route", trade)),
        relations=(),
        resources=(),
    )
    resolver = InteractionTargetResolver(MappingProxyType({"two_routes": _binding(world)}))

    with pytest.raises(AppError) as exc_info:
        resolve_tool_interaction(
            "two_routes",
            TRADE_ROUTE_INTERACTION,
            {"target_key": "first_route", "route_key": "second_route"},
            resolver=resolver,
        )

    assert exc_info.value.code == "INTERACTION_TARGET_CONFLICT"


def test_repair_runtime_model_accepts_legacy_missing_target() -> None:
    parsed = OutpostRepairArgs.model_validate(
        {
            "repair_level": "TEMPORARY",
            "food_commitment": 20,
            "gold_commitment": 20,
            "idempotency_key": "repair-target-0001",
        }
    )

    assert parsed.target_key is None
    assert (
        resolve_tool_interaction("starfire_command", REPAIR_INTERACTION, parsed).key
        == "starfire_outpost"
    )


def test_target_guidance_uses_the_supplied_world_definition() -> None:
    recon = InteractionDefinition(key="reconnaissance", name="RECONNAISSANCE")
    world = WorldDefinition(
        key="custom_world",
        name="Custom World",
        interactions=(recon,),
        nodes=(_node("custom_pass", recon),),
        relations=(),
        resources=(),
    )
    resolver = InteractionTargetResolver(MappingProxyType({"custom_world": _binding(world)}))

    guidance = interaction_target_guidance("custom_world", RECON_INTERACTION, resolver=resolver)

    assert guidance.endswith("reconnaissance: custom_pass.")


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


def test_executor_preserves_legacy_trade_arguments_and_uses_canonical_target(
    session: Session,
) -> None:
    service = GameService(session)
    player = service.create_player("Legacy Trade Lord")
    service.set_world_fact(player.id, "valley_security", {"status": "SAFE"})
    service.set_world_fact(player.id, "starfire_outpost_status", {"status": "OPERATIONAL"})
    service.set_world_fact(player.id, "village_support", {"status": "GUIDE"})
    service.unlock_node(player.id, "northern_trade_route")
    conversation = ConversationSession(player_id=player.id, npc_id=seed_id("npc:lu_ning"))
    session.add(conversation)
    session.flush()
    run = AgentRun(
        request_id=uuid4(),
        session_id=conversation.id,
        model="unit-test",
        input_message="legacy trade target",
        max_rounds=1,
    )
    session.add(run)
    session.commit()
    arguments = {
        "route_key": "northern_trade_route",
        "idempotency_key": "legacy-trade-route-0001",
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
            id="legacy-trade-route",
            name="start_trade_route_test",
            arguments=arguments,
        ),
    )

    assert result.ok
    trace = session.scalar(
        select(ToolExecution).where(ToolExecution.tool_call_id == "legacy-trade-route")
    )
    operation = session.scalar(
        select(WorldOperation).where(WorldOperation.idempotency_key == "legacy-trade-route-0001")
    )
    assert trace is not None and operation is not None
    assert trace.arguments == arguments
    assert operation.target_key == "northern_trade_route"


def test_executor_recovers_legacy_repair_without_target(session: Session) -> None:
    service = GameService(session)
    player = service.create_player("Legacy Repair Lord")
    service.set_world_fact(player.id, "valley_security", {"status": "SAFE"})
    service.unlock_node(player.id, "starfire_outpost")
    conversation = ConversationSession(player_id=player.id, npc_id=seed_id("npc:lu_ning"))
    session.add(conversation)
    session.flush()
    run = AgentRun(
        request_id=uuid4(),
        session_id=conversation.id,
        model="unit-test",
        input_message="legacy repair target",
        max_rounds=1,
    )
    session.add(run)
    session.commit()
    arguments = {
        "repair_level": "TEMPORARY",
        "food_commitment": 20,
        "gold_commitment": 20,
        "idempotency_key": "legacy-repair-target-0001",
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
            id="legacy-repair-target",
            name="start_outpost_repair",
            arguments=arguments,
        ),
    )

    assert result.ok
    trace = session.scalar(
        select(ToolExecution).where(ToolExecution.tool_call_id == "legacy-repair-target")
    )
    operation = session.scalar(
        select(WorldOperation).where(WorldOperation.idempotency_key == "legacy-repair-target-0001")
    )
    assert trace is not None and operation is not None
    assert trace.arguments == arguments
    assert operation.target_key == "starfire_outpost"


def test_world_operation_idempotency_matches_legacy_and_canonical_targets(
    session: Session,
) -> None:
    service = GameService(session)
    player = service.create_player("Legacy Operation Lord")
    operation = WorldOperation(
        player_id=player.id,
        officer_npc_id=seed_id("npc:han_lie"),
        task_id=None,
        source_step_id=None,
        operation_type="RECONNAISSANCE",
        target_key="valley_entrance",
        parameters={"troop_count": 60, "approach": "CAUTIOUS"},
        idempotency_key="legacy-world-operation-0001",
    )
    session.add(operation)
    session.commit()

    resumed = service.start_recon_operation(
        player_id=player.id,
        officer_npc_id=seed_id("npc:han_lie"),
        task_id=None,
        source_step_id=None,
        target_key="northern_valley",
        troop_count=60,
        approach="CAUTIOUS",
        idempotency_key="legacy-world-operation-0001",
    )

    assert resumed.id == operation.id
    assert resumed.target_key == "valley_entrance"


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
