# Strategy Job Ops Visibility

## Goal

Add an operator-facing visibility slice for asynchronous strategy jobs. Operators
should be able to list recent jobs by status and advertiser from the API and CLI
so queued retries, running leases, completed jobs, and terminal failures are
easy to inspect without querying PostgreSQL manually.

## Context

- Relevant files:
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/persistence/strategy_job_store.py`
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/cli.py`
  - `tests/test_strategy_jobs.py`
  - `tests/integration/test_postgres_strategy_jobs.py`
- Current behavior:
  - `POST /growth-strategies/jobs` creates async jobs.
  - `GET /growth-strategies/jobs/{job_id}` returns one job.
  - `process-strategy-jobs` can claim jobs and perform retry/backoff.
  - There is no list API/CLI for queue inspection or failed-job triage.
- Constraints:
  - No new schema needed.
  - Keep response contracts typed and tenant-aware.
  - Keep list pagination minimal for this slice with a bounded `limit`.

## Plan

- [x] Create this ExecPlan and define the bounded ops visibility scope.
- [x] Add `StrategyJobListResponse` contract.
- [x] Add `list_jobs` to memory and Postgres strategy job stores.
- [x] Add `GET /growth-strategies/jobs` with `status`, `advertiser_id`, and
      `limit` filters.
- [x] Add CLI `list-strategy-jobs`.
- [x] Add unit and live Postgres integration coverage.
- [x] Update README.
- [x] Run verification, commit, push, and watch CI.

## Decisions

- Decision: Return full `StrategyJobDetailResponse` items in the first list API.
  Reason: The detail shape already contains retry, lease, error, and result
  fields operators need; a lighter summary model can come later if payload size
  becomes a problem.
- Decision: Keep pagination to a bounded `limit` for this slice.
  Reason: It provides immediate operational value without introducing cursor
  contracts prematurely.

## Discoveries

- Discovery: Exact-path `GET /growth-strategies/jobs` can coexist with the
  existing `GET /growth-strategies/jobs/{job_id}` detail route.
  Evidence: FastAPI distinguishes the exact list route from the dynamic detail
  route; unit coverage calls both paths.
- Discovery: The list API can reuse the full job detail contract without adding
  schema or read-model tables.
  Evidence: `StrategyJobDetailResponse` already carries retry timing, lease
  owner, attempts, error payload, and result payload.

## Verification

- [x] Targeted tests:
  Result: `.venv/bin/python -m pytest tests/test_strategy_jobs.py tests/test_database_schema.py` passed with 19 passed.
- [x] Full unit suite:
  Result: `.venv/bin/python -m pytest` passed with 147 passed, 16 skipped.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live Postgres integration:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/python -m pytest tests/integration/test_postgres_strategy_jobs.py` passed with 4 passed.
- [x] Docker cleanup:
  Result: `docker compose down` stopped and removed the local Postgres container.
- [x] CI:
  Result: GitHub Actions CI run `25788142029` passed, including unit, lint,
  e2e-smoke, postgres-integration, and release-readiness.

## Final Status

Complete. Operators can now list strategy jobs through the API and CLI with
status, advertiser, and limit filters. The list response reuses the typed job
detail contract so retry timing, leases, attempts, errors, and results are
visible for queue inspection and failed-job triage. Local tests, live Postgres
integration, and GitHub Actions CI all passed.
