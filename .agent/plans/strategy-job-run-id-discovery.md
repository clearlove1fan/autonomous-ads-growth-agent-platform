# Strategy Job Run ID Discovery

## Goal

Make async strategy jobs easier to discover from product, operator, and
observability workflows by allowing clients to filter jobs by `run_id`.

## Scope

- Add `run_id` filtering to the strategy job store contract.
- Support `run_id` query filtering in `GET /growth-strategies/jobs`.
- Support `--run-id` in `ads-growth-agent list-strategy-jobs`.
- Return the active filter in `StrategyJobListResponse`.
- Add regression coverage for API, CLI, and Postgres store behavior.

## Plan

- [x] Add this ExecPlan.
- [x] Extend strategy job contracts and stores.
- [x] Wire API and CLI filter parameters.
- [x] Add focused tests for API, CLI, and Postgres store filtering.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [x] Commit and push the slice.

## Decisions

- Decision: Extend the existing list endpoint instead of adding a new route.
  Reason: It preserves the current product API shape and composes naturally
  with existing status, advertiser, and limit filters.
- Decision: Filter by exact `run_id`.
  Reason: Run IDs are already stable identifiers in logs, LangSmith metadata,
  job creation responses, and performance event references.

## Verification

- [x] `.venv/bin/pytest tests/test_strategy_jobs.py`
  Result: 17 passed.
- [x] `.venv/bin/pytest`
  Result: 184 passed, 18 skipped.
- [x] `.venv/bin/ruff check .`
  Result: All checks passed.
- [x] `git diff --check`
  Result: Passed.

## Final Status

Implemented, locally verified, and committed for CI verification.
