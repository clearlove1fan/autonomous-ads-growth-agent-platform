# Project Maturity Roadmap

This roadmap defines the order in which the Autonomous Ads Growth Agent Platform should mature. The goal is to avoid jumping directly into distributed-system hardening before the core technical project is strong, explainable, and demo-ready.

## Current Maturity Snapshot

| Dimension | Current Estimate | Target Before Moving On | Status |
|---|---:|---:|---|
| Interview-quality technical project | 65-70% | 85-90% | In progress |
| Production architecture skeleton | 50-55% | 75-80% | In progress |
| True production-ready system | 15-20% | 60%+ for this repo | Early |

These numbers are intentionally conservative. The project now has a credible agent-runtime and production-skeleton foundation, including persistence, tenant scoping, retry/resume, checkpointing, and event feedback. It should still not claim production-grade availability, security, or distributed-system readiness yet.

## Phase 1: Interview-Quality Technical Project

Goal: demonstrate a product-level AI Agent platform aligned with the TikTok Software Engineer, AI Agent role.

Completion standard:

- The project tells a clear product story: autonomous ads growth for advertisers.
- The architecture maps directly to JD keywords: LangGraph orchestration, tool use, memory, RAG, structured output, multi-step reasoning, critique loop, LLMOps, and evaluation.
- The codebase can be run locally through API and CLI.
- The workflow is explainable from input brief to final strategy.
- The README, HLD, ADRs, and demo commands are interview-ready.
- Tests cover the major workflow contracts and failure paths.

Completed:

- FastAPI and CLI entry points.
- Typed domain contracts with Pydantic v2.
- Internal typed tool registry.
- Deterministic LangGraph workflow.
- LiteLLM gateway with structured output fallback and repair.
- Opt-in LLM planner feature flag.
- Opt-in LLM critic feature flag.
- Bounded self-reflection revision loop.
- Deterministic RAG and advertiser-memory foundation.
- Structured JSON logs.
- LangSmith-compatible run metadata.
- Local evaluation suite.
- PostgreSQL-backed knowledge store adapter with seed loader.
- Runtime knowledge-store backend switch for memory or Postgres retrieval.
- Optional PostgreSQL run persistence for `agent_runs` and `agent_run_steps`.
- Optional PostgreSQL campaign draft persistence for draft-only business artifacts.
- Optional PostgreSQL API idempotency for duplicate request replay and conflict detection.
- Optional LangGraph memory/PostgreSQL checkpointer for durable graph state.
- Run detail, retry, and resume APIs.
- Campaign performance event ingestion and deterministic feedback analysis.
- Performance event idempotency and conflict protection.
- HLD implementation-sync sections with current architecture and sequence diagrams.
- Liveness/readiness health endpoints with configured dependency checks.

Remaining:

- Add a curated demo script with expected outputs and screenshots/log excerpts.
- Add agent-eval cases for planner, retrieval grounding, critic, and revision behavior.
- Add a resume/interview mapping section that ties project features to the TikTok JD.
- Add negative demo cases that show safe failure rather than silent bad output.

Exit criteria:

- A reviewer can understand the system in 10 minutes from README + HLD.
- A reviewer can run a deterministic demo without external model keys.
- A reviewer can optionally enable LLM planner/critic through LiteLLM.
- The project clearly shows agent-platform engineering rather than a prompt demo.

## Phase 2: Production Architecture Skeleton

Goal: make the repo look like the backend foundation of a real platform, even if it still runs locally.

Completion standard:

- Durable data model exists for business data, knowledge, memory, retrieval events, and agent runs.
- Runtime state can be persisted and resumed.
- API boundaries include idempotency, tenant/advertiser isolation, and structured failure responses.
- Retrieval uses a real adapter behind the existing knowledge-store interface.
- Observability is queryable locally without relying only on stdout.

Planned work:

- PostgreSQL schema and Alembic migrations.
- Partition-aware logical schema design:
  - `tenant_id`
  - `advertiser_id`
  - `run_id`
  - `created_at`
  - `partition_date`
- `knowledge_documents` and `knowledge_chunks` with pgvector support.
- `advertiser_memories` table.
- `retrieval_events` table.
- `agent_runs` and `agent_run_steps` tables.
- `campaign_drafts` persistence.
- Seed loader for local knowledge corpus.
- `PostgresKnowledgeStore` adapter.
- `PostgresAgentRunStore` adapter.
- LangGraph Postgres checkpointer.
- API idempotency key for strategy generation.
- Tenant-aware request context.
- Repository/service layer around persistence.
- Run detail, retry, and resume APIs.
- Campaign performance event persistence and idempotency.

Exit criteria:

- The in-memory knowledge store can be swapped for Postgres without changing graph logic.
- Agent runs and steps are persisted with enough detail for replay/debugging.
- Campaign drafts are stored as drafts only.
- Campaign performance events are persisted, replayable, and conflict-safe.
- Failed runs can be retried and failed/running runs can be resumed with clear semantics.
- Alembic migrations create the local database from scratch.
- Local Docker Compose can run API, Postgres + pgvector, and LiteLLM together.

## Phase 3: True Production Hardening

Goal: add availability, scalability, and distributed-system controls expected in a production platform.

Completion standard:

- The system has explicit SLOs, failure modes, retries, rate limits, and operational dashboards.
- Data model includes partition and replication strategy.
- High-write tables are safe from unbounded growth and hot partitions.
- LLM and retrieval dependencies degrade gracefully.
- Critical actions are idempotent and auditable.

Planned work:

- Availability design:
  - readiness and liveness probes
  - dependency health checks
  - graceful degradation for LLM and retrieval failure
  - model fallback and circuit breaker
  - bounded retries with exponential backoff
  - timeout budgets per graph node
- Scalability design:
  - partition keys for high-volume tables
  - read replicas for reporting and retrieval traffic
  - vector read replica strategy
  - hot advertiser mitigation
  - retention policy for retrieval events and run steps
- Reliability architecture:
  - queue for long-running strategy generation
  - outbox pattern for side effects
  - dead-letter queue
  - replay tooling
  - idempotency conflict handling
- Security and tenancy:
  - auth boundary
  - tenant isolation
  - per-tenant rate limits
  - audit log
  - secret management plan
- Observability:
  - metrics endpoint
  - latency/error/cost dashboards
  - SLO alerts
  - retrieval quality metrics
  - LLM structured-output failure metrics
- Resilience validation:
  - load tests
  - failure injection
  - replica lag tests
  - model gateway timeout tests

Exit criteria:

- The HLD can defend partition keys, replica usage, and consistency choices.
- The service has test coverage for major failure modes.
- The system can explain its degraded behavior when model, database, or retrieval dependencies fail.
- The project remains honest: production-ready claims are only made for implemented and verified capabilities.

## Execution Rules

- Finish Phase 1 before deep Phase 3 work.
- Do not add distributed-system complexity until the agent workflow remains explainable.
- Every substantial implementation slice should have an ExecPlan under `.agent/plans/`.
- Every committed slice should include verification notes.
- Prefer replacing interfaces over rewriting graph logic.
- Keep default local behavior deterministic and model-key-free.

## Next Recommended Backlog

1. Add curated demo script and expected outputs for the fitness app scenario.
2. Add agent-eval cases for RAG grounding and critique/revision.
3. Add negative demo cases for safe failure, idempotency conflict, and event conflict.
4. Add asynchronous workflow job model for long-running strategy generation.
5. Add auth boundary design and first local API key/JWT guard.
6. Add production metrics endpoint for run latency, validation failures, tool failures, and feedback events.
7. Add timeout budgets and circuit-breaker behavior for LLM, retrieval, and tool execution.
8. Implement native partition migrations and replica-aware read routing as a later production-hardening slice.
