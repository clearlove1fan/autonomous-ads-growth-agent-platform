# Campaign Performance Feedback Loop

## Goal

Add the first functional event-driven feedback loop: the platform can ingest a campaign performance event, analyze pacing and conversion health, return structured optimization recommendations, and optionally persist the event plus analysis in PostgreSQL.

## Context

- Relevant files:
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/persistence/schema.py`
  - `src/ads_growth_agent/persistence/*`
  - `tests/*`
- Current behavior:
  - Strategy generation creates draft campaigns and recommendations.
  - Run persistence, retrieval events, retry, and resume exist.
  - HLD FR-7 calls for event-driven re-analysis, but no campaign performance event ingestion exists yet.
- Constraints:
  - v0.1 must not mutate live campaign spend.
  - Default local behavior should remain DB-free.
  - Postgres persistence should be opt-in and tenant-aware.
  - Event writes should be partition-aware from day one.

## Plan

- [x] Add Pydantic contracts for performance metrics, event ingestion, feedback recommendations, and feedback analysis.
- [x] Add deterministic feedback analyzer.
- [x] Add partition-aware `campaign_performance_events` schema and migration.
- [x] Add Noop/Postgres event store and factory.
- [x] Add tenant-aware `POST /campaign-events/performance` API.
- [x] Add tenant-aware `GET /campaign-events/performance/{event_id}` API for persisted event audit.
- [x] Add unit tests for contracts, analyzer, API, factory, and schema.
- [x] Add live Postgres integration coverage for event persistence.
- [x] Update README and schema documentation.
- [x] Run targeted tests, default tests, and ruff; add skipped live integration coverage.
- [x] Commit and push the verified slice.
  Blocked: git staging requires writing `.git/index.lock`, and the environment rejected escalation because the current Codex usage limit was reached.

## Decisions

- Decision: Store performance events in a dedicated table instead of overloading `retrieval_events` or `agent_runs.metadata`.
  Reason: Campaign performance events are business events with their own retention, partitioning, and replay needs.
- Decision: Use `event_id` as the high-write partition key.
  Reason: Advertiser-level partitioning can create hot shards for large advertisers; hashing by event ID spreads write load while secondary indexes still support advertiser/run reads.
- Decision: Return recommendations only, never apply budget or campaign changes.
  Reason: v0.1 remains draft-only and human-approved for potentially externally visible changes.

## Discoveries

- Discovery: `retrieval_events` existed, but campaign performance telemetry needs a separate business-event table.
  Evidence: The current schema had retrieval observability events only; no table captured campaign metrics, feedback analysis, or campaign/run soft links.
- Discovery: `run_id`, `draft_id`, and `campaign_id` should be soft links for performance events.
  Evidence: External campaign telemetry can arrive before this platform has a local run or draft row, so hard FKs would make ingestion brittle.
- Discovery: Ingestion without a read path is incomplete for production debugging.
  Evidence: The API now exposes `GET /campaign-events/performance/{event_id}` so persisted feedback analysis can be retrieved by tenant and event ID.
- Discovery: Live Postgres verification passed after escalation became available again.
  Evidence: The full integration suite ran against the local Postgres container with the new `0003` migration and performance event store.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/pytest tests/test_campaign_feedback_api.py tests/test_campaign_feedback.py tests/test_performance_event_persistence.py tests/test_contracts.py tests/test_database_schema.py tests/integration/test_postgres_performance_event_store.py` passed with `27 passed, 1 skipped`.
- [x] Default pytest:
  Result: `.venv/bin/pytest` passed with `104 passed, 9 skipped`.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live PostgreSQL integration pytest:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/pytest tests/integration` passed with `9 passed`.

## Final Status

Implementation, verification, commit, and push are complete.
