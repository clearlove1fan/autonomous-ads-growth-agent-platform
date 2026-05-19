# Database Schema

This document describes the v0.1 production-skeleton schema for the Autonomous Ads Growth Agent Platform.

The schema is intentionally partition-aware from the first migration, even though local development still runs on a single PostgreSQL instance. The goal is to avoid painting the system into a corner when the project later adds replicas, native partitions, or distributed Postgres.

## Design Principles

- Separate business data, RAG knowledge, advertiser memory, agent runtime, and idempotency.
- Include tenant and partition fields on high-volume tables from day one.
- Keep graph runtime decoupled from storage through interfaces such as `KnowledgeStore`.
- Optimize read paths around advertiser-scoped workflows and retrieval-heavy RAG queries.
- Store observability data locally without replacing LangSmith traces.

## Common Partition Fields

High-volume tables include:

| Column | Purpose |
|---|---|
| `tenant_id` | Tenant isolation and future regional routing. |
| `partition_key` | Logical sharding key, usually `advertiser_id`, `run_id`, `document_id`, or `idempotency_key`. |
| `partition_bucket` | Stable hash bucket for future shard balancing. v0.1 uses `0` by default. |
| `partition_date` | Date bucket for time-based pruning and future range partitions. |
| `created_at` | Ordering, retention, and audit. |

The first migration does not create native partitioned tables. Instead it creates partition-ready tables and indexes. Future migrations can convert selected high-volume tables to:

- range partitions by `partition_date`
- hash subpartitions by `partition_bucket`
- distributed shards by `tenant_id` and `partition_key`

## Core Tables

| Table | Data Domain | Primary Access Pattern | Partition Strategy |
|---|---|---|---|
| `tenants` | tenant registry | lookup tenant config | small reference table |
| `advertisers` | business data | tenant + advertiser lookup | hash by `advertiser_id` |
| `campaign_drafts` | draft output | advertiser history, run-created drafts | advertiser hash + time range |
| `campaign_performance_events` | feedback loop events | advertiser/run/campaign/draft event analysis | time range + event hash |
| `strategy_jobs` | async API job state | job polling, advertiser job history | job hash + time range |
| `knowledge_documents` | RAG metadata | source/category/objective filters | source/type/category + document hash |
| `knowledge_chunks` | vector RAG chunks | filtered vector search | source/category prefilter + document hash |
| `advertiser_memories` | long-term memory | advertiser-scoped memory retrieval | advertiser hash |
| `retrieval_events` | retrieval observability | run debug, eval, time retention | monthly range + run hash |
| `agent_runs` | workflow run header | run lookup, advertiser run history | advertiser hash + time range |
| `agent_run_steps` | workflow run details | full run trace by run_id | run hash |
| `idempotency_keys` | retry safety | key lookup before execution | idempotency key hash |

## Access Patterns

### Strategy Generation

1. API receives advertiser brief.
2. `idempotency_keys` is checked when the caller supplies an idempotency key.
3. For synchronous requests, `agent_runs` creates a run record.
4. For asynchronous requests, `strategy_jobs` creates a queued job and soft-links it to the planned `run_id`.
5. `retriever` searches `knowledge_chunks` and `advertiser_memories`.
6. `retrieval_events` records query, filters, top-k results, and latency.
7. `campaign_drafts` stores draft output after finalization.
8. `agent_run_steps` stores node-level execution details.
9. `strategy_jobs` stores completed response JSON or structured failure details for polling when the async job endpoint is used.

### Campaign Feedback

1. API receives a campaign performance event.
2. The feedback analyzer computes CTR, CVR, CPA, and optional ROAS.
3. The system returns draft-only recommendations such as creative refresh, audience narrowing, tracking inspection, or continued monitoring.
4. `campaign_performance_events` stores the raw metrics, normalized event hash, and analysis when persistence is enabled.
5. The event is indexed by advertiser, run, campaign, draft, and occurrence time for replay and later async feedback workflows. `run_id`, `draft_id`, and `campaign_id` are soft links because telemetry can arrive from external campaign systems before this platform has a local run or draft record.
6. Replaying the same `event_id` with the same event hash returns the stored analysis; reusing the same `event_id` with a different payload is rejected as a conflict.

### Retrieval

RAG retrieval should filter before vector ranking:

```sql
where tenant_id = :tenant_id
  and source_type in ('rag_document', 'historical_case')
  and product_categories && :product_categories
  and objectives && :objectives
order by embedding <=> :query_embedding
limit :top_k
```

Advertiser memory retrieval is scoped:

```sql
where tenant_id = :tenant_id
  and advertiser_id = :advertiser_id
  and memory_type in ('profile', 'constraint', 'preference', 'historical_performance')
order by embedding <=> :query_embedding
limit :top_k
```

## Replica Strategy

| Traffic | Preferred Target | Consistency |
|---|---|---|
| campaign draft creation | primary | strong |
| campaign performance event ingestion | primary or async writer | eventual acceptable |
| strategy job create/update | primary | strong |
| strategy job reads | read replica | eventual acceptable |
| idempotency key write/check | primary | strong |
| agent run creation/update | primary | strong |
| agent run history reads | read replica | eventual acceptable |
| retrieval over knowledge chunks | vector read replica | eventual acceptable |
| advertiser memory reads | primary or low-lag replica | read-after-write preferred |
| retrieval event writes | primary or async writer | eventual acceptable |
| analytics/eval/reporting | read replica | eventual acceptable |

The first implementation uses a single Postgres instance. Production deployment should split traffic into:

- Primary writer for strong-consistency mutations.
- General read replicas for history/debug/eval.
- Vector read replicas for retrieval-heavy pgvector queries.

## Hotspot and Balance Plan

Risk: large advertisers can create hot `advertiser_id` partitions.

Mitigations:

- Use `partition_bucket` derived from stable hash of `tenant_id + partition_key`.
- For `agent_run_steps`, use `run_id` as the partition key so one run's trace is colocated.
- For append-only tables like `retrieval_events` and `campaign_performance_events`, combine `partition_date` with hash bucket.
- For async workflow jobs, use `job_id` as the partition key so queue polling and completion updates spread across buckets.
- For high-write campaign performance events, use `event_id` as the partition key to avoid hot advertisers dominating a shard.
- For vector retrieval, filter by source/category/objective before vector ranking.
- Keep `knowledge_chunks` source/category metadata duplicated on the chunk table to avoid mandatory joins in the hot query path.

## Consistency Choices

| Scenario | Requirement |
|---|---|
| campaign draft status and budget | strong |
| idempotency conflict handling | strong |
| strategy job status transition | strong |
| advertiser profile memory update | read-after-write preferred |
| knowledge document ingestion | eventual |
| vector index availability | eventual |
| retrieval event logging | eventual |
| campaign performance event analysis | eventual |
| agent trace analytics | eventual |

## v0.1 Scope

Implemented now:

- SQLAlchemy Core metadata in `src/ads_growth_agent/persistence/schema.py`.
- Alembic scaffold.
- Initial migration `0001_partition_aware_core_schema`.
- Follow-up migrations for execution identity, campaign performance events, and draft-linked feedback lookup.
- Follow-up migration for `strategy_jobs`.
- pgvector columns for `knowledge_chunks` and `advertiser_memories`.
- Partition-ready columns and indexes.
- PostgreSQL-backed knowledge retrieval, run persistence, campaign draft persistence, idempotency, and performance event persistence.
- PostgreSQL-backed strategy job persistence for async API polling.
- Schema tests for table coverage and partition fields.

Not implemented yet:

- Native partition DDL.
- Replica-aware query routing.

Those are intentionally separate slices so the schema can be reviewed before runtime behavior depends on it.
