"""Phase D stepwise player execution pacing

Revision ID: d80000000001
Revises: d70000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d80000000001"
down_revision: str | None = "d70000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Phase D introduced CANCELLED after the original operation enum was
    # created with an eight-character VARCHAR.  Widen it so PostgreSQL can
    # persist the already-supported lifecycle value as SQLite does.
    with op.batch_alter_table("world_operations") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(length=8),
            type_=sa.Enum(
                "PENDING",
                "RESOLVED",
                "CANCELLED",
                name="worldoperationstatus",
                native_enum=False,
                length=9,
            ),
            existing_nullable=False,
        )
    op.create_table(
        "player_execution_checkpoints",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("phase", sa.String(length=40), nullable=False),
        sa.Column("last_action_step_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_instance_id"], ["game_instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_action_step_id"], ["agent_steps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index(
        "ix_player_execution_checkpoints_game_instance_id",
        "player_execution_checkpoints",
        ["game_instance_id"],
    )
    op.create_index(
        "ix_player_execution_checkpoints_last_action_step_id",
        "player_execution_checkpoints",
        ["last_action_step_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_player_execution_checkpoints_last_action_step_id",
        table_name="player_execution_checkpoints",
    )
    op.drop_index(
        "ix_player_execution_checkpoints_game_instance_id",
        table_name="player_execution_checkpoints",
    )
    op.drop_table("player_execution_checkpoints")
    # Keep VARCHAR(9) on downgrade so existing CANCELLED audit rows are not
    # truncated or rejected by PostgreSQL.
    with op.batch_alter_table("world_operations") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.Enum(
                "PENDING",
                "RESOLVED",
                "CANCELLED",
                name="worldoperationstatus",
                native_enum=False,
                length=9,
            ),
            type_=sa.String(length=9),
            existing_nullable=False,
        )
