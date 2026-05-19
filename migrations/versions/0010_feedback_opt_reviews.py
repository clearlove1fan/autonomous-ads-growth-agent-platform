"""add feedback optimization review table

Revision ID: 0010_feedback_opt_reviews
Revises: 0009_perf_event_draft_idx
Create Date: 2026-05-19
"""

from collections.abc import Sequence

from alembic import op

from ads_growth_agent.persistence.schema import feedback_optimization_reviews

revision: str = "0010_feedback_opt_reviews"
down_revision: str | None = "0009_perf_event_draft_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    feedback_optimization_reviews.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    feedback_optimization_reviews.drop(op.get_bind(), checkfirst=True)
