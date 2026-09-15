"""Add immutable confirmable resolved Goal drafts.

Revision ID: r99100000004
Revises: r99100000003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r99100000004"
down_revision: str | None = "r99100000003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resolved_goal_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("resolution_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("original_goal_text", sa.String(length=4000), nullable=False),
        sa.Column("scenario_version_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_content_hash", sa.String(length=64), nullable=False),
        sa.Column("formal_goal_contract_schema_version", sa.Integer(), nullable=False),
        sa.Column("formal_goal_source_kind", sa.String(length=30), nullable=False),
        sa.Column("formal_goal_contract_json", sa.JSON(), nullable=False),
        sa.Column("formal_goal_contract_hash", sa.String(length=64), nullable=False),
        sa.Column("formal_goal_compiler_version", sa.String(length=100), nullable=False),
        sa.Column("resolver_source", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("confirmed_task_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'CONFIRMED' AND confirmed_task_id IS NOT NULL "
            "AND confirmed_at IS NOT NULL) OR "
            "(status IN ('READY','SUPERSEDED') AND confirmed_task_id IS NULL "
            "AND confirmed_at IS NULL)",
            name="ck_resolved_goal_drafts_confirmation",
        ),
        sa.ForeignKeyConstraint(
            ["game_instance_id"], ["game_instances.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["resolution_attempt_id"], ["goal_resolution_attempts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["scenario_version_id"], ["scenario_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_task_id"], ["agent_tasks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resolution_attempt_id"),
        sa.UniqueConstraint("confirmed_task_id"),
    )
    op.create_index(
        "ix_resolved_goal_drafts_game_instance_id",
        "resolved_goal_drafts",
        ["game_instance_id"],
    )
    op.create_index(
        "uq_resolved_goal_drafts_instance_ready",
        "resolved_goal_drafts",
        ["game_instance_id"],
        unique=True,
        sqlite_where=sa.text("status = 'READY'"),
        postgresql_where=sa.text("status = 'READY'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_resolved_goal_drafts_instance_ready", table_name="resolved_goal_drafts"
    )
    op.drop_index(
        "ix_resolved_goal_drafts_game_instance_id", table_name="resolved_goal_drafts"
    )
    op.drop_table("resolved_goal_drafts")
