# Autonomous Ads Growth Agent Platform

Production-style AI agent platform for advertiser growth automation.

The platform turns advertiser goals into structured campaign strategies across audience, creative, budget, bidding, measurement, and feedback optimization. It is designed around LangGraph-based orchestration, typed tool execution, RAG, advertiser memory, structured outputs, and LangSmith evaluation.

## Current Status

This repository is in v0.1 bootstrap. The first implementation milestone is an end-to-end local workflow:

1. Accept an advertiser growth goal through FastAPI or CLI.
2. Convert the request into a structured advertiser brief.
3. Route tasks through a LangGraph StateGraph.
4. Call typed mock ads tools.
5. Retrieve campaign knowledge from PostgreSQL + pgvector.
6. Run a critic pass.
7. Return a validated campaign growth strategy.

## Architecture Direction

The HLD is maintained in:

- [RFC-Autonomous-Ads-Growth-Agent-Platform-v0.1.md](./RFC-Autonomous-Ads-Growth-Agent-Platform-v0.1.md)

The project maturity roadmap is maintained in:

- [PROJECT-MATURITY-ROADMAP.md](./PROJECT-MATURITY-ROADMAP.md)

The production-skeleton database schema is maintained in:

- [DATABASE-SCHEMA.md](./DATABASE-SCHEMA.md)

Locked v0.1 stack:

- FastAPI product API
- CLI for local demo and evals
- LangGraph StateGraph for orchestration
- LiteLLM Proxy as the multi-provider LLM gateway
- PostgreSQL + pgvector for business data, RAG, memory, and checkpoints
- SQLAlchemy 2 + Alembic for data access and migrations
- Pydantic v2 for API, tool, and final output schemas
- LangSmith for agent traces and evaluations
- Structured JSON logs for service diagnostics
- Docker Compose for local development

## Development

Python 3.11+ is expected.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

For reproducible CI-style installs, use the committed lock file and then install
the local package without resolving dependencies again:

```bash
python -m pip install -r requirements-lock.txt
python -m pip install -e . --no-deps --no-build-isolation
```

Run the local API:

```bash
uvicorn ads_growth_agent.api:app --reload
```

Run the CLI:

```bash
ads-growth-agent health
ads-growth-agent plan examples/fitness_app_brief.json
ads-growth-agent seed-knowledge
ads-growth-agent eval examples/eval_cases.json
```

Run tests:

```bash
pytest
```

Run the deterministic product smoke tests:

```bash
pytest -m e2e
```

Run live PostgreSQL integration tests:

```bash
docker compose up -d postgres
RUN_POSTGRES_INTEGRATION=1 \
  TEST_DATABASE_URL=postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth \
  pytest tests/integration
```

The live tests create and drop isolated temporary databases. They are skipped by default unless `RUN_POSTGRES_INTEGRATION=1` is set.

## Local Runtime Stack

The local product-like stack uses Docker Compose:

- `api`: FastAPI service on port `8000`
- `postgres`: PostgreSQL 16 with pgvector on port `5432`
- `litellm`: LiteLLM Proxy on port `4000`

Create a local environment file:

```bash
cp .env.example .env
```

Set `OPENAI_API_KEY` in `.env` before calling LiteLLM-backed model endpoints. Liveness does not require a model key; readiness checks LiteLLM only when LLM planner or critic features are enabled.

Start the stack:

```bash
docker compose up --build
```

Check the API:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

`/health/live` is a shallow process check. `/health/ready` validates dependencies that are required by the current configuration, such as PostgreSQL-backed persistence or LiteLLM-backed agent reasoning.

Generate a draft growth strategy:

```bash
curl -X POST http://localhost:8000/growth-strategies \
  -H "Content-Type: application/json" \
  -d '{"brief":{"advertiser_id":"adv_fitness_001","product_name":"FitTrack Pro","product_category":"fitness app","objective":"registrations","budget":"2000.00","currency":"USD","duration_days":14,"target_market":"United States","primary_kpi":"trial registrations","target_cpa":"20.00"}}'
```

Submit a strategy generation job and poll it:

```bash
curl -i -X POST http://localhost:8000/growth-strategies/jobs \
  -H "Content-Type: application/json" \
  -d '{"brief":{"advertiser_id":"adv_fitness_001","product_name":"FitTrack Pro","product_category":"fitness app","objective":"registrations","budget":"2000.00","currency":"USD","duration_days":14,"target_market":"United States","primary_kpi":"trial registrations","target_cpa":"20.00"}}'

curl http://localhost:8000/growth-strategies/jobs/job_abc123

curl "http://localhost:8000/growth-strategies/jobs?status=queued&limit=20"
```

The default job executor uses FastAPI background tasks for local development. Set `STRATEGY_JOB_BACKEND=postgres` to persist job status and completed results in `strategy_jobs`.

For a production-style worker path, set `STRATEGY_JOB_BACKEND=postgres` and `STRATEGY_JOB_EXECUTION_MODE=external`. The API will leave jobs in `queued` state, and bounded workers can claim distinct jobs with PostgreSQL row locks:

```bash
STRATEGY_JOB_BACKEND=postgres STRATEGY_JOB_EXECUTION_MODE=external ads-growth-agent process-strategy-jobs --limit 10 --worker-id worker_a

STRATEGY_JOB_BACKEND=postgres ads-growth-agent list-strategy-jobs --status failed --limit 20
```

Worker claims use `FOR UPDATE SKIP LOCKED`, `attempt_count`, `locked_by`, and `locked_until` so multiple workers can process the same queue without claiming the same job at the same time.

External workers retry failed jobs with bounded exponential backoff. Configure the retry budget with `STRATEGY_JOB_MAX_ATTEMPTS`, `STRATEGY_JOB_RETRY_BASE_DELAY_SECONDS`, and `STRATEGY_JOB_RETRY_MAX_DELAY_SECONDS`. Failed attempts return the job to `queued` with `next_attempt_at`; once attempts are exhausted the job becomes terminal `failed`.

The current deterministic workflow runs through explicit LangGraph nodes:

```text
planner -> retriever -> tool_executor -> critic -> finalizer
```

Responses include `run_metadata` for local observability and LangSmith correlation:

- `run_id`
- `execution_id`
- `strategy_id`
- `trace_id`
- `langsmith_project`
- `tracing_enabled`
- `node_path`
- `tool_count`
- `failed_tool_count`
- `tool_summaries`

Run deterministic local evaluators:

```bash
ads-growth-agent eval examples/eval_cases.json
```

The local suite currently evaluates:

- budget consistency
- tool use correctness
- strategy completeness
- retrieval grounding, including expected source IDs/types and minimum relevance
- draft-only safety
- observability metadata

The `retriever` node uses the v0.1 knowledge layer to attach deterministic local RAG results before tool execution. The default seed corpus includes campaign strategy playbooks, historical cases, and advertiser profile memory. Final strategies cite retrieved sources as `rag_document`, `historical_case`, or `advertiser_memory`.

The default API/CLI workflow still uses the in-memory knowledge store for fast offline demos. Set `KNOWLEDGE_STORE_BACKEND=postgres` to make strategy generation use the PostgreSQL-backed `PostgresKnowledgeStore`.

Before switching to Postgres retrieval locally, apply migrations and seed the default corpus:

```bash
docker compose up -d postgres
alembic upgrade head
ads-growth-agent seed-knowledge
KNOWLEDGE_STORE_BACKEND=postgres ads-growth-agent plan examples/fitness_app_brief.json
```

The live integration suite verifies seeded retrieval from `knowledge_documents`, `knowledge_chunks`, and `advertiser_memories`, plus `retrieval_events` recording for run-level observability. This is the first database-backed RAG slice before adding embedding generation and hybrid vector ranking.

Run audit persistence is also opt-in. Set `RUN_PERSISTENCE_BACKEND=postgres` to write strategy executions into `agent_runs` and derived node records into `agent_run_steps`:

```bash
RUN_PERSISTENCE_BACKEND=postgres ads-growth-agent plan examples/fitness_app_brief.json
```

`strategy.strategy_id` is stable for a given advertiser brief, while `run_metadata.run_id` is a per-execution ID. `run_metadata.execution_id` mirrors `run_id` for clarity, and `run_metadata.strategy_id` links the execution back to the stable strategy. Repeated identical runs now create separate audit rows under the same `strategy_id`, which is closer to production replay, retry, and concurrency semantics.

When run persistence is enabled, each execution is first recorded as `running` before LangGraph starts. The same row is then updated to `completed` or `failed`, with node-level rows written to `agent_run_steps` once terminal state is reached. This lifecycle is the foundation for later resume and retry endpoints.

Persisted runs can be queried through the API:

```bash
curl http://localhost:8000/runs/run_abc123 \
  -H "X-Tenant-ID: tenant_demo"
```

The response includes lifecycle status, `strategy_id`, `execution_id`, trace metadata, the final strategy when completed, error summaries when failed, and ordered node step records.

Failed runs can be retried as a new execution:

```bash
curl -X POST http://localhost:8000/runs/run_failed_abc/retry \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: tenant_demo" \
  -d '{"brief":{"advertiser_id":"adv_fitness_001","product_name":"FitTrack Pro","product_category":"fitness app","objective":"registrations","budget":"2000.00","currency":"USD","duration_days":14,"target_market":"United States","primary_kpi":"trial registrations"}}'
```

Retry is intentionally separate from resume: the original failed run remains unchanged, and the retry creates a fresh `run_metadata.run_id` under the same stable strategy identity. Only failed runs are retryable, and the retry brief must match the original run's advertiser and objective.

Failed or running persisted runs can also be resumed under the same execution ID:

```bash
curl -X POST http://localhost:8000/runs/run_failed_abc/resume \
  -H "X-Tenant-ID: tenant_demo"
```

Resume uses the original `advertiser_brief` stored in `agent_runs.metadata`, rejects completed runs, and returns the normal `GrowthStrategyResponse` with `Resumed-Run-ID` and `Resume-Mode` headers. If `GRAPH_CHECKPOINTER_BACKEND=postgres`, the same LangGraph checkpoint thread is reused; otherwise v0.1 resume is same-run replay with honest API semantics.

Campaign performance events can trigger a first-pass feedback analysis loop:

```bash
curl -X POST http://localhost:8000/campaign-events/performance \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: tenant_demo" \
  -d '{"event_id":"evt_perf_001","advertiser_id":"adv_fitness_001","campaign_id":"cmp_fitness_001","objective":"registrations","event_type":"performance_snapshot","occurred_at":"2026-05-12T12:00:00Z","metrics":{"impressions":10000,"clicks":500,"spend":"1000.00","conversions":20},"target_cpa":"20.00","attribution_window_days":7}'
```

The response includes `health_status`, metric summaries such as CTR/CVR/CPA, draft-only feedback recommendations, and guardrails requiring human approval before budget or targeting changes. Set `PERFORMANCE_EVENT_PERSISTENCE_BACKEND=postgres` to persist events and analyses in `campaign_performance_events`.

Set `ADVERTISER_MEMORY_PERSISTENCE_BACKEND=postgres` to also write analyzed feedback into `advertiser_memories` as `historical_performance` memory. For production-style/high-concurrency ingestion, also set `OUTBOX_BACKEND=postgres`; the API will enqueue a durable `campaign_performance_analyzed` event and return `Advertiser-Memory-Status: queued` instead of doing the memory write on the request path. A bounded worker can then process the queue:

```bash
OUTBOX_BACKEND=postgres ADVERTISER_MEMORY_PERSISTENCE_BACKEND=postgres ads-growth-agent process-outbox --limit 100
```

After the worker completes, replaying the same event returns `Advertiser-Memory-Status: recorded`, and later strategy-generation runs with `KNOWLEDGE_STORE_BACKEND=postgres` can retrieve that memory as an `advertiser_memory` citation. If `OUTBOX_BACKEND=none`, the service keeps the simpler synchronous memory-write fallback for local demos.

Memory retrieval usage tracking is also asynchronous. Set `MEMORY_USAGE_TRACKING_BACKEND=outbox` together with `KNOWLEDGE_STORE_BACKEND=postgres` and `OUTBOX_BACKEND=postgres` to enqueue `advertiser_memory_retrieved` events whenever Postgres RAG cites advertiser memory. `ads-growth-agent process-outbox` updates `advertiser_memories.last_used_at` and `usage_count` outside the retrieval path.

When persistence is enabled, event ingestion is idempotent by `event_id`: replaying the same normalized payload returns the already persisted analysis with `Performance-Event-Status: replayed`; reusing the same `event_id` with different metrics or metadata returns HTTP `409` with `PERFORMANCE_EVENT_ID_CONFLICT`.

Persisted performance events can be queried for audit and replay:

```bash
curl http://localhost:8000/campaign-events/performance/evt_perf_001 \
  -H "X-Tenant-ID: tenant_demo"
```

Campaign draft persistence is separately opt-in. Set `CAMPAIGN_DRAFT_PERSISTENCE_BACKEND=postgres` to store the `create_campaign_draft` tool output in `campaign_drafts`:

```bash
CAMPAIGN_DRAFT_PERSISTENCE_BACKEND=postgres ads-growth-agent plan examples/fitness_app_brief.json
```

Persisted drafts keep `status=draft`, store the final strategy JSON for explainability, and include metadata such as campaign name, daily budget, audience segments, creative angles, and the safety note. No live campaign launch or spend mutation is performed.

API idempotency is opt-in for production-style duplicate request protection. Set `IDEMPOTENCY_BACKEND=postgres` and send an `Idempotency-Key` header:

```bash
IDEMPOTENCY_BACKEND=postgres RUN_PERSISTENCE_BACKEND=postgres \
  curl -X POST http://localhost:8000/growth-strategies \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-fitness-001" \
  -H "X-Tenant-ID: tenant_demo" \
  -d '{"brief":{"advertiser_id":"adv_fitness_001","product_name":"FitTrack Pro","product_category":"fitness app","objective":"registrations","budget":"2000.00","currency":"USD","duration_days":14,"target_market":"United States","primary_kpi":"trial registrations"}}'
```

The first request stores an `idempotency_keys` row as `completed`; a repeated request with the same key and identical body replays the saved response; a repeated request with the same key and different body returns HTTP `409`. `RUN_PERSISTENCE_BACKEND=postgres` is recommended with idempotency so the idempotency record can link to `agent_runs.run_id`. API callers can override the process-level `TENANT_ID` per request with `X-Tenant-ID`; the effective tenant is returned in the response header and is used by idempotency, run persistence, draft persistence, RAG/memory stores, and LangGraph checkpoint thread IDs.

LangGraph checkpointing is also configurable. The default `GRAPH_CHECKPOINTER_BACKEND=none` keeps local demos simple. Use `memory` for local debugging, or `postgres` for durable LangGraph checkpoints:

```bash
GRAPH_CHECKPOINTER_BACKEND=postgres ads-growth-agent plan examples/fitness_app_brief.json
```

The Postgres backend uses the official `langgraph-checkpoint-postgres` `PostgresSaver` and creates LangGraph-owned tables such as `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` when `GRAPH_CHECKPOINTER_SETUP=true`. These tables are separate from the application-owned Alembic schema. Checkpoint `thread_id` values are namespaced as `<tenant_id>:<run_id>` so workflow executions do not collide across tenants.

The LLM gateway foundation targets LiteLLM's OpenAI-compatible API and supports:

- native JSON schema structured output requests
- JSON-schema prompt fallback
- Pydantic validation
- bounded repair retry
- safe failure when validation cannot be repaired

This gateway is covered by offline `httpx.MockTransport` tests. The planner node can now be switched from deterministic planning to LiteLLM-backed structured planning:

```bash
USE_LLM_PLANNER=true ads-growth-agent plan examples/fitness_app_brief.json
```

When enabled, the LLM may propose only the initial draft-safe tool intents. The platform still validates the returned `ToolIntent` plan, rejects unknown or missing tools, and executes through the internal typed tool registry. Invalid planner output returns a structured safe failure before any tool action runs.

The critic node can also be switched to LiteLLM-backed structured critique:

```bash
USE_LLM_CRITIC=true ads-growth-agent plan examples/fitness_app_brief.json
```

The LLM critic returns a validated `CritiqueReport`. The workflow finalizes only when the critique passes and meets `LLM_CRITIC_MIN_SCORE`. If the critic returns a valid rejection, the graph can route through a bounded self-reflection step:

```bash
USE_LLM_CRITIC=true MAX_REVISION_ATTEMPTS=1 ads-growth-agent plan examples/fitness_app_brief.json
```

That route appears explicitly in `node_path` as `critic -> revision -> critic`. The revision node records critic issues and required revisions in graph state before the second critique. If the strategy is still rejected after the configured attempt limit, the workflow records a structured `llm_critic` safe failure before finalization.

API and CLI runs emit structured JSON logs to stderr. CLI command payloads remain on stdout, while logs include summary fields such as:

- `event`
- `run_id`
- `execution_id`
- `strategy_id`
- `trace_id`
- `advertiser_id`
- `node_path`
- `tool_count`
- `failed_tool_count`
- `suite_id`
- `pass_rate`

Check the repository-defined services:

```bash
docker compose ps
```

Stop the stack:

```bash
docker compose down
```

Remove local Postgres data when you want a clean database:

```bash
docker compose down -v
```

## Repository Principles

- The model proposes structured intent; the platform validates and executes.
- Tool execution goes through typed internal registries, not raw model authority.
- Recommendations must be grounded in retrieved sources, tool outputs, or explicit assumptions.
- v0.1 creates drafts and recommendations only; it does not launch live campaigns or change spend.

## Quality Gates

GitHub Actions runs separate checks for lint, unit tests, deterministic product
smoke tests, live PostgreSQL integration tests, and release-readiness checks.
The Postgres job uses `pgvector/pgvector:pg16` and runs with
`RUN_POSTGRES_INTEGRATION=1`.

Branch, PR, release, and changelog expectations are documented in
[CONTRIBUTING.md](./CONTRIBUTING.md). Meaningful release notes live in
[CHANGELOG.md](./CHANGELOG.md).
