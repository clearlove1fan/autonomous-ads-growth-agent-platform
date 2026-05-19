# Feedback Optimization Draft API And CLI

## Goal

Let users turn a persisted feedback action plan into a concrete draft-only
optimization draft. The draft should show specific budget, creative, audience,
or measurement changes that can be reviewed without executing live campaign
mutations.

## Scope

- Add optimization-draft contracts for feedback-derived draft changes.
- Add a deterministic builder from persisted performance event detail and
  action-plan steps.
- Add `GET /campaign-events/performance/{event_id}/optimization-draft`.
- Add a CLI command for fetching the same draft.
- Extend the persisted product loop verifier.
- Add focused tests and update project docs.

## Plan

- [x] Add this ExecPlan.
- [x] Add contracts and deterministic builder.
- [x] Wire FastAPI and CLI read surfaces.
- [x] Extend walkthrough and tests.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [x] Commit and push the slice.

## Decisions

- Decision: Derive optimization drafts from persisted feedback instead of
  adding a new table.
  Reason: The draft is deterministic and reproducible from the event analysis;
  approval workflow state can be added later when needed.
- Decision: Keep all mutation-like changes draft-only and approval-gated.
  Reason: v0.1 must not execute live spend, targeting, or creative changes.
- Decision: Map each action-plan step to a concrete change type.
  Reason: Product users need a readable plan organized by budget, creative,
  audience, and measurement concerns.

## Verification

- [x] Focused feedback/API/CLI tests.
  Result: `.venv/bin/pytest tests/test_campaign_feedback.py tests/test_campaign_feedback_api.py tests/test_auth.py tests/integration/test_postgres_product_loop_walkthrough.py`
  passed with 38 passed and 1 skipped.
- [x] Persisted product-loop integration skip/pass behavior.
  Result: Included in the focused command above and skipped locally when
  `RUN_POSTGRES_INTEGRATION` was unset.
- [x] Full pytest.
  Result: `.venv/bin/pytest` passed with 216 passed and 19 skipped.
- [x] Ruff.
  Result: `.venv/bin/ruff check ...` passed for touched code, tests, and script.
- [x] `git diff --check`.
  Result: Passed.

## Final Status

Implemented, locally verified, and ready for CI PostgreSQL verification.
