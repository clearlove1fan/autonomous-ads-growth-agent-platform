# Feedback Action Plan API And CLI

## Goal

Turn persisted campaign feedback analysis into a user-facing draft-only action
plan. After a performance event is ingested, a user should be able to fetch the
ranked next steps through API or CLI without reading raw analysis internals.

## Scope

- Add action-plan contracts that preserve draft-only safety and human approval.
- Add a deterministic action-plan builder from persisted performance event
  detail and feedback analysis.
- Add `GET /campaign-events/performance/{event_id}/action-plan`.
- Add a CLI command for fetching the same plan.
- Extend the persisted product loop verifier to cover the new action-plan path.
- Add focused API/CLI/unit tests.
- Update README, RFC, changelog, and roadmap.

## Plan

- [x] Add this ExecPlan.
- [x] Add contracts and deterministic builder.
- [x] Wire FastAPI and CLI read surfaces.
- [x] Extend walkthrough and tests.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [x] Commit and push the slice.

## Decisions

- Decision: Action plans are derived from persisted event analysis, not a new
  mutable execution state.
  Reason: v0.1 remains draft-only and should avoid another persistence table
  until users need approval workflow state.
- Decision: Keep output deterministic and non-LLM.
  Reason: This is a product-readability layer over validated feedback analysis;
  LLM variability would weaken regression stability.
- Decision: Expose matched strategy rule IDs and recommended action text.
  Reason: The user should see how an action links back to the original strategy
  optimization rules.

## Verification

- [x] Focused feedback/API/CLI tests.
  Result: `.venv/bin/pytest tests/test_campaign_feedback.py tests/test_campaign_feedback_api.py tests/integration/test_postgres_product_loop_walkthrough.py`
  passed with 25 passed and 1 skipped.
- [x] Persisted product-loop integration skip/pass behavior.
  Result: Included in the focused command above and skipped locally when
  `RUN_POSTGRES_INTEGRATION` was unset.
- [x] Full pytest.
  Result: `.venv/bin/pytest` passed with 211 passed and 19 skipped.
- [x] Ruff.
  Result: `.venv/bin/ruff check ...` passed for touched code, tests, and script.
- [x] `git diff --check`.
  Result: Passed.

## Final Status

Implemented, locally verified, and ready for CI PostgreSQL verification.
