import pytest

from app.scenarios.starfire.objectives import STARFIRE_OBJECTIVES
from app.scenarios.starfire.ruleset import (
    StarfireFactState,
    StarfireResources,
    StarfireRuleState,
)


@pytest.mark.parametrize(
    ("valley", "outpost", "trade", "completed"),
    [
        ("SAFE", "OPERATIONAL", "OPEN", True),
        ("SAFE", "RESTORED", "OPEN", True),
        ("UNSAFE", "OPERATIONAL", "OPEN", False),
        ("SAFE", "DAMAGED", "OPEN", False),
        ("SAFE", "OPERATIONAL", "CLOSED", False),
    ],
)
def test_starfire_objectives_are_state_based_and_canonical(
    valley: str,
    outpost: str,
    trade: str,
    completed: bool,
) -> None:
    state = StarfireRuleState(
        facts={
            ("northern_valley", "valley_security"): StarfireFactState(valley),
            ("starfire_outpost", "outpost_status"): StarfireFactState(outpost),
            ("northern_trade_route", "trade_route_status"): StarfireFactState(trade),
        },
        resources=StarfireResources(soldiers_available=0, food=0, gold=0, morale=0),
    )

    result = STARFIRE_OBJECTIVES.evaluate(state)

    assert result.completed is completed
    assert result.details == {
        "northern_valley.valley_security": valley,
        "starfire_outpost.outpost_status": outpost,
        "northern_trade_route.trade_route_status": trade,
    }
