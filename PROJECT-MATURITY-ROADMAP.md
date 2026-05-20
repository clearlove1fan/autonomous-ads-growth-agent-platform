# Project Maturity Roadmap

This roadmap defines the order in which the Autonomous Ads Growth Agent Platform should mature. The goal is to avoid jumping directly into distributed-system hardening before the core technical project is strong, explainable, and demo-ready.

## Current Maturity Snapshot

| Dimension | Current Estimate | Target Before Moving On | Status |
|---|---:|---:|---|
| Interview-quality technical project | 90-95% | 85-90% | Phase 1 MVP complete for v0.1 |
| Engineering workflow and quality gates | 80%+ | 75%+ | CI split, lock, release notes, and policy docs added; branch protection blocked by GitHub plan |
| Production architecture skeleton | 68-72% | 75-80% | In progress |
| True production-ready system | 15-20% | 60%+ for this repo | Early |

These numbers are intentionally conservative. The project now has a credible agent-runtime and production-skeleton foundation, including persistence, tenant scoping, retry/resume, checkpointing, async strategy jobs, event feedback, feedback action plans, feedback optimization drafts, persisted feedback review decisions, revision drafts for `needs_revision` reviews, persisted dry-run execution validation, performance event discovery, advertiser memory write/read surfaces, dependency locking, explicit CI quality gates, a deterministic one-command MVP demo, local agent evals, curated positive/negative demo verifiers, a persisted product loop verifier, and an optional local API key boundary. Phase 1 is complete for the v0.1 MVP, while branch protection remains a GitHub repository setting tracked in Phase 1.5 and is currently blocked for the private repo by GitHub plan limits. The project should still not claim production-grade availability, full security, or distributed-system readiness yet.

## Phase 1: Interview-Quality Technical Project

Goal: demonstrate a product-level AI Agent platform aligned with the TikTok Software Engineer, AI Agent role.

Completion standard:

- The project tells a clear product story: autonomous ads growth for advertisers.
- The architecture maps directly to JD keywords: LangGraph orchestration, tool use, memory, RAG, structured output, multi-step reasoning, critique loop, LLMOps, and evaluation.
- The codebase can be run locally through API and CLI.
- The workflow is explainable from input brief to final strategy.
- The README, HLD, ADRs, and demo commands are interview-ready.
- Tests cover the major workflow contracts and failure paths.
- Basic engineering quality gates are documented honestly, including what is implemented and what is still missing.

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
- Optional PostgreSQL campaign draft persistence and read APIs/CLI for
  draft-only business artifacts.
- Optional PostgreSQL advertiser memory persistence and read APIs/CLI for
  long-term memory review.
- Optional PostgreSQL API idempotency for duplicate request replay and conflict detection.
- Optional LangGraph memory/PostgreSQL checkpointer for durable graph state.
- Run detail, retry, and resume APIs.
- Campaign performance event ingestion and deterministic feedback analysis.
- Campaign performance event detail/list APIs and CLI for feedback review.
- Draft-only feedback action plan API and CLI for ranked next-step review.
- Draft-only feedback optimization draft API and CLI for concrete change review.
- Feedback optimization review API and CLI for approval, rejection, or revision
  decisions on draft-only changes.
- Feedback revision draft API and CLI for `needs_revision` review decisions.
- Dry-run feedback execution plan API and CLI for approved review decisions,
  plus typed registry validation of approved dry-run steps.
- Feedback execution dry-run persistence and read APIs/CLI for validation audit.
- Performance event idempotency and conflict protection.
- Strategy-linked feedback context in final strategies.
- One-command deterministic Phase 1 demo through the CLI.
- Negative demo coverage for structured safe failure.
- Agent eval scores for planner orchestration, retrieval grounding, critic quality gate, and revision behavior.
- README mapping from project features to TikTok AI Agent role themes.
- HLD implementation-sync sections with current architecture and sequence diagrams.
- Liveness/readiness health endpoints with configured dependency checks.
- Async strategy job API and CLI with natural-language submission, pollable
  memory/Postgres job state, `run_id` discovery, and v0.1 background execution.

Remaining Phase 1 blockers:

- None for the v0.1 functional MVP. GitHub branch protection is an external repository setting and is tracked in Phase 1.5.

Phase 1 completion note, 2026-05-18:

- A reviewer can run `ads-growth-agent demo` without model provider keys and see the full natural-language intake, strategy generation, feedback-context reuse, and performance feedback loop.
- README, RFC/HLD, roadmap, eval cases, and changelog now describe the implemented path instead of future intent.
- The project remains draft-only and local-stack oriented; production auth, rate limits, SLO dashboards, native partitioning, replica routing, and live ad execution remain future work.

Exit criteria:

- A reviewer can understand the system in 10 minutes from README + HLD.
- A reviewer can run a deterministic demo without external model keys.
- A reviewer can optionally enable LLM planner/critic through LiteLLM.
- The project clearly shows agent-platform engineering rather than a prompt demo.
- A reviewer can see which checks run automatically and which launch gates are still pending.

## Phase 1.5: Engineering Workflow and Quality Gates

Goal: make the project safe to iterate on by turning local quality expectations into repeatable repository-level gates.

Completion standard:

- CI has separate, readable checks for lint, unit tests, deterministic end-to-end smoke, and integration/release verification.
- Dependency installs are reproducible from a committed lock file.
- The branch and PR workflow is documented and reflected in GitHub repository settings where possible.
- Release milestones include version tags, changelog notes, and verification notes.
- The roadmap remains honest about which gates are implemented versus planned.

Current state:

- `.github/workflows/ci.yml` now separates lint, unit, deterministic E2E smoke, Postgres integration, and release-readiness jobs.
- `requirements-lock.txt` is committed for reproducible v0.1 CI and demo installs.
- `CHANGELOG.md` records the Phase 1 MVP completion scope.
- Branch protection and required PR approval are documented but blocked by GitHub private-repository plan limits unless GitHub Pro is enabled or the repository is made public.
- Deterministic product smoke coverage exists for direct API, async job, and CLI boundaries.
- `v0.1.0` tag and GitHub Release are published for the Phase 1 MVP demo milestone.
- Curated demo verification is available through `python scripts/verify_phase1_demo.py`.
- Curated negative demo verification is available through `python scripts/verify_negative_demos.py`.
- Persisted product-loop verification is available through
  `python scripts/verify_persisted_product_loop.py` when Docker PostgreSQL is
  available.

Planned work:

- Configure GitHub branch protection for `main` after GitHub Pro is enabled or the repository is made public.
- Keep dependency lock refreshes deliberate and tied to full verification.

Exit criteria:

- `main` cannot be updated through a normal PR path unless required checks pass.
- A clean machine or CI runner can install reproducible dependencies and run the deterministic smoke path.
- A release candidate has a clear tag, changelog entry, and verification record.
- Any skipped integration or release gate has an explicit reason and owner.

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
- `campaign_drafts` persistence and tenant-scoped read APIs/CLI.
- `advertiser_memories` write path plus tenant-scoped read APIs/CLI.
- Seed loader for local knowledge corpus.
- `PostgresKnowledgeStore` adapter.
- `PostgresAgentRunStore` adapter.
- LangGraph Postgres checkpointer.
- API idempotency key for strategy generation.
- Tenant-aware request context.
- Repository/service layer around persistence.
- Run detail, retry, and resume APIs.
- Campaign performance event persistence and idempotency.
- Campaign performance event discovery APIs/CLI with advertiser, run, campaign,
  draft, and event-type filters.
- Feedback action plan APIs/CLI for persisted performance events.
- Feedback optimization draft APIs/CLI for persisted performance events.
- Feedback optimization review APIs/CLI and PostgreSQL audit state for persisted
  performance events.
- Feedback revision draft APIs/CLI for `needs_revision` review decisions.
- Feedback execution plan APIs/CLI for approved feedback optimization reviews,
  including draft-only typed registry validation.
- Feedback execution dry-run persistence and tenant-scoped read APIs/CLI.
- Async strategy job store and API.
- Async strategy job submission from natural-language advertiser goals.
- Async strategy job submission and detail lookup from the CLI.
- Async strategy job discovery by `run_id` from API and CLI.
- Optional local API key authentication boundary for product endpoints.

Exit criteria:

- The in-memory knowledge store can be swapped for Postgres without changing graph logic.
- Agent runs and steps are persisted with enough detail for replay/debugging.
- Campaign drafts are stored as drafts only.
- Persisted campaign drafts can be retrieved for review and audit.
- Persisted advertiser memories can be retrieved for review and audit.
- Campaign performance events are persisted, replayable, and conflict-safe.
- Persisted campaign performance events can be discovered for review and audit.
- Persisted feedback action plans can be retrieved for ranked draft-only next steps.
- Persisted feedback optimization drafts can be retrieved for concrete draft-only changes.
- Persisted feedback optimization reviews can be recorded and retrieved for
  approval, rejection, or revision audit.
- `needs_revision` feedback optimization reviews can produce draft-only revised
  proposals for another approval pass.
- Approved feedback optimization reviews can produce dry-run tool intent plans
  and validate those plans through draft-only tools without live campaign
  mutation.
- Persisted feedback execution dry-run results can be retrieved and listed for
  validation audit.
- A live PostgreSQL walkthrough proves strategy draft -> feedback event ->
  optimization review -> revision draft -> dry-run execution plan -> persisted
  execution dry-run validation -> outbox memory -> API/CLI reads -> later RAG
  retrieval.
- Failed runs can be retried and failed/running runs can be resumed with clear semantics.
- Strategy generation can be submitted as a pollable job with persisted status.
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
- Do not claim launch readiness until CI, dependency locking, branch protection, and deterministic E2E smoke gates are explicitly accounted for.

## Next Recommended Backlog

1. Configure branch protection for `main` after GitHub Pro is enabled or the repository is made public.
2. Replace in-process background jobs with a durable worker queue design and outbox/DLQ plan.
3. Add production identity mapping, JWT validation, RBAC, and per-tenant authorization.
4. Add production metrics endpoint for run latency, validation failures, tool failures, and feedback events.
5. Add timeout budgets and circuit-breaker behavior for LLM, retrieval, and tool execution.
6. Implement native partition migrations and replica-aware read routing as a later production-hardening slice.
