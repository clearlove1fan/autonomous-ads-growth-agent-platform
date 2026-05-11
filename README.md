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

Set `OPENAI_API_KEY` in `.env` before calling LiteLLM-backed model endpoints. The current API health endpoint does not require a model key.

Start the stack:

```bash
docker compose up --build
```

Check the API:

```bash
curl http://localhost:8000/health
```

Generate a draft growth strategy:

```bash
curl -X POST http://localhost:8000/growth-strategies \
  -H "Content-Type: application/json" \
  -d '{"brief":{"advertiser_id":"adv_fitness_001","product_name":"FitTrack Pro","product_category":"fitness app","objective":"registrations","budget":"2000.00","currency":"USD","duration_days":14,"target_market":"United States","primary_kpi":"trial registrations","target_cpa":"20.00"}}'
```

The current deterministic workflow runs through explicit LangGraph nodes:

```text
planner -> retriever -> tool_executor -> critic -> finalizer
```

Responses include `run_metadata` for local observability and LangSmith correlation:

- `run_id`
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
- retrieval grounding
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

Run audit persistence is also opt-in. Set `RUN_PERSISTENCE_BACKEND=postgres` to write completed or failed strategy runs into `agent_runs` and derived node records into `agent_run_steps`:

```bash
RUN_PERSISTENCE_BACKEND=postgres ads-growth-agent plan examples/fitness_app_brief.json
```

The current v0.1 `run_id` is deterministic for a given advertiser brief, so repeated identical runs update the same `agent_runs` row and replace its derived step rows. This is deliberate for local idempotent demos; later production work can introduce per-execution run IDs and replay tooling.

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
