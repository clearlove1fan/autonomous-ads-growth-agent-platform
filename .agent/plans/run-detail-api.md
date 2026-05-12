# Run Detail API

## Goal

Expose a tenant-aware `GET /runs/{run_id}` API that returns persisted execution audit detail from Postgres: run status, strategy linkage, trace metadata, final strategy or errors, and ordered run steps.

## Context

- Relevant files:
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/persistence/run_store.py`
  - `src/ads_growth_agent/run_store_factory.py`
  - `tests/test_agent_run_persistence.py`
  - `tests/test_strategy_api_cli.py`
  - `tests/integration/test_postgres_agent_run_store.py`
- Current behavior:
  - Run persistence writes `agent_runs` and `agent_run_steps`, including lifecycle status.
  - There is no API read path for execution audit.
  - API request tenant is already resolved through `X-Tenant-ID`.
- Constraints:
  - Keep default local behavior DB-free.
  - Keep the response schema explicit and stable instead of returning raw DB rows.
  - Preserve tenant isolation on reads.

## Plan

- [x] Add Pydantic response contracts for run detail and run steps.
- [x] Add a run read store with no-op and Postgres implementations.
- [x] Add a factory for request-scoped run read stores.
- [x] Add `GET /runs/{run_id}` with tenant-aware lookup and structured 404.
- [x] Add unit and live Postgres integration tests.
- [x] Update README and verification notes.
- [x] Run default tests and live Postgres integration tests.
- [x] Commit and push the verified slice.

## Decisions

- Decision: Implement run reads in a separate read store instead of extending the command-oriented `AgentRunStore` protocol.
  Reason: Query models and command writes evolve differently; separating them keeps retry/resume work cleaner.
- Decision: Return 404 for missing runs and disabled/no-op persistence.
  Reason: The caller asks for a concrete execution resource; if it cannot be found in the effective tenant scope, the resource is not available.

## Discoveries

- Discovery: The existing request settings dependency already gives us tenant-aware API reads.
  Evidence: `get_request_settings` applies `X-Tenant-ID` before store factories are called.
- Discovery: No schema migration is needed for this read path.
  Evidence: `agent_runs` and `agent_run_steps` already contain the fields needed by `AgentRunDetailResponse`.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/pytest tests/test_agent_run_persistence.py tests/test_strategy_api_cli.py tests/integration/test_postgres_agent_run_store.py` passed with `15 passed, 1 skipped`.
- [x] Default pytest:
  Result: `.venv/bin/pytest` passed with `84 passed, 8 skipped`.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live PostgreSQL integration pytest:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/pytest tests/integration` passed with `8 passed`.

## Final Status

Implementation, verification, commit, and push are complete.
