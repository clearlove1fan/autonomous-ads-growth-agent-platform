# Agent Run Persistence

## Goal

Persist completed and failed strategy generation runs into PostgreSQL so the platform has a queryable audit trail for run metadata, node path, tool results, final strategy payloads, and failure summaries.

When complete:

- Default local behavior remains offline with `RUN_PERSISTENCE_BACKEND=none`.
- `RUN_PERSISTENCE_BACKEND=postgres` writes `agent_runs` and `agent_run_steps`.
- Repeated deterministic runs replace the same run id's derived step rows instead of creating duplicates.
- Live integration tests verify successful strategy generation is persisted against a migrated PostgreSQL database.

## Context

- Relevant files:
  - `src/ads_growth_agent/strategy.py`
  - `src/ads_growth_agent/config.py`
  - `src/ads_growth_agent/persistence/schema.py`
  - `src/ads_growth_agent/persistence/partitioning.py`
  - `tests/integration/`
- Current behavior:
  - `RunMetadata` is returned in API/CLI responses and emitted in logs.
  - The schema already contains `agent_runs` and `agent_run_steps`.
  - No runtime code writes those tables yet.
- Constraints:
  - Keep graph orchestration independent from persistence details.
  - Do not require Postgres for default tests or demos.
  - The current run id is deterministic from the advertiser brief, so persistence must be idempotent for repeated identical runs.

## Plan

- [x] Add `RUN_PERSISTENCE_BACKEND=none|postgres` setting.
- [x] Implement no-op and Postgres run-store adapters.
- [x] Wire strategy generation to record completed and failed runs through the adapter.
- [x] Add offline tests for factory and success/failure recording hooks.
- [x] Add live Postgres integration test for `agent_runs` and `agent_run_steps`.
- [x] Update docs and roadmap.
- [x] Run default and Docker-backed verification.
- [ ] Commit and push.

## Decisions

- Decision: Keep persistence outside LangGraph nodes for this slice.
  Reason: The immediate goal is queryable run audit, not per-node durable execution. A later LangGraph checkpointer slice can address resumability.
- Decision: Replace existing step rows for the same deterministic `run_id`.
  Reason: The current run id is deterministic; replacing derived rows prevents stale steps when a run path changes.
- Decision: Make Postgres run persistence opt-in.
  Reason: The default interview/demo path should stay fast, deterministic, and database-free.

## Discoveries

- Discovery:
- Discovery: Default tests remain DB-free; the new live run-store test is skipped unless explicitly enabled.
  Evidence: `.venv/bin/pytest` reported `62 passed, 4 skipped`.
- Discovery: Live Postgres verification passed for migrations, knowledge retrieval, and agent run persistence together.
  Evidence: `RUN_POSTGRES_INTEGRATION=1 ... .venv/bin/pytest tests/integration` reported `4 passed`.
- Discovery: The Postgres container was stopped after verification.
  Evidence: `docker compose stop postgres` completed.

## Verification

- [x] `.venv/bin/python -m compileall src tests`
  Result: Passed.
- [x] `.venv/bin/ruff check .`
  Result: Passed.
- [x] `.venv/bin/pytest`
  Result: `62 passed, 4 skipped`.
- [x] `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=... .venv/bin/pytest tests/integration`
  Result: `4 passed`.

## Final Status

Implementation and verification are complete. Commit and push are pending.
