from __future__ import annotations

from typing import Any
from uuid import UUID

from app.domain.enums import StepExecutionType


def initial_starfire_plan(task_id: UUID) -> dict[str, Any]:
    return {
        "task_id": str(task_id),
        "strategy_summary": (
            "Inspect live state, issue the road quest, open the approved route, wait for "
            "the player encounter, verify the result, restore the outpost, grant access, "
            "and record the relationship outcome."
        ),
        "steps": [
            tool_step(
                "Inspect player and Starfire prerequisite state",
                "inspect_task_requirements",
                {},
                {"player_level_min": 1},
            ),
            tool_step(
                "Issue the approved road-security quest",
                "create_quest",
                {
                    "template_key": "secure_starfire_road",
                    "difficulty": "NORMAL",
                    "narrative_title": "Secure the Broken Lantern Road",
                    "narrative_description": (
                        "Clear the raiders so Starfire Outpost can be restored."
                    ),
                },
                {"status": "AVAILABLE"},
            ),
            tool_step(
                "Open the quest-approved route to the encounter",
                "prepare_starfire_route",
                {},
                {"status": "AVAILABLE"},
            ),
            wait_step(
                "Wait for the player to complete the road encounter",
                "starfire_road_raiders",
            ),
            tool_step(
                "Verify that the road is safe after the encounter",
                "inspect_task_requirements",
                {},
                {"road_safe": True},
            ),
            tool_step(
                "Restore Starfire Outpost",
                "restore_outpost",
                {},
                {"outpost_operational": True},
            ),
            tool_step(
                "Grant the player access to Starfire Outpost",
                "grant_access",
                {},
                {"access_granted": True},
            ),
            tool_step(
                "Record Captain Aria's trust in the player",
                "update_relationship",
                {
                    "delta": 5,
                    "reason_code": "PLAYER_KEPT_PROMISE",
                    "evidence": "The player secured the road and restored the outpost.",
                },
                {"score_min": 5},
            ),
        ],
        "idempotency_key": f"task-plan-{task_id}-v1",
    }


def recovery_starfire_plan(task_id: UUID, next_version: int, reason: str) -> dict[str, Any]:
    return {
        "task_id": str(task_id),
        "strategy_summary": (
            "Recover from the failed direct encounter by obtaining Captain Aria's approved "
            "assistance, then wait for a retry and continue only after verified victory."
        ),
        "replan_reason": reason,
        "steps": [
            tool_step(
                "Inspect the failed attempt and current prerequisites",
                "inspect_task_requirements",
                {},
                {"player_level_min": 1},
            ),
            tool_step(
                "Request approved NPC assistance for the next attempt",
                "request_npc_assistance",
                {},
                {"assistance_active": True},
            ),
            wait_step(
                "Wait for the player to retry the road encounter with assistance",
                "starfire_road_raiders",
            ),
            tool_step(
                "Verify victory and road safety",
                "inspect_task_requirements",
                {},
                {"road_safe": True},
            ),
            tool_step(
                "Restore Starfire Outpost after verified victory",
                "restore_outpost",
                {},
                {"outpost_operational": True},
            ),
            tool_step(
                "Grant the player access to the restored outpost",
                "grant_access",
                {},
                {"access_granted": True},
            ),
            tool_step(
                "Record the successful recovery with Captain Aria",
                "update_relationship",
                {
                    "delta": 5,
                    "reason_code": "PLAYER_KEPT_PROMISE",
                    "evidence": ("The player adapted, secured the road, and restored the outpost."),
                },
                {"score_min": 5},
            ),
        ],
        "idempotency_key": f"task-replan-{task_id}-v{next_version}",
    }


def tool_step(
    description: str,
    tool_name: str,
    arguments: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, Any]:
    return {
        "description": description,
        "execution_type": StepExecutionType.TOOL.value,
        "selected_tool_name": tool_name,
        "tool_arguments": arguments,
        "expected_outcome": expected,
    }


def wait_step(description: str, encounter_key: str) -> dict[str, Any]:
    return {
        "description": description,
        "execution_type": StepExecutionType.WAIT_FOR_USER.value,
        "selected_tool_name": None,
        "tool_arguments": {},
        "expected_outcome": {"encounter_result": "VICTORY"},
        "resume_condition": {
            "type": "ENCOUNTER_RESULT",
            "encounter_key": encounter_key,
            "required_result": "VICTORY",
        },
    }
