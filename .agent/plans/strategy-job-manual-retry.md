# Strategy Job Manual Retry

## Goal

Add a control-plane action for operators to manually retry terminal failed
strategy jobs. A failed job should be put back into `queued` state with a fresh
attempt budget, visible audit metadata, and no direct database edits required.

## Context

- Relevant files:
  - `src/ads_growth_agent/persistence/strategy_job_store.py`
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/cli.py`
  - `tests/test_strategy_jobs.py`
  - `tests/integration/test_postgres_strategy_jobs.py`
- Current behavior:
  - Jobs can be listed and inspected.
  - External workers retry transient failures until attempts are exhausted.
  - Exhausted jobs end as `failed`.
  - Operators cannot manually requeue failed jobs.
- Constraints:
  - No schema migration needed; reuse `metadata`, `attempt_count`,
    `max_attempts`, and `next_attempt_at`.
  - Only terminal `failed` jobs should be retried manually.
  - Keep background/local behavior unchanged.

## Plan

- [x] Create this ExecPlan and define manual retry semantics.
- [x] Add `retry_failed` to memory and Postgres strategy job stores.
- [x] Add API `POST /growth-strategies/jobs/{job_id}/retry`.
- [x] Add CLI `retry-strategy-job`.
- [x] Add unit and live Postgres integration tests.
- [x] Update README.
- [x] Run verification, commit, push, and watch CI.

## Decisions

- Decision: Manual retry resets `attempt_count` to 0 and applies the current
  configured `STRATEGY_JOB_MAX_ATTEMPTS`.
  Reason: An operator retry should give the job a fresh budget after an
  intervention, not immediately fail because the previous budget was exhausted.
- Decision: Keep the previous error in `metadata.previous_error` while clearing
  `error`.
  Reason: The requeued job should look actionable as queued work, while still
  preserving triage context.
- Decision: Accept optional operator identity through `X-Operator-ID` for API
  calls and `--requested-by` for CLI calls.
  Reason: Manual retry is an operator action, so the audit trail should record
  who or what initiated the retry instead of only recording the interface name.

## Discoveries

- Discovery: No schema migration was required for manual retry.
  Evidence: Existing `strategy_jobs` columns already include `metadata`,
  `attempt_count`, `max_attempts`, `next_attempt_at`, lock ownership, result,
  error, and completion timestamps.

## Verification

- [x] Targeted tests: `.venv/bin/python -m pytest tests/test_strategy_jobs.py`
  Result: 13 passed.
- [x] Full unit suite: `.venv/bin/python -m pytest`
  Result: 150 passed, 17 skipped.
- [x] Ruff: `.venv/bin/ruff check .`
  Result: All checks passed.
- [x] Live Postgres integration:
  `RUN_POSTGRES_INTEGRATION=1 .venv/bin/python -m pytest tests/integration/test_postgres_strategy_jobs.py`
  Result: 5 passed.
- [x] CI: `gh run watch 25790168738 --exit-status`
  Result: Passed. Jobs: lint, unit, e2e-smoke, postgres-integration, and
  release-readiness.

## Final Status

Complete. Manual retry is available through API and CLI, covered by unit tests,
full local tests, live PostgreSQL integration, and GitHub CI. Docker was stopped
after local live integration.
