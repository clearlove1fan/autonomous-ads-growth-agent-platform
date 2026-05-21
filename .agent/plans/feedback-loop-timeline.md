# Feedback Loop Timeline

Add an event-rooted timeline/audit projection for one persisted campaign
performance event so operators can inspect the feedback loop in chronological
order without manually joining summary, review, execution, and handoff APIs.

## Scope

- Add typed timeline entry and response contracts.
- Add a pure builder that composes the existing feedback summary, reviews,
  revision drafts, execution plans, dry-runs, handoff packages, and handoff
  outcome records.
- Add FastAPI and CLI read surfaces.
- Extend unit, API/CLI, auth, and persisted product-loop verification.
- Sync README, RFC/HLD, roadmap, and changelog.

## Checklist

- [x] Create this ExecPlan.
- [x] Add timeline contracts and builder.
- [x] Wire API and CLI timeline endpoints.
- [x] Add focused tests and product-loop verifier assertions.
- [x] Update docs.
- [x] Run focused tests, full tests, ruff, and diff check.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Use `/campaign-events/performance/{event_id}/feedback-loop-timeline`.
  Reason: The performance event is the root object for the post-strategy loop.
- Decision: Compose the timeline as a read model instead of persisting a new
  table.
  Reason: All source records already exist or are deterministic derived
  artifacts; the timeline should reflect the latest persisted audit state.
- Decision: Include derived action-plan, optimization-draft, execution-plan, and
  handoff-package entries.
  Reason: Operators need to see product steps even when those steps are
  draft-only read projections.

## Verification

- [x] API timeline includes event, action-plan, optimization-draft, review,
  revision, execution, dry-run, handoff package, and handoff outcome entries.
- [x] CLI timeline mirrors API output.
- [x] Persisted product-loop verifier covers timeline reads.
- [x] Focused tests: `.venv/bin/pytest tests/test_campaign_feedback.py tests/test_campaign_feedback_api.py tests/test_auth.py` passed with 97 passed.
- [x] Full pytest: `.venv/bin/pytest` passed with 285 passed, 20 skipped.
- [x] Ruff: `.venv/bin/ruff check .` passed.
- [x] Compile check: `.venv/bin/python -m py_compile scripts/verify_persisted_product_loop.py src/ads_growth_agent/feedback_loop_timeline.py` passed.
- [x] `git diff --check` passed.
