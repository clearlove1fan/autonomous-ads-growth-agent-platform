# Live Migration Smoke Tests

## Goal

Add live PostgreSQL migration smoke tests for the partition-aware schema. The tests should be opt-in, create an isolated temporary database, run Alembic upgrade/downgrade, and verify key extensions, tables, vector columns, and indexes.

## Context

- Relevant files:
  - `compose.yaml` defines a `postgres` service using `pgvector/pgvector:pg16`.
  - `alembic.ini` and `migrations/` define the current schema migration.
  - `tests/test_database_schema.py` validates schema metadata offline.
  - `pyproject.toml` currently runs all tests by default in CI without Docker services.
- Current behavior:
  - We have metadata-level tests and Alembic offline SQL rendering.
  - No test applies migrations to a real PostgreSQL database yet.
- Constraints:
  - Do not make normal `pytest` depend on Docker/Postgres.
  - Do not mutate the default `ads_growth` database in integration tests.
  - Use an isolated temporary database and clean it up after the test.

## Plan

- [x] Add pytest integration marker.
- [x] Add live migration smoke test guarded by `RUN_POSTGRES_INTEGRATION=1`.
- [x] Document how to run the integration test with Docker Compose.
- [x] Run default tests and lint.
- [x] If Docker is available, start Postgres and run the integration test.
- [x] Commit and push.

## Decisions

- Decision: Make live DB tests opt-in.
  Reason: CI and local offline development should remain deterministic without Docker.
- Decision: Create a temporary database for each integration test run.
  Reason: Migration tests should not drop or alter the developer's default local database.

## Discoveries

- Discovery: `compose.yaml` already exposes Postgres on `localhost:5432`.
  Evidence: The `postgres` service maps `"5432:5432"` and uses `pgvector/pgvector:pg16`.
- Discovery: The migration test uses an isolated database per run.
  Evidence: `tests/integration/test_migrations_postgres.py` creates `ads_growth_test_<uuid>` and drops it in `finally`.
- Discovery: The local Docker daemon was initially stopped but could be opened and used for the integration test.
  Evidence: `open -a Docker`, `docker compose up -d postgres`, and the live pytest command succeeded.
- Discovery: The live migration test requires elevated execution in this environment to reach `localhost:5432`.
  Evidence: The sandboxed run failed with `Operation not permitted`; the escalated run passed.

## Verification

- [x] `.venv/bin/python -m pytest`
  Result: 52 passed, 1 skipped.
- [x] `.venv/bin/python -m ruff check .`
  Result: All checks passed.
- [x] `.venv/bin/python -m compileall src tests migrations`
  Result: Completed successfully.
- [x] `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=... .venv/bin/python -m pytest tests/integration/test_migrations_postgres.py`
  Result: 1 passed against local Docker Postgres.
- [x] `docker compose stop postgres`
  Result: Postgres stopped after integration verification.

## Final Status

Complete. The repo now has opt-in live PostgreSQL migration smoke tests that create an isolated temporary database, run Alembic upgrade/downgrade, and verify key schema objects.
