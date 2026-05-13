"""add strategy job cancellation status

Revision ID: 0008_strategy_job_cancellation
Revises: 0007_job_worker_leases
Create Date: 2026-05-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_strategy_job_cancellation"
down_revision: str | None = "0007_job_worker_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE strategy_jobs DROP CONSTRAINT IF EXISTS strategy_job_status")
    op.execute(
        """
        ALTER TABLE strategy_jobs
        ADD CONSTRAINT strategy_job_status
        CHECK (status in ('queued', 'running', 'completed', 'failed', 'cancelled'))
        """
    )
    op.execute(
        "ALTER TABLE strategy_jobs "
        "DROP CONSTRAINT IF EXISTS strategy_job_completed_at_status"
    )
    op.execute(
        """
        ALTER TABLE strategy_jobs
        ADD CONSTRAINT strategy_job_completed_at_status
        CHECK (
          (status in ('queued', 'running') and completed_at is null)
          or (status in ('completed', 'failed', 'cancelled') and completed_at is not null)
        )
        """
    )


def downgrade() -> None:
    op.execute("UPDATE strategy_jobs SET status = 'failed' WHERE status = 'cancelled'")
    op.execute(
        "ALTER TABLE strategy_jobs "
        "DROP CONSTRAINT IF EXISTS strategy_job_completed_at_status"
    )
    op.execute(
        """
        ALTER TABLE strategy_jobs
        ADD CONSTRAINT strategy_job_completed_at_status
        CHECK (
          (status in ('queued', 'running') and completed_at is null)
          or (status in ('completed', 'failed') and completed_at is not null)
        )
        """
    )
    op.execute("ALTER TABLE strategy_jobs DROP CONSTRAINT IF EXISTS strategy_job_status")
    op.execute(
        """
        ALTER TABLE strategy_jobs
        ADD CONSTRAINT strategy_job_status
        CHECK (status in ('queued', 'running', 'completed', 'failed'))
        """
    )
