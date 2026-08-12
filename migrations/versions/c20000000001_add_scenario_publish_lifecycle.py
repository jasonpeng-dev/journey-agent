"""add scenario publish lifecycle

Revision ID: c20000000001
Revises: c10000000001
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c20000000001"
down_revision: str | None = "c10000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.add_column(sa.Column("current_published_version_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_scenarios_current_published_version",
            "scenario_versions",
            ["current_published_version_id"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("scenario_drafts") as batch_op:
        batch_op.add_column(sa.Column("content_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("base_scenario_version_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_scenario_drafts_base_version",
            "scenario_versions",
            ["base_scenario_version_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("scenario_drafts") as batch_op:
        batch_op.drop_constraint("fk_scenario_drafts_base_version", type_="foreignkey")
        batch_op.drop_column("base_scenario_version_id")
        batch_op.drop_column("content_hash")
    with op.batch_alter_table("scenarios") as batch_op:
        batch_op.drop_constraint(
            "fk_scenarios_current_published_version",
            type_="foreignkey",
        )
        batch_op.drop_column("current_published_version_id")
