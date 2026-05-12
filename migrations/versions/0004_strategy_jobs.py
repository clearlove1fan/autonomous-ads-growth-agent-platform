"""add strategy jobs

Revision ID: 0004_strategy_jobs
Revises: 0003_campaign_performance_events
Create Date: 2026-05-12
"""

from collections.abc import Sequence

from alembic import op

from ads_growth_agent.persistence.schema import strategy_jobs

revision: str = "0004_strategy_jobs"
down_revision: str | None = "0003_campaign_performance_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    strategy_jobs.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    strategy_jobs.drop(op.get_bind(), checkfirst=True)
