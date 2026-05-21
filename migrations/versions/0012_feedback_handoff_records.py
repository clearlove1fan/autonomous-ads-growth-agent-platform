"""Add feedback handoff records.

Revision ID: 0012_feedback_handoff_records
Revises: 0011_feedback_execution_dry_runs
Create Date: 2026-05-20 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

from ads_growth_agent.persistence.schema import feedback_handoff_records

revision: str = "0012_feedback_handoff_records"
down_revision: str | None = "0011_feedback_execution_dry_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    feedback_handoff_records.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    feedback_handoff_records.drop(op.get_bind(), checkfirst=True)
