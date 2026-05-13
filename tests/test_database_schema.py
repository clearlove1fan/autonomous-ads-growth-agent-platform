import sqlalchemy as sa

from ads_growth_agent.persistence.schema import (
    CORE_TABLES,
    EMBEDDING_DIMENSIONS,
    HIGH_VOLUME_TABLES,
    advertiser_memories,
    agent_run_steps,
    agent_runs,
    campaign_performance_events,
    idempotency_keys,
    knowledge_chunks,
    knowledge_documents,
    metadata,
    outbox_events,
    retrieval_events,
    strategy_jobs,
)


def test_core_schema_tables_are_defined() -> None:
    assert {table.name for table in CORE_TABLES} == {
        "tenants",
        "advertisers",
        "campaign_drafts",
        "campaign_performance_events",
        "strategy_jobs",
        "outbox_events",
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
    assert "last_used_at" in advertiser_memories.c
    assert "usage_count" in advertiser_memories.c


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


def test_campaign_performance_events_support_feedback_loop_access_patterns() -> None:
    columns = campaign_performance_events.c
    index_names = {index.name for index in campaign_performance_events.indexes}

    assert "event_id" in columns
    assert "run_id" in columns
    assert "campaign_id" in columns
    assert "draft_id" in columns
    assert "metrics_json" in columns
    assert "analysis_json" in columns
    assert "occurred_at" in columns
    assert "ix_campaign_performance_events_advertiser_occurred" in index_names
    assert "ix_campaign_performance_events_run_occurred" in index_names
    assert "ix_campaign_performance_events_partition_date" in index_names
    assert {
        foreign_key.column.table.name
        for foreign_key in campaign_performance_events.foreign_keys
    } == {"advertisers"}


def test_strategy_jobs_support_async_workflow_access_patterns() -> None:
    columns = strategy_jobs.c
    index_names = {index.name for index in strategy_jobs.indexes}
    check_constraints = {
        constraint.name.removeprefix("ck_strategy_jobs_"): str(constraint.sqltext)
        for constraint in strategy_jobs.constraints
        if isinstance(constraint, sa.CheckConstraint) and constraint.name is not None
    }

    assert "job_id" in columns
    assert "strategy_id" in columns
    assert "run_id" in columns
    assert "trace_id" in columns
    assert "request_json" in columns
    assert "response_json" in columns
    assert "error_json" in columns
    assert "attempt_count" in columns
    assert "max_attempts" in columns
    assert "next_attempt_at" in columns
    assert "locked_by" in columns
    assert "locked_until" in columns
    assert "completed_at" in columns
    assert "ix_strategy_jobs_status_created" in index_names
    assert "ix_strategy_jobs_claimable" in index_names
    assert "ix_strategy_jobs_advertiser_created" in index_names
    assert "ix_strategy_jobs_run_id" in index_names
    assert {
        foreign_key.column.table.name for foreign_key in strategy_jobs.foreign_keys
    } == {"advertisers"}
    assert "cancelled" in check_constraints["strategy_job_status"]
    assert "cancelled" in check_constraints["strategy_job_completed_at_status"]


def test_outbox_events_support_high_concurrency_worker_access_patterns() -> None:
    columns = outbox_events.c
    index_names = {index.name for index in outbox_events.indexes}

    assert "outbox_event_id" in columns
    assert "event_type" in columns
    assert "idempotency_key" in columns
    assert "status" in columns
    assert "payload" in columns
    assert "attempt_count" in columns
    assert "max_attempts" in columns
    assert "locked_by" in columns
    assert "locked_until" in columns
    assert "ix_outbox_events_status_next_attempt" in index_names
    assert "ix_outbox_events_aggregate" in index_names
    assert "ix_outbox_events_partition_date" in index_names
