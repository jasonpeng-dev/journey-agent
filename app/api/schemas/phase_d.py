"""Phase D HTTP request/response DTOs.

These models describe the browser-product boundary.  They deliberately contain
no persistence or gameplay behavior: application services remain responsible
for transactions, validation, versioning, and Runtime mutations.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class ScenarioCreateMode(StrEnum):
    BLANK = "BLANK"
    CLONE_VERSION = "CLONE_VERSION"
    EXAMPLE = "EXAMPLE"


class ValidationSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class ReadinessLevel(StrEnum):
    STRUCTURALLY_VALID = "STRUCTURALLY_VALID"
    MINIMUM_RUNNABLE = "MINIMUM_RUNNABLE"
    MINIMUM_PLAYABLE = "MINIMUM_PLAYABLE"
    PUBLISH_READY = "PUBLISH_READY"


class PublicGameStatus(StrEnum):
    PENDING_INITIALIZATION = "PENDING_INITIALIZATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class GoalSubmissionStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    UNSUPPORTED = "UNSUPPORTED"


class PublicTaskStatus(StrEnum):
    ACTIVE = "ACTIVE"
    NEEDS_PLAYER_INPUT = "NEEDS_PLAYER_INPUT"
    BLOCKED_BY_PLAYER_DECISION = "BLOCKED_BY_PLAYER_DECISION"
    UNREACHABLE_IN_CURRENT_STATE = "UNREACHABLE_IN_CURRENT_STATE"
    MODEL_PLAN_REJECTED = "MODEL_PLAN_REJECTED"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


class PublicStepStatus(StrEnum):
    PENDING = "PENDING"
    CURRENT = "CURRENT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class PublicPlanHistoryStatus(StrEnum):
    EXECUTING = "EXECUTING"
    ADJUSTED = "ADJUSTED"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class PublicPlanHistoryStepStatus(StrEnum):
    PLANNED = "PLANNED"
    CURRENT = "CURRENT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PublicPlanInterruptionKind(StrEnum):
    FAILURE = "FAILURE"
    KNOWLEDGE_CONFLICT = "KNOWLEDGE_CONFLICT"


class MissionRoadmapStageStatus(StrEnum):
    COMPLETED = "COMPLETED"
    CURRENT = "CURRENT"
    PENDING = "PENDING"


class PublicExecutionPhase(StrEnum):
    AWAITING_PLAN_START = "AWAITING_PLAN_START"
    AWAITING_ACTION_ACK = "AWAITING_ACTION_ACK"
    AWAITING_DEBRIEF_ACK = "AWAITING_DEBRIEF_ACK"
    AWAITING_REPLAN_ACK = "AWAITING_REPLAN_ACK"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    ABORTED = "ABORTED"


class PublicTimelineEventKind(StrEnum):
    GOAL_ACCEPTED = "GOAL_ACCEPTED"
    PLAN_CREATED = "PLAN_CREATED"
    TASK_STARTED = "TASK_STARTED"
    ACTION_BRIEFING = "ACTION_BRIEFING"
    ACTION_RESULT = "ACTION_RESULT"
    PLAN_UPDATED = "PLAN_UPDATED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_APPROVED = "APPROVAL_APPROVED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_BLOCKED = "TASK_BLOCKED"
    TASK_ABORTED = "TASK_ABORTED"


class ScenarioCreateRequest(ApiModel):
    mode: ScenarioCreateMode
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")
    name: str = Field(min_length=1, max_length=160)
    source_version_id: UUID | None = None
    example_key: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_source(self) -> ScenarioCreateRequest:
        if self.mode == ScenarioCreateMode.BLANK:
            if self.source_version_id is not None or self.example_key is not None:
                raise ValueError("BLANK creation cannot define a source")
        elif self.mode == ScenarioCreateMode.CLONE_VERSION:
            if self.source_version_id is None or self.example_key is not None:
                raise ValueError("CLONE_VERSION requires only source_version_id")
        elif self.example_key is None or self.source_version_id is not None:
            raise ValueError("EXAMPLE creation requires only example_key")
        return self


class ScenarioSummaryResponse(ApiModel):
    id: UUID
    key: str
    name: str
    status: ScenarioStatus
    draft_revision: int = Field(ge=1)
    current_published_version_id: UUID | None
    current_published_version_number: int | None = Field(default=None, ge=1)
    created_at: datetime
    updated_at: datetime


class ScenarioDetailResponse(ScenarioSummaryResponse):
    version_count: int = Field(ge=0)


class DraftResponse(ApiModel):
    scenario_id: UUID
    revision: int = Field(ge=1)
    definition_document: dict[str, Any]
    validation_status: str
    validation_issues: list[ValidationIssueResponse] = Field(default_factory=list)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    base_scenario_version_id: UUID | None
    updated_at: datetime


class DraftReplaceRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    definition_document: dict[str, Any]


class DraftRevisionRequest(ApiModel):
    expected_revision: int = Field(ge=1)


class DraftPublishRequest(DraftRevisionRequest):
    expected_content_hash: str | None = Field(default=None, min_length=64, max_length=64)


class DraftRestoreRequest(DraftRevisionRequest):
    version_id: UUID


class DraftRenameKeyRequest(DraftRevisionRequest):
    object_kind: str = Field(min_length=1, max_length=80)
    old_key: str = Field(min_length=1, max_length=100)
    new_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")


class DraftDeleteObjectRequest(DraftRevisionRequest):
    object_kind: str = Field(min_length=1, max_length=80)
    object_key: str = Field(min_length=1, max_length=100)


class ObjectLocator(ApiModel):
    object_kind: str
    object_key: str | None = None
    field_path: str | None = None


class ValidationIssueResponse(ApiModel):
    severity: ValidationSeverity
    code: str
    path: str
    message: str
    locator: ObjectLocator | None = None


class ReadinessCheckResponse(ApiModel):
    level: ReadinessLevel
    passed: bool
    issue_codes: list[str] = Field(default_factory=list)


class DraftValidationResponse(ApiModel):
    scenario_id: UUID
    revision: int = Field(ge=1)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    issues: list[ValidationIssueResponse]
    readiness: list[ReadinessCheckResponse]
    publish_ready: bool


class DraftSandboxRequest(DraftRevisionRequest):
    goal: str | None = Field(default=None, min_length=1, max_length=4000)


class ScenarioVersionSummaryResponse(ApiModel):
    id: UUID
    scenario_id: UUID
    version_number: int = Field(ge=1)
    schema_version: Literal[2]
    content_hash: str = Field(min_length=64, max_length=64)
    published_at: datetime


class ScenarioVersionDetailResponse(ScenarioVersionSummaryResponse):
    definition_document: dict[str, Any]


class ScenarioPublishResponse(ApiModel):
    scenario: ScenarioSummaryResponse
    version: ScenarioVersionSummaryResponse


class ScenarioExampleResponse(ApiModel):
    key: str
    name: str
    description: str
    maturity: ReadinessLevel


class ReferenceEdgeResponse(ApiModel):
    source: ObjectLocator
    target: ObjectLocator


class ReferenceIndexResponse(ApiModel):
    scenario_id: UUID
    revision: int = Field(ge=1)
    references: list[ReferenceEdgeResponse]


class NewGameRequest(ApiModel):
    scenario_version_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=160)


class GameSummaryResponse(ApiModel):
    id: UUID
    scenario_id: UUID
    scenario_version_id: UUID
    scenario_version_number: int = Field(ge=1)
    scenario_content_hash: str = Field(min_length=64, max_length=64)
    status: PublicGameStatus
    active_task_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class GoalSubmissionRequest(ApiModel):
    goal: str = Field(min_length=1, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=160)


class PublicActionLocationResponse(ApiModel):
    """One player-safe location projection shared by all action views."""

    kind: str
    summary: str
    detail: str | None = None


class PublicPlanStepResponse(ApiModel):
    id: UUID
    sequence: int = Field(ge=1)
    description: str
    assigned_actor_name: str
    status: PublicStepStatus
    result_summary: str | None = None
    location: PublicActionLocationResponse | None = None


class PublicPlanResponse(ApiModel):
    strategy_summary: str
    updated: bool = False
    steps: list[PublicPlanStepResponse]


class PublicPlanHistoryStepResponse(ApiModel):
    id: UUID
    sequence: int = Field(ge=1)
    action_name: str
    assigned_actor_name: str
    status: PublicPlanHistoryStepStatus
    result_summary: str | None = None
    location: PublicActionLocationResponse | None = None


class PublicPlanInterruptionResponse(ApiModel):
    kind: PublicPlanInterruptionKind
    step_id: UUID
    sequence: int = Field(ge=1)
    step_name: str


class PublicPlanHistoryResponse(ApiModel):
    id: UUID
    ordinal: int = Field(ge=1)
    status: PublicPlanHistoryStatus
    completed_steps: int = Field(ge=0)
    total_steps: int = Field(ge=0)
    failed_step_name: str | None = None
    interruption: PublicPlanInterruptionResponse | None = None
    steps: list[PublicPlanHistoryStepResponse]


class MissionRoadmapStageResponse(ApiModel):
    key: str
    name: str
    description: str
    status: MissionRoadmapStageStatus
    objective_key: str | None = None


class MissionRoadmapResponse(ApiModel):
    stages: list[MissionRoadmapStageResponse]


class PublicKnowledgeChangeResponse(ApiModel):
    kind: Literal["NODE_REVEALED", "FACT_REVEALED", "RESOURCE_DISCOVERED"]
    key: str
    name: str
    value: str | int | bool | None = None


class PublicActionBriefingResponse(ApiModel):
    step_id: UUID
    action_name: str
    actor_name: str
    target_name: str
    purpose: str
    location: PublicActionLocationResponse | None = None


class PublicActionDebriefResponse(ApiModel):
    step_id: UUID
    action_name: str
    success: bool
    result_summary: str
    knowledge_changes: list[PublicKnowledgeChangeResponse] = Field(default_factory=list)
    plan_adjusted: bool = False
    plan_adjustment_summary: str | None = None
    plan_invalidated: bool = False
    plan_invalidation_reason: str | None = None
    location: PublicActionLocationResponse | None = None


class PublicTimelineEventResponse(ApiModel):
    id: str
    kind: PublicTimelineEventKind
    title: str
    detail: str | None = None
    actor_name: str | None = None
    result_summary: str | None = None
    success: bool | None = None
    knowledge_changes: list[PublicKnowledgeChangeResponse] = Field(default_factory=list)
    occurred_at: datetime | None = None
    location: PublicActionLocationResponse | None = None
    # A persisted operation snapshot, never a live client timer.  This is
    # derived from the task's provider/application audit metadata so it stays
    # stable after a page refresh.
    duration_ms: int | None = Field(default=None, ge=0)


class PublicTaskResponse(ApiModel):
    id: UUID
    version: int = Field(ge=1)
    goal: str
    status: PublicTaskStatus
    execution_phase: PublicExecutionPhase
    pacing_version: int = Field(ge=1)
    objective_names: list[str]
    roadmap: MissionRoadmapResponse
    plan: PublicPlanResponse | None = None
    plan_history: list[PublicPlanHistoryResponse] = Field(default_factory=list)
    timeline: list[PublicTimelineEventResponse] = Field(default_factory=list)
    briefing: PublicActionBriefingResponse | None = None
    debrief: PublicActionDebriefResponse | None = None
    explanation: str | None = None


class PublicTaskSummaryResponse(ApiModel):
    """Compact player-safe entry used to switch between task histories."""

    id: UUID
    sequence: int = Field(ge=1)
    goal: str
    objective_names: list[str]
    status: PublicTaskStatus
    execution_phase: PublicExecutionPhase
    created_at: datetime
    completed_at: datetime | None = None


class GoalSubmissionResponse(ApiModel):
    status: GoalSubmissionStatus
    task: PublicTaskResponse | None = None
    clarification_prompt: str | None = None
    candidate_objective_names: list[str] = Field(default_factory=list)
    explanation: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> GoalSubmissionResponse:
        if self.status == GoalSubmissionStatus.ACCEPTED and self.task is None:
            raise ValueError("ACCEPTED goal submission requires a Task")
        if self.status != GoalSubmissionStatus.ACCEPTED and self.task is not None:
            raise ValueError("Unaccepted goal submission cannot expose a Task")
        return self


class PublicFactResponse(ApiModel):
    node_key: str
    fact_key: str
    name: str
    value: str | int | bool
    node_name: str | None = None
    node_type_key: str | None = None
    region_key: str | None = None
    region_name: str | None = None
    endpoint_region_keys: list[str] = Field(default_factory=list)
    endpoint_region_names: list[str] = Field(default_factory=list)


class PublicNodeResponse(ApiModel):
    key: str
    name: str
    accessible: bool
    node_type_key: str | None = None
    region_key: str | None = None
    region_name: str | None = None
    endpoint_region_keys: list[str] = Field(default_factory=list)
    endpoint_region_names: list[str] = Field(default_factory=list)
    associated_known_resources: list[dict[str, Any]] = Field(default_factory=list)


class PublicRelationResponse(ApiModel):
    source_node_key: str
    relation_type_key: str
    target_node_key: str
    source_node_name: str | None = None
    target_node_name: str | None = None


class PublicActionRequirementResponse(ApiModel):
    action_key: str
    action_name: str
    required_actor_role_key: str | None = None
    required_actor_role_name: str | None = None
    source_relation_type_key: str | None = None
    known_preconditions: list[dict[str, Any]] = Field(default_factory=list)


class PublicResourceResponse(ApiModel):
    key: str
    name: str
    value: int
    reserved_value: int
    pool_key: str = "default"
    facility_key: str | None = None
    availability: Literal["AVAILABLE", "UNAVAILABLE"] = "AVAILABLE"
    availability_requirement: dict[str, Any] | None = None
    availability_requirement_status: Literal["KNOWN", "UNKNOWN"] | None = None
    scope_node_key: str | None = None
    scope_node_name: str | None = None
    scope_region_key: str | None = None
    scope_region_name: str | None = None


class PublicActorResponse(ApiModel):
    key: str
    name: str
    role_name: str
    current_node_name: str
    command_reachability: Literal["ONLINE", "DISCONNECTED"] = "ONLINE"


class PlayerGameStateResponse(ApiModel):
    game: GameSummaryResponse
    visible_nodes: list[PublicNodeResponse]
    known_facts: list[PublicFactResponse]
    known_relations: list[PublicRelationResponse] = Field(default_factory=list)
    known_action_requirements: list[PublicActionRequirementResponse] = Field(default_factory=list)
    resources: list[PublicResourceResponse]
    resource_intelligence: dict[str, Any] = Field(default_factory=dict)
    actors: list[PublicActorResponse] = Field(default_factory=list)
    current_task: PublicTaskResponse | None
    task_history: list[PublicTaskSummaryResponse] = Field(default_factory=list)
    pending_approval_id: UUID | None = None


class DraftSandboxResponse(ApiModel):
    scenario_id: UUID
    revision: int = Field(ge=1)
    sandbox_started: bool
    issues: list[ValidationIssueResponse]
    goal_status: str | None = None
    task: PublicTaskResponse | None = None
    visible_nodes: list[PublicNodeResponse] = Field(default_factory=list)
    known_facts: list[PublicFactResponse] = Field(default_factory=list)
    resources: list[PublicResourceResponse] = Field(default_factory=list)
    resource_intelligence: dict[str, Any] = Field(default_factory=dict)


class DeveloperGameSnapshotResponse(ApiModel):
    """Internal response intentionally separate from PlayerGameStateResponse."""

    game: GameSummaryResponse
    truth: dict[str, Any]
    knowledge: dict[str, Any]
    actors: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    plans: list[dict[str, Any]]
    operations: list[dict[str, Any]]
    rule_outcomes: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    memory: list[dict[str, Any]]
    history: list[dict[str, Any]]


class ApprovalDecisionRequest(ApiModel):
    expected_task_version: int = Field(ge=1)


class PlayerPacingRequest(ApiModel):
    expected_pacing_version: int = Field(ge=1)


__all__ = [
    "ApprovalDecisionRequest",
    "DeveloperGameSnapshotResponse",
    "DraftDeleteObjectRequest",
    "DraftPublishRequest",
    "DraftRenameKeyRequest",
    "DraftReplaceRequest",
    "DraftResponse",
    "DraftRestoreRequest",
    "DraftRevisionRequest",
    "DraftSandboxRequest",
    "DraftSandboxResponse",
    "DraftValidationResponse",
    "GameSummaryResponse",
    "GoalSubmissionRequest",
    "GoalSubmissionResponse",
    "GoalSubmissionStatus",
    "NewGameRequest",
    "ObjectLocator",
    "PlayerGameStateResponse",
    "PlayerPacingRequest",
    "PublicActionBriefingResponse",
    "PublicActionDebriefResponse",
    "PublicActionRequirementResponse",
    "PublicExecutionPhase",
    "PublicGameStatus",
    "PublicPlanResponse",
    "PublicPlanStepResponse",
    "PublicStepStatus",
    "PublicTaskResponse",
    "PublicTaskStatus",
    "PublicTaskSummaryResponse",
    "ReadinessCheckResponse",
    "ReadinessLevel",
    "ReferenceEdgeResponse",
    "ReferenceIndexResponse",
    "ScenarioCreateMode",
    "ScenarioCreateRequest",
    "ScenarioDetailResponse",
    "ScenarioExampleResponse",
    "ScenarioPublishResponse",
    "ScenarioStatus",
    "ScenarioSummaryResponse",
    "ScenarioVersionDetailResponse",
    "ScenarioVersionSummaryResponse",
    "ValidationIssueResponse",
    "ValidationSeverity",
]
