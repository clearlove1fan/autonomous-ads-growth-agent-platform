"""separate strategy and execution run identifiers

Revision ID: 0002_execution_run_ids
Revises: 0001_partition_aware_core_schema
Create Date: 2026-05-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_execution_run_ids"
down_revision: str | None = "0001_partition_aware_core_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS strategy_id text")
    op.execute("UPDATE agent_runs SET strategy_id = run_id WHERE strategy_id IS NULL")
    op.execute("ALTER TABLE agent_runs ALTER COLUMN strategy_id SET NOT NULL")

    op.execute("ALTER TABLE agent_run_steps ADD COLUMN IF NOT EXISTS strategy_id text")
    op.execute("UPDATE agent_run_steps SET strategy_id = run_id WHERE strategy_id IS NULL")
    op.execute("ALTER TABLE agent_run_steps ALTER COLUMN strategy_id SET NOT NULL")

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_runs_strategy_created "
        "ON agent_runs (tenant_id, strategy_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_run_steps_strategy_index "
        "ON agent_run_steps (tenant_id, strategy_id, step_index)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_run_steps_strategy_index")
    op.execute("DROP INDEX IF EXISTS ix_agent_runs_strategy_created")
    op.execute("ALTER TABLE agent_run_steps DROP COLUMN IF EXISTS strategy_id")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS strategy_id")
