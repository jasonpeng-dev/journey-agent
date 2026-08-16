"""Add explicit scoped Resource balances.

Revision ID: r91000000001
Revises: d80000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r91000000001"
down_revision: str | None = "d80000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_instance_resource_states_scoped",
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("resource_identity", sa.String(length=161), nullable=False),
        sa.Column("resource_key", sa.String(length=80), nullable=False),
        sa.Column("scope_node_key", sa.String(length=80), nullable=True),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("reserved_value", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("value >= 0", name="ck_instance_resource_value"),
        sa.CheckConstraint(
            "reserved_value >= 0 AND reserved_value <= value",
            name="ck_instance_resource_reserved",
        ),
        sa.ForeignKeyConstraint(
            ["game_instance_id"],
            ["game_instances.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("game_instance_id", "resource_identity"),
    )
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO game_instance_resource_states_scoped
                (game_instance_id, resource_identity, resource_key, scope_node_key,
                 value, reserved_value, version, created_at, updated_at)
            SELECT game_instance_id, resource_key, resource_key, NULL,
                   value, reserved_value, version, created_at, updated_at
            FROM game_instance_resource_states
            """
        )
    )
    op.drop_table("game_instance_resource_states")
    op.rename_table("game_instance_resource_states_scoped", "game_instance_resource_states")
    op.create_index(
        "ix_instance_resource_scope",
        "game_instance_resource_states",
        ["game_instance_id", "resource_key", "scope_node_key"],
    )
    op.create_index(
        "ix_game_instance_resource_states_resource_key",
        "game_instance_resource_states",
        ["resource_key"],
    )


def downgrade() -> None:
    raise RuntimeError("Scoped Resource balance migration is intentionally irreversible")
