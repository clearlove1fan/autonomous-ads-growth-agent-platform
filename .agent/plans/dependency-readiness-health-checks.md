# Dependency Readiness Health Checks

## Goal

Add production-style liveness and readiness health checks. The API should distinguish "process is alive" from "configured runtime dependencies are reachable." When this work is done, `/health/live` should be a shallow liveness check, `/health/ready` should validate configured Postgres and LiteLLM dependencies, and tests should cover ready and not-ready behavior without requiring external services.

## Context

- Relevant files:
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/config.py`
  - `tests/test_health.py`
  - `README.md`
  - `compose.yaml`
- Current behavior:
  - `/health` returns a simple `ok` response.
  - Docker Compose healthcheck calls `/health`.
  - Postgres and LiteLLM are optional for the default deterministic local workflow, but become required when specific backends or LLM feature flags are enabled.
- Constraints:
  - Default local behavior must not fail readiness just because Postgres or LiteLLM is not running.
  - Readiness must fail when a configured required dependency is unavailable.
  - Tests must not depend on live Docker services.

## Plan

- [x] Create this ExecPlan.
- [x] Add health/readiness models and dependency checker implementation.
- [x] Add `/health/live` and `/health/ready` endpoints while keeping `/health` backward compatible.
- [x] Update Docker Compose and README to use/read the readiness endpoint.
- [x] Add unit/API tests for liveness, skipped dependencies, failed Postgres readiness, and LiteLLM readiness.
- [x] Run targeted tests, full tests, and ruff.

## Decisions

- Decision: Only check dependencies that are required by current configuration.
  Reason: The default deterministic demo should remain model-key-free and DB-free; readiness should not fail for optional services.
- Decision: Keep `/health` backward compatible as the shallow health endpoint.
  Reason: Existing docs and local callers already use `/health`; adding `/health/live` and `/health/ready` should not break older workflows.

## Discoveries

- Discovery: Docker Compose was still using `/health` for the API service healthcheck.
  Evidence: `compose.yaml` called `http://localhost:8000/health`; it now calls `/health/ready`.
- Discovery: Live Postgres readiness can be validated without running migrations because the dependency check only executes `select 1`.
  Evidence: `tests/integration/test_readiness_health_postgres.py` uses `TEST_DATABASE_URL` directly and does not create or mutate schema.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/pytest tests/test_health.py tests/integration/test_readiness_health_postgres.py` passed with `5 passed, 1 skipped`.
- [x] Full pytest:
  Result: `.venv/bin/pytest` passed with `111 passed, 10 skipped`.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live Postgres readiness integration:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/pytest tests/integration/test_readiness_health_postgres.py` passed with `1 passed`. The temporary Postgres container was stopped afterward with `docker compose stop postgres`.

## Final Status

Completed. The API now exposes backward-compatible `/health`, shallow `/health/live`, and production-style `/health/ready` dependency readiness. Readiness checks only required dependencies from the active configuration, returns HTTP 503 when a required dependency fails, and has unit plus opt-in live Postgres integration coverage.
