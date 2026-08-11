from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.types import ToolContext

ToolHandler = Callable[[Session, ToolContext, BaseModel], dict[str, Any] | list[Any]]
ToolPreflight = Callable[[Session, ToolContext, BaseModel], None]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: ToolHandler
    preflight: ToolPreflight | None = None
    write: bool = False
    allowed_roles: frozenset[str] = frozenset()
    require_permission_profile: bool = False
