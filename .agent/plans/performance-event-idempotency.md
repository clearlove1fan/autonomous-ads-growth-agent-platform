# Performance Event Idempotency

## Goal

Make campaign performance event ingestion idempotent and conflict-safe. Replaying the same event payload with the same `event_id` should return the already persisted analysis; reusing the same `event_id` for different metrics or metadata should return a structured conflict and must not overwrite historical telemetry.

## Context

- Relevant files:
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/persistence/performance_event_store.py`
  - `tests/test_campaign_feedback_api.py`
  - `tests/integration/test_postgres_performance_event_store.py`
- Current behavior:
  - `POST /campaign-events/performance` analyzes an event and persists it when PostgreSQL persistence is enabled.
  - `GET /campaign-events/performance/{event_id}` reads a persisted event.
  - PostgreSQL persistence currently upserts on duplicate `event_id`, which can silently overwrite telemetry.
- Constraints:
  - Keep default no-DB behavior simple.
  - Keep tenant scoping.
  - Preserve the existing event table shape by storing the event fingerprint in metadata.

## Plan

- [x] Add deterministic event fingerprinting.
- [x] Store `event_hash` in persisted event metadata.
- [x] Replay same-hash events from the persisted detail response.
- [x] Reject same `event_id` with different payload as HTTP 409.
- [x] Add store-level conflict protection inside the PostgreSQL transaction.
- [x] Add unit and live integration tests.
- [x] Run targeted tests, default tests, ruff, and live integration tests.
- [x] Commit and push the verified slice.

## Decisions

- Decision: Store the event hash in `metadata` instead of adding a new column.
  Reason: The table already has metadata and this is a narrow reliability improvement; a dedicated indexed column can be added later if high-volume conflict checks need it.
- Decision: Treat the full normalized request payload as the fingerprint input.
  Reason: For telemetry, changing metrics, attribution window, notes, or references changes the event meaning.
- Decision: Replay same-hash events instead of re-running the analyzer.
  Reason: Replaying persisted analysis preserves audit stability if analyzer logic changes later.

## Discoveries

- Discovery: API-level duplicate checks are useful for replay, but the store also needs transaction-level protection.
  Evidence: `PostgresCampaignPerformanceEventStore.record_analyzed` now locks an existing event row before deciding whether to no-op or reject a conflicting payload.
- Discovery: Persisted analysis should be replayed rather than regenerated.
  Evidence: The API returns the stored `CampaignFeedbackAnalysis` for matching event hashes, which keeps audit output stable if analyzer logic evolves.
- Discovery: The prior feedback-loop plan still had a stale blocked note after the successful commit/push.
  Evidence: This slice removes that stale note while adding the idempotency behavior.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/pytest tests/test_campaign_feedback_api.py tests/test_campaign_feedback.py tests/test_performance_event_persistence.py tests/test_contracts.py tests/test_database_schema.py tests/integration/test_postgres_performance_event_store.py` passed with `30 passed, 1 skipped`.
- [x] Default pytest:
  Result: `.venv/bin/pytest` passed with `107 passed, 9 skipped`.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live PostgreSQL integration pytest:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/pytest tests/integration` passed with `9 passed`.

## Final Status

Implementation, verification, commit, and push are complete.
