"""Persist Scenario-wide PresentationProfile revisions.

Revision ID: r99200000001
Revises: r99100000005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r99200000001"
down_revision: str | None = "r99100000005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenario_presentation_profiles",
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("profile_document", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("scenario_id"),
    )
    op.create_table(
        "scenario_presentation_profile_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("profile_document", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scenario_id", "revision"),
    )
    op.create_index(
        "ix_scenario_presentation_profile_revisions_scenario_revision",
        "scenario_presentation_profile_revisions",
        ["scenario_id", "revision"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scenario_presentation_profile_revisions_scenario_revision",
        table_name="scenario_presentation_profile_revisions",
    )
    op.drop_table("scenario_presentation_profile_revisions")
    op.drop_table("scenario_presentation_profiles")
