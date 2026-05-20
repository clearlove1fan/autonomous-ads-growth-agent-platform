# Persist Feedback Execution Dry Runs

## Goal

Persist approved feedback execution dry-run validation results so operators can
audit which approved optimization reviews were validated, whether validation
passed, and which draft-only tool steps were blocked.

## Scope

- Add a partition-aware PostgreSQL table for feedback execution dry-run results.
- Add store/factory support with a no-op default and optional Postgres backend.
- Record dry-run results from API and CLI when execution persistence is enabled.
- Add API and CLI read/list surfaces for persisted dry-run results.
- Extend the persisted product-loop verifier and integration tests.
- Update README, RFC/HLD, roadmap, database schema notes, changelog, and env
  example.

## Plan

- [x] Add this ExecPlan.
- [x] Add schema, migration, config, health readiness, and store/factory.
- [x] Wire API and CLI persistence/read/list surfaces.
- [x] Extend tests and persisted product-loop verifier.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [x] Commit, push, and verify CI.

## Decisions

- Decision: Keep persistence optional with `FEEDBACK_EXECUTION_PERSISTENCE_BACKEND`.
  Reason: Local deterministic demos should remain model-key-free and database-free
  unless the product loop explicitly opts into Postgres persistence.
- Decision: Use deterministic `dry_run_id` as the storage key.
  Reason: Re-running validation for the same approved execution plan should update
  the latest validation snapshot instead of creating duplicate audit records.
- Decision: Store both denormalized query columns and JSON snapshots.
  Reason: Operators need fast review/event/advertiser filters, while the full
  execution plan and dry-run response must remain inspectable for audit.

## Verification

- [x] Focused feedback execution persistence tests:
  `.venv/bin/pytest tests/test_feedback_execution_persistence.py tests/test_database_schema.py tests/test_health.py tests/test_auth.py tests/test_campaign_feedback_api.py tests/integration/test_postgres_feedback_execution_store.py tests/integration/test_postgres_product_loop_walkthrough.py`
  passed with 76 passed and 2 skipped.
- [x] API/CLI tests for record, get, list, and disabled persistence behavior.
- [x] Live PostgreSQL integration skip/pass behavior:
  local run skipped live PostgreSQL as expected without `RUN_POSTGRES_INTEGRATION=1`.
- [x] Full pytest: `.venv/bin/pytest` passed with 251 passed and 20 skipped.
- [x] Ruff: `.venv/bin/ruff check src tests scripts migrations` passed.
- [x] `git diff --check` passed.

## Final Status

Completed. Local verification passed; remote CI is expected to run after push.
