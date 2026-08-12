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
from app.scenarios.starfire.objective_catalog import STARFIRE_OBJECTIVE_CATALOG

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
            "PLAN_EXHAUSTED_SCOPE_INCOMPLETE",
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
        del selected_tools, wait_count
        issues: list[ScenarioPlanIssue] = []
        for index, step in enumerate(steps):
            arguments = step.get("tool_arguments")
            tool_arguments = arguments if isinstance(arguments, Mapping) else {}
            planned_countermeasure = any(
                prior.get("selected_tool_name") == "start_military_operation"
                and isinstance(prior.get("tool_arguments"), Mapping)
                and prior["tool_arguments"].get("mission_type") == "DISRUPT_SUPPLY"
                for prior in steps[:index]
            )
            if (
                is_replan
                and step.get("selected_tool_name") == "start_military_operation"
                and tool_arguments.get("mission_type") == "CLEAR_VALLEY"
                and state.fact_known("enemy_north_supply_route", "supply_status")
                and self._value(state, "enemy_north_supply_route", "supply_status") == "ACTIVE"
                and not planned_countermeasure
            ):
                issues.append(
                    ScenarioPlanIssue(
                        code="PLAN_KNOWN_COUNTERMEASURE_REQUIRED",
                        path=f"steps.{index}.tool_arguments.mission_type",
                        message=(
                            "Known state proves CLEAR_VALLEY will fail while the enemy supply "
                            "route remains ACTIVE. Disrupt the known supply target and wait for "
                            "that operation before attempting CLEAR_VALLEY again."
                        ),
                    )
                )
            terminal_effect = self._terminal_action_effect(step)
            if terminal_effect is not None and terminal_effect not in self._in_scope_terminal_facts(
                scope
            ):
                tool_name = str(step.get("selected_tool_name"))
                node_key, fact_key = terminal_effect
                issues.append(
                    ScenarioPlanIssue(
                        code="PLAN_TERMINAL_EFFECT_OUTSIDE_OBJECTIVE_SCOPE",
                        path=f"steps.{index}.selected_tool_name",
                        message=(
                            f"{tool_name} pursues terminal effect {node_key}.{fact_key}, "
                            "which is neither a terminal requirement nor a prerequisite "
                            "of the frozen objective_scope. Remove this terminal action "
                            "and its paired wait; keep only actions that support the "
                            "current frozen scope."
                        ),
                    )
                )
            if step.get("selected_tool_name") != "negotiate_village_support":
                continue
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
                if not state.fact_known("enemy_north_supply_route", "supply_status"):
                    return False
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
        objective_facts, prerequisite_facts = self._scoped_terminal_fact_groups(scope)
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
                "do_not_pursue_terminal_effects_outside_frozen_scope": True,
            },
            "terminal_effect_scope": {
                "objective_terminal_facts": sorted(
                    self._format_ref(ref) for ref in objective_facts
                ),
                "prerequisite_terminal_facts": sorted(
                    self._format_ref(ref) for ref in prerequisite_facts
                ),
                "rule": (
                    "A terminal action is valid only when its terminal fact is an explicit "
                    "objective terminal fact or a declared prerequisite terminal fact. "
                    "Information gathering and supporting actions remain available when "
                    "they help the frozen scope. Do not add later terminal outcomes."
                ),
            },
            "final_verification": "BACKEND_SCOPED_OBJECTIVE_EVALUATOR",
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
            "concise Simplified Chinese. The request already contains the latest Known "
            "World; do not add routine initial, post-operation, or final inspection steps. "
            "Treat objective_scope as the complete set of requested terminal outcomes. "
            "Do not describe or plan any later terminal outcome outside that scope; allowed "
            "tools may still be used for information, support, or declared prerequisites. "
            "Use inspect_command_state only when necessary observable information is "
            "missing. Backend scoped objective evaluation owns final verification."
        )

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
    def _terminal_action_effect(step: Mapping[str, Any]) -> tuple[str, str] | None:
        tool_name = step.get("selected_tool_name")
        if tool_name == "start_outpost_repair":
            return ("starfire_outpost", "outpost_status")
        if tool_name == "start_trade_route_test":
            return ("northern_trade_route", "trade_route_status")
        if tool_name == "start_military_operation":
            arguments = step.get("tool_arguments")
            if isinstance(arguments, Mapping) and arguments.get("mission_type") == "CLEAR_VALLEY":
                return ("northern_valley", "valley_security")
        return None

    @staticmethod
    def _scoped_terminal_fact_groups(
        scope: ObjectiveScope,
    ) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
        objective_facts = {
            (requirement.node_key, requirement.fact_key)
            for requirement in STARFIRE_OBJECTIVE_CATALOG.verification_requirements(scope)
        }
        prerequisite_facts = {
            (requirement.node_key, requirement.fact_key)
            for prerequisite in STARFIRE_OBJECTIVE_CATALOG.prerequisites(scope)
            for requirement in prerequisite.requirements
        }
        return objective_facts, prerequisite_facts

    @classmethod
    def _in_scope_terminal_facts(cls, scope: ObjectiveScope) -> set[tuple[str, str]]:
        objective_facts, prerequisite_facts = cls._scoped_terminal_fact_groups(scope)
        return objective_facts | prerequisite_facts

    @staticmethod
    def _format_ref(ref: tuple[str, str]) -> str:
        return f"{ref[0]}.{ref[1]}"

    @staticmethod
    def _value(state: ScenarioRuntimeState, node_key: str, fact_key: str) -> str:
        return state.fact_value(node_key, fact_key)


STARFIRE_PLANNING_POLICY = StarfirePlanningPolicy()
