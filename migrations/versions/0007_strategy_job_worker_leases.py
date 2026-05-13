"""add strategy job worker leases

Revision ID: 0007_job_worker_leases
Revises: 0006_memory_usage_count
Create Date: 2026-05-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_job_worker_leases"
down_revision: str | None = "0006_memory_usage_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE strategy_jobs "
        "ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE strategy_jobs "
        "ADD COLUMN IF NOT EXISTS max_attempts integer NOT NULL DEFAULT 3"
    )
    op.execute(
        "ALTER TABLE strategy_jobs "
        "ADD COLUMN IF NOT EXISTS next_attempt_at timestamp with time zone"
    )
    op.execute("ALTER TABLE strategy_jobs ADD COLUMN IF NOT EXISTS locked_by text")
    op.execute(
        "ALTER TABLE strategy_jobs "
        "ADD COLUMN IF NOT EXISTS locked_until timestamp with time zone"
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'strategy_job_attempt_count_nonnegative'
          ) THEN
            ALTER TABLE strategy_jobs
              ADD CONSTRAINT strategy_job_attempt_count_nonnegative
              CHECK (attempt_count >= 0);
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'strategy_job_max_attempts_positive'
          ) THEN
            ALTER TABLE strategy_jobs
              ADD CONSTRAINT strategy_job_max_attempts_positive
              CHECK (max_attempts > 0);
          END IF;
        END $$;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_strategy_jobs_claimable "
        "ON strategy_jobs "
        "(tenant_id, status, next_attempt_at, locked_until, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_strategy_jobs_claimable")
    op.execute(
        "ALTER TABLE strategy_jobs "
        "DROP CONSTRAINT IF EXISTS strategy_job_max_attempts_positive"
    )
    op.execute(
        "ALTER TABLE strategy_jobs "
        "DROP CONSTRAINT IF EXISTS strategy_job_attempt_count_nonnegative"
    )
    op.execute("ALTER TABLE strategy_jobs DROP COLUMN IF EXISTS locked_until")
    op.execute("ALTER TABLE strategy_jobs DROP COLUMN IF EXISTS locked_by")
    op.execute("ALTER TABLE strategy_jobs DROP COLUMN IF EXISTS next_attempt_at")
    op.execute("ALTER TABLE strategy_jobs DROP COLUMN IF EXISTS max_attempts")
    op.execute("ALTER TABLE strategy_jobs DROP COLUMN IF EXISTS attempt_count")
