"""add campaign performance events

Revision ID: 0003_campaign_performance_events
Revises: 0002_execution_run_ids
Create Date: 2026-05-12
"""

from collections.abc import Sequence

from alembic import op

from ads_growth_agent.persistence.schema import campaign_performance_events

revision: str = "0003_campaign_performance_events"
down_revision: str | None = "0002_execution_run_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    campaign_performance_events.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    campaign_performance_events.drop(op.get_bind(), checkfirst=True)
