# Feedback Lineage List

## Goal

Add a batch audit view for feedback review lineage so operators can list review
lineages by event, advertiser, optimization draft, decision, or lineage stage.

## Scope

- Add a typed lineage list response.
- Add a builder that derives lineage lists from existing review records.
- Add FastAPI and CLI list surfaces.
- Extend tests and persisted product-loop verifier.
- Update README, RFC/HLD, roadmap, and changelog.

## Plan

- [x] Add this ExecPlan.
- [x] Add lineage list contract and builder.
- [x] Add API and CLI list surfaces.
- [x] Extend tests and persisted product-loop verifier.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [ ] Commit, push, and verify CI. Blocked locally by Codex escalation usage
  limit for git staging/push; implementation is verified and remains in the
  worktree.

## Decisions

- Decision: Use `/feedback-optimization-review-lineages` for the API list route.
  Reason: It avoids route ambiguity with `/feedback-optimization-reviews/{review_id}`.
- Decision: Derive list items from existing review records.
  Reason: Review lineage is a projection and should not require a new table in
  this slice.
- Decision: Apply lineage-stage filtering after building lineage records.
  Reason: Stage is derived from review snapshots and revision metadata, not a
  stored column.

## Verification

- [x] API list can filter by event, decision, and lineage stage.
- [x] CLI list can filter by event, decision, and lineage stage.
- [x] Persisted product-loop verifier covers lineage list reads.
- [x] Full pytest: `270 passed, 20 skipped`.
- [x] Ruff.
- [x] `git diff --check`.

## Final Status

Implementation complete and locally verified. Commit/push/CI verification is
pending until Codex can run escalated git operations again.
