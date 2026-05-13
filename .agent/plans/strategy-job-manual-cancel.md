# Strategy Job Manual Cancel

## Goal

Add an operator control-plane action to manually cancel queued or running
strategy jobs. A cancelled job should become terminal, stop being claimable by
workers, preserve audit metadata, and resist being overwritten by a worker that
finishes after cancellation.

## Context

- Relevant files:
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/persistence/schema.py`
  - `src/ads_growth_agent/persistence/strategy_job_store.py`
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/cli.py`
  - `migrations/versions/`
  - `tests/test_strategy_jobs.py`
  - `tests/integration/test_postgres_strategy_jobs.py`
- Current behavior:
  - Jobs can be created, listed, inspected, retried after failure, and processed
    by background or external workers.
  - There is no operator-facing cancellation path.
  - Worker completion currently writes terminal state without checking that the
    job is still `running`, so a running job cancelled mid-flight would risk
    being overwritten by a late completion.
- Constraints:
  - Preserve tenant scoping.
  - Use explicit terminal status instead of overloading `failed`.
  - Keep queued/running claim logic compatible with high-concurrency workers.

## Plan

- [x] Create this ExecPlan and define cancellation semantics.
- [x] Add `cancelled` strategy job status and migration.
- [x] Add store-level `cancel` operation with memory and Postgres support.
- [x] Guard worker terminal writes so cancelled jobs are not overwritten.
- [x] Add API `POST /growth-strategies/jobs/{job_id}/cancel`.
- [x] Add CLI `cancel-strategy-job`.
- [x] Add unit and live Postgres integration tests.
- [x] Update README.
- [~] Run verification, commit, push, and watch CI.

## Decisions

- Decision: Represent manual cancellation as status `cancelled`.
  Reason: Cancellation is operator intent, not a system failure, and should be
  visible as a separate terminal state in job dashboards and filters.
- Decision: Allow cancellation only from `queued` or `running`.
  Reason: Completed, failed, and already-cancelled jobs are terminal and should
  not be mutated through cancel.
- Decision: Store cancellation reason and operator identity in both `error` and
  metadata.
  Reason: The detail response should expose why the job is terminal, while
  metadata keeps an audit-friendly record of who initiated the transition and
  which state it came from.

## Discoveries

- Discovery: SQLAlchemy naming conventions prefix check constraint names in
  metadata.
  Evidence: Local schema tests saw `ck_strategy_jobs_strategy_job_status`
  instead of raw `strategy_job_status`, so tests normalize the prefix before
  asserting cancellation support.
- Discovery: Cancellation needs terminal write guards, not just a new status.
  Evidence: `mark_completed`, `mark_failed`, and `mark_attempt_failed`
  previously updated by job ID alone; they now only transition jobs that are
  still `running`.

## Verification

- [x] Targeted tests:
  `.venv/bin/python -m pytest tests/test_strategy_jobs.py tests/test_database_schema.py`
  Result: 26 passed.
- [x] Full unit suite: `.venv/bin/python -m pytest`
  Result: 154 passed, 18 skipped.
- [x] Ruff: `.venv/bin/ruff check .`
  Result: All checks passed.
- [x] Live Postgres integration:
  `RUN_POSTGRES_INTEGRATION=1 .venv/bin/python -m pytest tests/integration/test_migrations_postgres.py tests/integration/test_postgres_strategy_jobs.py`
  Result: 7 passed.
- [ ] CI:

## Final Status

Implementation complete locally. Manual cancellation is available through API
and CLI, includes a migration for terminal `cancelled` status, and prevents late
worker completion from overwriting cancellation. Commit, push, and CI watch
remain.
