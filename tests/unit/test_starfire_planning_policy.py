from uuid import uuid4

from app.scenarios.registry import scenario_binding
from app.scenarios.starfire.fallback_plans import (
    STARFIRE_FALLBACK_PLANS,
    initial_strategic_starfire_plan,
)
from app.scenarios.starfire.objective_catalog import FULL_STARFIRE_SCOPE
from app.scenarios.starfire.planning_policy import STARFIRE_PLANNING_POLICY
from app.scenarios.starfire.ruleset import (
    StarfireFactState,
    StarfireResources,
    StarfireRuleState,
)


def test_registry_binds_starfire_planning_objectives_and_fallbacks() -> None:
    scenario = scenario_binding("starfire_command")

    assert scenario is not None
    assert scenario.planning_policy is STARFIRE_PLANNING_POLICY
    assert scenario.objective_evaluator is not None
    assert scenario.fallback_plans is not None


def test_starfire_policy_accepts_current_initial_fallback_plan() -> None:
    proposal = initial_strategic_starfire_plan(uuid4(), FULL_STARFIRE_SCOPE)
    steps = proposal["steps"]
    selected_tools = [
        str(step["selected_tool_name"]) for step in steps if step["selected_tool_name"] is not None
    ]

    issues = STARFIRE_PLANNING_POLICY.validate_candidate_plan(
        steps,
        selected_tools,
        sum(step["execution_type"] != "TOOL" for step in steps),
        is_replan=False,
        state=_state(),
        scope=FULL_STARFIRE_SCOPE,
    )

    assert issues == ()


def test_starfire_policy_uses_canonical_state_for_completed_effects() -> None:
    state = _state(
        valley_security="SAFE",
        outpost_status="OPERATIONAL",
        trade_route_status="CLOSED",
    )

    assert STARFIRE_PLANNING_POLICY.effect_satisfied("start_military_operation", {}, state)
    assert STARFIRE_PLANNING_POLICY.effect_satisfied("start_outpost_repair", {}, state)
    assert not STARFIRE_PLANNING_POLICY.effect_satisfied("start_trade_route_test", {}, state)
    constraints = STARFIRE_PLANNING_POLICY.build_planning_constraints(
        "REPLAN", "TRADE_SUPPORT_REQUIRED", state, FULL_STARFIRE_SCOPE
    )
    canonical = constraints["canonical_facts"]
    assert isinstance(canonical, dict)
    assert canonical["northern_valley.valley_security"] == "SAFE"
    assert canonical["starfire_outpost.outpost_status"] == "OPERATIONAL"


def test_starfire_policy_rejects_wrong_business_order() -> None:
    proposal = initial_strategic_starfire_plan(uuid4(), FULL_STARFIRE_SCOPE)
    steps = proposal["steps"]
    repair_index = next(
        index
        for index, step in enumerate(steps)
        if step["selected_tool_name"] == "start_outpost_repair"
    )
    trade_index = next(
        index
        for index, step in enumerate(steps)
        if step["selected_tool_name"] == "start_trade_route_test"
    )
    steps[repair_index], steps[trade_index] = steps[trade_index], steps[repair_index]
    selected_tools = [
        str(step["selected_tool_name"]) for step in steps if step["selected_tool_name"] is not None
    ]

    issues = STARFIRE_PLANNING_POLICY.validate_candidate_plan(
        steps,
        selected_tools,
        sum(step["execution_type"] != "TOOL" for step in steps),
        is_replan=False,
        state=_state(),
        scope=FULL_STARFIRE_SCOPE,
    )

    assert "PLAN_STEP_ORDER_INVALID" in {issue.code for issue in issues}


def test_state_aware_fallback_uses_only_remaining_canonical_suffix() -> None:
    proposal = STARFIRE_FALLBACK_PLANS.state_aware_recovery(
        uuid4(),
        2,
        "TRADE_SUPPORT_REQUIRED",
        _state(
            valley_security="SAFE",
            outpost_status="OPERATIONAL",
            trade_route_status="CLOSED",
        ),
        FULL_STARFIRE_SCOPE,
    )
    tool_steps = [step for step in proposal["steps"] if step["selected_tool_name"]]
    tool_names = [step["selected_tool_name"] for step in tool_steps]

    assert "start_military_operation" not in tool_names
    assert "start_outpost_repair" not in tool_names
    assert "negotiate_village_support" in tool_names
    trade = next(
        step for step in tool_steps if step["selected_tool_name"] == "start_trade_route_test"
    )
    assert trade["tool_arguments"] == {"target_key": "northern_trade_route"}


def _state(
    *,
    valley_security: str = "UNSAFE",
    outpost_status: str = "DAMAGED",
    trade_route_status: str = "CLOSED",
) -> StarfireRuleState:
    return StarfireRuleState(
        facts={
            ("north_village", "village_support"): StarfireFactState("NONE"),
            ("northern_valley", "valley_intelligence"): StarfireFactState("INCOMPLETE"),
            ("northern_valley", "valley_security"): StarfireFactState(valley_security),
            ("northern_valley", "ambush_status"): StarfireFactState("ACTIVE"),
            ("enemy_north_supply_route", "supply_status"): StarfireFactState("ACTIVE"),
            ("starfire_outpost", "outpost_status"): StarfireFactState(outpost_status),
            ("northern_trade_route", "trade_route_status"): StarfireFactState(trade_route_status),
        },
        resources=StarfireResources(soldiers_available=280, food=120, gold=80, morale=65),
    )
