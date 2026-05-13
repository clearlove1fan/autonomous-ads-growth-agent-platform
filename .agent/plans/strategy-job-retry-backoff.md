# Strategy Job Retry Backoff

## Goal

Add production-style retry semantics to durable strategy jobs. External workers
should retry transient failures with bounded exponential backoff and only mark a
job terminal `failed` after `max_attempts` is exhausted. Local FastAPI background
execution should keep its simpler one-shot failure behavior.

## Context

- Relevant files:
  - `src/ads_growth_agent/config.py`
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/persistence/strategy_job_store.py`
  - `src/ads_growth_agent/strategy_job_worker.py`
  - `tests/test_strategy_jobs.py`
  - `tests/integration/test_postgres_strategy_jobs.py`
- Current behavior:
  - Worker claim metadata exists: `attempt_count`, `max_attempts`,
    `next_attempt_at`, `locked_by`, `locked_until`.
  - `claim_queued` increments `attempt_count` and marks jobs running.
  - `mark_failed` always marks jobs terminal `failed`.
- Constraints:
  - Do not introduce a separate queue service yet.
  - Keep default background API behavior straightforward for local demos.
  - Avoid a schema migration unless we need new columns; the current schema
    already has retry scheduling fields.

## Plan

- [x] Capture the plan and current failure semantics.
- [x] Add settings for strategy job retry attempts and backoff bounds.
- [x] Persist configured `max_attempts` when jobs are created.
- [x] Add store support for retryable failure scheduling.
- [x] Update external worker execution to retry with exponential backoff.
- [x] Expose `next_attempt_at` in job detail responses.
- [x] Add unit and integration coverage for retry scheduling and retry
      exhaustion.
- [x] Run verification, commit, push, and watch CI.

## Decisions

- Decision: Reuse `status=queued` plus `next_attempt_at` for scheduled retries.
  Reason: It keeps the queue model simple and works with the existing claim
  query.
- Decision: Keep terminal failures as `status=failed` instead of introducing a
  new `dead_letter` status in this slice.
  Reason: Existing API clients already understand `failed`; DLQ listing can be a
  later read-model/API feature.
- Decision: Apply retry semantics only to external worker processing.
  Reason: Background mode is for local development and has no long-running
  worker loop to pick up delayed retries automatically.

## Discoveries

- Discovery: The previous worker slice already added all columns needed for
  retry scheduling.
  Evidence: `strategy_jobs` includes `attempt_count`, `max_attempts`, and
  `next_attempt_at`.
- Discovery: Local integration tests that connect to Docker PostgreSQL need
  escalated execution in this sandbox.
  Evidence: Non-escalated pytest hit `Operation not permitted` connecting to
  `localhost:5432`; the same command passed with escalation.
- Discovery: The in-memory job store must also honor `next_attempt_at` or unit
  tests can drift from PostgreSQL queue semantics.
  Evidence: `InMemoryStrategyJobStore.claim_queued` now filters queued jobs by
  `next_attempt_at`, and `tests/test_strategy_jobs.py` covers delayed retry
  claims.

## Verification

- [x] Targeted tests:
  Result: `.venv/bin/python -m pytest tests/test_strategy_jobs.py tests/test_database_schema.py` passed with 17 passed.
- [x] Full unit suite:
  Result: `.venv/bin/python -m pytest` passed with 145 passed, 15 skipped.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live Postgres integration:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/python -m pytest tests/integration/test_postgres_strategy_jobs.py` passed with 3 passed after rerunning with local network escalation.
- [x] Docker cleanup:
  Result: `docker compose down` stopped and removed the local Postgres container.
- [x] CI:
  Result: GitHub Actions CI run `25784038410` passed, including unit, lint,
  e2e-smoke, postgres-integration, and release-readiness.

## Final Status

Complete. External strategy job workers now schedule retryable failures with
bounded exponential backoff, expose retry timing through job details, and mark
jobs terminal `failed` only after attempts are exhausted. Local and remote unit,
lint, e2e, and PostgreSQL integration checks passed.
