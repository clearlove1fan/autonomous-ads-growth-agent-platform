# Feedback Handoff Package

## Goal

Add a read-only manual handoff package for approved feedback optimization
reviews so an operator can safely inspect the approved draft actions, latest
dry-run validation, checklist, and guardrails before manually applying changes
outside the system.

## Scope

- Add typed handoff package contracts.
- Add a pure builder that composes approved review, execution plan, and latest
  dry-run validation.
- Add FastAPI and CLI read surfaces.
- Extend unit, API, CLI, auth, and persisted product-loop verification.
- Update README, RFC/HLD, roadmap, and changelog.

## Plan

- [x] Add this ExecPlan.
- [x] Add handoff package contracts and builder.
- [x] Add API and CLI read surfaces.
- [x] Extend tests and persisted product-loop verifier.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Use `/feedback-optimization-reviews/{review_id}/handoff-package`.
  Reason: The approved review is the permissioned root for the draft action
  package.
- Decision: Keep the package read-only and draft-only.
  Reason: v0.1 must not mutate live ad platform state.
- Decision: Surface `validation_missing` and `validation_failed` instead of
  hiding packages without a passing dry run.
  Reason: Operators need to know why a package is not ready.

## Verification

- [x] Approved review with passed dry run returns `ready_for_manual_handoff`.
- [x] Approved review without dry run returns `validation_missing`.
- [x] Non-approved review is rejected consistently.
- [x] API and CLI expose the same package shape.
- [x] Persisted product-loop verifier covers handoff package reads.
- [x] Full pytest: `277 passed, 20 skipped`.
- [x] Ruff.
- [x] `git diff --check`.

## Final Status

In progress.
