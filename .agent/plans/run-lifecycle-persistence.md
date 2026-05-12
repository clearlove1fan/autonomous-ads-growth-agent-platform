# Run Lifecycle Persistence

## Goal

Persist an agent execution as `running` before the LangGraph workflow starts, then transition the same execution row to `completed` or `failed`. This gives retry/resume work a reliable lifecycle record instead of only creating audit rows after execution has already ended.

## Context

- Relevant files:
  - `src/ads_growth_agent/strategy.py`
  - `src/ads_growth_agent/graph.py`
  - `src/ads_growth_agent/observability.py`
  - `src/ads_growth_agent/persistence/run_store.py`
  - `tests/test_agent_run_persistence.py`
  - `tests/integration/test_postgres_agent_run_store.py`
- Current behavior:
  - `generate_growth_strategy` calls `run_growth_strategy_graph`, then records completed/failed runs after the graph returns or raises.
  - `run_growth_strategy_graph` currently creates its own `RunContext`, so the application layer cannot persist a `running` row before execution.
  - `agent_runs.status` already allows `running`, `completed`, and `failed`.
- Constraints:
  - Keep default DB-free behavior unchanged.
  - Keep `run_metadata.run_id` as the execution ID.
  - Avoid putting persistence writes inside LangGraph nodes.
  - Preserve existing successful and failed audit semantics.

## Plan

- [x] Allow the application layer to create and pass a `RunContext` into the graph runner.
- [x] Add `AgentRunStore.record_started` for no-op and Postgres stores.
- [x] Write `running` rows with `completed_at = NULL`, then update the same row on completion/failure.
- [x] Update strategy orchestration to record started before invoking LangGraph.
- [x] Add unit and integration tests for started -> completed/failed lifecycle.
- [x] Update README and verification notes.
- [x] Run default tests and live Postgres integration tests.
- [x] Commit and push the verified slice.

## Decisions

- Decision: Create lifecycle records in `strategy.generate_growth_strategy`, not inside graph nodes.
  Reason: Persistence belongs at the application service boundary; LangGraph nodes should stay focused on planning, routing, tool execution, critique, and finalization.
- Decision: Reuse the same `RunContext` for `record_started`, graph invocation, and terminal persistence.
  Reason: This guarantees the `running`, `completed`, and `failed` transitions target the same execution row.

## Discoveries

- Discovery: The database schema already supports a `running` run status.
  Evidence: `agent_runs` has check constraint `status in ('running', 'completed', 'failed')`.
- Discovery: `run_growth_strategy_graph` can accept an external `RunContext` without changing the default direct graph API.
  Evidence: Direct callers still get an internally generated context, while `strategy.generate_growth_strategy` now passes one in.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/pytest tests/test_agent_run_persistence.py tests/test_graph_workflow.py tests/test_strategy_api_cli.py tests/test_campaign_draft_persistence.py` passed with `32 passed`.
- [x] Default pytest:
  Result: `.venv/bin/pytest` passed with `82 passed, 8 skipped`.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Live PostgreSQL integration pytest:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/pytest tests/integration` passed with `8 passed`.

## Final Status

Implementation, verification, commit, and push are complete.
