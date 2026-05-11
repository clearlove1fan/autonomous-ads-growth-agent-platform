# LangGraph Postgres Checkpointer

## Goal

Wire LangGraph's checkpoint mechanism into the strategy workflow so the agent runtime can use an opt-in durable checkpoint backend.

When complete:

- Default local behavior remains checkpoint-free with `GRAPH_CHECKPOINTER_BACKEND=none`.
- `GRAPH_CHECKPOINTER_BACKEND=memory` can be used for local/debug checkpoint tests.
- `GRAPH_CHECKPOINTER_BACKEND=postgres` uses the official `langgraph-checkpoint-postgres` `PostgresSaver`.
- Live integration tests verify checkpoint tables and rows are created for a strategy run.

## Context

- Relevant files:
  - `src/ads_growth_agent/graph.py`
  - `src/ads_growth_agent/observability.py`
  - `src/ads_growth_agent/config.py`
  - `pyproject.toml`
  - `tests/integration/`
- Current behavior:
  - The graph compiles without a checkpointer.
  - Run persistence stores audit records, but it is not LangGraph durable execution.
  - Installed LangGraph only includes `InMemorySaver`; Postgres support comes from `langgraph-checkpoint-postgres`.
- Constraints:
  - Keep default tests and demos database-free.
  - Do not mix checkpointer tables with the application-owned Alembic schema.
  - Preserve existing graph state and response contracts.

## Plan

- [x] Add `langgraph-checkpoint-postgres` dependency.
- [x] Add checkpointer settings and a context-managed factory for none, memory, and Postgres.
- [x] Pass checkpointer and thread config into graph compilation/invocation.
- [x] Add offline factory and graph wiring tests.
- [x] Add live Postgres integration test for checkpoint tables and rows.
- [x] Update docs and roadmap.
- [x] Run default and Docker-backed verification.
- [ ] Commit and push.

## Decisions

- Decision: Use official `PostgresSaver` rather than implementing the saver protocol.
  Reason: LangGraph checkpointer semantics include checkpoints, blobs, writes, versions, and pending sends; the official saver is the right production-style adapter.
- Decision: Keep checkpointer setup opt-in but enabled by default for local Postgres runs.
  Reason: Local integration should bootstrap clean databases; production can manage these tables separately later.
- Decision: Use `run_id` as the LangGraph `thread_id`.
  Reason: It lets checkpoint rows correlate directly with `run_metadata.run_id` and `agent_runs.run_id`.

## Discoveries

- Discovery:
- Discovery: The base LangGraph install includes `MemorySaver`; Postgres support requires `langgraph-checkpoint-postgres`.
  Evidence: Local module inspection found `langgraph.checkpoint.memory` and no `langgraph.checkpoint.postgres` before adding the package.
- Discovery: Default tests remain DB-free; the Postgres checkpointer integration test is skipped unless explicitly enabled.
  Evidence: `.venv/bin/pytest` reported `77 passed, 7 skipped`.
- Discovery: Live Postgres verification passed across migrations, retrieval, run persistence, draft persistence, idempotency, and LangGraph checkpoints.
  Evidence: `RUN_POSTGRES_INTEGRATION=1 ... .venv/bin/pytest tests/integration` reported `7 passed`.
- Discovery: The Postgres container was stopped after verification.
  Evidence: `docker compose stop postgres` completed.

## Verification

- [x] `.venv/bin/python -m compileall src tests`
  Result: Passed.
- [x] `.venv/bin/ruff check .`
  Result: Passed.
- [x] `.venv/bin/pytest`
  Result: `77 passed, 7 skipped`.
- [x] `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=... .venv/bin/pytest tests/integration`
  Result: `7 passed`.

## Final Status

Implementation and verification are complete. Commit and push are pending.
