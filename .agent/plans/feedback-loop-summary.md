# Feedback Loop Summary

## Goal

Add an operator-facing summary view for one persisted campaign performance
event so a user can see the whole feedback loop status without manually joining
event, action-plan, optimization-draft, review, lineage, and dry-run endpoints.

## Scope

- Add a typed feedback-loop summary response.
- Add a pure builder that composes existing feedback projections.
- Add FastAPI and CLI read surfaces.
- Extend unit, API, CLI, auth, and persisted product-loop verification.
- Update README, RFC/HLD, roadmap, and changelog.

## Plan

- [x] Add this ExecPlan.
- [x] Add summary contract and builder.
- [x] Add API and CLI summary surfaces.
- [x] Extend tests and persisted product-loop verifier.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [ ] Commit, push, and verify CI when escalated git operations are available.

## Decisions

- Decision: Use `/campaign-events/performance/{event_id}/feedback-loop-summary`.
  Reason: The performance event is the root object for the post-strategy
  feedback loop.
- Decision: Compose from existing stores and projections instead of persisting a
  new summary table.
  Reason: The summary is an operator read model and should reflect current
  review/dry-run state.
- Decision: Do not require review or execution persistence to render the event
  summary.
  Reason: The endpoint should still show action plan and optimization draft
  state for an event when later audit stores are disabled.

## Verification

- [x] API summary includes event, action plan, optimization draft, reviews,
  lineages, and dry-runs.
- [x] CLI summary mirrors API output.
- [x] Persisted product-loop verifier covers summary reads.
- [x] Full pytest: `273 passed, 20 skipped`.
- [x] Ruff.
- [x] `git diff --check`.

## Final Status

Implementation complete and locally verified. Commit/push/CI verification is
pending until Codex can run escalated git operations again.
