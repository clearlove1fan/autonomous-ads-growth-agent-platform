"""add advertiser memory usage count

Revision ID: 0006_memory_usage_count
Revises: 0005_outbox_events
Create Date: 2026-05-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_memory_usage_count"
down_revision: str | None = "0005_outbox_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE advertiser_memories "
        "ADD COLUMN IF NOT EXISTS usage_count integer NOT NULL DEFAULT 0"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_advertiser_memories_advertiser_memory_usage_count_nonnegative'
            ) THEN
                ALTER TABLE advertiser_memories
                ADD CONSTRAINT ck_advertiser_memories_advertiser_memory_usage_count_nonnegative
                CHECK (usage_count >= 0);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE advertiser_memories "
        "DROP CONSTRAINT IF EXISTS "
        "ck_advertiser_memories_advertiser_memory_usage_count_nonnegative"
    )
    op.execute("ALTER TABLE advertiser_memories DROP COLUMN IF EXISTS usage_count")
