# Persisted Product Loop Walkthrough

## Goal

Prove the v0.1 product loop through persisted API and CLI boundaries:
strategy draft generation, campaign performance feedback ingestion, outbox
memory materialization, and later RAG retrieval of the learned advertiser
memory.

## Scope

- Add a reusable local walkthrough script for the persisted product loop.
- Exercise FastAPI endpoints for strategy generation, draft reads, performance
  event ingestion, event discovery, and advertiser memory reads.
- Exercise CLI reads for persisted campaign draft, performance event, and
  advertiser memory discovery.
- Verify outbox processing turns performance feedback into advertiser memory.
- Verify a later strategy generation retrieves the newly created advertiser
  memory through PostgreSQL RAG.
- Add integration regression coverage guarded behind the existing live
  PostgreSQL flag.
- Update roadmap/RFC notes with the new acceptance path.

## Plan

- [x] Add this ExecPlan.
- [x] Implement persisted product loop walkthrough helper/script.
- [x] Add live PostgreSQL integration regression coverage.
- [x] Update roadmap/RFC notes.
- [x] Run focused and full local verification.
- [x] Commit and push the slice.

## Decisions

- Decision: Keep this as a live PostgreSQL integration path.
  Reason: The value is proving persistence, outbox processing, tenant-scoped
  reads, and PostgreSQL-backed RAG retrieval together.
- Decision: Use FastAPI TestClient plus Typer CliRunner.
  Reason: The product boundary for v0.1 is API + CLI, so the walkthrough should
  validate both without requiring a browser UI.
- Decision: Use deterministic non-LLM settings.
  Reason: This should be a stable acceptance path; LLM-provider behavior is
  covered by separate gateway and structured-output tests.

## Verification

- [x] Focused integration skip/pass behavior.
  Result: `.venv/bin/pytest tests/integration/test_postgres_product_loop_walkthrough.py`
  passed in local skip mode when `RUN_POSTGRES_INTEGRATION` was not set.
- [x] Full pytest.
  Result: `.venv/bin/pytest` passed with 206 passed and 19 skipped.
- [x] Ruff.
  Result: `.venv/bin/ruff check scripts/verify_persisted_product_loop.py tests/integration/test_postgres_product_loop_walkthrough.py`
  passed.
- [x] `git diff --check`.
  Result: Passed.

Note: Local live Docker verification was not run because Docker Desktop was not
running; `docker compose ps` could not connect to the Docker socket. The live
PostgreSQL behavior is covered by the guarded integration test and should run
in CI where the Postgres service is available.

## Final Status

Implemented, locally verified in default skip/offline mode, and ready for CI
PostgreSQL verification.
