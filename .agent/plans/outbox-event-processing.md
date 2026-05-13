# Outbox Event Processing

## Goal

Move feedback-derived advertiser memory writes off the synchronous API path for
better high-concurrency behavior. Campaign performance ingestion should enqueue a
durable outbox event, and a worker/CLI should process that event into
`advertiser_memories`.

## Context

- Relevant files:
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/cli.py`
  - `src/ads_growth_agent/config.py`
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/persistence/schema.py`
  - `src/ads_growth_agent/persistence/advertiser_memory_store.py`
  - `tests/test_campaign_feedback_api.py`
  - `tests/integration/test_postgres_performance_event_store.py`
- Current behavior:
  - `POST /campaign-events/performance` can synchronously write advertiser memory.
  - This gives a functional memory loop, but high-concurrency ingestion is coupled
    to memory writes and can amplify latency/locking.
- Constraints:
  - Default local behavior must stay DB-free.
  - Outbox processing must be tenant-aware, idempotent, and safe under multiple workers.
  - v0.1 still generates drafts/recommendations only; no live ad mutation.

## Plan

- [x] Add this ExecPlan.
- [x] Add partition-aware `outbox_events` schema and migration.
- [x] Add Postgres outbox store with idempotent enqueue and `FOR UPDATE SKIP LOCKED` claiming.
- [x] Add outbox worker that handles `campaign_performance_analyzed` events by writing advertiser memory.
- [x] Wire campaign performance ingestion to queue memory writes when `OUTBOX_BACKEND=postgres`.
- [x] Add CLI command for bounded local worker processing.
- [x] Add unit tests for schema, factory, API queue behavior, worker behavior, and CLI.
- [x] Update live Postgres integration to verify queued event processing and later RAG retrieval.
- [x] Update docs and env example.
- [x] Run targeted tests, full tests, ruff, and live integration.
- [ ] Commit and push the verified slice.

## Decisions

- Decision: Keep a direct synchronous memory-write fallback when `OUTBOX_BACKEND=none`.
  Reason: This preserves the current simple local/product demo behavior while allowing production-like deployments to enable async outbox processing.
- Decision: Use `SELECT ... FOR UPDATE SKIP LOCKED` for worker claims.
  Reason: Multiple workers can process pending events concurrently without double-claiming the same row.

## Discoveries

- Discovery: The previous memory write was functionally correct but sat on the API request path.
  Evidence: `POST /campaign-events/performance` directly called `memory_store.record_feedback_memory(...)` before returning.
- Discovery: The schema migration pattern can safely add `outbox_events` through a new migration even though `0001` uses current metadata for fresh installs.
  Evidence: Existing later migrations create tables with `checkfirst=True`, and live migration tests passed with the new `0005_outbox_events` revision.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/python -m pytest tests/test_outbox.py tests/test_campaign_feedback_api.py tests/test_database_schema.py tests/test_health.py tests/test_strategy_api_cli.py` passed with 51 passed.
- [x] Full pytest:
  Result: `.venv/bin/python -m pytest` passed with 135 passed and 13 skipped.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live Postgres integration:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/python -m pytest tests/integration` passed with 13 passed. Docker Postgres was stopped with `docker compose down`.

## Final Status

Completed. Campaign performance ingestion can now enqueue advertiser memory work
through a tenant-aware Postgres outbox. The bounded worker processes
`campaign_performance_analyzed` events into `advertiser_memories`, and live
integration proves the resulting memory is retrievable by later Postgres RAG.
The synchronous write path remains available when `OUTBOX_BACKEND=none`.
