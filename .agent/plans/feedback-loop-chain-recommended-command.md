# Feedback Loop Chain Recommended Command

Make the feedback loop chain view actionable by returning the concrete
operator command that matches the recommended focus.

## Scope

- Add recommended-command fields to `CampaignFeedbackLoopChainResponse`.
- Reuse existing command-center command objects instead of inventing another
  action shape.
- Select the command from either the baseline command center or the follow-up
  command center depending on the chain focus.
- Surface command ID/source in API response headers.
- Extend unit/API/CLI/product-loop tests and docs.

## Checklist

- [x] Add this ExecPlan.
- [x] Extend chain contract.
- [x] Add command selection logic to chain builder.
- [x] Update API headers and tests.
- [x] Update docs and product-loop verifier.
- [x] Run focused tests, full tests, ruff, compile check, and diff check.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Reuse `FeedbackLoopOperatorCommand`.
  Reason: The command center already defines typed API/CLI affordances with
  persistence requirements and guardrails.
- Decision: Keep full command centers out of the chain response.
  Reason: Chain view should stay compact; users can still call command-center
  endpoints for exhaustive command lists.
- Decision: Prefer the baseline command center for follow-up snapshot and
  outcome re-entry commands, and the follow-up command center for commands that
  advance the follow-up loop itself.
  Reason: This mirrors how an operator thinks about a baseline handoff outcome
  leading into the next event-rooted loop.

## Verification

- [x] Regressed follow-up chain recommends the follow-up review command.
- [x] Improved follow-up chain recommends recording/monitoring the next
  performance event.
- [x] API and CLI expose the same recommended command ID.
- [x] Product-loop verifier includes chain command ID in its summary.
