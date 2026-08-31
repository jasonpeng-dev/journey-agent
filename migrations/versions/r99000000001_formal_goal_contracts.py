"""Persist frozen Formal Goal V1 contracts and PlanningCycle linkage.

Revision ID: r99000000001
Revises: r98000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r99000000001"
down_revision: str | None = "r98000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TASK_COLUMNS = (
    sa.Column("formal_goal_contract_schema_version", sa.Integer(), nullable=True),
    sa.Column("formal_goal_source_kind", sa.String(length=30), nullable=True),
    sa.Column("formal_goal_contract_json", sa.JSON(), nullable=True),
    sa.Column("formal_goal_contract_hash", sa.String(length=64), nullable=True),
    sa.Column("formal_goal_scenario_version_id", sa.Uuid(), nullable=True),
    sa.Column("formal_goal_scenario_content_hash", sa.String(length=64), nullable=True),
    sa.Column("formal_goal_compiler_version", sa.String(length=100), nullable=True),
)


def _create_sqlite_guard() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_agent_task_scope_immutable")
    op.execute(
        """
        CREATE TRIGGER trg_agent_task_scope_immutable
        BEFORE UPDATE OF objective_scope_keys, objective_catalog_version,
          objective_scope_hash, formal_goal_contract_schema_version,
          formal_goal_source_kind, formal_goal_contract_json,
          formal_goal_contract_hash, formal_goal_scenario_version_id,
          formal_goal_scenario_content_hash, formal_goal_compiler_version
        ON agent_tasks
        WHEN OLD.objective_frozen_at IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'frozen Task Goal is immutable'); END
        """
    )


def _create_postgres_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_frozen_objective_scope_drift() RETURNS trigger AS $$
        BEGIN
          IF OLD.objective_frozen_at IS NOT NULL AND
             (OLD.objective_scope_keys IS DISTINCT FROM NEW.objective_scope_keys OR
              OLD.objective_catalog_version IS DISTINCT FROM NEW.objective_catalog_version OR
              OLD.objective_scope_hash IS DISTINCT FROM NEW.objective_scope_hash OR
              OLD.formal_goal_contract_schema_version IS DISTINCT FROM
                NEW.formal_goal_contract_schema_version OR
              OLD.formal_goal_source_kind IS DISTINCT FROM NEW.formal_goal_source_kind OR
              OLD.formal_goal_contract_json IS DISTINCT FROM NEW.formal_goal_contract_json OR
              OLD.formal_goal_contract_hash IS DISTINCT FROM NEW.formal_goal_contract_hash OR
              OLD.formal_goal_scenario_version_id IS DISTINCT FROM
                NEW.formal_goal_scenario_version_id OR
              OLD.formal_goal_scenario_content_hash IS DISTINCT FROM
                NEW.formal_goal_scenario_content_hash OR
              OLD.formal_goal_compiler_version IS DISTINCT FROM NEW.formal_goal_compiler_version)
          THEN RAISE EXCEPTION 'frozen Task Goal is immutable'; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
        """
    )


def _restore_sqlite_guard() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_agent_task_scope_immutable")
    op.execute(
        """
        CREATE TRIGGER trg_agent_task_scope_immutable
        BEFORE UPDATE OF objective_scope_keys, objective_catalog_version, objective_scope_hash
        ON agent_tasks
        WHEN OLD.objective_frozen_at IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'frozen ObjectiveScope is immutable'); END
        """
    )


def _restore_postgres_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_frozen_objective_scope_drift() RETURNS trigger AS $$
        BEGIN
          IF OLD.objective_frozen_at IS NOT NULL AND
             (OLD.objective_scope_keys IS DISTINCT FROM NEW.objective_scope_keys OR
              OLD.objective_catalog_version IS DISTINCT FROM NEW.objective_catalog_version OR
              OLD.objective_scope_hash IS DISTINCT FROM NEW.objective_scope_hash)
          THEN RAISE EXCEPTION 'frozen ObjectiveScope is immutable'; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
        """
    )


def upgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch_op:
        for column in _TASK_COLUMNS:
            batch_op.add_column(column)
    with op.batch_alter_table("planning_cycles") as batch_op:
        batch_op.add_column(
            sa.Column("formal_goal_contract_hash", sa.String(length=64), nullable=True)
        )

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _create_sqlite_guard()
    elif bind.dialect.name == "postgresql":
        _create_postgres_guard()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _restore_sqlite_guard()
    elif bind.dialect.name == "postgresql":
        _restore_postgres_guard()

    with op.batch_alter_table("planning_cycles") as batch_op:
        batch_op.drop_column("formal_goal_contract_hash")
    with op.batch_alter_table("agent_tasks") as batch_op:
        for column in reversed(_TASK_COLUMNS):
            batch_op.drop_column(column.name)
