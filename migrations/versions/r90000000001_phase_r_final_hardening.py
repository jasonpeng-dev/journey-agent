"""Phase R final hardening

Revision ID: r90000000001
Revises: r80000000001
"""

from collections.abc import Sequence
import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision: str = "r90000000001"
down_revision: str | None = "r80000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _scope_hash(keys: list[str], catalog: str) -> str:
    payload = json.dumps(
        {"catalog_version": catalog, "objective_keys": tuple(sorted(set(keys)))},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def upgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch_op:
        batch_op.add_column(sa.Column("objective_scope_hash", sa.String(64), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, objective_scope_keys, objective_catalog_version FROM agent_tasks"
        )
    ).mappings()
    for row in rows:
        raw_keys = row["objective_scope_keys"]
        keys = json.loads(raw_keys) if isinstance(raw_keys, str) else raw_keys
        catalog = row["objective_catalog_version"]
        if not isinstance(keys, list) or not keys or not catalog:
            raise RuntimeError("Cannot migrate an invalid frozen ObjectiveScope")
        bind.execute(
            sa.text("UPDATE agent_tasks SET objective_scope_hash=:hash WHERE id=:id"),
            {"hash": _scope_hash(keys, catalog), "id": row["id"]},
        )
    with op.batch_alter_table("agent_tasks") as batch_op:
        batch_op.alter_column("objective_scope_hash", existing_type=sa.String(64), nullable=False)

    op.create_table(
        "action_decision_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("source_step_id", sa.Uuid(), nullable=True),
        sa.Column("actor_key", sa.String(80), nullable=False),
        sa.Column("action_key", sa.String(80), nullable=False),
        sa.Column("target_key", sa.String(100), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("policy_details", sa.JSON(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["game_instance_id"], ["game_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_step_id"], ["agent_steps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_instance_id", "idempotency_key"),
    )
    op.create_index(
        "ix_action_decisions_instance_status",
        "action_decision_requests",
        ["game_instance_id", "status"],
    )

    if bind.dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_agent_task_scope_immutable
            BEFORE UPDATE OF objective_scope_keys, objective_catalog_version, objective_scope_hash
            ON agent_tasks
            WHEN OLD.objective_frozen_at IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'frozen ObjectiveScope is immutable'); END
            """
        )
    elif bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_frozen_objective_scope_drift() RETURNS trigger AS $$
            BEGIN
              IF OLD.objective_frozen_at IS NOT NULL AND
                 (OLD.objective_scope_keys IS DISTINCT FROM NEW.objective_scope_keys OR
                  OLD.objective_catalog_version IS DISTINCT FROM NEW.objective_catalog_version OR
                  OLD.objective_scope_hash IS DISTINCT FROM NEW.objective_scope_hash)
              THEN RAISE EXCEPTION 'frozen ObjectiveScope is immutable'; END IF;
              RETURN NEW;
            END; $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            "CREATE TRIGGER trg_agent_task_scope_immutable BEFORE UPDATE ON agent_tasks "
            "FOR EACH ROW EXECUTE FUNCTION reject_frozen_objective_scope_drift()"
        )


def downgrade() -> None:
    raise RuntimeError("Phase R final hardening is intentionally irreversible")
