# Campaign Draft Persistence

## Goal

Persist generated campaign drafts into PostgreSQL so the platform stores draft business artifacts, not only run audit records.

When complete:

- Default local behavior remains offline with `CAMPAIGN_DRAFT_PERSISTENCE_BACKEND=none`.
- `CAMPAIGN_DRAFT_PERSISTENCE_BACKEND=postgres` writes the `create_campaign_draft` tool output into `campaign_drafts`.
- Repeated deterministic runs update the same draft row instead of creating duplicates.
- Live integration tests verify persisted draft status, budget, strategy payload, metadata, and run linkage.

## Context

- Relevant files:
  - `src/ads_growth_agent/tools.py`
  - `src/ads_growth_agent/strategy.py`
  - `src/ads_growth_agent/persistence/schema.py`
  - `src/ads_growth_agent/persistence/run_store.py`
  - `tests/integration/`
- Current behavior:
  - `create_campaign_draft` returns a validated `CampaignDraftOutput`.
  - `campaign_drafts` exists in the schema but no runtime code writes it.
  - Run persistence now writes `agent_runs` and `agent_run_steps` when enabled.
- Constraints:
  - Keep default API/CLI behavior database-free.
  - Persist draft-only recommendations; do not imply live launch or spend mutation.
  - Keep tool execution itself deterministic and side-effect-free for default demos.

## Plan

- [x] Add `CAMPAIGN_DRAFT_PERSISTENCE_BACKEND=none|postgres` setting.
- [x] Add shared tenant/advertiser upsert helper for persistence stores.
- [x] Implement no-op and Postgres campaign draft stores.
- [x] Wire strategy generation to persist drafts after successful graph completion.
- [x] Add offline tests for factory and success/failure wiring.
- [x] Add live Postgres integration test for `campaign_drafts`.
- [x] Update docs and roadmap.
- [x] Run default and Docker-backed verification.
- [ ] Commit and push.

## Decisions

- Decision: Persist campaign drafts after graph completion, not inside the mock tool.
  Reason: The tool should remain a typed draft generator; persistence is a platform side effect controlled by runtime config.
- Decision: Keep draft persistence opt-in.
  Reason: Default demos should remain fast and database-free.
- Decision: Store the full final strategy JSON in `campaign_drafts.strategy_json`.
  Reason: A persisted draft should carry enough context to explain why it was generated.

## Discoveries

- Discovery:
- Discovery: Default tests remain DB-free; campaign draft persistence live tests are skipped unless explicitly enabled.
  Evidence: `.venv/bin/pytest` reported `66 passed, 5 skipped`.
- Discovery: Live Postgres verification passed across migrations, knowledge retrieval, run persistence, and campaign draft persistence.
  Evidence: `RUN_POSTGRES_INTEGRATION=1 ... .venv/bin/pytest tests/integration` reported `5 passed`.
- Discovery: The Postgres container was stopped after verification.
  Evidence: `docker compose stop postgres` completed.

## Verification

- [x] `.venv/bin/python -m compileall src tests`
  Result: Passed.
- [x] `.venv/bin/ruff check .`
  Result: Passed.
- [x] `.venv/bin/pytest`
  Result: `66 passed, 5 skipped`.
- [x] `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=... .venv/bin/pytest tests/integration`
  Result: `5 passed`.

## Final Status

Implementation and verification are complete. Commit and push are pending.
