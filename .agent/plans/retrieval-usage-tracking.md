# Retrieval Usage Tracking

## Goal

Track advertiser memory usage without updating memory rows on the retrieval
request path. When Postgres RAG retrieves advertiser memories, the system can
enqueue durable usage events and a worker can update `last_used_at` plus
`usage_count` asynchronously.

## Context

- Relevant files:
  - `src/ads_growth_agent/persistence/knowledge_store.py`
  - `src/ads_growth_agent/persistence/advertiser_memory_store.py`
  - `src/ads_growth_agent/persistence/schema.py`
  - `src/ads_growth_agent/outbox.py`
  - `src/ads_growth_agent/knowledge_store_factory.py`
  - `tests/integration/test_postgres_knowledge_store.py`
- Current behavior:
  - Postgres RAG records `retrieval_events`.
  - `advertiser_memories.last_used_at` exists but is never updated.
  - `outbox_events` can asynchronously process feedback-derived memory writes.
- Constraints:
  - Default local behavior should remain DB-free and tracking-free.
  - Retrieval must not synchronously update advertiser memory rows.
  - Outbox event IDs should be idempotent per run/source so retries or replayed retrievals do not create duplicate usage work for the same run.

## Plan

- [x] Add this ExecPlan.
- [x] Add `usage_count` to advertiser memories plus migration.
- [x] Add a memory usage tracking setting and wire Postgres knowledge store factory.
- [x] Enqueue `advertiser_memory_retrieved` outbox events from Postgres retrieval when enabled.
- [x] Extend the outbox worker to process memory usage events.
- [x] Add unit tests for worker behavior and factory wiring.
- [x] Add live Postgres integration proving retrieval enqueue + worker update `last_used_at` and `usage_count`.
- [x] Update docs and env example.
- [x] Run targeted tests, full tests, ruff, and live integration.
- [ ] Commit and push the verified slice.

## Decisions

- Decision: Store `usage_count` as a column, not only metadata.
  Reason: Usage count is operational state likely to influence ranking and dashboards, so it should be typed and queryable.
- Decision: Use outbox events for memory usage updates.
  Reason: Updating `last_used_at` directly in retrieval would turn a hot read path into a row-update path and increase lock contention.

## Discoveries

- Discovery: Later migrations that add columns must tolerate fresh installs where `0001` creates current metadata.
  Evidence: The first live migration run failed because `metadata.create_all()` in `0001` had already created `advertiser_memories.usage_count`; `0006` now uses `ADD COLUMN IF NOT EXISTS`.
- Discovery: Alembic revision IDs must fit the default `alembic_version.version_num` length.
  Evidence: The initial `0006_advertiser_memory_usage_count` revision string was too long and failed with `value too long for type character varying(32)`; the revision ID is now `0006_memory_usage_count`.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/python -m pytest tests/test_outbox.py tests/test_knowledge_store_factory.py tests/test_database_schema.py tests/test_health.py` passed with 28 passed.
- [x] Full pytest:
  Result: `.venv/bin/python -m pytest` passed with 140 passed and 13 skipped.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live Postgres integration:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/python -m pytest tests/integration` passed with 13 passed. Docker Postgres was stopped with `docker compose down`.

## Final Status

Completed. Postgres RAG can now enqueue `advertiser_memory_retrieved` events
when `MEMORY_USAGE_TRACKING_BACKEND=outbox` is enabled. The outbox worker
updates `advertiser_memories.last_used_at` and `usage_count` asynchronously, and
live integration verifies retrieval enqueue, worker processing, and persisted
usage state.
