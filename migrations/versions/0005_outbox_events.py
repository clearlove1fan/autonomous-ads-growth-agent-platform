"""add outbox events

Revision ID: 0005_outbox_events
Revises: 0004_strategy_jobs
Create Date: 2026-05-13
"""

from collections.abc import Sequence

from alembic import op

from ads_growth_agent.persistence.schema import outbox_events

revision: str = "0005_outbox_events"
down_revision: str | None = "0004_strategy_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    outbox_events.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    outbox_events.drop(op.get_bind(), checkfirst=True)
