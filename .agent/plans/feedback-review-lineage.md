# Feedback Review Lineage

## Goal

Add audit lineage for feedback optimization reviews so an operator can inspect
how an optimization draft moved through review, revision draft, revision review,
and execution readiness without manually joining multiple API calls.

## Scope

- Add a typed lineage contract.
- Build lineage from existing persisted review snapshots, without a new table.
- Add FastAPI and CLI read surfaces.
- Extend tests and the persisted product-loop verifier.
- Update README, RFC/HLD, roadmap, and changelog.

## Plan

- [x] Add this ExecPlan.
- [x] Add lineage contract and builder.
- [x] Add API and CLI lineage read surfaces.
- [x] Extend tests and persisted product-loop verifier.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Derive lineage from existing review records.
  Reason: The review table already stores the reviewed draft snapshot, selected
  change IDs, reviewer notes, and deterministic revision draft IDs.
- Decision: Support lineage lookups from either the source `needs_revision`
  review or a later revision review.
  Reason: Operators often start from the latest approval record, not the first
  revision request.
- Decision: Do not persist execution plan lineage in this slice.
  Reason: Execution plans are deterministic projections from approved reviews
  and can be included as readiness metadata first.

## Verification

- [x] Source review lineage includes revision draft and revision reviews.
- [x] Revision review lineage resolves back to the source review.
- [x] API/CLI lineage tests.
- [x] Persisted product-loop verifier covers lineage read.
- [x] Full pytest: `266 passed, 20 skipped`.
- [x] Ruff.
- [x] `git diff --check`.

## Final Status

Implementation complete locally. Waiting on commit, push, and CI verification.
