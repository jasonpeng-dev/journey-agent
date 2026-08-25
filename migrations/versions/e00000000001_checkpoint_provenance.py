"""Add Checkpoint provenance and inherited task boundary.

Revision ID: e00000000001
Revises: d98000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e00000000001"
down_revision: str | None = "d98000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("game_instances", recreate="always") as batch_op:
            batch_op.add_column(
                sa.Column("checkpointed_from_game_instance_id", sa.Uuid(), nullable=True)
            )
            batch_op.add_column(
                sa.Column("checkpoint_source_runtime_revision", sa.Integer(), nullable=True)
            )
            batch_op.add_column(
                sa.Column(
                    "inherited_task_count",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
            batch_op.create_foreign_key(
                "fk_game_instances_checkpointed_from_game_instance",
                "game_instances",
                ["checkpointed_from_game_instance_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_check_constraint(
                "ck_game_instance_checkpoint_source_revision",
                "checkpoint_source_runtime_revision IS NULL "
                "OR checkpoint_source_runtime_revision >= 0",
            )
            batch_op.create_check_constraint(
                "ck_game_instance_inherited_task_count",
                "inherited_task_count >= 0",
            )
    else:
        op.add_column(
            "game_instances",
            sa.Column("checkpointed_from_game_instance_id", sa.Uuid(), nullable=True),
        )
        op.add_column(
            "game_instances",
            sa.Column("checkpoint_source_runtime_revision", sa.Integer(), nullable=True),
        )
        op.add_column(
            "game_instances",
            sa.Column(
                "inherited_task_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
        op.create_foreign_key(
            "fk_game_instances_checkpointed_from_game_instance",
            "game_instances",
            "game_instances",
            ["checkpointed_from_game_instance_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_check_constraint(
            "ck_game_instance_checkpoint_source_revision",
            "game_instances",
            "checkpoint_source_runtime_revision IS NULL "
            "OR checkpoint_source_runtime_revision >= 0",
        )
        op.create_check_constraint(
            "ck_game_instance_inherited_task_count",
            "game_instances",
            "inherited_task_count >= 0",
        )

    op.create_index(
        "ix_game_instances_checkpointed_from_game_instance_id",
        "game_instances",
        ["checkpointed_from_game_instance_id"],
        unique=False,
    )
    op.create_index(
        "uq_game_instances_checkpoint_source_revision",
        "game_instances",
        ["checkpointed_from_game_instance_id", "checkpoint_source_runtime_revision"],
        unique=True,
        sqlite_where=sa.text(
            "checkpointed_from_game_instance_id IS NOT NULL "
            "AND checkpoint_source_runtime_revision IS NOT NULL"
        ),
        postgresql_where=sa.text(
            "checkpointed_from_game_instance_id IS NOT NULL "
            "AND checkpoint_source_runtime_revision IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_game_instances_checkpoint_source_revision", table_name="game_instances")
    op.drop_index(
        "ix_game_instances_checkpointed_from_game_instance_id",
        table_name="game_instances",
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("game_instances", recreate="always") as batch_op:
            batch_op.drop_constraint(
                "ck_game_instance_inherited_task_count", type_="check"
            )
            batch_op.drop_constraint(
                "ck_game_instance_checkpoint_source_revision", type_="check"
            )
            batch_op.drop_constraint(
                "fk_game_instances_checkpointed_from_game_instance", type_="foreignkey"
            )
            batch_op.drop_column("inherited_task_count")
            batch_op.drop_column("checkpoint_source_runtime_revision")
            batch_op.drop_column("checkpointed_from_game_instance_id")
    else:
        op.drop_constraint(
            "fk_game_instances_checkpointed_from_game_instance",
            "game_instances",
            type_="foreignkey",
        )
        op.drop_constraint(
            "ck_game_instance_inherited_task_count", "game_instances", type_="check"
        )
        op.drop_constraint(
            "ck_game_instance_checkpoint_source_revision",
            "game_instances",
            type_="check",
        )
        op.drop_column("inherited_task_count", table_name="game_instances")
        op.drop_column("checkpoint_source_runtime_revision", table_name="game_instances")
        op.drop_column("checkpointed_from_game_instance_id", table_name="game_instances")
