# Run Resume API

## Goal

Add a tenant-aware `POST /runs/{run_id}/resume` API that resumes a persisted execution using the same `run_id`. In v0.1, this establishes the product API and audit semantics for durable execution; when a LangGraph checkpointer is enabled, the same checkpoint thread can be reused.

## Context

- Relevant files:
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/strategy.py`
  - `src/ads_growth_agent/persistence/run_store.py`
  - `tests/test_strategy_api_cli.py`
  - `tests/integration/test_postgres_agent_run_store.py`
- Current behavior:
  - `GET /runs/{run_id}` can read tenant-scoped execution detail.
  - `POST /runs/{run_id}/retry` creates a fresh execution for a failed run.
  - Run metadata does not yet store the original advertiser brief.
- Constraints:
  - Resume reuses the original run ID.
  - Completed runs are not resumable.
  - Resume must be tenant-scoped and use the stored original advertiser brief.
  - If no checkpointer is configured, v0.1 resume behaves as same-run replay.

## Plan

- [x] Persist `advertiser_brief` in `agent_runs.metadata`.
- [x] Allow `generate_growth_strategy` to accept an external `RunContext`.
- [x] Add `POST /runs/{run_id}/resume` with missing-run, completed-run, missing-brief, and brief-mismatch guards.
- [x] Return the normal `GrowthStrategyResponse` and include resume headers.
- [x] Add unit tests for successful resume and guard failures.
- [x] Add live Postgres integration coverage for same-run resume.
- [x] Update README with resume behavior.
- [x] Run default tests and live Postgres integration tests.
- [x] Commit and push the verified slice.

## Decisions

- Decision: Resume uses the stored original `AdvertiserBrief`, not a request body.
  Reason: Resume should recover the same execution intent instead of accepting a potentially different user request.
- Decision: Completed runs are rejected with `RUN_NOT_RESUMABLE`.
  Reason: Completed runs already have a terminal final strategy; replaying them under the same execution would blur audit semantics.
- Decision: Without a configured LangGraph checkpointer, the API still reuses the run ID and reports replay mode.
  Reason: This gives a stable v0.1 product contract while keeping the implementation honest about runtime durability.

## Discoveries

- Discovery: The in-memory LangGraph checkpointer is process-local and freshly opened per workflow invocation in this project, so the API reports checkpoint resume mode only for the Postgres checkpointer.
  Evidence: `open_configured_graph_checkpointer` creates a new `MemorySaver()` in the workflow context manager, while Postgres uses external storage.
- Discovery: Resume requires persisting the original brief, not only advertiser/objective identity.
  Evidence: `strategy_id_for_brief` hashes the full normalized `AdvertiserBrief`, so partial identity is not enough to safely reconstruct the original strategy intent.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/pytest tests/test_strategy_api_cli.py tests/test_agent_run_persistence.py` passed with `22 passed`.
- [x] Default pytest:
  Result: `.venv/bin/pytest` passed with `91 passed, 8 skipped`.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live PostgreSQL integration pytest:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/pytest tests/integration` passed with `8 passed`.

## Final Status

Implementation, verification, commit, and push are complete.
