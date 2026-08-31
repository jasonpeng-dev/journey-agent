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
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.domain.enums import (
    AgentPlanStatus,
    AgentStepStatus,
    AgentTaskStatus,
    DecisionStatus,
    GameInstanceStatus,
    MessageRole,
    NodeStatus,
    PlayerStatus,
    RelationVisibility,
    ResourceInventoryVisibility,
    ResourcePoolAvailability,
    ResourcePoolVisibility,
    SessionStatus,
    StepExecutionType,
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
    engine_contract_key: Mapped[str] = mapped_column(String(100))
    engine_contract_version: Mapped[str] = mapped_column(String(100))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScenarioVersionImmutableError(RuntimeError):
    """Raised when ORM code attempts to mutate a published ScenarioVersion."""


@event.listens_for(ScenarioVersion, "before_update")
@event.listens_for(ScenarioVersion, "before_delete")
def _reject_scenario_version_mutation(*_args: object) -> None:
    raise ScenarioVersionImmutableError("Published ScenarioVersion rows are immutable")


class Player(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "players"
    __table_args__ = (
        CheckConstraint("level >= 1", name="ck_player_level"),
        CheckConstraint("gold >= 0", name="ck_player_gold"),
    )

    name: Mapped[str] = mapped_column(String(100))
    level: Mapped[int] = mapped_column(Integer, default=1)
    gold: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[PlayerStatus] = mapped_column(
        Enum(PlayerStatus, native_enum=False), default=PlayerStatus.ACTIVE
    )
    version: Mapped[int] = mapped_column(Integer, default=1)


class GameInstance(UUIDPrimaryKey, TimestampMixin, Base):
    """One game with a permanent Player and immutable ScenarioVersion binding."""

    __tablename__ = "game_instances"
    __table_args__ = (
        CheckConstraint("runtime_revision >= 0", name="ck_game_instance_runtime_revision"),
        Index("ix_game_instances_player_status", "player_id", "status"),
        Index(
            "uq_game_instances_player_creation_key",
            "player_id",
            "creation_key",
            unique=True,
            sqlite_where=text("creation_key IS NOT NULL"),
            postgresql_where=text("creation_key IS NOT NULL"),
        ),
        CheckConstraint(
            "checkpoint_source_runtime_revision IS NULL OR checkpoint_source_runtime_revision >= 0",
            name="ck_game_instance_checkpoint_source_revision",
        ),
        CheckConstraint(
            "inherited_task_count >= 0",
            name="ck_game_instance_inherited_task_count",
        ),
        Index(
            "uq_game_instances_checkpoint_source_revision",
            "checkpointed_from_game_instance_id",
            "checkpoint_source_runtime_revision",
            unique=True,
            sqlite_where=text(
                "checkpointed_from_game_instance_id IS NOT NULL "
                "AND checkpoint_source_runtime_revision IS NOT NULL"
            ),
            postgresql_where=text(
                "checkpointed_from_game_instance_id IS NOT NULL "
                "AND checkpoint_source_runtime_revision IS NOT NULL"
            ),
        ),
    )

    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    scenario_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("scenario_versions.id", ondelete="RESTRICT")
    )
    forked_from_game_instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "game_instances.id",
            name="fk_game_instances_forked_from_game_instance",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    checkpointed_from_game_instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "game_instances.id",
            name="fk_game_instances_checkpointed_from_game_instance",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    checkpoint_source_runtime_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inherited_task_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    status: Mapped[GameInstanceStatus] = mapped_column(
        Enum(GameInstanceStatus, native_enum=False, length=30),
        default=GameInstanceStatus.PENDING_INITIALIZATION,
    )
    current_node_key: Mapped[str | None] = mapped_column(String(80))
    creation_key: Mapped[str] = mapped_column(String(160))
    runtime_revision: Mapped[int] = mapped_column(Integer, default=0)


class GameInstanceBindingImmutableError(RuntimeError):
    """Raised when ORM code attempts to change an Instance ownership binding."""


@event.listens_for(GameInstance, "before_update")
def _reject_game_instance_binding_drift(
    _mapper: object, _connection: object, target: object
) -> None:
    state = inspect(target)
    assert state is not None
    if (
        state.attrs.player_id.history.has_changes()
        or state.attrs.scenario_version_id.history.has_changes()
        or (
            state.attrs.creation_key.history.has_changes()
            and state.attrs.creation_key.history.deleted
            and state.attrs.creation_key.history.deleted[0] is not None
        )
    ):
        raise GameInstanceBindingImmutableError(
            "GameInstance Player, ScenarioVersion, and creation bindings are immutable"
        )


class GameInstanceNodeState(TimestampMixin, Base):
    """Instance-owned node access and knowledge state, keyed by snapshot Node key."""

    __tablename__ = "game_instance_node_states"

    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE"), primary_key=True
    )
    node_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[NodeStatus] = mapped_column(Enum(NodeStatus, native_enum=False))
    visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility, native_enum=False), default=Visibility.KNOWN
    )
    version: Mapped[int] = mapped_column(Integer, default=1)


class GameInstanceFactState(TimestampMixin, Base):
    """Instance-owned canonical Truth and independent Knowledge visibility."""

    __tablename__ = "game_instance_fact_states"

    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE"), primary_key=True
    )
    node_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    fact_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    truth_value: Mapped[Any] = mapped_column(JSON)
    visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility, native_enum=False), default=Visibility.KNOWN
    )
    version: Mapped[int] = mapped_column(Integer, default=1)


class GameInstanceResourceState(TimestampMixin, Base):
    """Generic Instance-owned balance, optionally scoped to a Region Node.

    ``resource_identity`` is deliberately non-null because SQLite does not
    enforce NULL-containing composite primary keys.  Global rows retain the
    legacy identity ``resource_key``; scoped rows use the deterministic
    ``resource_key@scope_node_key`` identity.
    """

    __tablename__ = "game_instance_resource_states"
    __table_args__ = (
        Index(
            "ix_instance_resource_scope",
            "game_instance_id",
            "resource_key",
            "scope_node_key",
        ),
        CheckConstraint("value >= 0", name="ck_instance_resource_value"),
        CheckConstraint(
            "reserved_value >= 0 AND reserved_value <= value",
            name="ck_instance_resource_reserved",
        ),
    )

    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE"), primary_key=True
    )
    resource_identity: Mapped[str] = mapped_column(String(161), primary_key=True)
    resource_key: Mapped[str] = mapped_column(String(80), index=True)
    scope_node_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    region_key = synonym("scope_node_key")
    pool_key: Mapped[str] = mapped_column(String(80), default="default", server_default="default")
    facility_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    value: Mapped[int] = mapped_column(Integer)
    reserved_value: Mapped[int] = mapped_column(Integer, default=0)
    visibility: Mapped[ResourcePoolVisibility] = mapped_column(
        Enum(ResourcePoolVisibility, native_enum=False),
        default=ResourcePoolVisibility.VISIBLE,
        server_default=ResourcePoolVisibility.VISIBLE.value,
        nullable=False,
    )
    availability: Mapped[ResourcePoolAvailability] = mapped_column(
        Enum(ResourcePoolAvailability, native_enum=False),
        default=ResourcePoolAvailability.AVAILABLE,
        server_default=ResourcePoolAvailability.AVAILABLE.value,
        nullable=False,
    )
    survey_discoverable: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )
    availability_requirement: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class GameInstanceRegionResourceKnowledge(TimestampMixin, Base):
    """Instance-owned Region resource intelligence, separate from Pool state."""

    __tablename__ = "game_instance_region_resource_knowledge"

    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE"), primary_key=True
    )
    region_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    resource_inventory_visibility: Mapped[ResourceInventoryVisibility] = mapped_column(
        Enum(ResourceInventoryVisibility, native_enum=False),
        default=ResourceInventoryVisibility.VISIBLE,
        server_default=ResourceInventoryVisibility.VISIBLE.value,
        nullable=False,
    )
    resource_survey_completed: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="1",
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)


class GameInstanceRelationKnowledge(TimestampMixin, Base):
    """Instance-owned visibility for immutable Scenario Relations."""

    __tablename__ = "game_instance_relation_knowledge"

    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE"), primary_key=True
    )
    relation_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    visibility: Mapped[RelationVisibility] = mapped_column(
        Enum(RelationVisibility, native_enum=False),
        default=RelationVisibility.VISIBLE,
        server_default=RelationVisibility.VISIBLE.value,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)


class GameInstanceActor(TimestampMixin, Base):
    """Instance-owned runtime actor materialized from the exact ScenarioVersion."""

    __tablename__ = "game_instance_actors"
    __table_args__ = (
        Index(
            "uq_game_instance_primary_actor",
            "game_instance_id",
            unique=True,
            sqlite_where=text("is_primary = 1"),
            postgresql_where=text("is_primary IS TRUE"),
        ),
    )

    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE"), primary_key=True
    )
    actor_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    role_key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    persona: Mapped[str] = mapped_column(Text)
    doctrine: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    current_node_key: Mapped[str] = mapped_column(String(80))
    allowed_action_keys: Mapped[list[str]] = mapped_column(JSON, default=list)
    authority_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    command_reachability: Mapped[str] = mapped_column(
        String(20), default="ONLINE", server_default="ONLINE", nullable=False
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")
    version: Mapped[int] = mapped_column(Integer, default=1)


class GameInstanceMemoryEvent(UUIDPrimaryKey, Base):
    """Generic Instance/Actor memory emitted by a declarative Rule."""

    __tablename__ = "game_instance_memory_events"
    __table_args__ = (
        Index("ix_instance_memory_actor_created", "game_instance_id", "actor_key", "created_at"),
    )

    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE")
    )
    actor_key: Mapped[str | None] = mapped_column(String(80))
    event_key: Mapped[str] = mapped_column(String(80))
    content: Mapped[str] = mapped_column(Text)
    source_rule_key: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConversationSession(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "conversation_sessions"

    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE"),
        index=True,
    )
    actor_key: Mapped[str] = mapped_column(String(80))
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


class AgentTask(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (
        Index("ix_agent_tasks_player_status", "player_id", "status"),
        Index("ix_agent_tasks_instance_status", "game_instance_id", "status"),
        UniqueConstraint(
            "game_instance_id",
            "submission_idempotency_key",
            name="uq_agent_tasks_instance_submission_key",
        ),
        Index(
            "uq_agent_tasks_instance_active",
            "game_instance_id",
            unique=True,
            sqlite_where=text(
                "status IN ('ACTIVE','REQUIRES_PLAYER_DECISION',"
                "'WAITING_FOR_PLAYER_ACTION','WAITING_FOR_WORLD_EVENT')"
            ),
            postgresql_where=text(
                "status IN ('ACTIVE','REQUIRES_PLAYER_DECISION',"
                "'WAITING_FOR_PLAYER_ACTION','WAITING_FOR_WORLD_EVENT')"
            ),
        ),
    )

    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE"),
        index=True,
    )
    owner_actor_key: Mapped[str] = mapped_column(String(80))
    origin_session_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_sessions.id"))
    last_session_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_sessions.id"))
    goal_description: Mapped[str] = mapped_column(Text)
    submission_idempotency_key: Mapped[str | None] = mapped_column(String(160))
    rejected_proposal_signatures: Mapped[list[str]] = mapped_column(JSON, default=list)
    scenario_key: Mapped[str] = mapped_column(String(100))
    objective_resolution_status: Mapped[str] = mapped_column(String(30), default="UNRESOLVED")
    objective_scope_keys: Mapped[list[str] | None] = mapped_column(JSON)
    objective_catalog_version: Mapped[str | None] = mapped_column(String(100))
    objective_scope_hash: Mapped[str] = mapped_column(String(64))
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
    last_error_detail: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanningCycle(UUIDPrimaryKey, TimestampMixin, Base):
    """Durable boundary for one INITIAL/REPLAN attempt sequence."""

    __tablename__ = "planning_cycles"
    __table_args__ = (
        Index("ix_planning_cycles_task_status", "task_id", "status"),
        Index("ix_planning_cycles_task_created", "task_id", "created_at"),
    )

    task_id: Mapped[UUID] = mapped_column(ForeignKey("agent_tasks.id", ondelete="CASCADE"))
    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    base_call_type: Mapped[str] = mapped_column(String(20))
    replan_reason: Mapped[str | None] = mapped_column(String(160))
    frozen_objective_scope: Mapped[list[str]] = mapped_column(JSON, default=list)
    planner_input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    planner_input_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="RUNNING")
    current_attempt: Mapped[int] = mapped_column(Integer, default=0)
    rejected_segment: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    current_violations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    anti_regression_memory: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class PlanningAttempt(UUIDPrimaryKey, TimestampMixin, Base):
    """Durable audit record for one Provider proposal attempt."""

    __tablename__ = "planning_attempts"
    __table_args__ = (
        UniqueConstraint("cycle_id", "attempt_index"),
        Index("ix_planning_attempts_cycle_attempt", "cycle_id", "attempt_index"),
    )

    cycle_id: Mapped[UUID] = mapped_column(ForeignKey("planning_cycles.id", ondelete="CASCADE"))
    task_id: Mapped[UUID] = mapped_column(ForeignKey("agent_tasks.id", ondelete="CASCADE"))
    attempt_index: Mapped[int] = mapped_column(Integer)
    call_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    provider_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    rejected_segment: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    validator_violations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    anti_regression_memory: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    stop_reason: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    finish_reason: Mapped[str | None] = mapped_column(String(80))


class PlayerExecutionCheckpoint(TimestampMixin, Base):
    """Phase D presentation pacing, deliberately separate from AgentTask state."""

    __tablename__ = "player_execution_checkpoints"

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE"), index=True
    )
    phase: Mapped[str] = mapped_column(String(40))
    last_action_step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="SET NULL"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)


class ObjectiveScopeImmutableError(RuntimeError):
    """Raised if persisted code attempts to drift a frozen Task objective scope."""


@event.listens_for(AgentTask, "before_update")
def _reject_frozen_objective_scope_drift(
    _mapper: object, _connection: object, target: AgentTask
) -> None:
    state = inspect(target)
    assert state is not None
    if target.objective_frozen_at is not None and any(
        state.attrs[name].history.has_changes()
        for name in ("objective_scope_keys", "objective_catalog_version", "objective_scope_hash")
    ):
        raise ObjectiveScopeImmutableError("A frozen Task ObjectiveScope is immutable")


class AgentPlan(UUIDPrimaryKey, Base):
    __tablename__ = "agent_plans"
    __table_args__ = (
        UniqueConstraint("task_id", "version"),
        Index("ix_agent_plans_task_version", "task_id", "version"),
        Index("ix_agent_plans_planning_cycle", "planning_cycle_id"),
    )

    task_id: Mapped[UUID] = mapped_column(ForeignKey("agent_tasks.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[AgentPlanStatus] = mapped_column(
        Enum(AgentPlanStatus, native_enum=False), default=AgentPlanStatus.ACTIVE
    )
    strategy_summary: Mapped[str] = mapped_column(Text)
    replan_reason: Mapped[str | None] = mapped_column(String(160))
    # A stable projection boundary for the planning cycle that produced this
    # accepted Plan. It is nullable for legacy plans created before cycle
    # identity was persisted.
    planning_cycle_id: Mapped[UUID | None] = mapped_column()
    supersedes_plan_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_plans.id", ondelete="SET NULL")
    )
    # Kept as an audit identifier without a foreign key to avoid a schema cycle:
    # plans are produced by runs, while step runs refer back to plans and steps.
    created_by_run_id: Mapped[UUID | None]
    created_by_actor_key: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(40), default="MANUAL")
    planner_model: Mapped[str | None] = mapped_column(String(100))
    validation_status: Mapped[str] = mapped_column(String(30), default="PASSED")
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    stop_reason: Mapped[str] = mapped_column(String(40), default="OBJECTIVE_COMPLETION")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentStep(UUIDPrimaryKey, Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("plan_id", "sequence"),
        Index("ix_agent_steps_plan_sequence", "plan_id", "sequence"),
    )

    plan_id: Mapped[UUID] = mapped_column(ForeignKey("agent_plans.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    planner_step_id: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text)
    execution_type: Mapped[StepExecutionType] = mapped_column(
        Enum(StepExecutionType, native_enum=False, length=30)
    )
    status: Mapped[AgentStepStatus] = mapped_column(
        Enum(AgentStepStatus, native_enum=False, length=30),
        default=AgentStepStatus.PENDING,
    )
    assigned_actor_key: Mapped[str] = mapped_column(String(80), index=True)
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


class WorldOperation(UUIDPrimaryKey, TimestampMixin, Base):
    """A deterministic, externally-resolved military, construction, or trade operation."""

    __tablename__ = "world_operations"
    __table_args__ = (
        Index(
            "uq_world_operations_instance_idempotency",
            "game_instance_id",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_world_operations_task_status", "task_id", "status"),
    )

    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE"),
        index=True,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_tasks.id", ondelete="SET NULL"), index=True
    )
    source_step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="SET NULL"), index=True
    )
    actor_key: Mapped[str] = mapped_column(String(80))
    action_key: Mapped[str] = mapped_column(String(80))
    execution_mode: Mapped[str] = mapped_column(String(20))
    target_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[WorldOperationStatus] = mapped_column(
        Enum(WorldOperationStatus, native_enum=False, length=9),
        default=WorldOperationStatus.PENDING,
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    resolution_key: Mapped[str | None] = mapped_column(String(160))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActionDecisionRequest(UUIDPrimaryKey, TimestampMixin, Base):
    """Instance-owned approval gate for one exact generic Action input."""

    __tablename__ = "action_decision_requests"
    __table_args__ = (
        UniqueConstraint("game_instance_id", "idempotency_key"),
        Index("ix_action_decisions_instance_status", "game_instance_id", "status"),
    )

    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    game_instance_id: Mapped[UUID] = mapped_column(
        ForeignKey("game_instances.id", ondelete="CASCADE")
    )
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_tasks.id", ondelete="CASCADE"))
    source_step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="CASCADE")
    )
    actor_key: Mapped[str] = mapped_column(String(80))
    action_key: Mapped[str] = mapped_column(String(80))
    target_key: Mapped[str] = mapped_column(String(100))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[DecisionStatus] = mapped_column(
        Enum(DecisionStatus, native_enum=False, length=30),
        default=DecisionStatus.PENDING,
    )
    reason_code: Mapped[str] = mapped_column(String(100))
    policy_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
