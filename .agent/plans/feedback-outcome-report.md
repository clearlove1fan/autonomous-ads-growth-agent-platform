# Feedback Outcome Report

Add a product-facing outcome report for a feedback loop after the operator
records a manual handoff and ingests the next performance snapshot.

## Scope

- Add typed contracts for baseline-vs-follow-up performance deltas.
- Build a deterministic outcome report from one baseline performance event and
  the next later persisted event for the same advertiser/campaign/draft/run
  context.
- Expose the report through FastAPI and CLI.
- Add a command-center inspection affordance for the report.
- Extend focused unit/API/CLI tests and the persisted product-loop verifier.
- Update README, RFC/HLD, roadmap, changelog, and this ExecPlan.

## Checklist

- [x] Create this ExecPlan.
- [x] Add outcome report contracts and builder.
- [x] Wire API and CLI report reads.
- [x] Add command-center report affordance.
- [x] Extend focused tests and persisted product-loop verifier.
- [x] Update docs.
- [x] Run focused tests, full tests, ruff, compile check, and diff check.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Compare the baseline event to the earliest later event in the same
  advertiser/run/campaign/draft context.
  Reason: Operators need to answer whether the first post-handoff snapshot
  improved, before broader trend analysis exists.
- Decision: Keep the report read-only and deterministic.
  Reason: v0.1 remains draft-only; the report should explain outcomes without
  launching or mutating live campaigns.
- Decision: Classify outcome from efficiency and conversion deltas.
  Reason: CPA, CVR, CTR, conversions, and ROAS are enough for a useful MVP
  judgment while keeping the logic auditable.

## Verification

- [x] No-follow-up state returns `no_followup_event`.
- [x] Improved follow-up metrics return `improved`.
- [x] API and CLI expose the same outcome report.
- [x] Command center includes an outcome-report inspection command.
- [x] Persisted product-loop verifier proves a post-handoff follow-up report.
