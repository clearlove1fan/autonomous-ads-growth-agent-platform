# Feedback Optimization Review API And CLI

## Goal

Let users record an auditable human review decision for a feedback-derived
optimization draft. This closes the MVP loop from performance signal to
recommendation to draft to explicit approval, rejection, or revision request.

## Scope

- Add typed review request/response contracts.
- Add deterministic review builder with selected-change validation.
- Add PostgreSQL persistence for optimization draft reviews.
- Add FastAPI endpoints to submit, fetch, and list reviews.
- Add CLI commands for the same product workflow.
- Extend the persisted product loop verifier.
- Add focused regression and integration coverage.

## Plan

- [x] Add this ExecPlan.
- [x] Add contracts and deterministic review builder.
- [x] Add schema, migration, and persistence store.
- [x] Wire FastAPI and CLI review surfaces.
- [x] Extend walkthrough and tests.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [ ] Commit and push the slice.

## Decisions

- Decision: Persist review state separately from the deterministic optimization
  draft.
  Reason: The draft can be reconstructed from feedback, but the human decision,
  selected changes, notes, and reviewer identity are durable product state.
- Decision: Keep approved reviews as approvals only, not live campaign changes.
  Reason: v0.1 remains draft-only and must not mutate live spend, targeting, or
  creatives.
- Decision: Store a draft snapshot with each review.
  Reason: It preserves the exact reviewed proposal if recommendation logic
  evolves later.

## Verification

- [x] Focused feedback/API/CLI tests.
  Result: `.venv/bin/pytest tests/test_campaign_feedback.py tests/test_campaign_feedback_api.py tests/test_feedback_review_persistence.py tests/test_database_schema.py tests/test_health.py tests/test_auth.py tests/integration/test_postgres_product_loop_walkthrough.py` passed with 71 passed and 1 skipped.
- [x] Database schema and migration checks.
  Result: Schema tests cover `feedback_optimization_reviews`; live migration integration remains skipped locally without Docker daemon.
- [x] Persisted product-loop integration skip/pass behavior.
  Result: Integration test skipped locally when `RUN_POSTGRES_INTEGRATION` was unset.
- [x] Full pytest.
  Result: `.venv/bin/pytest` passed with 231 passed and 19 skipped.
- [x] Ruff.
  Result: `.venv/bin/ruff check src tests scripts migrations` passed.
- [x] `git diff --check`.
  Result: Passed.

## Final Status

Implemented and locally verified. Local Docker Postgres was not running, so live
PostgreSQL execution is left for CI or a machine with Docker daemon available.
