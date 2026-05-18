# Async Strategy Job From Text

## Goal

Let advertisers submit a natural-language goal as an async strategy-generation
job, then poll the existing job endpoint for the completed structured strategy.

## Scope

- Add `POST /growth-strategies/jobs/from-text`.
- Reuse the existing brief intake parser and async strategy job executor.
- Return both parsed intake details and the accepted job envelope.
- Keep health endpoints public and protect the new product endpoint with the
  existing API auth dependency.
- Add focused API, auth-route, and product smoke coverage.

## Plan

- [x] Add this ExecPlan.
- [x] Add response contract for text-based job submission.
- [x] Refactor job enqueueing into a shared API helper.
- [x] Add the text-to-job API route.
- [x] Add tests and product smoke coverage.
- [x] Update README, changelog, roadmap, and RFC notes.
- [x] Run focused and full verification.
- [x] Commit and push the slice.

## Decisions

- Decision: Parse the natural-language brief synchronously before enqueueing.
  Reason: The caller gets immediate intake confidence and assumptions, while
  the heavier strategy generation remains async and pollable.
- Decision: Return `intake` and `job` rather than changing the existing job
  accepted response.
  Reason: It preserves backward compatibility for structured job submission.

## Verification

- [x] `.venv/bin/pytest tests/test_strategy_jobs.py tests/test_auth.py tests/e2e/test_product_smoke.py`
  Result: 32 passed.
- [x] `.venv/bin/pytest`
  Result: 186 passed, 18 skipped.
- [x] `.venv/bin/ruff check .`
  Result: All checks passed.
- [x] `git diff --check`
  Result: Passed.

## Final Status

Implemented, locally verified, and committed for CI verification.
