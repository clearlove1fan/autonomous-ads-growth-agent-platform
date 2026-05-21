# Feedback Loop Handoff Outcome Summary

Extend the event-rooted feedback loop summary so operators can see manual
handoff outcome records without joining the handoff-record APIs themselves.

## Scope

- Add compact handoff record fields to `CampaignFeedbackLoopSummaryResponse`.
- Let the summary builder optionally read `FeedbackHandoffRecordStore`.
- Update API and CLI summary paths to pass the configured handoff store.
- Extend unit, API/CLI, and persisted product-loop coverage.
- Sync README, RFC/HLD, roadmap, and changelog.

## Checklist

- [x] Create this ExecPlan.
- [x] Extend summary contracts and builder.
- [x] Wire API and CLI summary endpoints.
- [x] Add focused tests and product-loop verifier assertions.
- [x] Update docs.
- [x] Run focused tests, full tests, ruff, and diff check.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Model handoff outcomes as terminal-like feedback stages.
  Reason: Once a manual handoff is applied, blocked, or skipped, that is the
  current operator-visible status of the feedback loop.
- Decision: Reuse the handoff record list contract inside the summary.
  Reason: The full audit payload is already typed and useful for operator review.

## Verification

- Focused tests: `.venv/bin/pytest tests/test_campaign_feedback.py tests/test_campaign_feedback_api.py tests/test_auth.py` passed with 96 passed.
- Full default suite: `.venv/bin/pytest` passed with 284 passed, 20 skipped.
- Lint: `.venv/bin/ruff check .` passed.
- Diff check: `git diff --check` passed.
