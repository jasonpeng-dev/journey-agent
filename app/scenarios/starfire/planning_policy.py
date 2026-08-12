"""Scenario-level planning constraints for Starfire Command."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, Literal

from app.scenarios.contracts import (
    ObjectiveScope,
    ScenarioPlanIssue,
    ScenarioRuntimeState,
)
from app.scenarios.starfire.objective_catalog import (
    STARFIRE_OBJECTIVE_CATALOG,
)

_EXECUTION_TOOLS = frozenset(
    {
        "inspect_command_state",
        "start_recon_operation",
        "start_military_operation",
        "negotiate_village_support",
        "start_outpost_repair",
        "start_trade_route_test",
    }
)
_IDEMPOTENT_TOOLS = _EXECUTION_TOOLS - {"inspect_command_state"}
_OPERATION_TOOLS = frozenset(
    {
        "start_recon_operation",
        "start_military_operation",
        "start_outpost_repair",
        "start_trade_route_test",
    }
)
_EXPECTED_OUTCOME_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "inspect_command_state": frozenset(
            {
                "soldiers_total_min",
                "food_min",
                "gold_min",
                "valley_intelligence",
                "enemy_supply_route",
                "valley_security",
                "village_support",
                "starfire_outpost_status",
                "northern_trade_route_status",
            }
        ),
        "start_recon_operation": frozenset({"operation_type", "status", "target_key"}),
        "start_military_operation": frozenset({"operation_type", "status", "target_key"}),
        "negotiate_village_support": frozenset({"village_support"}),
        "start_outpost_repair": frozenset({"operation_type", "status", "target_key"}),
        "start_trade_route_test": frozenset({"operation_type", "status", "target_key"}),
    }
)
_FIXED_TOOL_EXPECTED_OUTCOMES: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "start_recon_operation": MappingProxyType(
            {"operation_type": "RECONNAISSANCE", "status": "PENDING"}
        ),
        "start_military_operation": MappingProxyType(
            {"operation_type": "MILITARY", "status": "PENDING"}
        ),
        "start_outpost_repair": MappingProxyType(
            {"operation_type": "CONSTRUCTION", "status": "PENDING"}
        ),
        "start_trade_route_test": MappingProxyType(
            {"operation_type": "TRADE_TEST", "status": "PENDING"}
        ),
    }
)
_WORLD_OPERATION_SUCCESS_OUTCOMES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "start_recon_operation": ("PARTIAL_SUCCESS", "VICTORY"),
        "start_military_operation": ("VICTORY",),
        "start_outpost_repair": ("COMPLETED",),
        "start_trade_route_test": ("COMPLETED",),
    }
)


class StarfirePlanningPolicy:
    execution_tools = _EXECUTION_TOOLS
    idempotent_tools = _IDEMPOTENT_TOOLS
    operation_tools = _OPERATION_TOOLS
    expected_outcome_fields = _EXPECTED_OUTCOME_FIELDS
    fixed_tool_expected_outcomes = _FIXED_TOOL_EXPECTED_OUTCOMES
    world_operation_success_outcomes = _WORLD_OPERATION_SUCCESS_OUTCOMES
    allowed_player_action_facts = frozenset(
        {
            "village_support",
            "valley_intelligence",
            "valley_security",
            "starfire_outpost_status",
            "northern_trade_route_status",
        }
    )
    recoverable_failures = frozenset(
        {
            "EXPECTED_OUTCOME_NOT_MET",
            "STATE_VERSION_CONFLICT",
            "ENEMY_SUPPLY_ROUTE_UNKNOWN",
            "ENCOUNTER_DEFEAT",
            "RESOURCE_INSUFFICIENT",
            "SUPPLY_INSUFFICIENT",
            "SOLDIERS_UNAVAILABLE",
            "STARFIRE_OUTPOST_OFFLINE",
            "WORLD_OPERATION_DEFEAT",
            "PLAYER_DECISION_REJECTED",
            "VALLEY_UNSAFE",
            "TRADE_SUPPORT_REQUIRED",
            "WORLD_STATE_CHANGED",
        }
    )

    def validate_candidate_plan(
        self,
        steps: Sequence[Mapping[str, Any]],
        selected_tools: Sequence[str],
        wait_count: int,
        *,
        is_replan: bool,
        state: ScenarioRuntimeState,
        scope: ObjectiveScope,
    ) -> tuple[ScenarioPlanIssue, ...]:
        del selected_tools, wait_count, is_replan, state
        issues: list[ScenarioPlanIssue] = []
        for index, step in enumerate(steps):
            if step.get("selected_tool_name") != "negotiate_village_support":
                continue
            arguments = step.get("tool_arguments")
            tool_arguments = arguments if isinstance(arguments, Mapping) else {}
            food_offer = tool_arguments.get("food_offer")
            requested_support = tool_arguments.get("requested_support")
            required_support = (
                requested_support
                if isinstance(food_offer, int)
                and not isinstance(food_offer, bool)
                and food_offer >= 20
                else "INTELLIGENCE"
            )
            expected = step.get("expected_outcome")
            expected_outcome = expected if isinstance(expected, Mapping) else {}
            if expected_outcome.get("village_support") != required_support:
                issues.append(
                    ScenarioPlanIssue(
                        code="PLAN_EXPECTED_OUTCOME_VALUE_INVALID",
                        path=f"steps.{index}.expected_outcome.village_support",
                        message=(
                            "Village support must match the deterministic result "
                            "derived from the selected offer"
                        ),
                    )
                )
        issues.extend(self._validate_final_verification(steps, scope))
        return tuple(issues)

    def effect_satisfied(
        self,
        tool_name: str,
        tool_arguments: Mapping[str, Any],
        state: ScenarioRuntimeState,
    ) -> bool:
        if tool_name == "start_recon_operation":
            return self._value(state, "northern_valley", "valley_intelligence") in {
                "PARTIAL",
                "COMPLETE",
            }
        if tool_name == "negotiate_village_support":
            return self._value(state, "north_village", "village_support") in {
                "GUIDE",
                "SUPPLIES",
            }
        if tool_name == "start_military_operation":
            if tool_arguments.get("mission_type") == "DISRUPT_SUPPLY":
                return (
                    self._value(state, "enemy_north_supply_route", "supply_status") == "DISRUPTED"
                )
            return self._value(state, "northern_valley", "valley_security") == "SAFE"
        if tool_name == "start_outpost_repair":
            return self._value(state, "starfire_outpost", "outpost_status") in {
                "OPERATIONAL",
                "RESTORED",
            }
        if tool_name == "start_trade_route_test":
            return self._value(state, "northern_trade_route", "trade_route_status") == "OPEN"
        return False

    def build_planning_constraints(
        self,
        kind: Literal["PLAN", "REPLAN"],
        reason: str | None,
        state: ScenarioRuntimeState,
        scope: ObjectiveScope,
    ) -> Mapping[str, object]:
        canonical_facts = self._canonical_facts(state)
        return {
            "canonical_facts": canonical_facts,
            "planning_kind": kind,
            "failure_code": reason,
            "guardrails": {
                "scope_must_remain_frozen": True,
                "use_only_known_targets": True,
                "respect_tool_and_officer_authority": True,
                "asynchronous_operations_require_adjacent_wait_steps": True,
                "do_not_repeat_completed_effects": True,
            },
            "required_final_step": {
                "execution_type": "TOOL",
                "assigned_officer_key": "shen_ce",
                "action_intent": "VERIFY_AND_REPORT",
                "allowed_tool_names": ["inspect_command_state"],
                "selected_tool_name": "inspect_command_state",
                "tool_arguments": {},
                "expected_outcome": self._verification_expectations(scope),
                "resume_condition": None,
            },
        }

    def replan_guidance(self, reason: str | None) -> str | None:
        if reason is None:
            return None
        return (
            f"Use the structured failure {reason}, current known state, and completed effects "
            "to choose a new legal strategy without changing objective_scope."
        )

    def planner_instruction(self, kind: Literal["PLAN", "REPLAN"]) -> str:
        return (
            f" This is a {kind.lower()} request. Choose a legal strategy from known state, "
            "preserve objective_scope, and write strategy_summary and step descriptions in "
            "concise Simplified Chinese."
        )

    @staticmethod
    def _validate_final_verification(
        steps: Sequence[Mapping[str, Any]],
        scope: ObjectiveScope,
    ) -> tuple[ScenarioPlanIssue, ...]:
        if not steps:
            return ()
        final = steps[-1]
        if (
            final.get("execution_type") != "TOOL"
            or final.get("assigned_officer_key") != "shen_ce"
            or final.get("selected_tool_name") != "inspect_command_state"
            or final.get("action_intent") != "VERIFY_AND_REPORT"
        ):
            return (
                ScenarioPlanIssue(
                    code="PLAN_FINAL_VERIFICATION_REQUIRED",
                    path=f"steps.{len(steps) - 1}",
                    message=(
                        "The final Step must assign Shen Ce a TOOL call to "
                        "inspect_command_state with action_intent=VERIFY_AND_REPORT"
                    ),
                ),
            )
        issues = []
        expected_outcome = final.get("expected_outcome")
        expected_values = expected_outcome if isinstance(expected_outcome, Mapping) else {}
        for key, accepted in StarfirePlanningPolicy._verification_accepted(scope).items():
            if expected_values.get(key) not in accepted:
                issues.append(
                    ScenarioPlanIssue(
                        code="PLAN_FINAL_VERIFICATION_REQUIRED",
                        path=f"steps.{len(steps) - 1}.expected_outcome.{key}",
                        message=(f"The final verification must expect {key} in {sorted(accepted)}"),
                    )
                )
        return tuple(issues)

    @staticmethod
    def _verification_accepted(scope: ObjectiveScope) -> dict[str, frozenset[str]]:
        field_by_fact = {
            ("northern_valley", "valley_intelligence"): "valley_intelligence",
            ("northern_valley", "valley_security"): "valley_security",
            ("starfire_outpost", "outpost_status"): "starfire_outpost_status",
            ("northern_trade_route", "trade_route_status"): ("northern_trade_route_status"),
        }
        return {
            field_by_fact[(requirement.node_key, requirement.fact_key)]: (
                requirement.accepted_values
            )
            for requirement in STARFIRE_OBJECTIVE_CATALOG.verification_requirements(scope)
        }

    @classmethod
    def _verification_expectations(cls, scope: ObjectiveScope) -> dict[str, str]:
        preferred = {
            "valley_intelligence": "PARTIAL",
            "valley_security": "SAFE",
            "starfire_outpost_status": "OPERATIONAL",
            "northern_trade_route_status": "OPEN",
        }
        return {field: preferred[field] for field in cls._verification_accepted(scope)}

    @classmethod
    def _canonical_facts(cls, state: ScenarioRuntimeState) -> dict[str, str]:
        refs = (
            ("north_village", "village_support"),
            ("northern_valley", "valley_intelligence"),
            ("northern_valley", "valley_security"),
            ("northern_valley", "ambush_status"),
            ("enemy_north_supply_route", "supply_status"),
            ("starfire_outpost", "outpost_status"),
            ("northern_trade_route", "trade_route_status"),
        )
        return {
            f"{node_key}.{fact_key}": cls._value(state, node_key, fact_key)
            for node_key, fact_key in refs
            if state.node_known(node_key) and state.fact_known(node_key, fact_key)
        }

    @staticmethod
    def _value(state: ScenarioRuntimeState, node_key: str, fact_key: str) -> str:
        return state.fact_value(node_key, fact_key)


STARFIRE_PLANNING_POLICY = StarfirePlanningPolicy()
