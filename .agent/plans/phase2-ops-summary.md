# Phase 2 Ops Summary

## Goal

Add a local, queryable operator summary for Phase 2 so the platform can surface
important persisted runtime state without relying only on stdout logs. This is
not a production metrics stack; it is a product-level diagnostic read model over
existing stores.

## Scope

- Add compact ops summary contracts for failed runs, failed jobs, failed outbox
  events, and feedback events that need operator attention.
- Add `AgentRunReadStore.list_runs` for recent run inspection.
- Add an ops summary builder that composes existing run/job/outbox/performance
  event/feedback stores.
- Expose `GET /ops/summary`.
- Add `ads-growth-agent ops-summary`.
- Add unit/API/CLI/auth coverage.
- Update README, RFC, roadmap, and changelog.

## Non-Goals

- No Prometheus/OpenTelemetry metrics endpoint.
- No dashboards, alerts, SLOs, or time-series aggregation.
- No production IAM, rate limits, or replica routing.

## Acceptance Criteria

- The summary returns recent failed runs, failed strategy jobs, failed outbox
  events, and feedback loops that have a next operator command.
- Empty/noop backends return an empty but valid summary.
- API and CLI produce the same contract.
- Product routes remain covered by the local auth dependency.

## Verification

- [x] Focused ops/auth tests pass through the full suite coverage.
- [x] Full suite passes: `311 passed, 21 skipped`.
- [x] Ruff, py_compile, and diff check pass.
