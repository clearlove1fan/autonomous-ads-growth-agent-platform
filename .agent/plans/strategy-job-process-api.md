# Strategy Job Process API

## Goal

Expose the existing bounded strategy-job worker through a protected API endpoint
so external execution mode can be driven from either API or CLI. This keeps the
single-user MVP usable while preserving the current deterministic worker logic.

## Scope

- Add `POST /growth-strategies/jobs/process`.
- Reuse `process_strategy_jobs` and `StrategyJobWorkerReport`.
- Return worker outcome headers for claimed/completed/retry/failed/cancelled
  counts.
- Add API tests covering queued external jobs processed through the endpoint.
- Update auth route coverage and public docs.

## Non-Goals

- No new external queue service.
- No daemon worker process.
- No background scheduling or cron semantics.

## Acceptance Criteria

- A queued external strategy job can be submitted, processed through API, and
  read back as completed.
- The endpoint is protected by the same local auth dependency as other product
  APIs.
- The API response includes the same worker report shape as the CLI command.

## Verification

- [x] Focused strategy job/auth tests pass.
  - Result:
    `.venv/bin/python -m pytest tests/test_strategy_jobs.py tests/test_auth.py`
    passed with 31 passed.
- [x] Full suite passes or failures are documented.
  - Result: `.venv/bin/python -m pytest` passed with 308 passed, 21 skipped.
- [x] Ruff, py_compile, and diff check pass.
  - Result: `.venv/bin/ruff check .` passed.
  - Result:
    `PYTHONPYCACHEPREFIX=/private/tmp/ads_growth_pycache .venv/bin/python -m py_compile $(find src tests scripts -name '*.py')`
    passed.
  - Result: `git diff --check` passed.
