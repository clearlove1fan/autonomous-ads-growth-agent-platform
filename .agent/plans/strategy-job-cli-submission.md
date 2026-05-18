# Strategy Job CLI Submission

## Goal

Complete the command-line async job lifecycle so operators can submit, process,
inspect, retry, and cancel strategy jobs without going through the HTTP API.

## Scope

- Add a shared strategy job enqueue helper used by API and CLI.
- Add CLI commands for structured brief submission and natural-language text
  submission.
- Add a CLI command to fetch one strategy job by ID.
- Keep job processing explicit in the CLI; submission queues the job and the
  existing `process-strategy-jobs` command performs execution.
- Add regression tests for CLI submit/get behavior.

## Plan

- [x] Add this ExecPlan.
- [x] Extract shared strategy job enqueue helper.
- [x] Wire API job submission through the shared helper.
- [x] Add CLI submit/get commands.
- [x] Add focused CLI lifecycle tests.
- [x] Update README, changelog, roadmap, and RFC notes.
- [x] Run focused and full verification.
- [x] Commit and push the slice.

## Decisions

- Decision: CLI submit commands queue jobs instead of running them immediately.
  Reason: This mirrors the external worker model and keeps long-running work
  controlled by `process-strategy-jobs`.
- Decision: Add both structured and text submission.
  Reason: Structured JSON is useful for repeatable tests and operators, while
  natural-language input is the product-facing advertiser path.

## Verification

- [x] `.venv/bin/pytest tests/test_strategy_jobs.py`
  Result: 22 passed.
- [x] `.venv/bin/pytest`
  Result: 190 passed, 18 skipped.
- [x] `.venv/bin/ruff check .`
  Result: All checks passed.
- [x] `git diff --check`
  Result: Passed.

## Final Status

Implemented, locally verified, and committed for CI verification.
