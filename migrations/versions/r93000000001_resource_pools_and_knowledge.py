"""Add Resource Pools and Region resource intelligence.

Revision ID: r93000000001
Revises: r92000000001
"""

from collections.abc import Sequence
import json

import sqlalchemy as sa
from alembic import op

revision: str = "r93000000001"
down_revision: str | None = "r92000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("game_instance_resource_states") as batch_op:
        batch_op.add_column(
            sa.Column("pool_key", sa.String(length=80), server_default="default", nullable=False)
        )
        batch_op.add_column(sa.Column("facility_key", sa.String(length=80), nullable=True))
        batch_op.add_column(
            sa.Column("visibility", sa.String(length=20), server_default="VISIBLE", nullable=False)
        )
        batch_op.add_column(
            sa.Column("availability", sa.String(length=20), server_default="AVAILABLE", nullable=False)
        )
        batch_op.add_column(
            sa.Column("survey_discoverable", sa.Boolean(), server_default=sa.text("0"), nullable=False)
        )
        batch_op.add_column(sa.Column("availability_requirement", sa.JSON(), nullable=True))

    op.create_index(
        "ix_instance_resource_pool",
        "game_instance_resource_states",
        ["game_instance_id", "resource_key", "scope_node_key", "pool_key"],
    )
    op.create_table(
        "game_instance_region_resource_knowledge",
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("region_key", sa.String(length=80), nullable=False),
        sa.Column("resource_inventory_visibility", sa.String(length=20), nullable=False),
        sa.Column("resource_survey_completed", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["game_instance_id"],
            ["game_instances.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("game_instance_id", "region_key"),
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT gi.id, sv.snapshot_document "
            "FROM game_instances gi "
            "JOIN scenario_versions sv ON sv.id = gi.scenario_version_id"
        )
    ).mappings()
    for row in rows:
        document = row["snapshot_document"]
        if isinstance(document, str):
            document = json.loads(document)
        locality = document.get("metadata", {}).get("locality", {})
        if not locality.get("enabled"):
            continue
        region_type = locality.get("region_node_type_key")
        if not region_type:
            continue
        for node in document.get("world", {}).get("nodes", []):
            if node.get("node_type_key") != region_type:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO game_instance_region_resource_knowledge "
                    "(game_instance_id, region_key, resource_inventory_visibility, "
                    "resource_survey_completed, version, created_at, updated_at) "
                    "VALUES (:game_instance_id, :region_key, 'VISIBLE', 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"game_instance_id": row["id"], "region_key": node["key"]},
            )


def downgrade() -> None:
    raise RuntimeError("Resource Pool migration is intentionally irreversible")
