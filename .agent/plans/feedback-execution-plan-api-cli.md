# Feedback Execution Plan API And CLI

## Goal

Turn an approved feedback optimization review into a dry-run execution plan that
shows the exact draft action intents the platform would prepare next. This keeps
the product loop moving from recommendation to approval to action planning
without mutating live campaigns.

## Scope

- Add execution-plan contracts for approved feedback reviews.
- Add a deterministic builder that maps selected draft changes to tool intents.
- Add `GET /feedback-optimization-reviews/{review_id}/execution-plan`.
- Add a CLI command for fetching the same plan.
- Extend the persisted product-loop verifier.
- Add focused regression tests and update project docs.

## Plan

- [x] Add this ExecPlan.
- [x] Add contracts and deterministic builder.
- [x] Wire FastAPI and CLI read surfaces.
- [x] Extend walkthrough and tests.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [ ] Commit and push the slice.

## Decisions

- Decision: Derive execution plans from persisted review state instead of
  adding a new execution table.
  Reason: v0.1 still produces dry-run previews only; durable execution attempts
  should be a separate later slice.
- Decision: Require review decision `approved`.
  Reason: rejected or revision-requested drafts should not produce executable
  action previews.
- Decision: Use explicit dry-run tool intents.
  Reason: users can inspect intended platform actions while live ad mutation
  remains disabled.

## Verification

- [x] Focused feedback/API/CLI tests.
  Result: `.venv/bin/pytest tests/test_campaign_feedback.py tests/test_campaign_feedback_api.py tests/test_auth.py tests/integration/test_postgres_product_loop_walkthrough.py` passed with 55 passed and 1 skipped.
- [x] Persisted product-loop integration skip/pass behavior.
  Result: Walkthrough integration test skipped locally when `RUN_POSTGRES_INTEGRATION` was unset.
- [x] Full pytest.
  Result: `.venv/bin/pytest` passed with 237 passed and 19 skipped.
- [x] Ruff.
  Result: `.venv/bin/ruff check src tests scripts migrations` passed.
- [x] `git diff --check`.
  Result: Passed.

## Final Status

Implemented and locally verified. Live PostgreSQL verification should run in CI
because the local Docker daemon was unavailable in the prior slice.
