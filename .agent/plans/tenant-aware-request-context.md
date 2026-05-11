# Tenant-Aware Request Context

## Goal

HTTP clients can select the effective tenant for a growth strategy request by sending `X-Tenant-ID`. The selected tenant must flow into idempotency, run persistence, campaign draft persistence, knowledge store construction, and LangGraph checkpoint thread IDs. Default local and CLI behavior remains unchanged and uses `TENANT_ID` from settings, which defaults to `default`.

## Context

- Relevant files:
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/graph.py`
  - `src/ads_growth_agent/graph_checkpointer.py`
  - `tests/test_strategy_api_cli.py`
  - `tests/test_graph_checkpointer.py`
  - `tests/integration/test_postgres_graph_checkpointer.py`
- Current behavior:
  - Store factories already build tenant-scoped stores from `settings.tenant_id`.
  - FastAPI currently depends directly on global runtime settings, so every HTTP request uses the process-level tenant.
  - LangGraph checkpoint `thread_id` currently equals `run_id`; because run IDs are deterministic from the advertiser brief, two tenants with identical briefs can collide in LangGraph-owned checkpoint tables.
- Constraints:
  - Keep deterministic, DB-free default tests and local demo behavior.
  - Do not change CLI tenant behavior.
  - Keep tenant validation simple and explicit at the API boundary.
  - Use existing app-owned composite tenant keys; do not introduce a new migration unless required.

## Plan

- [x] Add request-scoped tenant settings in the FastAPI layer.
- [x] Prefix LangGraph checkpoint thread IDs with the effective tenant when a checkpointer is enabled.
- [x] Add unit tests for tenant header propagation, validation, and checkpoint config.
- [x] Add live Postgres integration coverage for tenant isolation across API idempotency, run persistence, and campaign drafts.
- [x] Update docs to mention `X-Tenant-ID`.
- [x] Run default tests and live integration tests with Docker Postgres.
- [x] Commit and push the verified slice.

## Decisions

- Decision: Treat `X-Tenant-ID` as an API-only override and keep CLI/env behavior unchanged.
  Reason: Product APIs need per-request multi-tenancy, while CLI demos should remain easy to run locally.
- Decision: Validate tenant IDs at the HTTP boundary with a conservative ASCII slug pattern.
  Reason: Tenant IDs become persistence keys and checkpoint thread prefixes; rejecting ambiguous or high-entropy input early reduces operational risk.
- Decision: Use tenant-prefixed LangGraph checkpoint thread IDs instead of adding app-owned tenant columns to LangGraph tables.
  Reason: LangGraph owns the checkpoint schema; namespacing the thread ID preserves tenant isolation without forking the official saver.

## Discoveries

- Discovery: Existing app-owned tables already include `tenant_id` in primary keys or tenant-scoped indexes.
  Evidence: `src/ads_growth_agent/persistence/schema.py` defines composite tenant primary keys for agent runs, campaign drafts, idempotency keys, knowledge, and memory tables.
- Discovery: FastAPI idempotency store construction already goes through a dependency, so switching that dependency to request-scoped settings propagates tenant IDs without touching store factories.
  Evidence: `src/ads_growth_agent/api.py` now resolves both the endpoint settings and `get_runtime_idempotency_store` from `get_request_settings`.
- Discovery: LangGraph checkpoint tables are owned by the official saver and do not include the application tenant key.
  Evidence: The durable thread namespace is now encoded as `<tenant_id>:<run_id>` before graph invocation.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/pytest tests/test_strategy_api_cli.py tests/test_graph_checkpointer.py` passed with `14 passed`.
- [x] Default pytest:
  Result: `.venv/bin/pytest` passed with `79 passed, 8 skipped`.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live PostgreSQL integration pytest:
  Result: Initial sandbox run failed with `Operation not permitted` for `localhost:5432`; escalated run passed with `8 passed`.

## Final Status

Implementation and verification complete. The API now supports request-scoped tenant selection through `X-Tenant-ID`, tenant settings flow into idempotency and strategy execution, checkpoint thread IDs are tenant-prefixed, docs describe the behavior, and tests cover offline and live Postgres isolation paths.
