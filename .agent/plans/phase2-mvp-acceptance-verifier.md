# Phase 2 MVP Acceptance Verifier

## Goal

Finish Phase 2 by adding one executable acceptance verifier that proves the
production-architecture skeleton works as a coherent local MVP. The verifier
should combine the existing live PostgreSQL product loop with the Phase 2
control-plane surfaces added later, such as external job processing, run
lifecycle CLI commands, and local ops summary.

## Scope

- Add `scripts/verify_phase2_mvp.py`.
- Reuse the live persisted product-loop verifier instead of duplicating that
  workflow.
- Add additional control-plane checks for:
  - external strategy job processing through API
  - run lifecycle CLI: `get-run`, `resume-run`, `retry-run`
  - local ops summary CLI contract
- Update the live PostgreSQL integration test to run the Phase 2 verifier.
- Update README, RFC, roadmap, and changelog to mark Phase 2 as functionally
  complete, while keeping Phase 3 production-hardening boundaries explicit.

## Non-Goals

- No Phase 3 production hardening.
- No native partition migrations, replica routing, rate limits, RBAC, external
  queue, DLQ, SLO dashboard, or load testing.
- No real ad platform mutation.

## Acceptance Criteria

- `python scripts/verify_phase2_mvp.py` produces an operator-readable summary
  when `RUN_POSTGRES_INTEGRATION=1` and PostgreSQL is available.
- CI PostgreSQL integration runs the Phase 2 verifier.
- The verifier fails fast with structured issues if any core product-loop or
  control-plane contract regresses.
- Roadmap/RFC clearly say Phase 2 is functionally complete, not production
  ready.

## Verification

- [x] Focused Phase 2 verifier integration test passes locally as a Postgres-gated skip, and control-plane checks pass directly.
- [x] Full suite passes: `315 passed, 21 skipped`.
- [x] Ruff, py_compile, and diff check pass.
