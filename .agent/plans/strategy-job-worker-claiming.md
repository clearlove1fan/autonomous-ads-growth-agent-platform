# Strategy Job Worker Claiming

## Goal

Make asynchronous strategy generation jobs more production-shaped by adding a
durable worker path. PostgreSQL-backed jobs should be claimable by multiple
workers without duplicate claims, using row locks and short leases. The API
should still support local background execution by default, while production-like
configuration can leave jobs queued for an external worker command.

## Context

- Relevant files:
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/cli.py`
  - `src/ads_growth_agent/config.py`
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/persistence/schema.py`
  - `src/ads_growth_agent/persistence/strategy_job_store.py`
  - `tests/test_strategy_jobs.py`
  - `tests/integration/test_postgres_strategy_jobs.py`
- Current behavior:
  - `POST /growth-strategies/jobs` creates a job and immediately schedules a
    FastAPI background task.
  - `PostgresStrategyJobStore` persists status/result/error, but does not expose
    a worker claim API or lease metadata.
  - The README honestly calls the current executor a local v0.1 background task.
- Constraints:
  - Keep local demo behavior unchanged by default.
  - Prefer Postgres row locking with `SKIP LOCKED` for multi-worker concurrency.
  - Do not introduce Redis/Celery/Kafka yet.
  - Later migrations must be idempotent because early migrations create current
    metadata on fresh installs.

## Plan

- [x] Add this ExecPlan and record implementation decisions.
- [x] Add strategy job lease/attempt columns and an idempotent Alembic migration.
- [x] Extend the strategy job store with `claim_queued` using `FOR UPDATE SKIP LOCKED`.
- [x] Add reusable worker execution logic and a CLI command for bounded processing.
- [x] Add API config so local background execution remains default and external
      worker mode leaves jobs queued.
- [x] Add unit tests, schema tests, and live Postgres integration for distinct
      concurrent claims.
- [ ] Run verification, commit, push, and watch CI.

## Decisions

- Decision: Keep FastAPI background execution as the default mode.
  Reason: Existing local demos and smoke tests stay fast while production-like
  worker mode becomes opt-in.
- Decision: Use PostgreSQL job rows as the durable queue for this slice.
  Reason: This matches the current v0.1 stack and avoids adding queue
  infrastructure before the job contract is stable.
- Decision: Use worker leases and `SKIP LOCKED` rather than application-level
  mutexes.
  Reason: Database locking is the source of truth across processes and hosts.

## Discoveries

- Discovery: `strategy_jobs` already has partition-aware fields and indexes but
  lacks worker claim metadata.
  Evidence: `src/ads_growth_agent/persistence/schema.py` defines `status`,
  `request_json`, `response_json`, `error_json`, partition columns, and status
  indexes only.
- Discovery: FastAPI background execution can reuse the same worker execution
  helper after a normal `mark_running` claim.
  Evidence: `src/ads_growth_agent/api.py` now schedules
  `execute_background_strategy_job`, while CLI workers call
  `process_configured_strategy_jobs`.
- Discovery: Alembic offline SQL generation uses a `MockConnection` that does
  not expose `exec_driver_sql`.
  Evidence: `.venv/bin/alembic upgrade head --sql` failed until
  `migrations/versions/0007_strategy_job_worker_leases.py` switched to
  `op.execute`.

## Verification

- [x] Targeted tests:
  Result: `.venv/bin/python -m pytest tests/test_strategy_jobs.py tests/test_database_schema.py` passed with 14 passed.
- [x] Full unit suite:
  Result: `.venv/bin/python -m pytest` passed with 142 passed, 14 skipped.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Alembic offline SQL smoke:
  Result: `.venv/bin/alembic upgrade head --sql >/tmp/ads_growth_alembic_worker.sql && tail -n 80 /tmp/ads_growth_alembic_worker.sql` passed after changing the migration to use `op.execute`.
- [x] CLI registration:
  Result: `.venv/bin/ads-growth-agent --help` lists `process-strategy-jobs`.
- [ ] Live Postgres integration:
  Result: Not run in this turn because `docker compose up -d postgres` was blocked by the Codex usage-limit approval gate before the container could start.
- [ ] Commit/push:
  Result: Blocked. `git add ...` could not write `.git/index.lock` inside the sandbox, and the required escalation was rejected by the Codex usage-limit approval gate.
- [ ] CI:

## Final Status

Implementation is complete and local verification passed. The change adds
durable strategy job worker claiming, lease metadata, an external worker mode,
and `process-strategy-jobs`. Commit, push, CI, and live Postgres integration are
blocked until the Codex usage-limit approval gate allows escalated Docker/Git
operations again.
