# LangGraph Deterministic Workflow

## Goal

Connect the existing domain contracts and typed tool registry to a first LangGraph `StateGraph` workflow. When complete, FastAPI and CLI strategy generation should run through explicit graph nodes for planning, tool execution, critique, and finalization while keeping v0.1 deterministic and safe.

## Context

- Relevant files:
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/tools.py`
  - `src/ads_growth_agent/strategy.py`
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/cli.py`
  - `tests/test_strategy_api_cli.py`
  - `pyproject.toml`
- Current behavior:
  - `generate_mock_growth_strategy()` directly executes typed mock tools and builds a validated `FinalGrowthStrategy`.
  - API `POST /growth-strategies` and CLI `plan` call the direct deterministic strategy service.
  - There is no LangGraph workflow yet.
- Constraints:
  - Keep this slice deterministic and testable without live LLM, LangSmith, Postgres, Docker, or external API calls.
  - Preserve the typed tool registry boundary: graph nodes may create `ToolIntent`, but tools are executed only through `ToolRegistry`.
  - Do not launch real campaigns or mutate spend.
  - Follow the ExecPlan process from `/Users/learningmachine/Documents/New project/.agent/PLANS.md`.

## Plan

- [x] Create this ExecPlan and record scope.
- [x] Define a `TypedDict` graph state for advertiser brief, tool intents, tool results, draft artifacts, critique, final strategy, and errors.
- [x] Implement a deterministic `StateGraph` with planner, tool executor, critic, and finalizer nodes.
- [x] Route API and CLI strategy generation through the graph runner.
- [x] Add workflow tests that verify node path, tool results, final strategy validation, and safe error handling.
- [x] Run verification commands and record results.
- [x] Commit and push the completed slice.

## Decisions

- Decision: Use deterministic graph nodes before adding LLM calls.
  Reason: This proves orchestration, state shape, tool safety, and final validation before introducing probabilistic model output.

- Decision: Keep the graph in-process for v0.1.
  Reason: The project does not need a separate graph server until we add durable checkpointing and background execution.

- Decision: Keep checkpointing out of this slice.
  Reason: Postgres-backed checkpointing should be added after the graph state and workflow semantics are stable.

## Discoveries

- Discovery: LangGraph imports successfully in the project virtualenv.
  Evidence: `.venv/bin/python` imported `StateGraph`, `START`, and `END`. The import emitted a LangGraph/LangChain pending deprecation warning from installed dependencies, but no runtime failure.

- Discovery: The in-process LangGraph workflow returns the expected deterministic node path.
  Evidence: `.venv/bin/ads-growth-agent plan examples/fitness_app_brief.json` returned `["planner", "tool_executor", "critic", "finalizer"]` and five tool results.

- Discovery: The current LangGraph dependency emits a pending deprecation warning during import.
  Evidence: `pytest` reports `LangChainPendingDeprecationWarning` from `langgraph/cache/base/__init__.py`; tests still pass and the warning is outside project code.

## Verification

- [x] Command or check: `.venv/bin/python -m pytest`
  Result: Passed, 21 tests, with one third-party LangGraph/LangChain pending deprecation warning.

- [x] Command or check: `.venv/bin/python -m ruff check .`
  Result: Passed.

- [x] Command or check: `env PYTHONPYCACHEPREFIX=/private/tmp/ads-growth-agent-pycache .venv/bin/python -m compileall src tests`
  Result: Source and tests compiled successfully.

- [x] Command or check: `.venv/bin/ads-growth-agent plan examples/fitness_app_brief.json`
  Result: Returned node path `planner -> tool_executor -> critic -> finalizer` and five successful tool results.

## Final Status

Completed. The strategy path now runs through an explicit LangGraph `StateGraph` with planner, tool executor, critic, and finalizer nodes. API and CLI still use the same strategy service entrypoint, but the returned response now includes the graph node path for traceability. The next slice should add LangSmith tracing metadata and start shaping evaluation datasets.
