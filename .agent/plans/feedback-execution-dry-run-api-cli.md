# Feedback Execution Dry Run API And CLI

## Goal

Validate approved feedback execution plans through the internal typed tool
registry without mutating live campaign state. This makes the product loop move
from approved dry-run intents to executable validation while preserving v0.1
draft-only safety.

## Scope

- Add draft-only tool registry entries for feedback execution plan intents.
- Add dry-run result contracts.
- Add a deterministic dry-run executor for execution plans.
- Add API and CLI surfaces for dry-run validation.
- Extend the persisted product-loop verifier.
- Add focused tests and update docs.

## Plan

- [x] Add this ExecPlan.
- [x] Add draft-only tool definitions and dry-run contracts/executor.
- [x] Wire FastAPI and CLI dry-run surfaces.
- [x] Extend walkthrough and tests.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [x] Commit and push the slice.

## Decisions

- Decision: Use the internal typed tool registry for dry-run validation.
  Reason: This keeps the model/system separation intact and proves tool intent
  validation before any later live execution layer exists.
- Decision: Keep dry-run tool names distinct from live tool names.
  Reason: `draft_budget_reallocation` is visibly not `update_live_budget`, which
  reduces accidental execution ambiguity.
- Decision: Do not persist dry-run attempts in this slice.
  Reason: dry-run validation is deterministic and can be regenerated; durable
  execution attempts should be designed separately.

## Verification

- [x] Focused tool/feedback/API/CLI tests:
  `.venv/bin/pytest tests/test_tool_registry.py tests/test_campaign_feedback.py tests/test_campaign_feedback_api.py tests/test_auth.py tests/integration/test_postgres_product_loop_walkthrough.py`
  passed with 66 passed and 1 skipped.
- [x] Persisted product-loop integration skip/pass behavior:
  local run skipped live PostgreSQL as expected without `RUN_POSTGRES_INTEGRATION=1`.
- [x] Full pytest: `.venv/bin/pytest` passed with 242 passed and 19 skipped.
- [x] Ruff: `.venv/bin/ruff check src tests scripts migrations` passed.
- [x] `git diff --check` passed.

## Final Status

Completed. Local verification passed; remote CI is expected to run after push.
