from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.types import ToolContext

ToolHandler = Callable[[Session, ToolContext, BaseModel], dict[str, Any] | list[Any]]
ToolPreflight = Callable[[Session, ToolContext, BaseModel], None]


@dataclass(frozen=True, slots=True)
class InteractionRequirement:
    """Declarative node capability required before a tool may execute."""

    target_argument: str | None = None
    interaction_key: str | None = None
    operation_argument: str | None = None
    operation_interactions: Mapping[str, str] = field(default_factory=dict)
    infer_unique_target: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_interactions",
            MappingProxyType(dict(self.operation_interactions)),
        )
        has_static = self.interaction_key is not None
        has_dynamic = self.operation_argument is not None or bool(self.operation_interactions)
        if has_static == has_dynamic:
            raise ValueError(
                "interaction requirement must declare exactly one static or dynamic interaction"
            )
        if has_dynamic and (self.operation_argument is None or not self.operation_interactions):
            raise ValueError(
                "dynamic interaction requirement needs an operation argument and mapping"
            )
        if self.infer_unique_target == (self.target_argument is not None):
            raise ValueError(
                "interaction requirement must use one target argument or unique-target inference"
            )


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: ToolHandler
    preflight: ToolPreflight | None = None
    interaction_requirement: InteractionRequirement | None = None
    write: bool = False
    allowed_roles: frozenset[str] = frozenset()
    require_permission_profile: bool = False
