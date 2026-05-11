# LangSmith Run Metadata

## Goal

Add the first observability layer for graph runs. When complete, each growth strategy response should include structured run metadata with run ID, trace ID, LangSmith project, tracing flag, graph node path, tool summary, and error summary. The graph execution should be wrapped in LangSmith tracing context when enabled, while remaining deterministic and testable with tracing disabled.

## Context

- Relevant files:
  - `src/ads_growth_agent/config.py`
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/graph.py`
  - `src/ads_growth_agent/strategy.py`
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/cli.py`
  - `tests/test_graph_workflow.py`
  - `tests/test_strategy_api_cli.py`
- Current behavior:
  - Strategy generation runs through LangGraph nodes: `planner -> tool_executor -> critic -> finalizer`.
  - The response includes `strategy`, `tool_results`, and `node_path`.
  - LangSmith settings exist in config, but graph runs are not wrapped in tracing context and no run metadata is returned.
- Constraints:
  - Docker is not required for this slice because no Postgres, LiteLLM, or containerized API validation is needed.
  - Keep tests offline and deterministic with `LANGSMITH_TRACING=false`.
  - Do not require a LangSmith API key for local unit tests.
  - Do not log or expose provider credentials.

## Plan

- [x] Create this ExecPlan and record scope.
- [x] Add Pydantic run metadata contracts.
- [x] Add an observability helper that creates run IDs, trace IDs, and LangSmith tracing context.
- [x] Wrap graph execution with the observability helper.
- [x] Return run metadata through API and CLI responses.
- [x] Add tests for tracing-disabled metadata, node path, tool summary, and failure metadata.
- [x] Run verification commands and record results.
- [ ] Commit and push the completed slice.

## Decisions

- Decision: Keep Docker stopped for this slice.
  Reason: This is pure Python observability wiring around in-process LangGraph execution.

- Decision: Generate a local trace ID even when LangSmith upload is disabled.
  Reason: Responses and logs need a stable correlation field during local development and tests; LangSmith upload can be enabled later without changing response shape.

- Decision: Use LangSmith `tracing_context` and `traceable` rather than direct client calls.
  Reason: This keeps tracing optional and avoids network/API-key requirements in local unit tests.

## Discoveries

- Discovery: The installed LangSmith package exposes `traceable` and `tracing_context`.
  Evidence: `.venv/bin/python` reported LangSmith `0.8.3`, `has traceable True`, and `has tracing_context True`.

- Discovery: `traceable` supports `process_inputs` and `process_outputs`.
  Evidence: Local source inspection showed the decorator accepts custom serialization hooks. The graph trace wrapper uses those hooks so LangSmith receives sanitized graph metadata instead of a compiled graph object.

- Discovery: Run metadata can be produced without Docker or a LangSmith API key.
  Evidence: With default `LANGSMITH_TRACING=false`, tests and CLI output produced `run_metadata` containing a local `trace_id`, graph node path, and tool counts.

## Verification

- [x] Command or check: `.venv/bin/python -m pytest`
  Result: Passed, 21 tests, with one third-party LangGraph/LangChain pending deprecation warning.

- [x] Command or check: `.venv/bin/python -m ruff check .`
  Result: Passed.

- [x] Command or check: `.venv/bin/ads-growth-agent plan examples/fitness_app_brief.json`
  Result: Returned `run_metadata` with deterministic `run_id`, local `trace_id`, and `tool_count` of 5.

## Final Status

Implementation complete. Awaiting commit and push.
