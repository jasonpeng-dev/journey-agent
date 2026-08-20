from enum import StrEnum


class PlayerStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class GameInstanceStatus(StrEnum):
    PENDING_INITIALIZATION = "PENDING_INITIALIZATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class NPCRole(StrEnum):
    STRATEGIST = "STRATEGIST"
    GENERAL = "GENERAL"
    STEWARD = "STEWARD"


class NodeType(StrEnum):
    START = "START"
    NPC = "NPC"
    EVENT = "EVENT"
    ENCOUNTER = "ENCOUNTER"


class NodeStatus(StrEnum):
    LOCKED = "LOCKED"
    AVAILABLE = "AVAILABLE"
    ENTERED = "ENTERED"
    COMPLETED = "COMPLETED"


class CommandReachability(StrEnum):
    """Whether an Actor can currently receive ordinary commands."""

    ONLINE = "ONLINE"
    DISCONNECTED = "DISCONNECTED"


class ResourceInventoryVisibility(StrEnum):
    """Whether ordinary inventory information for a Region is known."""

    HIDDEN = "HIDDEN"
    VISIBLE = "VISIBLE"


class ResourcePoolVisibility(StrEnum):
    """Whether a concrete Resource Pool is known to the player/Planner."""

    HIDDEN = "HIDDEN"
    VISIBLE = "VISIBLE"


class ResourcePoolAvailability(StrEnum):
    """Whether a known Resource Pool can currently be consumed."""

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MemoryType(StrEnum):
    WORLD_EVENT = "WORLD_EVENT"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TerminationReason(StrEnum):
    FINAL_RESPONSE = "FINAL_RESPONSE"
    MAX_ROUNDS = "MAX_ROUNDS"
    TOOL_LIMIT = "TOOL_LIMIT"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    REPEATED_INVALID_TOOL_CALL = "REPEATED_INVALID_TOOL_CALL"
    SECURITY_REJECTION = "SECURITY_REJECTION"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AgentTaskStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REQUIRES_PLAYER_DECISION = "REQUIRES_PLAYER_DECISION"
    WAITING_FOR_PLAYER_ACTION = "WAITING_FOR_PLAYER_ACTION"
    WAITING_FOR_WORLD_EVENT = "WAITING_FOR_WORLD_EVENT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    ABORTED = "ABORTED"


class AgentPlanStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AgentStepStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    REQUIRES_PLAYER_DECISION = "REQUIRES_PLAYER_DECISION"
    WAITING_FOR_PLAYER_ACTION = "WAITING_FOR_PLAYER_ACTION"
    WAITING_FOR_WORLD_EVENT = "WAITING_FOR_WORLD_EVENT"
    SKIPPED = "SKIPPED"


class StepExecutionType(StrEnum):
    TOOL = "TOOL"
    WAIT_FOR_PLAYER_ACTION = "WAIT_FOR_PLAYER_ACTION"
    WAIT_FOR_WORLD_EVENT = "WAIT_FOR_WORLD_EVENT"


class AuthorityOutcome(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_PLAYER_DECISION = "REQUIRE_PLAYER_DECISION"
    DENY = "DENY"


class DecisionStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CONSUMED = "CONSUMED"
    CANCELLED = "CANCELLED"


class WorldOperationStatus(StrEnum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"
