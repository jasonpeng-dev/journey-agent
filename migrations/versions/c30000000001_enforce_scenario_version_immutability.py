"""enforce scenario version immutability

Revision ID: c30000000001
Revises: c20000000001
Create Date: 2026-08-12 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c30000000001"
down_revision: str | None = "c20000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER scenario_versions_reject_update
            BEFORE UPDATE ON scenario_versions
            BEGIN
                SELECT RAISE(ABORT, 'Published ScenarioVersion rows are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER scenario_versions_reject_delete
            BEFORE DELETE ON scenario_versions
            BEGIN
                SELECT RAISE(ABORT, 'Published ScenarioVersion rows are immutable');
            END
            """
        )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_scenario_version_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Published ScenarioVersion rows are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER scenario_versions_reject_mutation
            BEFORE UPDATE OR DELETE ON scenario_versions
            FOR EACH ROW EXECUTE FUNCTION reject_scenario_version_mutation()
            """
        )
    else:
        raise RuntimeError(f"ScenarioVersion immutability is unsupported on {dialect}")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER scenario_versions_reject_delete")
        op.execute("DROP TRIGGER scenario_versions_reject_update")
    elif dialect == "postgresql":
        op.execute("DROP TRIGGER scenario_versions_reject_mutation ON scenario_versions")
        op.execute("DROP FUNCTION reject_scenario_version_mutation()")
    else:
        raise RuntimeError(f"ScenarioVersion immutability is unsupported on {dialect}")
