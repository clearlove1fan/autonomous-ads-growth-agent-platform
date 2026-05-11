# Partition-Aware Database Schema

## Goal

Add the first production-skeleton database schema for the platform. The schema should cover tenant/advertiser business data, campaign drafts, RAG documents/chunks, advertiser memory, retrieval events, agent runs, run steps, and idempotency. It should explicitly include partition/shard fields and document replica/balancing strategy while remaining runnable as a local PostgreSQL + pgvector schema.

## Context

- Relevant files:
  - `pyproject.toml` already includes SQLAlchemy, Alembic, psycopg, asyncpg, and pgvector dependencies.
  - `src/ads_growth_agent/config.py` contains `database_url`.
  - `src/ads_growth_agent/knowledge.py` defines the current in-memory retrieval contract.
  - `PROJECT-MATURITY-ROADMAP.md` lists schema, partitioning, pgvector, and persistence as Phase 2 work.
- Current behavior:
  - The repo has no Alembic scaffold.
  - There is no SQLAlchemy metadata or database migration.
  - RAG/memory is deterministic in-memory only.
- Constraints:
  - Do not require Docker/Postgres to be running for this slice.
  - Keep this as schema foundation; do not wire graph runtime to DB yet.
  - Include partition-aware columns from the start.
  - Be honest that v0.1 runs as local Postgres while future production uses native partitions/shards/replicas.

## Plan

- [x] Add SQLAlchemy metadata for core production-skeleton tables.
- [x] Add Alembic scaffold and initial migration.
- [x] Add schema design document with access patterns, partition keys, replicas, and rebalancing.
- [x] Add tests that validate table coverage and partition-aware columns.
- [x] Run lint, tests, compile checks, then commit and push.

## Decisions

- Decision: Use partition-ready columns instead of native Postgres partitioned tables in the first migration.
  Reason: Local development stays simple, while `tenant_id`, `partition_key`, `partition_bucket`, and `partition_date` preserve future sharding and partition migration paths.
- Decision: Keep vector columns in `knowledge_chunks` and `advertiser_memories`.
  Reason: These are the two retrieval-heavy tables that will eventually need pgvector search and vector read replicas.
- Decision: Add `idempotency_keys` in the first schema slice.
  Reason: Reliable API retries require idempotency before true production hardening.

## Discoveries

- Discovery: The repo already has database dependencies but no migrations.
  Evidence: `pyproject.toml` includes Alembic, SQLAlchemy, psycopg, asyncpg, and pgvector; no `migrations/` directory exists.
- Discovery: The schema now covers the Phase 2 core tables without wiring runtime code to Postgres yet.
  Evidence: `src/ads_growth_agent/persistence/schema.py` defines tenants, advertisers, campaign drafts, RAG tables, memory, retrieval events, agent runs, run steps, and idempotency.
- Discovery: Alembic can render the initial migration SQL offline.
  Evidence: `.venv/bin/alembic upgrade head --sql` generated SQL for extensions, tables, indexes, and `alembic_version`.
- Discovery: Live migration was not applied in this slice.
  Evidence: Docker/Postgres was not required; verification used metadata tests and Alembic offline SQL rendering.

## Verification

- [x] `.venv/bin/python -m pytest`
  Result: 52 passed.
- [x] `.venv/bin/python -m ruff check .`
  Result: All checks passed.
- [x] `.venv/bin/python -m compileall src tests migrations`
  Result: Completed successfully.
- [x] `.venv/bin/alembic upgrade head --sql`
  Result: Rendered offline SQL successfully.

## Final Status

Complete. The repo now has a partition-aware production-skeleton schema, Alembic scaffold, schema design document, and offline tests.
