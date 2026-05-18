# Changelog

## Unreleased

- No unreleased changes.

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
