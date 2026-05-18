# Negative Demo Cases

## Goal

Add reviewer-friendly negative demo coverage for the highest-risk Phase 1/1.5
failure paths: safe failure before tool execution, idempotency conflict, and
campaign performance event conflict.

## Scope

- Keep the cases deterministic and model-key-free.
- Do not require Docker or live PostgreSQL.
- Exercise real graph/API validation paths where practical.
- Produce a short output excerpt that can be pasted into demo notes.

## Plan

- [x] Add this ExecPlan.
- [x] Add `scripts/verify_negative_demos.py`.
- [x] Add an expected-output excerpt under `examples/`.
- [x] Add e2e coverage for the negative demo verifier.
- [x] Update README, roadmap, RFC, and changelog references.
- [x] Run focused and full verification.
- [ ] Commit and push the slice.

## Decisions

- Decision: Use an invalid LLM planner tool plan for the safe-failure demo.
  Reason: It shows the platform rejecting model-proposed unsafe execution before
  any typed ads tool runs.
- Decision: Use in-process fake stores for conflict demos.
  Reason: The behavior under test is API conflict mapping and structured error
  shape; live Postgres behavior is already covered by integration tests.

## Verification

- [x] `.venv/bin/python scripts/verify_negative_demos.py`
  Result: Passed and printed the expected negative demo summary.
- [x] `.venv/bin/pytest tests/e2e/test_negative_demo_script.py`
  Result: 1 passed.
- [x] `.venv/bin/pytest`
  Result: 176 passed, 18 skipped.
- [x] `.venv/bin/ruff check .`
  Result: Passed.
- [x] `git diff --check`
  Result: Passed.

## Final Status

Implemented and locally verified. The slice is ready to commit and push.
