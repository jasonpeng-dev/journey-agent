from __future__ import annotations

from typing import Any
from uuid import UUID

from app.domain.enums import StepExecutionType
from app.scenarios.contracts import ObjectiveScope, ScenarioRuntimeState
from app.scenarios.starfire.objective_catalog import StarfireObjectiveKey


def initial_strategic_starfire_plan(
    task_id: UUID,
    scope: ObjectiveScope,
) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "task_id": str(task_id),
        "objective_scope": _scope_payload(scope),
        "strategy_summary": (
            "沈策先核验领地资源与公开情报, 再由韩烈侦察并清剿山谷, "
            "山谷安全后交由陆宁修复前哨并进行北方商路通行测试。"
        ),
        "steps": [
            _tool_step(
                "沈策核验领地资源和星火前哨公开情报",
                "shen_ce",
                "ASSESS_COMMAND",
                "inspect_command_state",
                {},
                {"soldiers_total_min": 1},
            ),
            _tool_step(
                "韩烈在山谷入口发起谨慎侦察",
                "han_lie",
                "RECON_VALLEY",
                "start_recon_operation",
                {
                    "target_key": "northern_valley",
                    "troop_count": 60,
                    "approach": "CAUTIOUS",
                },
                {"status": "PENDING", "operation_type": "RECONNAISSANCE"},
                constraints={"max_troops": 80, "avoid_major_engagement": True},
            ),
            _world_wait(
                "韩烈等待侦察结果由游戏世界结算",
                "han_lie",
                source_step_sequence=2,
                success_outcomes=["PARTIAL_SUCCESS", "VICTORY"],
            ),
            _tool_step(
                "韩烈发起有限兵力行动清剿伏击谷",
                "han_lie",
                "CLEAR_VALLEY",
                "start_military_operation",
                {
                    "target_key": "northern_valley",
                    "troop_count": 180,
                    "mission_type": "CLEAR_VALLEY",
                    "strategy": "STANDARD",
                },
                {"status": "PENDING", "operation_type": "MILITARY"},
                constraints={"max_troops": 200, "avoid_total_mobilization": True},
            ),
            _world_wait(
                "韩烈等待伏击谷清剿行动结算",
                "han_lie",
                source_step_sequence=4,
                success_outcomes=["VICTORY"],
            ),
            _tool_step(
                "山谷安全后, 陆宁启动星火前哨临时修复",
                "lu_ning",
                "REPAIR_OUTPOST",
                "start_outpost_repair",
                {
                    "target_key": "starfire_outpost",
                    "repair_level": "TEMPORARY",
                    "food_commitment": 20,
                    "gold_commitment": 20,
                },
                {"status": "PENDING", "operation_type": "CONSTRUCTION"},
            ),
            _world_wait(
                "陆宁等待前哨建设完成",
                "lu_ning",
                source_step_sequence=6,
                success_outcomes=["COMPLETED"],
            ),
            _tool_step(
                "陆宁启动北方商路通行测试",
                "lu_ning",
                "TEST_TRADE_ROUTE",
                "start_trade_route_test",
                {"target_key": "northern_trade_route"},
                {"status": "PENDING", "operation_type": "TRADE_TEST"},
            ),
            _world_wait(
                "陆宁等待北方商路测试结算",
                "lu_ning",
                source_step_sequence=8,
                success_outcomes=["COMPLETED"],
            ),
            _scoped_report_step(scope),
        ],
        "idempotency_key": f"task-plan-{task_id}-v1",
    }
    stage = _scope_stage(scope)
    last_operation_step = {1: 3, 2: 5, 3: 7, 4: 9}[stage]
    plan["steps"] = [
        *plan["steps"][:last_operation_step],
        _scoped_report_step(scope),
    ]
    return plan


def recovery_strategic_starfire_plan(
    task_id: UUID,
    next_version: int,
    reason: str,
    scope: ObjectiveScope,
) -> dict[str, Any]:
    # A failed trade start means the military and construction suffix has
    # already succeeded. Repeating those operations would be unsafe and can
    # consume resources twice, so recover only the missing trade prerequisite.
    if reason == "TRADE_SUPPORT_REQUIRED":
        plan = {
            "task_id": str(task_id),
            "objective_scope": _scope_payload(scope),
            "strategy_summary": (
                "北方商路测试缺少村落支持。陆宁先以授权范围内的粮草换取向导, "
                "再重新测试商路并由沈策核验结果。"
            ),
            "replan_reason": reason,
            "steps": [
                _tool_step(
                    "陆宁以授权范围内的粮草换取村落向导支持",
                    "lu_ning",
                    "SECURE_TRADE_SUPPORT",
                    "negotiate_village_support",
                    {"food_offer": 20, "requested_support": "GUIDE"},
                    {"village_support": "GUIDE"},
                    constraints={"coercion_forbidden": True, "autonomous_food_limit": 30},
                ),
                _tool_step(
                    "陆宁在前哨和山谷条件满足后重新测试北方商路",
                    "lu_ning",
                    "TEST_TRADE_ROUTE",
                    "start_trade_route_test",
                    {"target_key": "northern_trade_route"},
                    {"status": "PENDING", "operation_type": "TRADE_TEST"},
                ),
                _world_wait(
                    "陆宁等待北方商路测试结算",
                    "lu_ning",
                    source_step_sequence=2,
                    success_outcomes=["COMPLETED"],
                ),
                _scoped_report_step(scope),
            ],
            "idempotency_key": f"task-replan-{task_id}-v{next_version}",
        }
        return _trim_recovery_plan(plan, scope)
    plan = {
        "task_id": str(task_id),
        "objective_scope": _scope_payload(scope),
        "strategy_summary": (
            "沈策根据新发现的敌军补给线调整方案: 先由陆宁争取村落向导, "
            "再命韩烈切断补给并重新清剿山谷, 最后将安全通道交由陆宁"
            "修复前哨并恢复商路。"
        ),
        "replan_reason": reason,
        "steps": [
            _tool_step(
                "陆宁在不使用强制手段的前提下, 用粮草换取村落向导支援",
                "lu_ning",
                "SECURE_VILLAGE_GUIDES",
                "negotiate_village_support",
                {"food_offer": 35, "requested_support": "GUIDE"},
                {"village_support": "GUIDE"},
                constraints={"coercion_forbidden": True, "autonomous_food_limit": 30},
            ),
            _tool_step(
                "韩烈发起有限兵力行动切断敌军补给线",
                "han_lie",
                "DISRUPT_ENEMY_SUPPLY",
                "start_military_operation",
                {
                    "target_key": "enemy_north_supply_route",
                    "troop_count": 100,
                    "mission_type": "DISRUPT_SUPPLY",
                    "strategy": "CAUTIOUS",
                },
                {"status": "PENDING", "operation_type": "MILITARY"},
            ),
            _world_wait(
                "韩烈等待补给线破袭行动结算",
                "han_lie",
                source_step_sequence=2,
                success_outcomes=["VICTORY"],
            ),
            _tool_step(
                "敌军补给被切断后, 韩烈再次清剿伏击谷",
                "han_lie",
                "CLEAR_VALLEY",
                "start_military_operation",
                {
                    "target_key": "northern_valley",
                    "troop_count": 160,
                    "mission_type": "CLEAR_VALLEY",
                    "strategy": "CAUTIOUS",
                },
                {"status": "PENDING", "operation_type": "MILITARY"},
            ),
            _world_wait(
                "韩烈等待游戏世界确认山谷安全",
                "han_lie",
                source_step_sequence=4,
                success_outcomes=["VICTORY"],
            ),
            _tool_step(
                "陆宁按资源限额启动星火前哨临时修复",
                "lu_ning",
                "REPAIR_OUTPOST",
                "start_outpost_repair",
                {
                    "target_key": "starfire_outpost",
                    "repair_level": "TEMPORARY",
                    "food_commitment": 20,
                    "gold_commitment": 20,
                },
                {"status": "PENDING", "operation_type": "CONSTRUCTION"},
            ),
            _world_wait(
                "陆宁等待前哨建设完成",
                "lu_ning",
                source_step_sequence=6,
                success_outcomes=["COMPLETED"],
            ),
            _tool_step(
                "陆宁启动经过前置条件核验的北方商路测试",
                "lu_ning",
                "TEST_TRADE_ROUTE",
                "start_trade_route_test",
                {"target_key": "northern_trade_route"},
                {"status": "PENDING", "operation_type": "TRADE_TEST"},
            ),
            _world_wait(
                "陆宁等待北方商路重新开放",
                "lu_ning",
                source_step_sequence=8,
                success_outcomes=["COMPLETED"],
            ),
            _scoped_report_step(scope),
        ],
        "idempotency_key": f"task-replan-{task_id}-v{next_version}",
    }
    return _trim_recovery_plan(plan, scope)


def state_aware_strategic_recovery_plan(
    task_id: UUID,
    next_version: int,
    reason: str,
    state: ScenarioRuntimeState,
    scope: ObjectiveScope,
) -> dict[str, Any]:
    """Safe fallback when a model cannot produce a valid recovery suffix."""
    steps: list[dict[str, Any]] = []
    support = state.fact_value("north_village", "village_support")
    stage = _scope_stage(scope)
    if stage >= 4 and support not in {"GUIDE", "SUPPLIES"}:
        steps.append(
            _tool_step(
                "陆宁以授权范围内的粮草换取村落向导支持",
                "lu_ning",
                "SECURE_VILLAGE_GUIDES",
                "negotiate_village_support",
                {"food_offer": 20, "requested_support": "GUIDE"},
                {"village_support": "GUIDE"},
                constraints={"coercion_forbidden": True, "autonomous_food_limit": 30},
            )
        )
    if (
        stage >= 2
        and reason == "ENCOUNTER_DEFEAT"
        and state.fact_value("enemy_north_supply_route", "supply_status") == "ACTIVE"
    ):
        steps.append(
            _tool_step(
                "韩烈先行切断敌军北方补给线",
                "han_lie",
                "DISRUPT_ENEMY_SUPPLY",
                "start_military_operation",
                {
                    "target_key": "enemy_north_supply_route",
                    "troop_count": 100,
                    "mission_type": "DISRUPT_SUPPLY",
                    "strategy": "CAUTIOUS",
                },
                {"status": "PENDING", "operation_type": "MILITARY"},
            )
        )
        steps.append(
            _world_wait(
                "韩烈等待敌军补给线破袭结果",
                "han_lie",
                source_step_sequence=len(steps),
                success_outcomes=["VICTORY"],
            )
        )
    if stage >= 2 and state.fact_value("northern_valley", "valley_security") != "SAFE":
        steps.append(
            _tool_step(
                "敌军补给受阻后韩烈再次谨慎清剿山谷",
                "han_lie",
                "CLEAR_VALLEY",
                "start_military_operation",
                {
                    "target_key": "northern_valley",
                    "troop_count": 160,
                    "mission_type": "CLEAR_VALLEY",
                    "strategy": "CAUTIOUS",
                },
                {"status": "PENDING", "operation_type": "MILITARY"},
            )
        )
        steps.append(
            _world_wait(
                "韩烈等待游戏世界确认山谷安全",
                "han_lie",
                source_step_sequence=len(steps),
                success_outcomes=["VICTORY"],
            )
        )
    if stage >= 3 and state.fact_value("starfire_outpost", "outpost_status") not in {
        "OPERATIONAL",
        "RESTORED",
    }:
        steps.append(
            _tool_step(
                "陆宁在山谷安全后启动星火前哨完整修复",
                "lu_ning",
                "REPAIR_OUTPOST",
                "start_outpost_repair",
                {
                    "target_key": "starfire_outpost",
                    "repair_level": "FULL",
                    "food_commitment": 30,
                    "gold_commitment": 30,
                },
                {"status": "PENDING", "operation_type": "CONSTRUCTION"},
            )
        )
        steps.append(
            _world_wait(
                "陆宁等待星火前哨建设完成",
                "lu_ning",
                source_step_sequence=len(steps),
                success_outcomes=["COMPLETED"],
            )
        )
    if stage >= 4 and state.fact_value("northern_trade_route", "trade_route_status") != "OPEN":
        steps.append(
            _tool_step(
                "陆宁重新测试北方商路通行状态",
                "lu_ning",
                "TEST_TRADE_ROUTE",
                "start_trade_route_test",
                {"target_key": "northern_trade_route"},
                {"status": "PENDING", "operation_type": "TRADE_TEST"},
            )
        )
        steps.append(
            _world_wait(
                "陆宁等待北方商路测试结算",
                "lu_ning",
                source_step_sequence=len(steps),
                success_outcomes=["COMPLETED"],
            )
        )
    steps.append(_scoped_report_step(scope))
    return {
        "task_id": str(task_id),
        "objective_scope": _scope_payload(scope),
        "strategy_summary": (
            "模型方案未通过安全校验, 系统依据已验证世界状态启用受约束恢复方案: "
            "仅补足尚未完成的阶段, 避免重复消耗和重复行动。"
        ),
        "replan_reason": reason,
        "steps": steps,
        "idempotency_key": f"task-replan-{task_id}-v{next_version}",
    }


def _tool_step(
    description: str,
    officer_key: str,
    intent: str,
    tool_name: str,
    arguments: dict[str, Any],
    expected: dict[str, Any],
    *,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "description": description,
        "execution_type": StepExecutionType.TOOL.value,
        "assigned_officer_key": officer_key,
        "action_intent": intent,
        "constraints": constraints or {},
        "allowed_tool_names": [tool_name],
        "selected_tool_name": tool_name,
        "tool_arguments": arguments,
        "expected_outcome": expected,
    }


def _world_wait(
    description: str,
    officer_key: str,
    *,
    source_step_sequence: int,
    success_outcomes: list[str],
) -> dict[str, Any]:
    return {
        "description": description,
        "execution_type": StepExecutionType.WAIT_FOR_WORLD_EVENT.value,
        "assigned_officer_key": officer_key,
        "action_intent": "WAIT_FOR_OPERATION",
        "constraints": {},
        "allowed_tool_names": [],
        "selected_tool_name": None,
        "tool_arguments": {},
        "expected_outcome": {"operation_result_in": success_outcomes},
        "resume_condition": {
            "type": "WORLD_OPERATION",
            "source_step_sequence": source_step_sequence,
            "success_outcomes": success_outcomes,
        },
    }


def _scoped_report_step(scope: ObjectiveScope) -> dict[str, Any]:
    return _tool_step(
        "沈策按照冻结的任务目标核验终局状态并向主公汇报",
        "shen_ce",
        "VERIFY_AND_REPORT",
        "inspect_command_state",
        {},
        _scope_expected_outcomes(scope),
        constraints={"world_facts_must_be_verified": True},
    )


class StarfireFallbackPlans:
    def supports_state_aware_recovery(self, reason: str) -> bool:
        return reason in {"ENCOUNTER_DEFEAT", "TRADE_SUPPORT_REQUIRED"}

    def initial(self, task_id: UUID, scope: ObjectiveScope) -> dict[str, Any]:
        return initial_strategic_starfire_plan(task_id, scope)

    def recovery(
        self,
        task_id: UUID,
        next_version: int,
        reason: str,
        scope: ObjectiveScope,
    ) -> dict[str, Any]:
        return recovery_strategic_starfire_plan(task_id, next_version, reason, scope)

    def state_aware_recovery(
        self,
        task_id: UUID,
        next_version: int,
        reason: str,
        state: ScenarioRuntimeState,
        scope: ObjectiveScope,
    ) -> dict[str, Any]:
        return state_aware_strategic_recovery_plan(task_id, next_version, reason, state, scope)


def _scope_payload(scope: ObjectiveScope) -> dict[str, object]:
    return {
        "scenario_key": scope.scenario_key,
        "catalog_version": scope.catalog_version,
        "objective_keys": list(scope.objective_keys),
    }


def _scope_stage(scope: ObjectiveScope) -> int:
    keys = set(scope.objective_keys)
    if keys.intersection(
        {
            StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE.value,
            StarfireObjectiveKey.FULL_NORTHERN_RECOVERY.value,
        }
    ):
        return 4
    if StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST.value in keys:
        return 3
    if StarfireObjectiveKey.SECURE_NORTHERN_VALLEY.value in keys:
        return 2
    return 1


def _trim_recovery_plan(plan: dict[str, Any], scope: ObjectiveScope) -> dict[str, Any]:
    stage = _scope_stage(scope)
    allowed = {
        "inspect_command_state",
        "start_recon_operation",
        "start_military_operation",
    }
    if stage >= 3:
        allowed.add("start_outpost_repair")
    if stage >= 4:
        allowed.add("negotiate_village_support")
        allowed.add("start_trade_route_test")
    steps = list(plan["steps"])
    kept: list[dict[str, Any]] = []
    included_sequences: set[int] = set()
    for sequence, step in enumerate(steps, start=1):
        tool_name = step.get("selected_tool_name")
        if step.get("execution_type") == StepExecutionType.TOOL.value:
            if tool_name not in allowed:
                continue
            kept.append(step)
            included_sequences.add(sequence)
            continue
        condition = step.get("resume_condition")
        source = condition.get("source_step_sequence") if isinstance(condition, dict) else None
        if source in included_sequences:
            copied = dict(step)
            copied_condition = dict(condition)
            copied_condition["source_step_sequence"] = len(kept)
            copied["resume_condition"] = copied_condition
            kept.append(copied)
    if not kept or kept[-1].get("action_intent") != "VERIFY_AND_REPORT":
        kept.append(_scoped_report_step(scope))
    plan["steps"] = kept
    return plan


def _scope_expected_outcomes(scope: ObjectiveScope) -> dict[str, str]:
    keys = set(scope.objective_keys)
    if StarfireObjectiveKey.FULL_NORTHERN_RECOVERY.value in keys:
        return {
            "valley_security": "SAFE",
            "starfire_outpost_status": "OPERATIONAL",
            "northern_trade_route_status": "OPEN",
        }
    expected: dict[str, str] = {}
    if StarfireObjectiveKey.GATHER_VALLEY_INTELLIGENCE.value in keys:
        expected["valley_intelligence"] = "PARTIAL"
    if StarfireObjectiveKey.SECURE_NORTHERN_VALLEY.value in keys:
        expected["valley_security"] = "SAFE"
    if StarfireObjectiveKey.RESTORE_STARFIRE_OUTPOST.value in keys:
        expected["starfire_outpost_status"] = "OPERATIONAL"
    if StarfireObjectiveKey.OPEN_NORTHERN_TRADE_ROUTE.value in keys:
        expected["northern_trade_route_status"] = "OPEN"
    return expected


STARFIRE_FALLBACK_PLANS = StarfireFallbackPlans()
