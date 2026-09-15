"""Persist safe Goal resolution diagnostics outside Task creation transactions.

Revision ID: r99100000001
Revises: r99000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r99100000001"
down_revision: str | None = "r99000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "goal_resolution_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_version_id", sa.Uuid(), nullable=False),
        sa.Column("goal_hash", sa.String(length=64), nullable=False),
        sa.Column("resolution_status", sa.String(length=30), nullable=False),
        sa.Column("resolver_source", sa.String(length=100), nullable=False),
        sa.Column("grounding_source", sa.String(length=100), nullable=True),
        sa.Column("grounded_public_entity_keys", sa.JSON(), nullable=False),
        sa.Column("resolution_candidate_keys", sa.JSON(), nullable=False),
        sa.Column("public_catalog_hash", sa.String(length=64), nullable=True),
        sa.Column("focused_ontology_hash", sa.String(length=64), nullable=True),
        sa.Column("interpretation_status", sa.String(length=30), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=True),
        sa.Column("interpretation_attempts", sa.JSON(), nullable=False),
        sa.Column("recovery_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("backend_validation_result", sa.String(length=30), nullable=True),
        sa.Column("rejection_code", sa.String(length=100), nullable=True),
        sa.Column("provider_purpose", sa.String(length=100), nullable=True),
        sa.Column("provider_model", sa.String(length=100), nullable=True),
        sa.Column("provider_metadata", sa.JSON(), nullable=False),
        sa.Column("resolution_duration_ms", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["game_instance_id"],
            ["game_instances.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_version_id"],
            ["scenario_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_goal_resolution_attempts_instance_created",
        "goal_resolution_attempts",
        ["game_instance_id", "created_at"],
    )
    op.create_index(
        "ix_goal_resolution_attempts_instance_goal",
        "goal_resolution_attempts",
        ["game_instance_id", "goal_hash", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_goal_resolution_attempts_instance_goal",
        table_name="goal_resolution_attempts",
    )
    op.drop_index(
        "ix_goal_resolution_attempts_instance_created",
        table_name="goal_resolution_attempts",
    )
    op.drop_table("goal_resolution_attempts")
