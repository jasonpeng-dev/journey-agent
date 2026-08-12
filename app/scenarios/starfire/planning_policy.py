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
    StarfireObjectiveKey,
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
_REPLAN_GUIDANCE = MappingProxyType(
    {
        "ENCOUNTER_DEFEAT": (
            "The valley clearance failed and exposed the enemy supply route. Disrupt that "
            "route, secure useful village support, then retry only the unfinished phases."
        ),
        "TRADE_SUPPORT_REQUIRED": (
            "The trade test cannot start without village support. Acquire GUIDE or "
            "SUPPLIES support first, then retry the trade test; do not repeat the "
            "already completed military or construction operations."
        ),
        "WORLD_STATE_CHANGED": (
            "A prerequisite changed before world resolution. Re-inspect current state "
            "and rebuild only the invalidated suffix of the plan."
        ),
        "RESOURCE_INSUFFICIENT": (
            "The requested action exceeds current resources. Re-inspect resources and "
            "choose a cheaper authorized action or request player approval."
        ),
        "SUPPLY_INSUFFICIENT": (
            "The proposed food offer is unaffordable. Re-inspect supplies and use a "
            "lower offer within authority or request player approval."
        ),
        "SOLDIERS_UNAVAILABLE": (
            "The requested force is unavailable. Re-inspect committed troops and "
            "choose a smaller force or wait for the active operation to resolve."
        ),
        "ENEMY_SUPPLY_ROUTE_UNKNOWN": (
            "The enemy route is not discovered yet. Add reconnaissance or another "
            "information-gathering step before attempting disruption."
        ),
        "STARFIRE_OUTPOST_OFFLINE": (
            "The outpost prerequisite is not satisfied. Repair or restore it before "
            "retrying the dependent action."
        ),
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
        issues: list[ScenarioPlanIssue] = []
        required = self._required_tools(scope)
        if is_replan:
            required = {tool for tool in required if not self.effect_satisfied(tool, {}, state)}
        for tool_name in sorted(required.difference(selected_tools)):
            issues.append(
                ScenarioPlanIssue(
                    code="PLAN_GOAL_COVERAGE_INCOMPLETE",
                    path="steps",
                    message=f"The command plan cannot satisfy its goal without {tool_name}",
                )
            )
        required_waits = len(required.intersection(self.operation_tools))
        if not is_replan and wait_count < required_waits:
            issues.append(
                ScenarioPlanIssue(
                    code="PLAN_GOAL_COVERAGE_INCOMPLETE",
                    path="steps",
                    message="Every scoped asynchronous operation must be world-verified",
                )
            )
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
        ordered = [
            step.get("selected_tool_name")
            if step.get("execution_type") == "TOOL"
            else step.get("execution_type")
            for step in steps
        ]
        for before, after in (
            ("start_military_operation", "start_outpost_repair"),
            ("start_outpost_repair", "start_trade_route_test"),
        ):
            if (
                before in ordered
                and after in ordered
                and ordered.index(before) > ordered.index(after)
            ):
                issues.append(
                    ScenarioPlanIssue(
                        code="PLAN_STEP_ORDER_INVALID",
                        path="steps",
                        message=f"{before} must occur before {after}",
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
            "strategic_initial_plan_blueprint": (
                {
                    "applies_when": "kind=PLAN and scenario_key=starfire_command",
                    "verified_state_already_supplied": (
                        "Do not add inspect_task_requirements or a redundant initial "
                        "inspection step"
                    ),
                    "ordered_phases": self._scoped_blueprint(scope),
                    "dependency_rules": [
                        "Military valley clearance must occur before outpost repair",
                        "Outpost repair must occur before the trade test",
                        "GUIDE or SUPPLIES village support must exist before the trade test",
                    ],
                    "village_support_rule": (
                        "food_offer below 20 always yields INTELLIGENCE; food_offer of "
                        "20 or more yields the requested INTELLIGENCE, GUIDE, or SUPPLIES"
                    ),
                }
                if kind == "PLAN"
                else None
            ),
            "strategic_replan_blueprint": (
                self._replan_blueprint(reason, state) if kind == "REPLAN" else None
            ),
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
        return _REPLAN_GUIDANCE.get(reason) if reason is not None else None

    def planner_instruction(self, kind: Literal["PLAN", "REPLAN"]) -> str:
        if kind == "PLAN":
            return (
                " Follow the frozen objective_scope and its scoped constraints; do not add "
                "objectives, tools, or terminal verification outside that scope. "
                "Write strategy_summary and every step description in concise Simplified Chinese."
            )
        return (
            " Follow constraints.strategic_replan_blueprint and write strategy_summary "
            "and every step description in concise Simplified Chinese."
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
    def _required_tools(scope: ObjectiveScope) -> set[str]:
        keys = set(scope.objective_keys)
        required: set[str] = set()
        if StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE.value in keys:
            required.add("start_recon_operation")
        if keys.intersection(
            {
                StarfireObjectiveKey.SECURE_NORTHERN_VALLEY.value,
                StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST.value,
                StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE.value,
                StarfireObjectiveKey.FULL_NORTHERN_RECOVERY.value,
            }
        ):
            required.add("start_military_operation")
        if keys.intersection(
            {
                StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST.value,
                StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE.value,
                StarfireObjectiveKey.FULL_NORTHERN_RECOVERY.value,
            }
        ):
            required.add("start_outpost_repair")
        if keys.intersection(
            {
                StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE.value,
                StarfireObjectiveKey.FULL_NORTHERN_RECOVERY.value,
            }
        ):
            required.add("start_trade_route_test")
        return required

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
    def _scoped_blueprint(cls, scope: ObjectiveScope) -> list[str]:
        required = cls._required_tools(scope)
        phases: list[str] = []
        for tool, description in (
            ("start_recon_operation", "start_recon_operation and wait for resolution"),
            ("start_military_operation", "secure the valley and wait for resolution"),
            ("start_outpost_repair", "repair the outpost and wait for resolution"),
            ("start_trade_route_test", "test the trade route and wait for resolution"),
        ):
            if tool in required:
                phases.append(description)
        phases.append("inspect_command_state with action_intent=VERIFY_AND_REPORT")
        return phases

    def _replan_blueprint(
        self,
        reason: str | None,
        state: ScenarioRuntimeState,
    ) -> dict[str, object]:
        completed_effects = {
            "reconnaissance": self.effect_satisfied("start_recon_operation", {}, state),
            "village_trade_support": self.effect_satisfied("negotiate_village_support", {}, state),
            "valley_secured": self.effect_satisfied("start_military_operation", {}, state),
            "outpost_repaired": self.effect_satisfied("start_outpost_repair", {}, state),
            "trade_route_open": self.effect_satisfied("start_trade_route_test", {}, state),
        }
        if reason == "ENCOUNTER_DEFEAT":
            phases = [
                "start_military_operation with mission_type=DISRUPT_SUPPLY",
                "WAIT_FOR_WORLD_EVENT for supply disruption",
                "start_military_operation with mission_type=CLEAR_VALLEY",
                "WAIT_FOR_WORLD_EVENT for valley clearance",
                "start_outpost_repair if the outpost is not already repaired",
                "WAIT_FOR_WORLD_EVENT for construction when repair is included",
                "start_trade_route_test if the trade route is not already open",
                "WAIT_FOR_WORLD_EVENT for trade when testing is included",
                "inspect_command_state with action_intent=VERIFY_AND_REPORT",
            ]
        elif reason == "TRADE_SUPPORT_REQUIRED":
            phases = [
                "negotiate_village_support for GUIDE or SUPPLIES",
                "start_trade_route_test",
                "WAIT_FOR_WORLD_EVENT for trade resolution",
                "inspect_command_state with action_intent=VERIFY_AND_REPORT",
            ]
        else:
            phases = [
                "Use only allowed_tools whose effects are not already satisfied",
                "Re-establish the failed prerequisite",
                "Complete only the remaining goal suffix",
                "inspect_command_state with action_intent=VERIFY_AND_REPORT",
            ]
        return {
            "failure_code": reason,
            "completed_effects_do_not_repeat": completed_effects,
            "ordered_remaining_phases": phases,
            "rule": (
                "Do not include a phase whose completed_effects_do_not_repeat value is true; "
                "every selected step tool must be present in allowed_tools"
            ),
        }

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
