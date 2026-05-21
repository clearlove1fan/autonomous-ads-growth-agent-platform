# Outcome-Aware Command Center

Make the feedback loop command center react to an available follow-up outcome
report instead of always leaving post-handoff loops on "record next performance
event."

## Scope

- Add outcome-aware command-center stage/status fields.
- Let the command center optionally receive the performance event store and
  build a feedback outcome report.
- Promote `inspect_feedback_outcome_report` to the primary command when a
  follow-up snapshot exists.
- For regressed or mixed outcomes, guide the operator to inspect the follow-up
  event action plan.
- Wire FastAPI and CLI command-center calls to pass the performance event store.
- Extend focused tests and the persisted product-loop verifier.
- Update README, RFC/HLD, roadmap, changelog, and this ExecPlan.

## Checklist

- [x] Create this ExecPlan.
- [x] Add outcome-aware command-center contracts.
- [x] Update command-center builder and API/CLI wiring.
- [x] Extend focused tests and persisted verifier.
- [x] Update docs.
- [x] Run focused tests, full tests, ruff, compile check, and diff check.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Keep loop summary/timeline stage unchanged and let command center
  expose outcome-aware current stages.
  Reason: Summary and timeline describe the original event-rooted loop; command
  center is the operator affordance surface and can move to the next actionable
  state.
- Decision: Do not require outcome-event store injection.
  Reason: Pure unit callers can still build the command center without
  persistence; API/CLI pass the store when available.
- Decision: Reuse the outcome report read endpoint as the primary post-follow-up
  command.
  Reason: Operators should inspect the measured result before creating another
  feedback loop.

## Verification

- [x] No follow-up keeps `record_next_performance_event` primary.
- [x] Improved follow-up promotes `inspect_feedback_outcome_report`.
- [x] Regressed/mixed follow-up includes a follow-up action-plan command.
- [x] API and CLI command centers expose outcome-aware stage and status.
- [x] Persisted product-loop verifier proves post-follow-up command-center shift.
