from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class TaskRoute:
    mode: Literal["CONVERSATION", "STRUCTURED_TASK"]
    reason_code: str
    scenario_key: str | None = None


class TaskRouter:
    """Deterministic safety-first routing for currently supported long-running goals."""

    _starfire_markers = (
        "starfire",
        "outpost",
        "broken lantern road",
        "星火",
        "前哨",
        "断灯路",
    )
    _goal_markers = (
        "help me",
        "restore",
        "recover",
        "secure",
        "obtain access",
        "gain access",
        "make safe",
        "帮我",
        "帮助我",
        "恢复",
        "重建",
        "确保安全",
        "获得通行",
        "取得通行",
    )
    _explicit_tool_markers = ("use ", "call ", "invoke ", "调用工具", "使用工具")

    def route(self, user_content: str) -> TaskRoute:
        normalized = " ".join(user_content.casefold().split())
        if any(marker in normalized for marker in self._explicit_tool_markers):
            return TaskRoute("CONVERSATION", "EXPLICIT_TOOL_REQUEST")
        strategic_location_markers = (
            "northern trade route",
            "北方商路",
            "商路",
        )
        strategic_command_markers = (
            "reopen",
            "restore",
            "open the trade route",
            "secure the trade route",
            "重新开放",
            "恢复",
            "重开",
            "打通",
            "重新开放商路",
            "恢复商路",
            "重开商路",
            "打通商路",
        )
        if any(marker in normalized for marker in strategic_location_markers) and any(
            marker in normalized for marker in strategic_command_markers
        ):
            return TaskRoute(
                "STRUCTURED_TASK",
                "STRATEGIC_OFFICER_COMMAND",
                "starfire_command",
            )
        if any(marker in normalized for marker in self._starfire_markers) and any(
            marker in normalized for marker in self._goal_markers
        ):
            return TaskRoute(
                "STRUCTURED_TASK",
                "KNOWN_MULTI_STEP_GOAL",
                "starfire_outpost",
            )
        return TaskRoute("CONVERSATION", "NO_SUPPORTED_COMPLEX_GOAL")
