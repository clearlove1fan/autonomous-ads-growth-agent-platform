# Outbox Ops Visibility

## Goal

Add operator-facing visibility and manual recovery for durable outbox events.
The product loop already queues advertiser-memory side effects through the
outbox and exposes a CLI processor, but operators cannot inspect queued or
failed events through normal product surfaces.

## Scope

- Add typed outbox event list/detail/retry contracts.
- Extend the outbox store interface with tenant-scoped list, detail, and manual
  retry operations.
- Add API endpoints for listing, fetching, manually retrying, and processing a
  bounded outbox batch.
- Add CLI commands for listing, fetching, and retrying outbox events.
- Cover unit and API paths with deterministic tests.
- Update README, RFC, roadmap, and changelog to reflect implemented behavior.

## Non-Goals

- No new database migration unless existing columns are insufficient.
- No distributed DLQ service or external queue.
- No automatic daemon worker.

## Acceptance Criteria

- Operators can inspect pending, processing, completed, and failed outbox
  events with filters.
- Operators can fetch one event and see payload/result/error/attempt metadata.
- Failed events can be requeued manually while completed or active events remain
  protected.
- API and CLI expose equivalent core behavior.
- Existing product-loop outbox processing continues to work.

## Verification

- [x] Focused outbox/API/auth tests pass.
  - Result: `.venv/bin/python -m pytest tests/test_outbox.py tests/test_auth.py`
    passed with 20 passed.
- [x] Focused outbox/API/auth/Postgres integration selection passes.
  - Result:
    `.venv/bin/python -m pytest tests/test_outbox.py tests/test_auth.py tests/integration/test_postgres_outbox_store.py`
    passed with 20 passed, 2 skipped.
- [x] Full unit/e2e suite passes or failures are documented.
  - Result: `.venv/bin/python -m pytest` passed with 307 passed, 21 skipped.
- [x] Ruff and py_compile pass.
  - Result: `.venv/bin/ruff check .` passed.
  - Result:
    `PYTHONPYCACHEPREFIX=/private/tmp/ads_growth_pycache .venv/bin/python -m py_compile $(find src tests scripts -name '*.py')`
    passed.
