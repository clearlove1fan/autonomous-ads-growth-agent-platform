# API Idempotency Key Support

## Goal

Add production-style idempotency support to `POST /growth-strategies` using the existing `idempotency_keys` table.

When complete:

- Requests without `Idempotency-Key` behave exactly as they do today.
- `IDEMPOTENCY_BACKEND=postgres` enables persisted idempotency records.
- First request with a new key claims the key as `in_progress`, runs strategy generation, then stores the completed response JSON.
- Repeated request with the same key and identical body replays the saved response.
- Repeated request with the same key but different body returns HTTP 409.

## Context

- Relevant files:
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/config.py`
  - `src/ads_growth_agent/persistence/schema.py`
  - `src/ads_growth_agent/strategy.py`
  - `tests/test_strategy_api_cli.py`
  - `tests/integration/`
- Current behavior:
  - The API runs strategy generation synchronously.
  - `idempotency_keys` already exists in the schema.
  - Run persistence and campaign draft persistence are opt-in, so repeated requests can cause repeated side effects when those backends are enabled.
- Constraints:
  - Keep default API behavior database-free.
  - Avoid changing the request/response contract for clients that do not use the header.
  - Return structured conflict errors for key reuse problems.

## Plan

- [x] Add `IDEMPOTENCY_BACKEND=none|postgres` and TTL settings.
- [x] Implement no-op and Postgres idempotency stores with request hashing.
- [x] Wire FastAPI endpoint to claim, replay, complete, or fail idempotency records.
- [x] Add offline API tests for replay, completion, conflict, factory, and request hashing.
- [x] Add live Postgres integration tests for persisted replay and conflict.
- [x] Update docs and roadmap.
- [x] Run default and Docker-backed verification.
- [ ] Commit and push.

## Decisions

- Decision: Idempotency is API-level, not CLI-level.
  Reason: The header semantics and duplicate external client retries are primarily API concerns.
- Decision: Keep idempotency opt-in.
  Reason: Default local demos should not require Postgres.
- Decision: Store completed `GrowthStrategyResponse` JSON and replay it exactly.
  Reason: Idempotency should return the same logical result instead of re-running the workflow.

## Discoveries

- Discovery:
- Discovery: Default tests remain DB-free; idempotency live tests are skipped unless explicitly enabled.
  Evidence: `.venv/bin/pytest` reported `72 passed, 6 skipped`.
- Discovery: Live Postgres verification passed across migrations, retrieval, run persistence, draft persistence, and API idempotency.
  Evidence: `RUN_POSTGRES_INTEGRATION=1 ... .venv/bin/pytest tests/integration` reported `6 passed`.
- Discovery: The Postgres container was stopped after verification.
  Evidence: `docker compose stop postgres` completed.

## Verification

- [x] `.venv/bin/python -m compileall src tests`
  Result: Passed.
- [x] `.venv/bin/ruff check .`
  Result: Passed.
- [x] `.venv/bin/pytest`
  Result: `72 passed, 6 skipped`.
- [x] `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=... .venv/bin/pytest tests/integration`
  Result: `6 passed`.

## Final Status

Implementation and verification are complete. Commit and push are pending.
