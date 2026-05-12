# Run Retry API

## Goal

Add a tenant-aware `POST /runs/{run_id}/retry` API that retries a failed execution as a new execution. The original run remains immutable audit history, and the new run goes through the normal lifecycle persistence path.

## Context

- Relevant files:
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/persistence/run_read_store.py`
  - `tests/test_strategy_api_cli.py`
  - `tests/integration/test_postgres_agent_run_store.py`
- Current behavior:
  - `GET /runs/{run_id}` can read tenant-scoped execution detail.
  - `generate_growth_strategy` creates a new execution row and transitions it through running to completed/failed.
  - There is no API action to retry a failed execution.
- Constraints:
  - Do not mutate or delete the original failed run.
  - Retry creates a new execution ID.
  - Only failed runs are retryable in v0.1.
  - Require the retry brief to match the original run's advertiser and objective.

## Plan

- [x] Add `POST /runs/{run_id}/retry` API using the existing run read store.
- [x] Add retry guards for missing run, non-failed run, and brief mismatch.
- [x] Return the normal `GrowthStrategyResponse` for the new execution and include retry headers.
- [x] Add unit tests for successful retry and guard failures.
- [x] Add live Postgres integration coverage for retry-as-new-execution.
- [x] Update README and verification notes.
- [x] Run default tests and live Postgres integration tests.
- [x] Commit and push the verified slice.

## Decisions

- Decision: Retry is implemented as a new execution, not a resume of the old execution.
  Reason: The existing graph/checkpointer stack can support durable execution, but true resume semantics need a separate slice. Creating a new execution preserves audit history and is honest for v0.1.
- Decision: Use the same `GrowthStrategyRequest` body shape as `POST /growth-strategies`.
  Reason: The platform does not yet persist the full original advertiser brief, so retry needs an explicit brief while validating it against the failed run's identity.

## Discoveries

- Discovery: Run detail currently stores advertiser and objective, which is enough for a conservative retry guard.
  Evidence: `AgentRunDetailResponse` includes `advertiser_id` and `objective`.
- Discovery: Retry can reuse the existing strategy generation path without special persistence code.
  Evidence: `POST /runs/{run_id}/retry` calls the same `_generate_growth_strategy_response`, which records a fresh execution through the normal lifecycle path.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/pytest tests/test_strategy_api_cli.py tests/integration/test_postgres_agent_run_store.py` passed with `14 passed, 1 skipped`.
- [x] Default pytest:
  Result: `.venv/bin/pytest` passed with `87 passed, 8 skipped`.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live PostgreSQL integration pytest:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/pytest tests/integration` passed with `8 passed`.

## Final Status

Implementation, verification, commit, and push are complete.
