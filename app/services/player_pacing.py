"""Player presentation pacing for Formal Play.

This state is an application-product checkpoint.  It is intentionally not an
AgentTask status and has no authority or gameplay meaning.
"""

from __future__ import annotations

from enum import StrEnum


class PlayerExecutionPhase(StrEnum):
    AWAITING_ACTION_ACK = "AWAITING_ACTION_ACK"
    AWAITING_DEBRIEF_ACK = "AWAITING_DEBRIEF_ACK"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    ABORTED = "ABORTED"


__all__ = ["PlayerExecutionPhase"]
