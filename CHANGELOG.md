# Changelog

## Unreleased

- Added a curated Phase 1 demo verifier and expected-output excerpt for the
  fitness app scenario.
- Added curated negative demo coverage for invalid LLM planner safe failure,
  idempotency conflict, and performance event conflict.
- Added optional local API key authentication for product API endpoints while
  keeping health probes public.
- Added `run_id` filtering for async strategy job discovery in the API, CLI,
  memory store, and PostgreSQL store.
- Added natural-language async strategy job submission through
  `/growth-strategies/jobs/from-text`.
- Added CLI commands to submit structured or natural-language strategy jobs and
  fetch a single job by ID.
- Added campaign draft detail/list read APIs and CLI commands for persisted
  draft review.
- Added advertiser memory detail/list read APIs and CLI commands for persisted
  long-term memory review.
- Added campaign performance event list/detail CLI commands and filtered list
  API support for persisted feedback review.
- Added a live PostgreSQL persisted product loop verifier covering strategy
  draft, feedback event, outbox memory, API/CLI reads, and later RAG retrieval.
- Added draft-only feedback action plans through API and CLI for persisted
  campaign performance events.
- Added draft-only feedback optimization drafts through API and CLI for
  persisted campaign performance events.
- Added feedback optimization review persistence, API, and CLI commands so a
  reviewer can approve, reject, or request revision for draft-only changes.
- Added feedback revision draft API and CLI commands for `needs_revision`
  review records before another approval pass.
- Added feedback revision review API and CLI commands so revised drafts can be
  approved and continue into the existing dry-run execution plan path.
- Added feedback review lineage API and CLI commands for source review,
  revision draft, revision review, and execution-readiness audit.
- Extended feedback review lineage with compact execution-plan and persisted
  dry-run validation summaries.
- Added filtered feedback review lineage list API and CLI surfaces for audit by
  event, advertiser, optimization draft, decision, or lineage stage.
- Added feedback loop summary API and CLI surfaces so operators can inspect
  current stage, next actions, reviews, lineage, dry-run audit, and handoff
  outcome records from one persisted performance event.
- Added read-only feedback handoff package API and CLI surfaces for approved
  reviews with latest dry-run validation, manual steps, checklist, and
  guardrails.
- Added persisted feedback handoff outcome records through API and CLI so
  operators can audit applied, blocked, or skipped manual handoffs.
- Added dry-run feedback execution plans through API and CLI for approved
  optimization reviews.
- Added dry-run feedback execution validation through draft-only typed tool
  registry APIs and CLI commands, preserving no live campaign mutation.
- Added optional PostgreSQL persistence plus API/CLI read surfaces for feedback
  execution dry-run validation audit records.

## v0.1.0 - 2026-05-18

- Completed the v0.1 Phase 1 functional MVP documentation pass for the
  deterministic advertiser-growth loop.
- Added the one-command `ads-growth-agent demo` path covering natural-language
  intake, LangGraph strategy generation, feedback context reuse, and
  performance feedback analysis without external model keys.
- Expanded local agent eval coverage for planner orchestration, retrieval
  grounding, critic quality, revision behavior, draft-only safety, and
  observability metadata.
- Added repository quality gates for lint, unit tests, deterministic product
  smoke tests, live PostgreSQL integration tests, and release readiness.
- Added `requirements-lock.txt` for reproducible v0.1 CI and demo installs.
- Added deterministic API, async job, and CLI product smoke coverage.

Known limitations:

- v0.1 remains draft-only and does not execute live ad spend or mutate live ad
  platform state.
- GitHub `main` branch protection is documented but blocked for the current
  private repository unless GitHub Pro is enabled or the repository is made
  public.
