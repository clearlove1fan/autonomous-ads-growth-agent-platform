# Execution ID Run Semantics

## Goal

Separate stable strategy identity from per-execution run identity. A strategy generated from the same advertiser brief should keep the same `strategy_id`, while each workflow invocation gets a unique execution/run ID for tracing, checkpointing, persistence, retries, and audit.

## Context

- Relevant files:
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/observability.py`
  - `src/ads_growth_agent/graph.py`
  - `src/ads_growth_agent/persistence/schema.py`
  - `src/ads_growth_agent/persistence/run_store.py`
  - `migrations/versions/`
  - `tests/test_graph_workflow.py`
  - `tests/integration/test_postgres_agent_run_store.py`
- Current behavior:
  - `FinalGrowthStrategy.strategy_id` is deterministic from the advertiser brief.
  - `RunMetadata.run_id` is currently set to the same deterministic strategy ID.
  - Repeated identical runs overwrite the same `agent_runs` and `agent_run_steps` rows.
  - Tenant-aware checkpointing already namespaces by tenant, but still uses the deterministic run ID.
- Constraints:
  - Preserve `run_metadata.run_id` as the operational identifier in API responses for compatibility.
  - Add explicit `strategy_id` and `execution_id` metadata so the semantic split is visible.
  - Keep local default execution DB-free and deterministic in business outputs.
  - Make migrations safe for fresh databases even though revision 0001 imports current metadata.

## Plan

- [x] Extend run context and response metadata with `strategy_id` and `execution_id`.
- [x] Change graph invocation to use a unique execution run ID while keeping deterministic strategy IDs.
- [x] Add schema and migration support for `agent_runs.strategy_id` and `agent_run_steps.strategy_id`.
- [x] Update run persistence, retrieval context, checkpoint thread IDs, and observability metadata.
- [x] Update unit and integration tests for unique execution rows.
- [x] Update README and ExecPlan verification.
- [x] Run default tests and offline migration checks.
- [x] Run live Postgres integration tests when Docker escalation is available.
- [x] Commit and push the verified slice.

## Decisions

- Decision: Keep `run_metadata.run_id` as the execution ID and add `run_metadata.execution_id` as an explicit alias.
  Reason: Existing API clients already read `run_id`; treating it as the unique run instance is operationally natural and minimizes response breakage.
- Decision: Keep `strategy.strategy_id` as the stable logical strategy ID and copy it into `run_metadata.strategy_id`.
  Reason: This makes stable strategy correlation available without overloading run identity.
- Decision: Use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the new migration.
  Reason: Revision 0001 creates tables from current metadata, so the additive migration must be safe when columns already exist on fresh databases.

## Discoveries

- Discovery: The first Alembic revision calls `metadata.create_all()` rather than defining an immutable frozen schema.
  Evidence: `migrations/versions/0001_partition_aware_core_schema.py` imports `ads_growth_agent.persistence.schema.metadata`.
- Discovery: Tool execution and retrieval had been using the stable strategy ID as their run context.
  Evidence: `src/ads_growth_agent/graph.py` now carries `run_id` in graph state and passes it to `ToolExecutionContext` and `build_knowledge_query`.

## Verification

- [x] Targeted pytest:
  Result: `.venv/bin/pytest tests/test_graph_workflow.py tests/test_contracts.py tests/test_database_schema.py tests/test_evaluation.py` passed with `33 passed`.
- [x] Default pytest:
  Result: `.venv/bin/pytest` passed with `81 passed, 8 skipped`.
- [x] Ruff:
  Result: `.venv/bin/ruff check .` passed.
- [x] Alembic revision chain:
  Result: `.venv/bin/alembic heads` reported `0002_execution_run_ids (head)`, and `.venv/bin/alembic history --verbose` showed a linear `0001 -> 0002` chain.
- [x] Alembic offline SQL generation:
  Result: `.venv/bin/alembic upgrade head --sql` generated SQL including `strategy_id` columns and indexes.
- [x] Live PostgreSQL integration pytest:
  Result: `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth .venv/bin/pytest tests/integration` passed with `8 passed`.

## Final Status

Implementation complete with local/unit, offline migration, and live PostgreSQL integration verification. Commit and push are complete.
