"""create partition-aware core schema

Revision ID: 0001_partition_aware_core_schema
Revises: None
Create Date: 2026-05-11
"""

from collections.abc import Sequence

from alembic import op

from ads_growth_agent.persistence.schema import metadata

revision: str = "0001_partition_aware_core_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    metadata.create_all(bind=op.get_bind())
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_content_fts "
        "ON knowledge_chunks USING gin (to_tsvector('english', content))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_advertiser_memories_content_fts "
        "ON advertiser_memories USING gin (to_tsvector('english', content))"
    )


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind())
