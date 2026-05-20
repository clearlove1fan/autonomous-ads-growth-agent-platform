# Feedback Lineage Execution Audit

## Goal

Extend feedback review lineage so operators can see whether approved reviews in
the lineage have an execution plan projection and persisted dry-run validation
records.

## Scope

- Add compact execution/dry-run lineage summaries.
- Extend lineage builder to include execution readiness and persisted dry-run
  validation audit when an execution store is available.
- Wire API and CLI lineage reads to include execution dry-run persistence state.
- Extend tests and persisted product-loop verifier.
- Update README, RFC/HLD, roadmap, and changelog.

## Plan

- [x] Add this ExecPlan.
- [x] Add execution audit fields to lineage contract.
- [x] Extend lineage builder with optional execution-store reads.
- [x] Wire API/CLI to pass execution store.
- [x] Extend tests and persisted product-loop verifier.
- [x] Update docs and roadmap notes.
- [x] Run focused and full verification.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Store compact execution audit summaries in lineage.
  Reason: Operators need traceability without receiving full duplicated dry-run
  payloads in every lineage response.
- Decision: Keep execution plans deterministic projections.
  Reason: The current system does not persist execution plans separately; plan
  IDs and step counts can be derived from approved review snapshots.
- Decision: Treat dry-run records as optional.
  Reason: Dry-run persistence can be disabled locally, so lineage should still
  work and return empty dry-run audit fields.

## Verification

- [x] Lineage includes execution plan summary for approved reviews.
- [x] Lineage includes persisted dry-run summaries when available.
- [x] API/CLI lineage responses expose execution audit fields.
- [x] Persisted product-loop verifier covers dry-run lineage audit.
- [x] Full pytest: `266 passed, 20 skipped`.
- [x] Ruff.
- [x] `git diff --check`.

## Final Status

Implementation complete locally. Waiting on commit, push, and CI verification.
