# LLM Planner Feature Flag

## Goal

Add an opt-in LLM planner path to the LangGraph workflow. By default the workflow remains deterministic and offline-safe. When `USE_LLM_PLANNER=true`, the planner node calls the LiteLLM gateway for structured `ToolIntent` planning, validates the output, and only then allows the typed tool registry to execute draft-only actions.

## Context

- Relevant files:
  - `src/ads_growth_agent/graph.py` contains the deterministic LangGraph workflow and planner node.
  - `src/ads_growth_agent/llm.py` contains LiteLLM gateway client and structured output fallback/repair.
  - `src/ads_growth_agent/config.py` contains runtime settings.
  - `tests/test_graph_workflow.py` covers LangGraph workflow behavior.
  - `.env.example` and `README.md` document local runtime flags.
- Current behavior:
  - Planner is deterministic and always emits three initial tool intents: `recommend_audience`, `generate_creative_brief`, and `optimize_budget`.
  - Tool executor assumes these three intents are present and executes dependent tools afterward.
  - LLM gateway exists but is not wired into the graph.
- Constraints:
  - Do not require Docker for this slice.
  - Do not make real ad platform mutations.
  - Keep default behavior deterministic for local tests and CLI demos.
  - LLM output must be validated before any tool action executes.

## Plan

- [x] Add settings and documentation for an opt-in LLM planner mode.
- [x] Add a structured planner output model and prompt construction.
- [x] Wire the graph planner node to choose deterministic or LLM planning by feature flag.
- [x] Harden tool execution against invalid/missing LLM tool plans.
- [x] Add tests for default deterministic mode, LLM planner success, invalid plan safe failure, and gateway failure safe failure.
- [x] Run lint, tests, compile checks.
- [x] Commit and push verified changes.

## Decisions

- Decision: Keep `USE_LLM_PLANNER=false` by default.
  Reason: The product demo must remain stable without a running LiteLLM proxy or model credentials.
- Decision: Treat LLM planner output as structured intent, not executable authority.
  Reason: The internal typed tool registry and Pydantic validation remain the trusted system boundary.

## Discoveries

- Discovery: `run_growth_strategy_graph` already accepts optional settings, but graph compilation does not yet pass settings into nodes.
  Evidence: `src/ads_growth_agent/graph.py` currently calls `build_growth_strategy_graph(registry or build_default_tool_registry())`.
- Discovery: The tool executor currently relies on list position for the first three planner intents.
  Evidence: It reads `tool_intents[0]`, `[1]`, and `[2]`, which needs hardening before accepting LLM-generated order.
- Discovery: Settings now include `use_llm_planner` and `llm_structured_output_max_repair_attempts`.
  Evidence: `src/ads_growth_agent/config.py` and `.env.example` define the new env-driven controls.
- Discovery: LLM planner output is intentionally not counted as a successful tool run.
  Evidence: `src/ads_growth_agent/graph.py` stores successful planner rationale in state artifacts, while keeping `tool_results` for executable platform tools.
- Discovery: The graph workflow tests pass with both deterministic and LLM planner modes.
  Evidence: `.venv/bin/python -m pytest tests/test_graph_workflow.py` reported 9 passed.
- Discovery: Full verification passes after lint fixes.
  Evidence: Full pytest reports 40 passed; ruff reports all checks passed; compileall compiles `src` and `tests`.
- Discovery: Git write access is available again in the next turn.
  Evidence: `git add` succeeded with approved escalation after the earlier environment limit cleared.

## Verification

- [x] `.venv/bin/python -m pytest`
  Result: 40 passed.
- [x] `.venv/bin/python -m ruff check .`
  Result: All checks passed.
- [x] `.venv/bin/python -m compileall src tests`
  Result: Completed successfully.

## Final Status

Complete. The workflow now supports an opt-in LiteLLM-backed planner while preserving deterministic default behavior. The implementation is verified and ready in git.
