# Revision Draft Second Review Flow

## Goal

Close the human-in-the-loop revision loop so a `needs_revision` review can
produce a revision draft, that revision draft can be reviewed again, and an
approved revision review can continue into the existing execution-plan and
dry-run validation path.

## Scope

- Convert a revision draft into a reviewable draft artifact without adding a new
  persistence table.
- Add FastAPI and CLI commands for submitting a review on a revision draft.
- Extend tests and the persisted product-loop verifier.
- Update README, RFC/HLD, roadmap, and changelog.

## Plan

- [x] Add this ExecPlan.
- [x] Add reviewable revision draft builder.
- [x] Add API and CLI submit-review surfaces.
- [x] Extend unit, API/CLI, auth, and product-loop tests.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Reuse `CampaignFeedbackOptimizationReviewResponse` for revision
  reviews.
  Reason: A revision draft is still a draft-only optimization proposal with
  selected changes, reviewer notes, and an approval decision.
- Decision: Do not add a new revision-review table in this slice.
  Reason: Existing feedback review persistence already stores the reviewed
  draft snapshot and is enough to audit the first closed-loop implementation.
- Decision: Approved revision reviews reuse the existing execution-plan path.
  Reason: This proves the workflow contract without duplicating execution
  planning logic.

## Verification

- [x] Revision draft can be converted into a reviewable optimization draft.
- [x] API/CLI can approve a revision draft.
- [x] Approved revision review can produce an execution plan.
- [x] Persisted product-loop verifier covers revision draft second review.
- [x] Full pytest: `262 passed, 20 skipped`.
- [x] Ruff.
- [x] `git diff --check`.

## Final Status

Implementation complete locally. Waiting on commit, push, and CI verification.
