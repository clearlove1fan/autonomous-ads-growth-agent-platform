# Feedback Handoff Outcome Memory

Add learned advertiser memory for manual feedback handoff outcomes so applied,
blocked, or skipped operator decisions can influence later PostgreSQL-backed
RAG and strategy generation.

## Scope

- Extend advertiser memory persistence with a handoff outcome memory write path.
- Add an outbox event and handler for asynchronous handoff memory writes.
- Wire API and CLI handoff-record submission to schedule or record memory when
  advertiser memory persistence is enabled.
- Extend focused unit/API/CLI/outbox tests and the persisted product-loop
  verifier.
- Update README, RFC/HLD, roadmap, changelog, and this ExecPlan.

## Checklist

- [x] Create this ExecPlan.
- [x] Extend memory store contracts and Postgres implementation.
- [x] Add handoff outcome memory outbox enqueue/processing.
- [x] Wire API and CLI handoff-record submission.
- [x] Extend focused tests and persisted product-loop verifier.
- [x] Update docs.
- [x] Run focused tests, full tests, ruff, compile check, and diff check.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Store handoff outcome memory as `historical_performance`.
  Reason: Current memory taxonomy is intentionally small, and operator outcomes
  are historical campaign-performance learnings.
- Decision: Use the existing outbox worker when `OUTBOX_BACKEND=postgres`.
  Reason: This keeps API latency bounded for product-style/high-concurrency
  paths and matches performance-event memory behavior.
- Decision: Keep handoff-record response body unchanged and expose memory status
  through headers/API logs and persisted memory reads.
  Reason: Handoff records are already typed audit objects; memory is a derived
  side effect.

## Verification

- [x] Handoff memory source IDs are stable.
- [x] Noop memory store reports disabled handoff memory writes.
- [x] Postgres memory store records handoff outcomes through the persisted verifier.
- [x] Outbox queues and processes `feedback_handoff_recorded` events.
- [x] API and CLI submit paths schedule/record memory when enabled.
- [x] Persisted product-loop verifier proves handoff outcome memory reads.
