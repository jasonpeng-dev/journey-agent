"""Persist per-Instance Relation Knowledge visibility.

Revision ID: r94000000001
Revises: r93000000001
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r94000000001"
down_revision: str | None = "r93000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_instance_relation_knowledge",
        sa.Column("game_instance_id", sa.Uuid(), nullable=False),
        sa.Column("relation_key", sa.String(length=255), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["game_instance_id"],
            ["game_instances.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("game_instance_id", "relation_key"),
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
        for relation in document.get("world", {}).get("relations", []):
            relation_key = relation.get("key") or (
                f"{relation['source_node_key']}__"
                f"{relation['relation_type_key']}__{relation['target_node_key']}"
            )
            bind.execute(
                sa.text(
                    "INSERT INTO game_instance_relation_knowledge "
                    "(game_instance_id, relation_key, visibility, version, "
                    "created_at, updated_at) "
                    "VALUES (:game_instance_id, :relation_key, :visibility, 1, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "game_instance_id": row["id"],
                    "relation_key": relation_key,
                    "visibility": relation.get("initial_visibility", "VISIBLE"),
                },
            )


def downgrade() -> None:
    raise RuntimeError("Relation Knowledge migration is intentionally irreversible")
