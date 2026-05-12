# Async Strategy Job Model

## Goal

Add a first asynchronous strategy-generation API model. The service should accept a strategy request, create a durable/pollable job record, execute the existing graph in the background for local v0.1, and expose job status through a read endpoint. This moves the platform away from only synchronous long-running API calls while keeping the current deterministic workflow intact.

## Context

- Relevant files:
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/strategy.py`
  - `src/ads_growth_agent/persistence/schema.py`
  - `migrations/versions/`
  - `tests/`
- Current behavior:
  - `POST /growth-strategies` executes strategy generation synchronously.
  - Run persistence already records running/completed/failed agent executions when enabled.
  - There is no job abstraction for queueing, polling, or decoupling request latency from graph execution.
- Constraints:
  - Do not introduce an external worker/queue yet.
  - Reuse existing `generate_growth_strategy` so run persistence, RAG, draft persistence, and checkpointing stay in one path.
  - Default local behavior should work without PostgreSQL.
  - PostgreSQL-backed job persistence should be available for production skeleton mode.

## Plan

- [x] Create this ExecPlan.
- [x] Add job contracts for accepted/detail responses and job status.
- [x] Add partition-aware `strategy_jobs` schema and migration.
- [x] Implement in-memory and PostgreSQL strategy job stores.
- [x] Add job-store factory and settings.
- [x] Add `POST /growth-strategies/jobs` and `GET /growth-strategies/jobs/{job_id}`.
- [x] Add unit/API tests plus schema and persistence tests.
- [x] Update docs and roadmap/HLD status.
- [x] Run targeted tests, full tests, ruff, and live Postgres integration when useful.

## Decisions

- Decision: Use an in-process FastAPI `BackgroundTasks` executor for v0.1.
  Reason: It proves the API/job contract without introducing queue infrastructure too early; a real worker queue can replace the executor later.
- Decision: Keep strategy execution on the existing `generate_growth_strategy` path.
  Reason: This preserves existing graph behavior, run persistence, draft persistence, idempotency-adjacent semantics, and tests.
- Decision: Add a separate `strategy_jobs` table instead of overloading `agent_runs`.
  Reason: Jobs and runs are related but different concepts: a job is the API/queue unit, while a run is the graph execution/audit unit.

## Discoveries

- Discovery: The existing `generate_growth_strategy` already accepts a `RunContext`.
  Evidence: `src/ads_growth_agent/strategy.py` passes `run_context` into `run_growth_strategy_graph`, so async jobs can create a planned run ID before background execution and reuse it during graph execution.
- Discovery: Jobs and runs need separate storage semantics.
  Evidence: `agent_runs` records graph execution state only after `generate_growth_strategy` starts; `strategy_jobs` records the API queue/polling state before execution begins.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/pytest tests/test_strategy_jobs.py tests/test_database_schema.py tests/integration/test_postgres_strategy_jobs.py` passed with `11 passed, 1 skipped`.
- [x] Full pytest:
  Result: `.venv/bin/pytest` passed with `115 passed, 11 skipped`.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live Postgres integration:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/pytest tests/integration/test_postgres_strategy_jobs.py tests/integration/test_migrations_postgres.py` passed with `2 passed`.
- [ ] Stop temporary Postgres container:
  Result: Attempted `docker compose stop postgres`, but the tool escalation was rejected due the current usage limit. The container may still be running locally.

## Final Status

Completed. The API now supports pollable async strategy generation jobs through an in-process v0.1 background executor and memory/Postgres job stores. The `strategy_jobs` table is partition-aware and covered by migration, schema tests, API tests, and live Postgres integration. Remaining production work: replace the in-process executor with a durable queue/worker, add outbox/DLQ semantics, and connect job idempotency/rate limits.
