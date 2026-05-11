# Runtime Knowledge Store Switch

## Goal

Make the knowledge layer selectable at runtime so local demos can stay deterministic with the in-memory store while Docker/Postgres runs can use the seeded PostgreSQL knowledge store without changing graph logic.

When complete:

- `KNOWLEDGE_STORE_BACKEND=memory` remains the default.
- `KNOWLEDGE_STORE_BACKEND=postgres` makes API/CLI strategy generation use `PostgresKnowledgeStore`.
- The CLI can seed the default knowledge corpus into PostgreSQL.
- Live integration tests verify a full strategy run writes `retrieval_events` through the Postgres retrieval path.

## Context

- Relevant files:
  - `src/ads_growth_agent/config.py`
  - `src/ads_growth_agent/strategy.py`
  - `src/ads_growth_agent/cli.py`
  - `src/ads_growth_agent/persistence/knowledge_store.py`
  - `src/ads_growth_agent/persistence/knowledge_seed.py`
  - `tests/integration/test_postgres_knowledge_store.py`
- Current behavior:
  - The graph accepts any `KnowledgeStore`.
  - `PostgresKnowledgeStore` is implemented and live-tested, but default API/CLI calls still always build the in-memory store indirectly.
  - There is no user-facing seed command yet.
- Constraints:
  - Keep offline default behavior model-key-free and DB-free.
  - Do not require Docker for default `pytest`.
  - Keep graph orchestration independent from persistence details.

## Plan

- [x] Add settings for `KNOWLEDGE_STORE_BACKEND` and `TENANT_ID`.
- [x] Add a knowledge-store factory that builds memory or Postgres stores from settings.
- [x] Wire strategy generation through the factory while preserving explicit test injection.
- [x] Add a CLI command to seed the default knowledge corpus into Postgres.
- [x] Add unit tests for factory/CLI behavior and live integration for full strategy generation with Postgres retrieval.
- [x] Update README/runtime docs.
- [x] Run default and Docker-backed verification.
- [ ] Commit and push.

## Decisions

- Decision: Keep `memory` as the default backend.
  Reason: Interview/demo workflows should run offline without Docker or model credentials.
- Decision: Use a factory rather than branching inside graph nodes.
  Reason: The graph should depend on the `KnowledgeStore` interface, not infrastructure details.

## Discoveries

- Discovery:
- Discovery: Default tests cover the runtime switch, CLI seed command, and offline strategy/API/CLI behavior.
  Evidence: `.venv/bin/pytest` reported `58 passed, 3 skipped`.
- Discovery: Live Docker/Postgres verification is currently blocked by Codex escalation usage limits, not by a test failure.
  Evidence: `docker compose up -d postgres` was rejected by the approval layer before running.
- Discovery: After Docker Desktop was started, the live integration suite passed.
  Evidence: `RUN_POSTGRES_INTEGRATION=1 ... .venv/bin/pytest tests/integration` reported `3 passed`.

## Verification

- [x] `.venv/bin/python -m compileall src tests`
  Result: Passed.
- [x] `.venv/bin/ruff check .`
  Result: Passed.
- [x] `.venv/bin/pytest`
  Result: `58 passed, 3 skipped`.
- [x] `git diff --check`
  Result: Passed.
- [x] `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=... .venv/bin/pytest tests/integration`
  Result: `3 passed`.

## Final Status

Runtime implementation and verification are complete. Commit and push are pending.
