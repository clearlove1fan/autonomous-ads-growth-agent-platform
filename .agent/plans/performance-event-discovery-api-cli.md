# Performance Event Discovery API And CLI

## Goal

Make persisted campaign performance events discoverable after ingestion. Users
should be able to list recent feedback events by advertiser, run, campaign, or
draft so the feedback loop can be reviewed without knowing every event ID.

## Scope

- Add a campaign performance event list response contract.
- Extend the performance event store protocol with filtered list behavior.
- Implement Noop and PostgreSQL list behavior.
- Add `GET /campaign-events/performance` with tenant-scoped filters.
- Add `list-performance-events` CLI command.
- Add a draft/occurred index for draft-linked feedback discovery.
- Add focused API/CLI/unit tests and update Postgres integration coverage.
- Update README, changelog, roadmap, and RFC notes.

## Plan

- [x] Add this ExecPlan.
- [x] Add list contract and store methods.
- [x] Add draft lookup index and migration.
- [x] Wire FastAPI route and CLI command.
- [x] Add focused unit/API/CLI tests.
- [x] Update live Postgres integration coverage.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [x] Commit and push the slice.

## Decisions

- Decision: Keep list filters optional but tenant-scoped.
  Reason: Operators need recent-event discovery, and tenant isolation remains
  the primary data boundary for v0.1.
- Decision: Support advertiser, run, campaign, draft, and event type filters.
  Reason: These match the existing schema/index shape and the natural feedback
  review paths from strategy run, campaign draft, or campaign ID.
- Decision: Add a `draft_id, occurred_at` index.
  Reason: Draft-linked review is now a product path and should not rely on a
  tenant-wide scan.

## Verification

- [x] Focused tests.
  Result: `.venv/bin/pytest tests/test_campaign_feedback_api.py tests/test_performance_event_persistence.py tests/test_database_schema.py tests/test_auth.py` passed with 35 passed.
- [x] Full pytest.
  Result: `.venv/bin/pytest` passed with 206 passed and 18 skipped.
- [x] Ruff.
  Result: `.venv/bin/ruff check ...` passed for touched implementation and test files.
- [x] `git diff --check`.
  Result: Passed.

## Discoveries

- Discovery: Alembic revision IDs must fit the default 32-character
  `alembic_version.version_num` column.
  Evidence: CI Postgres integration failed when the revision ID was
  `0009_performance_event_draft_index`; the migration file now uses
  `0009_perf_event_draft_idx`.

## Final Status

Implemented and locally verified. CI Postgres integration initially caught the
overlong migration revision ID; the fix is in place and ready for re-run.
