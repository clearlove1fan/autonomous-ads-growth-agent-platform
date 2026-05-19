"""add performance event draft lookup index

Revision ID: 0009_performance_event_draft_index
Revises: 0008_strategy_job_cancellation
Create Date: 2026-05-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_performance_event_draft_index"
down_revision: str | None = "0008_strategy_job_cancellation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_campaign_performance_events_draft_occurred
        ON campaign_performance_events (tenant_id, draft_id, occurred_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_campaign_performance_events_draft_occurred")
