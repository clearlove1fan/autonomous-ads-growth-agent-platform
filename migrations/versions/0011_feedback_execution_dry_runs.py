"""add feedback execution dry-run table

Revision ID: 0011_feedback_execution_dry_runs
Revises: 0010_feedback_opt_reviews
Create Date: 2026-05-20
"""

from collections.abc import Sequence

from alembic import op

from ads_growth_agent.persistence.schema import feedback_execution_dry_runs

revision: str = "0011_feedback_execution_dry_runs"
down_revision: str | None = "0010_feedback_opt_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    feedback_execution_dry_runs.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    feedback_execution_dry_runs.drop(op.get_bind(), checkfirst=True)
