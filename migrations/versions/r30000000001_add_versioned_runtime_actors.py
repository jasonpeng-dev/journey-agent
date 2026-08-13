"""add versioned runtime actors

Revision ID: r30000000001
Revises: c80000000001
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r30000000001"
down_revision: str | None = "c80000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_instance_actors",
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("actor_key", sa.String(length=80), nullable=False),
        sa.Column("role_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("persona", sa.Text(), nullable=False),
        sa.Column("doctrine", sa.JSON(), nullable=False),
        sa.Column("current_node_key", sa.String(length=80), nullable=False),
        sa.Column("allowed_action_keys", sa.JSON(), nullable=False),
        sa.Column("authority_policy", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["game_instance_id"], ["game_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("game_instance_id", "actor_key"),
    )
    op.create_index(
        "uq_game_instance_primary_actor",
        "game_instance_actors",
        ["game_instance_id"],
        unique=True,
        sqlite_where=sa.text("is_primary = 1"),
        postgresql_where=sa.text("is_primary IS TRUE"),
    )
    with op.batch_alter_table("conversation_sessions") as batch_op:
        batch_op.alter_column("npc_id", existing_type=sa.Uuid(), nullable=True)
        batch_op.add_column(sa.Column("actor_key", sa.String(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversation_sessions") as batch_op:
        batch_op.drop_column("actor_key")
        batch_op.alter_column("npc_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_index("uq_game_instance_primary_actor", table_name="game_instance_actors")
    op.drop_table("game_instance_actors")
