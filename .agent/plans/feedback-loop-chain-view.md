# Feedback Loop Chain View

Add a read-only chain projection that lets an operator inspect the baseline
feedback loop, its follow-up outcome, and the follow-up event's loop status in
one API/CLI call.

## Scope

- Add a typed `CampaignFeedbackLoopChainResponse` contract.
- Build a chain projection from:
  - baseline loop summary
  - feedback outcome report
  - follow-up loop summary when a follow-up event exists
- Expose `GET /campaign-events/performance/{event_id}/feedback-loop-chain`.
- Add `ads-growth-agent get-feedback-loop-chain`.
- Return a typed `recommended_focus` so operators know whether to record a
  follow-up snapshot, review the follow-up draft, run dry-run validation,
  prepare handoff, or keep monitoring.
- Add unit/API/CLI/auth tests.
- Update README, RFC, roadmap, changelog, and this ExecPlan.

## Checklist

- [x] Add this ExecPlan.
- [x] Add chain response contract and builder.
- [x] Wire FastAPI and CLI.
- [x] Add focused unit/API/CLI/auth coverage.
- [x] Update docs.
- [x] Run focused tests, full tests, ruff, compile check, and diff check.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Make the chain projection read-only.
  Reason: Existing event-rooted endpoints already mutate/audit reviews and
  dry-runs; the chain view should only compose status.
- Decision: Embed full loop summaries instead of inventing partial ad hoc
  dictionaries.
  Reason: Existing summaries are typed, tested, and already carry the operator
  state needed for product-level debugging.
- Decision: Keep the baseline event as the chain root.
  Reason: Operators usually start from the original handoff outcome and want to
  see whether the follow-up loop has started.

## Verification

- [x] No follow-up recommends recording a follow-up snapshot.
- [x] Regressed follow-up with no review recommends reviewing the follow-up
  optimization draft.
- [x] API and CLI return matching chain status.
- [x] New chain endpoint remains protected by API auth.
