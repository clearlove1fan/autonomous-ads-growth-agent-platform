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
ads-growth-agent eval examples/eval_cases.json
```

Run tests:

```bash
pytest
```

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
planner -> tool_executor -> critic -> finalizer
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
- draft-only safety
- observability metadata

The LLM gateway foundation targets LiteLLM's OpenAI-compatible API and supports:

- native JSON schema structured output requests
- JSON-schema prompt fallback
- Pydantic validation
- bounded repair retry
- safe failure when validation cannot be repaired

This gateway is currently covered by offline `httpx.MockTransport` tests. Live model-backed planner and critic nodes are planned after this boundary remains stable.

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
