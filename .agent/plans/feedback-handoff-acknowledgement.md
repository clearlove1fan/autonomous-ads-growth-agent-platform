# Feedback Handoff Acknowledgement

Add an operator acknowledgement step after a manual feedback handoff package is
generated. The product loop should let an operator record whether the dry-run
validated package was manually applied, blocked, or skipped without mutating live
campaign state.

## Scope

- Add typed handoff acknowledgement request/response contracts.
- Add a PostgreSQL-backed append-only handoff record store.
- Add API and CLI commands to submit, fetch, and list handoff records.
- Extend schema/migrations with partition-aware indexes for audit access.
- Add unit, API/CLI, schema, integration, and persisted product-loop coverage.

## Checklist

- [x] Create this ExecPlan.
- [x] Add handoff acknowledgement contracts and validation.
- [x] Add partition-aware database schema and migration.
- [x] Implement handoff record store and factory.
- [x] Wire API endpoints and CLI commands.
- [x] Add tests for builder/store/API/CLI/schema/integration.
- [x] Extend persisted product-loop verifier.
- [x] Run focused tests, full tests, ruff, and diff check.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Keep acknowledgements append-only.
  Reason: Operator outcomes are audit records. Updating in place would make the
  manual execution trail less clear.
- Decision: Only `applied` requires a ready handoff package.
  Reason: Operators should be able to record `blocked` or `skipped` outcomes even
  when validation is missing or failed, but applied changes must require a passed
  dry run.
- Decision: Store full package and record snapshots.
  Reason: Manual execution audit should remain explainable even if derived
  package logic changes later.

## Verification

- Focused tests: `.venv/bin/pytest tests/test_campaign_feedback.py tests/test_campaign_feedback_api.py tests/test_database_schema.py tests/test_auth.py` passed with 107 passed.
- Full default suite: `.venv/bin/pytest` passed with 283 passed, 20 skipped.
- Lint: `.venv/bin/ruff check .` passed.
- Migration head: `.venv/bin/alembic heads` reported `0012_feedback_handoff_records (head)`.
- Diff check: `git diff --check` passed.
- Local live PostgreSQL verification was not run because Docker daemon was not
  running; CI should run the Postgres integration job after push.
