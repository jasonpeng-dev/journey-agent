"""HTTP DTOs for the Scenario portability adapters.

The artifact itself remains an opaque JSON object at this boundary.  The
portable codec is the authority for its strict envelope, size limits and V3
definition validation; these DTOs only describe request shape and concurrency
inputs.
"""

from __future__ import annotations

from typing import Any

from app.api.schemas.phase_d import ApiModel


class ScenarioArtifactRequest(ApiModel):
    artifact: dict[str, Any]
    target_scenario_key: str | None = None


__all__ = [
    "ScenarioArtifactRequest",
]
