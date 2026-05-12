from ads_growth_agent.persistence.schema import (
    CORE_TABLES,
    EMBEDDING_DIMENSIONS,
    HIGH_VOLUME_TABLES,
    advertiser_memories,
    agent_run_steps,
    agent_runs,
    idempotency_keys,
    knowledge_chunks,
    knowledge_documents,
    metadata,
    retrieval_events,
)


def test_core_schema_tables_are_defined() -> None:
    assert {table.name for table in CORE_TABLES} == {
        "tenants",
        "advertisers",
        "campaign_drafts",
        "knowledge_documents",
        "knowledge_chunks",
        "advertiser_memories",
        "retrieval_events",
        "agent_runs",
        "agent_run_steps",
        "idempotency_keys",
    }
    assert set(metadata.tables) >= {table.name for table in CORE_TABLES}


def test_high_volume_tables_have_partition_ready_columns() -> None:
    for table_name in HIGH_VOLUME_TABLES:
        columns = metadata.tables[table_name].c

        assert "tenant_id" in columns
        assert "partition_key" in columns
        assert "partition_bucket" in columns
        assert "partition_date" in columns
        assert "created_at" in columns


def test_vector_retrieval_tables_have_expected_embedding_dimension() -> None:
    assert knowledge_chunks.c.embedding.type.dim == EMBEDDING_DIMENSIONS
    assert advertiser_memories.c.embedding.type.dim == EMBEDDING_DIMENSIONS


def test_rag_tables_duplicate_hot_filter_columns_for_prefiltering() -> None:
    for table in (knowledge_documents, knowledge_chunks):
        assert "source_type" in table.c
        assert "product_categories" in table.c
        assert "objectives" in table.c


def test_runtime_tables_are_keyed_for_run_trace_access() -> None:
    assert "run_id" in agent_runs.c
    assert "strategy_id" in agent_runs.c
    assert "run_id" in agent_run_steps.c
    assert "strategy_id" in agent_run_steps.c
    assert "step_index" in agent_run_steps.c

    run_index_names = {index.name for index in agent_runs.indexes}
    index_names = {index.name for index in agent_run_steps.indexes}
    assert "ix_agent_runs_strategy_created" in run_index_names
    assert "ix_agent_run_steps_run_index" in index_names
    assert "ix_agent_run_steps_strategy_index" in index_names


def test_retrieval_events_and_idempotency_support_operational_access_patterns() -> None:
    retrieval_columns = retrieval_events.c
    idempotency_columns = idempotency_keys.c

    assert "run_id" in retrieval_columns
    assert "results" in retrieval_columns
    assert "latency_ms" in retrieval_columns
    assert "idempotency_key" in idempotency_columns
    assert "request_hash" in idempotency_columns
    assert "expires_at" in idempotency_columns
