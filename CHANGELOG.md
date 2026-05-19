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
