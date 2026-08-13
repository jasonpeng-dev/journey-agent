"""v2 generic engine cutover

Revision ID: r80000000001
Revises: r60000000001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "r80000000001"
down_revision: str | None = "r60000000001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # v1 runtimes are intentionally unsupported after Phase R. Remove incomplete
    # compatibility graphs before making exact v2 Actor ownership mandatory.
    bind.execute(sa.text("DELETE FROM world_operations WHERE actor_key IS NULL"))
    bind.execute(sa.text("DELETE FROM agent_steps WHERE assigned_actor_key IS NULL"))
    bind.execute(sa.text("DELETE FROM agent_plans WHERE created_by_actor_key IS NULL"))
    bind.execute(sa.text("DELETE FROM agent_tasks WHERE owner_actor_key IS NULL"))
    bind.execute(sa.text("DELETE FROM conversation_sessions WHERE actor_key IS NULL"))
    bind.execute(
        sa.text(
            "DELETE FROM game_instances WHERE id NOT IN "
            "(SELECT DISTINCT game_instance_id FROM game_instance_actors)"
        )
    )

    with op.batch_alter_table("scenario_versions") as batch_op:
        batch_op.alter_column(
            "behavior_bundle_key",
            new_column_name="engine_contract_key",
            existing_type=sa.String(length=100),
            nullable=False,
        )
        batch_op.alter_column(
            "behavior_bundle_version",
            new_column_name="engine_contract_version",
            existing_type=sa.String(length=100),
            nullable=False,
        )
    with op.batch_alter_table("conversation_sessions") as batch_op:
        batch_op.drop_column("npc_id")
        batch_op.alter_column("actor_key", existing_type=sa.String(length=80), nullable=False)
    with op.batch_alter_table("agent_tasks") as batch_op:
        batch_op.drop_column("owner_npc_id")
        batch_op.alter_column(
            "owner_actor_key", existing_type=sa.String(length=80), nullable=False
        )
    with op.batch_alter_table("agent_plans") as batch_op:
        batch_op.drop_column("created_by_npc_id")
        batch_op.alter_column(
            "created_by_actor_key", existing_type=sa.String(length=80), nullable=False
        )
    op.drop_index("ix_agent_steps_assigned_npc_id", table_name="agent_steps")
    with op.batch_alter_table("agent_steps") as batch_op:
        batch_op.drop_column("assigned_npc_id")
        batch_op.alter_column(
            "assigned_actor_key", existing_type=sa.String(length=80), nullable=False
        )
        batch_op.create_index("ix_agent_steps_assigned_actor_key", ["assigned_actor_key"])
    with op.batch_alter_table("world_operations") as batch_op:
        batch_op.drop_column("officer_npc_id")
        batch_op.drop_column("operation_type")
        batch_op.alter_column("actor_key", existing_type=sa.String(length=80), nullable=False)
        batch_op.alter_column("action_key", existing_type=sa.String(length=80), nullable=False)
        batch_op.alter_column(
            "execution_mode", existing_type=sa.String(length=20), nullable=False
        )
    with op.batch_alter_table("game_instance_node_states") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=30),
            type_=sa.Enum(
                "LOCKED", "AVAILABLE", "ENTERED", "COMPLETED", name="nodestatus",
                native_enum=False,
            ),
            nullable=False,
        )
        batch_op.alter_column(
            "visibility",
            existing_type=sa.String(length=30),
            type_=sa.Enum("HIDDEN", "KNOWN", name="visibility", native_enum=False),
            nullable=False,
        )
    with op.batch_alter_table("game_instance_fact_states") as batch_op:
        batch_op.alter_column(
            "visibility",
            existing_type=sa.String(length=30),
            type_=sa.Enum("HIDDEN", "KNOWN", name="visibility", native_enum=False),
            nullable=False,
        )

    for table_name in (
        "player_decision_requests",
        "tool_executions",
        "agent_runs",
        "memories",
        "game_instance_officer_appointments",
        "game_instance_world_facts",
        "player_world_fact_states",
        "player_world_facts",
        "player_domain_states",
        "officer_appointments",
        "player_node_states",
    ):
        op.drop_table(table_name)
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_column("current_node_id")
    op.drop_table("npcs")
    op.drop_table("world_nodes")
    op.drop_table("worlds")


def downgrade() -> None:
    raise RuntimeError("The v2 generic-engine cutover is intentionally irreversible")
