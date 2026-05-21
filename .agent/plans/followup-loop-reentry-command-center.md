# Follow-Up Loop Re-Entry Command Center

When an applied handoff is followed by regressed or mixed performance, the
feedback loop command center should guide the operator into the next
optimization loop for the follow-up event.

## Scope

- Add command-center action types for follow-up optimization draft inspection
  and review.
- Extend outcome-regressed/mixed commands to include follow-up action plan,
  follow-up optimization draft, and follow-up review affordances.
- Keep improved outcomes on monitor-next-snapshot behavior.
- Keep loop summary and timeline rooted in the baseline event; only command
  center exposes the next-loop affordances.
- Extend unit, API, and CLI tests for regressed follow-up outcomes.
- Update README, RFC, roadmap, changelog, and this ExecPlan.

## Checklist

- [x] Add this ExecPlan.
- [x] Add follow-up command action contracts.
- [x] Extend command-center builder for next-loop affordances.
- [x] Add focused unit/API/CLI coverage.
- [x] Update docs.
- [x] Run focused tests, full tests, ruff, compile check, and diff check.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Use follow-up event APIs instead of adding a new workflow endpoint.
  Reason: Existing event-rooted action-plan, optimization-draft, and review
  endpoints already model a new optimization loop safely.
- Decision: Keep `inspect_feedback_outcome_report` as primary for
  regressed/mixed outcomes.
  Reason: Operators should inspect measured outcome before approving the next
  draft.
- Decision: Make follow-up review disabled when feedback review persistence is
  not enabled.
  Reason: Review decisions are persisted audit records, and v0.1 should not
  pretend a review can be safely recorded without that backend.

## Verification

- [x] Regressed outcome includes follow-up action-plan, optimization-draft, and
  review commands.
- [x] Follow-up review command is enabled only when review persistence is
  enabled.
- [x] API command center exposes follow-up re-entry commands for regressed
  outcomes.
- [x] CLI command center exposes the same follow-up re-entry commands.
