# Advertiser Memory Write Path

## Goal

Turn campaign performance feedback into durable advertiser memory. When a
performance event is ingested, the platform can write a tenant-aware
`historical_performance` memory that later strategy-generation runs can retrieve
through the existing Postgres knowledge store.

## Context

- Relevant files:
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/config.py`
  - `src/ads_growth_agent/health.py`
  - `src/ads_growth_agent/persistence/schema.py`
  - `src/ads_growth_agent/persistence/knowledge_store.py`
  - `src/ads_growth_agent/persistence/performance_event_store.py`
  - `tests/test_campaign_feedback_api.py`
  - `tests/integration/test_postgres_performance_event_store.py`
- Current behavior:
  - `POST /campaign-events/performance` analyzes and optionally persists a
    performance event.
  - `advertiser_memories` already exists and Postgres RAG can retrieve memories.
  - Feedback analysis is not yet written into advertiser memory.
- Constraints:
  - Default local behavior must remain DB-free.
  - Memory persistence should be opt-in.
  - v0.1 still emits recommendations and drafts only; it must not mutate live ads.
  - The write path must be tenant-aware and partition-aware.

## Plan

- [x] Add this ExecPlan.
- [x] Add an opt-in advertiser memory persistence setting and readiness behavior.
- [x] Add Noop/Postgres advertiser memory store with deterministic memory source IDs.
- [x] Wire the memory store into campaign performance event ingestion.
- [x] Add unit tests for config/factory/API behavior.
- [x] Add live Postgres integration coverage proving feedback memory can be retrieved by RAG.
- [x] Update docs and examples for the new persistence backend.
- [x] Run targeted tests, full tests, ruff, and live integration.
- [ ] Commit and push the verified slice.

## Decisions

- Decision: Keep advertiser memory persistence disabled by default.
  Reason: Local demos and unit tests should remain model-key-free and DB-free unless a backend is explicitly enabled.
- Decision: Use deterministic `memory:performance:<hash>:v1` source IDs.
  Reason: The same performance event should map to a stable source citation while staying short enough for existing citation contracts.
- Decision: Start with synchronous writes in the API path.
  Reason: The current service is still v0.1; synchronous writes make correctness and tests easier before introducing an event queue/outbox.

## Discoveries

- Discovery: `advertiser_memories` already has the schema needed for feedback-derived long-term memory.
  Evidence: The table supports `historical_performance`, metadata JSONB, partition columns, importance scoring, and existing Postgres retrieval reads it as `advertiser_memory`.
- Discovery: Event feedback can be grounded without adding product category to the event contract.
  Evidence: Existing memory retrieval matches on objective metadata as well as product category and FTS, so feedback memories include `objectives: [event.objective]`.
- Discovery: Local live integration needs elevated sandbox permissions for Python to connect to Docker's mapped Postgres port.
  Evidence: The first `RUN_POSTGRES_INTEGRATION=1 ... pytest tests/integration` attempt failed with `Operation not permitted`; the same command passed after escalation.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/python -m pytest tests/test_campaign_feedback_api.py tests/test_advertiser_memory_persistence.py tests/test_health.py` passed with 18 passed.
- [x] Full pytest:
  Result: `.venv/bin/python -m pytest` passed with 126 passed and 12 skipped.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live Postgres integration:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/python -m pytest tests/integration` passed with 12 passed after running with Docker/localhost permissions. Docker Postgres was stopped with `docker compose down`.

## Final Status

Implementation and verification are complete. Campaign performance ingestion now has an opt-in advertiser memory write path, API responses expose memory persistence metadata, and live Postgres coverage proves a feedback-derived memory can be retrieved by later strategy generation through `PostgresKnowledgeStore`.
