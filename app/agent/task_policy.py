from __future__ import annotations

TASK_EXECUTION_TOOLS = frozenset(
    {
        "inspect_command_state",
        "start_recon_operation",
        "start_military_operation",
        "negotiate_village_support",
        "start_outpost_repair",
        "start_trade_route_test",
    }
)

STRATEGIC_TASK_EXECUTION_TOOLS = TASK_EXECUTION_TOOLS

IDEMPOTENT_TASK_TOOLS = frozenset(
    {
        "start_recon_operation",
        "start_military_operation",
        "negotiate_village_support",
        "start_outpost_repair",
        "start_trade_route_test",
    }
)

SECURITY_FAILURES = frozenset(
    {
        "NPC_PERMISSION_DENIED",
        "STEP_TOOL_MISMATCH",
        "STEP_ARGUMENT_MISMATCH",
        "TASK_CONTEXT_MISMATCH",
        "TASK_PLAYER_MISMATCH",
        "TASK_NPC_MISMATCH",
        "STEP_ACTOR_MISMATCH",
        "OFFICER_NOT_APPOINTED",
        "AUTHORITY_POLICY_INVALID",
        "IDEMPOTENCY_KEY_REUSED",
        "WORLD_OPERATION_CONTRACT_VIOLATION",
        "SECURITY_FAILURE_NOT_REPLANNABLE",
    }
)

PLAN_SECURITY_FAILURES = frozenset(
    {
        "PLAN_TASK_MISMATCH",
        "PLAN_NPC_UNAVAILABLE",
        "PLAN_TOOL_UNAUTHORIZED",
        "PLAN_OFFICER_NOT_APPOINTED",
        "PLAN_AUTHORITY_POLICY_INVALID",
    }
)

RECOVERABLE_FAILURES = frozenset(
    {
        "EXPECTED_OUTCOME_NOT_MET",
        "STATE_VERSION_CONFLICT",
        # A new plan can acquire the missing prerequisite or choose a
        # lower-cost action. These are business-state failures, not
        # authorization or schema violations.
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

REPLAN_GUIDANCE: dict[str, str] = {
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

PLAN_SOURCES = frozenset(
    {
        "DETERMINISTIC_RECOVERY_FALLBACK",
        "MOCK_PLANNER",
        "MODEL_PLANNER",
        "MANUAL",
    }
)

PLANNING_MODES = frozenset({"PROVIDER"})

EXPECTED_OUTCOME_FIELDS: dict[str, frozenset[str]] = {
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

FIXED_TOOL_EXPECTED_OUTCOMES: dict[str, dict[str, str]] = {
    "start_recon_operation": {
        "operation_type": "RECONNAISSANCE",
        "status": "PENDING",
    },
    "start_military_operation": {
        "operation_type": "MILITARY",
        "status": "PENDING",
    },
    "start_outpost_repair": {
        "operation_type": "CONSTRUCTION",
        "status": "PENDING",
    },
    "start_trade_route_test": {
        "operation_type": "TRADE_TEST",
        "status": "PENDING",
    },
}

WORLD_OPERATION_SUCCESS_OUTCOMES: dict[str, tuple[str, ...]] = {
    "start_recon_operation": ("PARTIAL_SUCCESS", "VICTORY"),
    "start_military_operation": ("VICTORY",),
    "start_outpost_repair": ("COMPLETED",),
    "start_trade_route_test": ("COMPLETED",),
}
