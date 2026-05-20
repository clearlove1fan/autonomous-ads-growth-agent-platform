# Feedback Revision Draft API And CLI

## Goal

Close the human-in-the-loop revision path. When a reviewer records
`needs_revision` for a feedback optimization draft, the platform should generate
a revised draft proposal that carries reviewer notes into concrete draft-only
changes without mutating live campaign state.

## Scope

- Add revision draft contracts.
- Add deterministic revision draft generation from a `needs_revision` review.
- Add API and CLI surfaces for fetching a revision draft by review ID.
- Extend product-loop verifier and tests.
- Update README, RFC/HLD, roadmap, and changelog.

## Plan

- [x] Add this ExecPlan.
- [x] Add contracts and revision draft builder.
- [x] Wire FastAPI and CLI surfaces.
- [x] Extend tests and persisted product-loop verifier.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Revision drafts are generated deterministically from the persisted
  review snapshot.
  Reason: The source optimization draft snapshot is already audited in the
  review record, so the first revision path can be regenerated without adding a
  new table.
- Decision: Only `needs_revision` reviews can produce a revision draft.
  Reason: Approved reviews move toward dry-run validation; rejected reviews are
  terminal until a new draft is created from fresh feedback.
- Decision: Keep revised changes draft-only and approval-gated.
  Reason: A revision draft should return to human review before any execution
  plan or dry-run validation is created.

## Verification

- [x] Focused campaign feedback revision tests.
- [x] API/CLI tests for success and invalid review decisions.
- [x] Persisted product-loop integration skip/pass behavior.
- [x] Full pytest: `257 passed, 20 skipped`.
- [x] Ruff.
- [x] `git diff --check`.

## Final Status

Implementation complete locally. Waiting on commit, push, and CI verification.
