import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

EMBEDDING_DIMENSIONS = 1536
PARTITION_BUCKETS = 128

metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def tenant_column() -> sa.Column:
    return sa.Column("tenant_id", sa.Text(), nullable=False, server_default="default")


def partition_columns() -> tuple[sa.Column, sa.Column, sa.Column]:
    return (
        sa.Column("partition_key", sa.Text(), nullable=False),
        sa.Column("partition_bucket", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "partition_date", sa.Date(), nullable=False, server_default=sa.text("CURRENT_DATE")
        ),
    )


def timestamp_columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


tenants = sa.Table(
    "tenants",
    metadata,
    sa.Column("tenant_id", sa.Text(), primary_key=True),
    sa.Column("display_name", sa.Text(), nullable=False),
    sa.Column("region", sa.Text(), nullable=False, server_default="us"),
    sa.Column("status", sa.Text(), nullable=False, server_default="active"),
    sa.Column(
        "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    ),
    *timestamp_columns(),
    sa.CheckConstraint("status in ('active', 'suspended', 'deleted')", name="tenant_status"),
)

advertisers = sa.Table(
    "advertisers",
    metadata,
    tenant_column(),
    sa.Column("advertiser_id", sa.Text(), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("industry", sa.Text(), nullable=True),
    sa.Column("default_currency", sa.Text(), nullable=False, server_default="USD"),
    sa.Column("target_markets", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("status", sa.Text(), nullable=False, server_default="active"),
    sa.Column(
        "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    ),
    *partition_columns(),
    *timestamp_columns(),
    sa.PrimaryKeyConstraint("tenant_id", "advertiser_id"),
    sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
    sa.CheckConstraint("char_length(default_currency) = 3", name="advertiser_currency_length"),
    sa.CheckConstraint("status in ('active', 'paused', 'deleted')", name="advertiser_status"),
    sa.CheckConstraint(
        f"partition_bucket >= 0 and partition_bucket < {PARTITION_BUCKETS}",
        name="advertiser_partition_bucket_range",
    ),
)

campaign_drafts = sa.Table(
    "campaign_drafts",
    metadata,
    tenant_column(),
    sa.Column("draft_id", sa.Text(), nullable=False),
    sa.Column("advertiser_id", sa.Text(), nullable=False),
    sa.Column("objective", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
    sa.Column("budget", sa.Numeric(12, 2), nullable=False),
    sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
    sa.Column("strategy_json", postgresql.JSONB(), nullable=False),
    sa.Column("created_by_run_id", sa.Text(), nullable=True),
    sa.Column(
        "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    ),
    *partition_columns(),
    *timestamp_columns(),
    sa.PrimaryKeyConstraint("tenant_id", "draft_id"),
    sa.ForeignKeyConstraint(
        ["tenant_id", "advertiser_id"],
        ["advertisers.tenant_id", "advertisers.advertiser_id"],
    ),
    sa.CheckConstraint("status = 'draft'", name="campaign_draft_status_draft_only"),
    sa.CheckConstraint("budget > 0", name="campaign_draft_budget_positive"),
    sa.CheckConstraint("char_length(currency) = 3", name="campaign_draft_currency_length"),
    sa.CheckConstraint(
        f"partition_bucket >= 0 and partition_bucket < {PARTITION_BUCKETS}",
        name="campaign_draft_partition_bucket_range",
    ),
)

knowledge_documents = sa.Table(
    "knowledge_documents",
    metadata,
    tenant_column(),
    sa.Column(
        "document_id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    ),
    sa.Column("source_id", sa.Text(), nullable=False),
    sa.Column("title", sa.Text(), nullable=False),
    sa.Column("source_type", sa.Text(), nullable=False),
    sa.Column(
        "product_categories", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"
    ),
    sa.Column("objectives", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("market", sa.Text(), nullable=True),
    sa.Column(
        "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    ),
    *partition_columns(),
    *timestamp_columns(),
    sa.PrimaryKeyConstraint("tenant_id", "document_id"),
    sa.UniqueConstraint("tenant_id", "source_id"),
    sa.ForeignKeyConstraint(["tenant_id"], ["tenants.tenant_id"]),
    sa.CheckConstraint(
        "source_type in ('rag_document', 'historical_case', 'policy', 'playbook')",
        name="knowledge_document_source_type",
    ),
    sa.CheckConstraint(
        f"partition_bucket >= 0 and partition_bucket < {PARTITION_BUCKETS}",
        name="knowledge_document_partition_bucket_range",
    ),
)

knowledge_chunks = sa.Table(
    "knowledge_chunks",
    metadata,
    tenant_column(),
    sa.Column(
        "chunk_id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    ),
    sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("chunk_index", sa.Integer(), nullable=False),
    sa.Column("content", sa.Text(), nullable=False),
    sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
    sa.Column("token_count", sa.Integer(), nullable=False),
    sa.Column("source_type", sa.Text(), nullable=False),
    sa.Column(
        "product_categories", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"
    ),
    sa.Column("objectives", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column(
        "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    ),
    *partition_columns(),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.PrimaryKeyConstraint("tenant_id", "chunk_id"),
    sa.ForeignKeyConstraint(
        ["tenant_id", "document_id"],
        ["knowledge_documents.tenant_id", "knowledge_documents.document_id"],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("tenant_id", "document_id", "chunk_index"),
    sa.CheckConstraint("chunk_index >= 0", name="knowledge_chunk_index_non_negative"),
    sa.CheckConstraint("token_count > 0", name="knowledge_chunk_token_count_positive"),
    sa.CheckConstraint(
        f"partition_bucket >= 0 and partition_bucket < {PARTITION_BUCKETS}",
        name="knowledge_chunk_partition_bucket_range",
    ),
)

advertiser_memories = sa.Table(
    "advertiser_memories",
    metadata,
    tenant_column(),
    sa.Column(
        "memory_id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    ),
    sa.Column("advertiser_id", sa.Text(), nullable=False),
    sa.Column("memory_type", sa.Text(), nullable=False),
    sa.Column("content", sa.Text(), nullable=False),
    sa.Column("summary", sa.Text(), nullable=True),
    sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
    sa.Column("importance_score", sa.Numeric(4, 3), nullable=False, server_default="0.500"),
    sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    ),
    *partition_columns(),
    *timestamp_columns(),
    sa.PrimaryKeyConstraint("tenant_id", "memory_id"),
    sa.ForeignKeyConstraint(
        ["tenant_id", "advertiser_id"],
        ["advertisers.tenant_id", "advertisers.advertiser_id"],
        ondelete="CASCADE",
    ),
    sa.CheckConstraint(
        "memory_type in ('profile', 'constraint', 'preference', 'historical_performance')",
        name="advertiser_memory_type",
    ),
    sa.CheckConstraint(
        "importance_score >= 0 and importance_score <= 1",
        name="advertiser_memory_importance_range",
    ),
    sa.CheckConstraint(
        f"partition_bucket >= 0 and partition_bucket < {PARTITION_BUCKETS}",
        name="advertiser_memory_partition_bucket_range",
    ),
)

retrieval_events = sa.Table(
    "retrieval_events",
    metadata,
    tenant_column(),
    sa.Column(
        "retrieval_id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    ),
    sa.Column("run_id", sa.Text(), nullable=False),
    sa.Column("advertiser_id", sa.Text(), nullable=False),
    sa.Column("query", sa.Text(), nullable=False),
    sa.Column("top_k", sa.Integer(), nullable=False),
    sa.Column("filters", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("results", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("latency_ms", sa.Integer(), nullable=False),
    *partition_columns(),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.PrimaryKeyConstraint("tenant_id", "retrieval_id"),
    sa.CheckConstraint("top_k > 0", name="retrieval_event_top_k_positive"),
    sa.CheckConstraint("latency_ms >= 0", name="retrieval_event_latency_non_negative"),
    sa.CheckConstraint(
        f"partition_bucket >= 0 and partition_bucket < {PARTITION_BUCKETS}",
        name="retrieval_event_partition_bucket_range",
    ),
)

agent_runs = sa.Table(
    "agent_runs",
    metadata,
    tenant_column(),
    sa.Column("run_id", sa.Text(), nullable=False),
    sa.Column("strategy_id", sa.Text(), nullable=False),
    sa.Column("advertiser_id", sa.Text(), nullable=False),
    sa.Column("objective", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("trace_id", sa.Text(), nullable=False),
    sa.Column("node_path", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("final_strategy_json", postgresql.JSONB(), nullable=True),
    sa.Column("error_summary", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
    sa.Column("idempotency_key", sa.Text(), nullable=True),
    sa.Column(
        "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    ),
    *partition_columns(),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint("tenant_id", "run_id"),
    sa.ForeignKeyConstraint(
        ["tenant_id", "advertiser_id"],
        ["advertisers.tenant_id", "advertisers.advertiser_id"],
    ),
    sa.CheckConstraint("status in ('running', 'completed', 'failed')", name="agent_run_status"),
    sa.CheckConstraint(
        f"partition_bucket >= 0 and partition_bucket < {PARTITION_BUCKETS}",
        name="agent_run_partition_bucket_range",
    ),
)

agent_run_steps = sa.Table(
    "agent_run_steps",
    metadata,
    tenant_column(),
    sa.Column(
        "step_id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    ),
    sa.Column("run_id", sa.Text(), nullable=False),
    sa.Column("strategy_id", sa.Text(), nullable=False),
    sa.Column("step_index", sa.Integer(), nullable=False),
    sa.Column("node_name", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column(
        "input_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    ),
    sa.Column(
        "output_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
    ),
    sa.Column("error_json", postgresql.JSONB(), nullable=True),
    sa.Column("latency_ms", sa.Integer(), nullable=False),
    *partition_columns(),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.PrimaryKeyConstraint("tenant_id", "step_id"),
    sa.ForeignKeyConstraint(
        ["tenant_id", "run_id"],
        ["agent_runs.tenant_id", "agent_runs.run_id"],
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("tenant_id", "run_id", "step_index"),
    sa.CheckConstraint("step_index >= 0", name="agent_run_step_index_non_negative"),
    sa.CheckConstraint(
        "status in ('started', 'completed', 'failed')", name="agent_run_step_status"
    ),
    sa.CheckConstraint("latency_ms >= 0", name="agent_run_step_latency_non_negative"),
    sa.CheckConstraint(
        f"partition_bucket >= 0 and partition_bucket < {PARTITION_BUCKETS}",
        name="agent_run_step_partition_bucket_range",
    ),
)

idempotency_keys = sa.Table(
    "idempotency_keys",
    metadata,
    tenant_column(),
    sa.Column("idempotency_key", sa.Text(), nullable=False),
    sa.Column("request_hash", sa.Text(), nullable=False),
    sa.Column("run_id", sa.Text(), nullable=True),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("response_json", postgresql.JSONB(), nullable=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    *partition_columns(),
    *timestamp_columns(),
    sa.PrimaryKeyConstraint("tenant_id", "idempotency_key"),
    sa.ForeignKeyConstraint(
        ["tenant_id", "run_id"],
        ["agent_runs.tenant_id", "agent_runs.run_id"],
        ondelete="SET NULL",
    ),
    sa.CheckConstraint(
        "status in ('in_progress', 'completed', 'failed')", name="idempotency_status"
    ),
    sa.CheckConstraint(
        f"partition_bucket >= 0 and partition_bucket < {PARTITION_BUCKETS}",
        name="idempotency_partition_bucket_range",
    ),
)

sa.Index("ix_advertisers_partition", advertisers.c.tenant_id, advertisers.c.partition_bucket)
sa.Index(
    "ix_campaign_drafts_advertiser_created",
    campaign_drafts.c.tenant_id,
    campaign_drafts.c.advertiser_id,
    campaign_drafts.c.created_at,
)
sa.Index(
    "ix_campaign_drafts_partition_date",
    campaign_drafts.c.tenant_id,
    campaign_drafts.c.partition_date,
    campaign_drafts.c.partition_bucket,
)
sa.Index(
    "ix_knowledge_documents_source_type",
    knowledge_documents.c.tenant_id,
    knowledge_documents.c.source_type,
)
sa.Index(
    "ix_knowledge_documents_product_categories",
    knowledge_documents.c.product_categories,
    postgresql_using="gin",
)
sa.Index(
    "ix_knowledge_documents_objectives",
    knowledge_documents.c.objectives,
    postgresql_using="gin",
)
sa.Index(
    "ix_knowledge_chunks_document",
    knowledge_chunks.c.tenant_id,
    knowledge_chunks.c.document_id,
    knowledge_chunks.c.chunk_index,
)
sa.Index(
    "ix_knowledge_chunks_filter",
    knowledge_chunks.c.tenant_id,
    knowledge_chunks.c.source_type,
    knowledge_chunks.c.partition_bucket,
)
sa.Index(
    "ix_knowledge_chunks_product_categories",
    knowledge_chunks.c.product_categories,
    postgresql_using="gin",
)
sa.Index(
    "ix_knowledge_chunks_objectives",
    knowledge_chunks.c.objectives,
    postgresql_using="gin",
)
sa.Index(
    "ix_knowledge_chunks_embedding_cosine",
    knowledge_chunks.c.embedding,
    postgresql_using="ivfflat",
    postgresql_ops={"embedding": "vector_cosine_ops"},
    postgresql_with={"lists": 100},
)
sa.Index(
    "ix_advertiser_memories_advertiser_type",
    advertiser_memories.c.tenant_id,
    advertiser_memories.c.advertiser_id,
    advertiser_memories.c.memory_type,
)
sa.Index(
    "ix_advertiser_memories_embedding_cosine",
    advertiser_memories.c.embedding,
    postgresql_using="ivfflat",
    postgresql_ops={"embedding": "vector_cosine_ops"},
    postgresql_with={"lists": 100},
)
sa.Index(
    "ix_retrieval_events_run_created",
    retrieval_events.c.tenant_id,
    retrieval_events.c.run_id,
    retrieval_events.c.created_at,
)
sa.Index(
    "ix_retrieval_events_advertiser_created",
    retrieval_events.c.tenant_id,
    retrieval_events.c.advertiser_id,
    retrieval_events.c.created_at,
)
sa.Index(
    "ix_retrieval_events_partition_date",
    retrieval_events.c.tenant_id,
    retrieval_events.c.partition_date,
    retrieval_events.c.partition_bucket,
)
sa.Index(
    "ix_agent_runs_advertiser_created",
    agent_runs.c.tenant_id,
    agent_runs.c.advertiser_id,
    agent_runs.c.created_at,
)
sa.Index(
    "ix_agent_runs_strategy_created",
    agent_runs.c.tenant_id,
    agent_runs.c.strategy_id,
    agent_runs.c.created_at,
)
sa.Index("ix_agent_runs_trace_id", agent_runs.c.trace_id)
sa.Index(
    "ix_agent_runs_idempotency_key",
    agent_runs.c.tenant_id,
    agent_runs.c.idempotency_key,
)
sa.Index(
    "ix_agent_run_steps_run_index",
    agent_run_steps.c.tenant_id,
    agent_run_steps.c.run_id,
    agent_run_steps.c.step_index,
)
sa.Index(
    "ix_agent_run_steps_strategy_index",
    agent_run_steps.c.tenant_id,
    agent_run_steps.c.strategy_id,
    agent_run_steps.c.step_index,
)
sa.Index(
    "ix_agent_run_steps_node_created",
    agent_run_steps.c.tenant_id,
    agent_run_steps.c.node_name,
    agent_run_steps.c.created_at,
)
sa.Index(
    "ix_idempotency_keys_expires_at",
    idempotency_keys.c.tenant_id,
    idempotency_keys.c.expires_at,
)


CORE_TABLES = (
    tenants,
    advertisers,
    campaign_drafts,
    knowledge_documents,
    knowledge_chunks,
    advertiser_memories,
    retrieval_events,
    agent_runs,
    agent_run_steps,
    idempotency_keys,
)

HIGH_VOLUME_TABLES = {
    "campaign_drafts",
    "knowledge_documents",
    "knowledge_chunks",
    "advertiser_memories",
    "retrieval_events",
    "agent_runs",
    "agent_run_steps",
    "idempotency_keys",
}
