from collections.abc import Mapping

from app.agent.types import ToolDefinition
from app.domain.world import AccessState
from app.tools.base import Tool
from app.tools.interaction_validation import interaction_target_guidance


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(
        self,
        scenario_key: str | None = None,
        *,
        target_states: Mapping[str, AccessState] | None = None,
    ) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=tool.name,
                description=self._description(tool, scenario_key, target_states),
                parameters=tool.arguments_model.model_json_schema(),
            )
            for tool in self._tools.values()
        ]

    @staticmethod
    def _description(
        tool: Tool,
        scenario_key: str | None,
        target_states: Mapping[str, AccessState] | None,
    ) -> str:
        if scenario_key is None or tool.interaction_requirement is None:
            return tool.description
        guidance = interaction_target_guidance(
            scenario_key,
            tool.interaction_requirement,
            target_states=target_states,
        )
        return f"{tool.description} {guidance}"
