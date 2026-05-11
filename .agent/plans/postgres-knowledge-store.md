# Postgres Knowledge Store

## Goal

Add a PostgreSQL-backed knowledge retrieval adapter and seed loader so the v0.1 RAG layer can read campaign playbooks, historical cases, and advertiser memory from the production-skeleton schema instead of only the in-memory corpus.

When complete, the default workflow will still use the in-memory store unless explicitly wired otherwise, but the Postgres adapter will be verified against a live migrated database.

## Context

- Relevant files:
  - `src/ads_growth_agent/knowledge.py`
  - `src/ads_growth_agent/graph.py`
  - `src/ads_growth_agent/persistence/schema.py`
  - `migrations/versions/0001_partition_aware_core_schema.py`
  - `tests/integration/test_migrations_postgres.py`
- Current behavior:
  - `InMemoryKnowledgeStore` retrieves deterministic local RAG results.
  - The database schema already includes `knowledge_documents`, `knowledge_chunks`, `advertiser_memories`, and `retrieval_events`.
  - Live migration smoke tests are skipped unless `RUN_POSTGRES_INTEGRATION=1`.
- Constraints:
  - Do not switch the main graph default to Postgres in this slice.
  - No real embeddings are generated in v0.1, so retrieval must use a deterministic Postgres FTS/metadata fallback.
  - Preserve partition-aware fields for high-volume tables.

## Plan

- [x] Review current knowledge contracts, graph integration point, schema, and live DB test helpers.
- [x] Add stable partition bucket helper and expose default seed documents.
- [x] Implement seed loader for tenants, advertisers, documents, chunks, and advertiser memories.
- [x] Implement `PostgresKnowledgeStore` with Postgres FTS/metadata retrieval and `retrieval_events` recording.
- [x] Pass graph run id into `KnowledgeQuery` so retrieval events can be correlated to runs.
- [x] Add tests for the helper/contract path and live Postgres retrieval.
- [x] Run default tests and Docker-backed integration tests.
- [ ] Commit and push the verified slice.

## Decisions

- Decision: Keep the graph default on `InMemoryKnowledgeStore`.
  Reason: This keeps local CLI/API demos fast and offline while the database adapter matures behind tests.
- Decision: Use Postgres FTS plus metadata boosts before embeddings.
  Reason: The schema supports vectors, but v0.1 has no embedding generation pipeline yet. FTS gives a realistic hybrid-search fallback path.
- Decision: Record retrieval events from the adapter itself.
  Reason: Retrieval is the correct boundary for latency, filters, and result attribution.

## Discoveries

- Discovery:
- Discovery: The default test environment skips live Postgres tests unless `RUN_POSTGRES_INTEGRATION=1`.
  Evidence: `pytest` reported `55 passed, 2 skipped`.
- Discovery: Sandbox network restrictions block localhost Postgres without escalation.
  Evidence: The first live test attempt failed with `Operation not permitted`; rerunning with approval passed.
- Discovery: The Postgres container was stopped after verification.
  Evidence: `docker compose stop postgres` completed.

## Verification

- [x] `.venv/bin/python -m compileall src tests`
  Result: Passed.
- [x] `.venv/bin/ruff check .`
  Result: Passed.
- [x] `.venv/bin/pytest`
  Result: `55 passed, 2 skipped`.
- [x] `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=... .venv/bin/pytest tests/integration/test_postgres_knowledge_store.py`
  Result: `1 passed`.
- [x] `RUN_POSTGRES_INTEGRATION=1 TEST_DATABASE_URL=... .venv/bin/pytest tests/integration`
  Result: `2 passed`.

## Final Status

Implementation and verification complete. Commit and push are pending.
