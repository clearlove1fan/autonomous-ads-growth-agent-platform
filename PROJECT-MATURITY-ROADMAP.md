# Project Maturity Roadmap

This roadmap defines the order in which the Autonomous Ads Growth Agent Platform should mature. The goal is to avoid jumping directly into distributed-system hardening before the core technical project is strong, explainable, and demo-ready.

## Current Maturity Snapshot

| Dimension | Current Estimate | Target Before Moving On | Status |
|---|---:|---:|---|
| Interview-quality technical project | 50-60% | 85-90% | In progress |
| Production architecture skeleton | 30-40% | 75-80% | Started |
| True production-ready system | 10-15% | 60%+ for this repo | Not yet |

These numbers are intentionally conservative. The project has strong agent-runtime foundations, but it should not claim production-grade availability or distributed-system readiness yet.

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

Remaining:

- Polish HLD and ADR appendix against the current implementation.
- Add architecture diagrams and request/response sequence diagrams.
- Add a curated demo script with expected outputs.
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

Exit criteria:

- The in-memory knowledge store can be swapped for Postgres without changing graph logic.
- Agent runs and steps are persisted with enough detail for replay/debugging.
- Campaign drafts are stored as drafts only.
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

1. Update HLD and ADRs to reflect current implemented graph.
2. Add architecture and sequence diagrams.
3. Add demo script and interview walkthrough.
4. Add agent-eval cases for RAG grounding and critique/revision.
5. Design production schema with partition keys and replica strategy.
6. Implement Alembic migrations for core tables.
7. Persist agent runs and steps to PostgreSQL.
8. Add LangGraph Postgres checkpointer.
