"""State-based completion objectives for the Starfire scenario."""

from app.scenarios.contracts import ObjectiveEvaluation, ScenarioRuntimeState


class StarfireObjectiveEvaluator:
    def evaluate(self, state: ScenarioRuntimeState) -> ObjectiveEvaluation:
        valley = state.fact_value("northern_valley", "valley_security")
        outpost = state.fact_value("starfire_outpost", "outpost_status")
        trade = state.fact_value("northern_trade_route", "trade_route_status")
        completed = valley == "SAFE" and outpost in {"OPERATIONAL", "RESTORED"} and trade == "OPEN"
        return ObjectiveEvaluation(
            completed=completed,
            details={
                "northern_valley.valley_security": valley,
                "starfire_outpost.outpost_status": outpost,
                "northern_trade_route.trade_route_status": trade,
            },
            summary=f"valley={valley}, outpost={outpost}, trade_route={trade}",
        )


STARFIRE_OBJECTIVES = StarfireObjectiveEvaluator()
