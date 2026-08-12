from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    DecisionStatus,
    MemoryType,
    MessageRole,
    NodeStatus,
    NodeType,
    NPCRole,
    PlayerStatus,
    RunStatus,
    SessionStatus,
    StepExecutionType,
    TerminationReason,
    WorldOperationStatus,
)
from app.domain.world import Visibility
from app.infrastructure.db.base import Base, TimestampMixin, UUIDPrimaryKey, utcnow


class Scenario(UUIDPrimaryKey, TimestampMixin, Base):
    """Stable identity and lifecycle metadata for an authored Scenario."""

    __tablename__ = "scenarios"

    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, default=1)
    current_published_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "scenario_versions.id",
            name="fk_scenarios_current_published_version",
            ondelete="SET NULL",
            use_alter=True,
        )
    )


class ScenarioDraft(TimestampMixin, Base):
    """Mutable authoring document; publication behavior is added in Phase C2."""

    __tablename__ = "scenario_drafts"

    scenario_id: Mapped[UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    definition_document: Mapped[dict[str, Any]] = mapped_column(JSON)
    validation_status: Mapped[str] = mapped_column(String(30), default="UNVALIDATED")
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    base_scenario_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scenario_versions.id", ondelete="SET NULL")
    )


class ScenarioVersion(UUIDPrimaryKey, Base):
    """Published Scenario snapshot storage; immutability is enforced in Phase C3."""

    __tablename__ = "scenario_versions"
    __table_args__ = (
        UniqueConstraint("scenario_id", "version_number"),
        Index("ix_scenario_versions_scenario_number", "scenario_id", "version_number"),
    )

    scenario_id: Mapped[UUID] = mapped_column(ForeignKey("scenarios.id", ondelete="CASCADE"))
    version_number: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer)
    snapshot_document: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    behavior_bundle_key: Mapped[str] = mapped_column(String(100))
    behavior_bundle_version: Mapped[str] = mapped_column(String(100))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScenarioVersionImmutableError(RuntimeError):
    """Raised when ORM code attempts to mutate a published ScenarioVersion."""


@event.listens_for(ScenarioVersion, "before_update")
@event.listens_for(ScenarioVersion, "before_delete")
def _reject_scenario_version_mutation(*_args: object) -> None:
    raise ScenarioVersionImmutableError("Published ScenarioVersion rows are immutable")


class World(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "worlds"

    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    chapter: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)


class WorldNode(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "world_nodes"
    __table_args__ = (UniqueConstraint("world_id", "key"),)

    world_id: Mapped[UUID] = mapped_column(ForeignKey("worlds.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    type: Mapped[NodeType] = mapped_column(Enum(NodeType, native_enum=False))
    default_status: Mapped[NodeStatus] = mapped_column(Enum(NodeStatus, native_enum=False))
    version: Mapped[int] = mapped_column(Integer, default=1)


class Player(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "players"
    __table_args__ = (
        CheckConstraint("level >= 1", name="ck_player_level"),
        CheckConstraint("gold >= 0", name="ck_player_gold"),
    )

    name: Mapped[str] = mapped_column(String(100))
    level: Mapped[int] = mapped_column(Integer, default=1)
    gold: Mapped[int] = mapped_column(Integer, default=0)
    current_node_id: Mapped[UUID | None] = mapped_column(ForeignKey("world_nodes.id"))
    status: Mapped[PlayerStatus] = mapped_column(
        Enum(PlayerStatus, native_enum=False), default=PlayerStatus.ACTIVE
    )
    version: Mapped[int] = mapped_column(Integer, default=1)


class PlayerNodeState(TimestampMixin, Base):
    __tablename__ = "player_node_states"

    player_id: Mapped[UUID] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[UUID] = mapped_column(
        ForeignKey("world_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[NodeStatus] = mapped_column(Enum(NodeStatus, native_enum=False))
    visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility, native_enum=False), default=Visibility.KNOWN
    )
    version: Mapped[int] = mapped_column(Integer, default=1)


class NPC(UUIDPrimaryKey, Base):
    __tablename__ = "npcs"

    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    persona: Mapped[str] = mapped_column(Text)
    doctrine: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    authority_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    current_node_id: Mapped[UUID] = mapped_column(ForeignKey("world_nodes.id"))
    role: Mapped[NPCRole] = mapped_column(Enum(NPCRole, native_enum=False))
    permission_profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class OfficerAppointment(TimestampMixin, Base):
    """Explicitly records that an NPC officer serves a player's domain."""

    __tablename__ = "officer_appointments"

    player_id: Mapped[UUID] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    npc_id: Mapped[UUID] = mapped_column(
        ForeignKey("npcs.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")
    authority_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)


class PlayerDomainState(TimestampMixin, Base):
    """Small, deterministic resource ledger for the strategic demo."""

    __tablename__ = "player_domain_states"
    __table_args__ = (
        CheckConstraint("soldiers_total >= 0", name="ck_domain_soldiers_total"),
        CheckConstraint(
            "soldiers_committed >= 0 AND soldiers_committed <= soldiers_total",
            name="ck_domain_soldiers_committed",
        ),
        CheckConstraint("food >= 0", name="ck_domain_food"),
        CheckConstraint("morale >= 0 AND morale <= 100", name="ck_domain_morale"),
    )

    player_id: Mapped[UUID] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    soldiers_total: Mapped[int] = mapped_column(Integer, default=300)
    soldiers_committed: Mapped[int] = mapped_column(Integer, default=0)
    food: Mapped[int] = mapped_column(Integer, default=100)
    morale: Mapped[int] = mapped_column(Integer, default=60)
    version: Mapped[int] = mapped_column(Integer, default=1)


class PlayerWorldFact(TimestampMixin, Base):
    """Legacy flat, player-visible compatibility projection.

    Canonical truth and fact visibility live in ``PlayerWorldFactState``. This table
    remains because historical tools and audit payloads still use the flat keys.
    """

    __tablename__ = "player_world_facts"

    player_id: Mapped[UUID] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)


class PlayerWorldFactState(TimestampMixin, Base):
    """Canonical per-player world truth with independent fact knowledge."""

    __tablename__ = "player_world_fact_states"

    player_id: Mapped[UUID] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[UUID] = mapped_column(
        ForeignKey("world_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    fact_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    truth_value: Mapped[Any] = mapped_column(JSON)
    visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility, native_enum=False), default=Visibility.KNOWN
    )
    version: Mapped[int] = mapped_column(Integer, default=1)


class ConversationSession(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "conversation_sessions"

    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    npc_id: Mapped[UUID] = mapped_column(ForeignKey("npcs.id"))
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, native_enum=False), default=SessionStatus.ACTIVE
    )
    summary: Mapped[str] = mapped_column(Text, default="")


class ConversationMessage(UUIDPrimaryKey, Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (Index("ix_messages_session_created", "session_id", "created_at"),)

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="CASCADE")
    )
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole, native_enum=False))
    content: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(100))
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Memory(UUIDPrimaryKey, Base):
    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_player_npc_importance", "player_id", "npc_id", "importance"),
    )

    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    npc_id: Mapped[UUID] = mapped_column(ForeignKey("npcs.id"))
    type: Mapped[MemoryType] = mapped_column(Enum(MemoryType, native_enum=False))
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(Integer, default=5)
    source_session_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversation_sessions.id"))
    source_event_id: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRun(UUIDPrimaryKey, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_session_started", "session_id", "started_at"),)

    request_id: Mapped[UUID]
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="CASCADE")
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False), default=RunStatus.RUNNING
    )
    model: Mapped[str] = mapped_column(String(100))
    input_message: Mapped[str] = mapped_column(Text)
    context_record_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    model_rounds: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    max_rounds: Mapped[int] = mapped_column(Integer)
    actual_rounds: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    termination_reason: Mapped[TerminationReason | None] = mapped_column(
        Enum(TerminationReason, native_enum=False)
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_tasks.id", ondelete="SET NULL"), index=True
    )
    plan_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_plans.id", ondelete="SET NULL"))
    step_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_steps.id", ondelete="SET NULL"))
    actor_npc_id: Mapped[UUID | None] = mapped_column(ForeignKey("npcs.id", ondelete="SET NULL"))
    officer_profile_version: Mapped[int | None] = mapped_column(Integer)
    authority_policy_version: Mapped[int | None] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(30), default="CONVERSATION")
    structured_output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    validation_status: Mapped[str | None] = mapped_column(String(30))
    validation_errors: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)


class AgentTask(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (Index("ix_agent_tasks_player_status", "player_id", "status"),)

    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    owner_npc_id: Mapped[UUID] = mapped_column(ForeignKey("npcs.id"))
    origin_session_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_sessions.id"))
    last_session_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_sessions.id"))
    goal_description: Mapped[str] = mapped_column(Text)
    scenario_key: Mapped[str] = mapped_column(String(100))
    objective_resolution_status: Mapped[str] = mapped_column(String(30), default="UNRESOLVED")
    objective_scope_keys: Mapped[list[str] | None] = mapped_column(JSON)
    objective_catalog_version: Mapped[str | None] = mapped_column(String(100))
    objective_resolver_source: Mapped[str | None] = mapped_column(String(100))
    objective_resolver_version: Mapped[str | None] = mapped_column(String(100))
    objective_resolution_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    objective_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    objective_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    objective_confirmation_source: Mapped[str | None] = mapped_column(String(100))
    objective_frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    objective_freeze_source: Mapped[str | None] = mapped_column(String(100))
    planning_mode: Mapped[str] = mapped_column(String(40), default="PROVIDER")
    status: Mapped[AgentTaskStatus] = mapped_column(
        Enum(AgentTaskStatus, native_enum=False, length=30),
        default=AgentTaskStatus.ACTIVE,
    )
    current_plan_version: Mapped[int] = mapped_column(Integer, default=0)
    replan_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentPlan(UUIDPrimaryKey, Base):
    __tablename__ = "agent_plans"
    __table_args__ = (
        UniqueConstraint("task_id", "version"),
        Index("ix_agent_plans_task_version", "task_id", "version"),
    )

    task_id: Mapped[UUID] = mapped_column(ForeignKey("agent_tasks.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[AgentPlanStatus] = mapped_column(
        Enum(AgentPlanStatus, native_enum=False), default=AgentPlanStatus.ACTIVE
    )
    strategy_summary: Mapped[str] = mapped_column(Text)
    replan_reason: Mapped[str | None] = mapped_column(String(160))
    supersedes_plan_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_plans.id", ondelete="SET NULL")
    )
    # Kept as an audit identifier without a foreign key to avoid a schema cycle:
    # plans are produced by runs, while step runs refer back to plans and steps.
    created_by_run_id: Mapped[UUID | None]
    created_by_npc_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("npcs.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(40), default="MANUAL")
    planner_model: Mapped[str | None] = mapped_column(String(100))
    validation_status: Mapped[str] = mapped_column(String(30), default="PASSED")
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentStep(UUIDPrimaryKey, Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("plan_id", "sequence"),
        Index("ix_agent_steps_plan_sequence", "plan_id", "sequence"),
    )

    plan_id: Mapped[UUID] = mapped_column(ForeignKey("agent_plans.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    execution_type: Mapped[StepExecutionType] = mapped_column(
        Enum(StepExecutionType, native_enum=False, length=30)
    )
    status: Mapped[AgentStepStatus] = mapped_column(
        Enum(AgentStepStatus, native_enum=False, length=30),
        default=AgentStepStatus.PENDING,
    )
    assigned_npc_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("npcs.id", ondelete="SET NULL"), index=True
    )
    action_intent: Mapped[str | None] = mapped_column(String(100))
    constraints: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    allowed_tool_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    selected_tool_name: Mapped[str | None] = mapped_column(String(100))
    tool_arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected_outcome: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actual_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    failure_code: Mapped[str | None] = mapped_column(String(100))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    resume_condition: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolExecution(UUIDPrimaryKey, Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "tool_call_id"),
        Index("ix_tool_execution_run_created", "agent_run_id", "created_at"),
    )

    agent_run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="SET NULL"), index=True
    )
    tool_call_id: Mapped[str] = mapped_column(String(160))
    tool_name: Mapped[str] = mapped_column(String(100))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON)
    validation_status: Mapped[str] = mapped_column(String(30))
    authorization_status: Mapped[str] = mapped_column(String(30))
    authority_details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    business_rule_status: Mapped[str] = mapped_column(String(30))
    execution_status: Mapped[str] = mapped_column(String(30))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(100))
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorldOperation(UUIDPrimaryKey, TimestampMixin, Base):
    """A deterministic, externally-resolved military, construction, or trade operation."""

    __tablename__ = "world_operations"
    __table_args__ = (
        UniqueConstraint("player_id", "idempotency_key"),
        Index("ix_world_operations_task_status", "task_id", "status"),
    )

    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_tasks.id", ondelete="SET NULL"), index=True
    )
    source_step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="SET NULL"), index=True
    )
    officer_npc_id: Mapped[UUID] = mapped_column(ForeignKey("npcs.id"))
    operation_type: Mapped[str] = mapped_column(String(50))
    target_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[WorldOperationStatus] = mapped_column(
        Enum(WorldOperationStatus, native_enum=False),
        default=WorldOperationStatus.PENDING,
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    resolution_key: Mapped[str | None] = mapped_column(String(160))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlayerDecisionRequest(UUIDPrimaryKey, TimestampMixin, Base):
    """A scoped approval/choice request generated by an authority policy."""

    __tablename__ = "player_decision_requests"
    __table_args__ = (Index("ix_decisions_task_status", "task_id", "status"),)

    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    task_id: Mapped[UUID] = mapped_column(ForeignKey("agent_tasks.id", ondelete="CASCADE"))
    step_id: Mapped[UUID] = mapped_column(ForeignKey("agent_steps.id", ondelete="CASCADE"))
    requested_by_npc_id: Mapped[UUID] = mapped_column(ForeignKey("npcs.id"))
    status: Mapped[DecisionStatus] = mapped_column(
        Enum(DecisionStatus, native_enum=False), default=DecisionStatus.PENDING
    )
    decision_kind: Mapped[str] = mapped_column(String(30), default="APPROVAL")
    summary: Mapped[str] = mapped_column(Text)
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    action_tool_name: Mapped[str] = mapped_column(String(100))
    action_arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    selected_option: Mapped[str | None] = mapped_column(String(80))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
