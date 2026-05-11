# Self-Reflection Revision Loop

## Goal

Add a bounded self-reflection loop to the LangGraph workflow. When a valid critic report rejects a strategy, the graph should route through a revision node, record the critique feedback as structured revision context, and run the critic again. The default deterministic workflow should remain unchanged, and unrecoverable model/gateway failures should still return structured safe failures.

## Context

- Relevant files:
  - `src/ads_growth_agent/graph.py` contains the LangGraph workflow, critic node, finalizer, and failure handling.
  - `src/ads_growth_agent/config.py` contains feature flags and runtime settings.
  - `tests/test_graph_workflow.py` covers graph paths for deterministic, LLM planner, and LLM critic behavior.
  - `.env.example` and `README.md` document runtime controls.
- Current behavior:
  - `critic` routes directly to `finalizer`.
  - If the LLM critic returns a valid but failing `CritiqueReport`, `_llm_critic_node` raises `StrategyGenerationError`.
  - If the LLM critic returns invalid structured output or the gateway fails, the workflow safe-fails.
- Constraints:
  - Keep default local workflow deterministic and offline-safe.
  - Limit revision attempts to a small configurable bound, defaulting to one attempt.
  - Keep revision as explicit graph state and node path, not hidden retry logic.
  - Do not call live ad platform mutation tools.

## Plan

- [x] Add settings and docs for bounded revision attempts.
- [x] Add revision state fields and conditional routing after critic.
- [x] Convert valid critic rejection into revision routing when attempts remain.
- [x] Add a revision node that records critique issues and required revisions in artifacts.
- [x] Preserve structured safe failure when max revision attempts are exhausted or the critic gateway/schema fails.
- [x] Add tests for successful revision loop and exhausted revision failure.
- [x] Run lint, tests, compile checks, then commit and push.

## Decisions

- Decision: Use an explicit LangGraph `revision` node.
  Reason: Node path and trace output should show self-reflection as a real orchestration step.
- Decision: Keep `MAX_REVISION_ATTEMPTS=1` by default.
  Reason: v0.1 needs to demonstrate the control loop without risking unbounded agent behavior.
- Decision: Do not re-run external tools in the revision node in this slice.
  Reason: The first revision capability should be low-risk and state-only; future work can add targeted re-planning or tool re-execution.

## Discoveries

- Discovery: The current graph has a direct `critic -> finalizer` edge.
  Evidence: `build_growth_strategy_graph` adds `builder.add_edge("critic", "finalizer")`.
- Discovery: Valid LLM critic rejection currently raises immediately.
  Evidence: `_llm_critic_node` raises `StrategyGenerationError` when `not critique.passed` or score is below the configured minimum.
- Discovery: Settings now include `max_revision_attempts`.
  Evidence: `src/ads_growth_agent/config.py` and `.env.example` define the new env-driven bound.
- Discovery: The graph now has an explicit revision route.
  Evidence: `build_growth_strategy_graph` uses `add_conditional_edges("critic", _route_after_critic, ...)` and adds `revision -> critic`.
- Discovery: Revision loop tests pass at the workflow level.
  Evidence: `.venv/bin/python -m pytest tests/test_graph_workflow.py` reported 13 passed.
- Discovery: Full verification passes after adding the revision loop.
  Evidence: Full pytest reports 44 passed; ruff reports all checks passed; compileall completes successfully.

## Verification

- [x] `.venv/bin/python -m pytest`
  Result: 44 passed.
- [x] `.venv/bin/python -m ruff check .`
  Result: All checks passed.
- [x] `.venv/bin/python -m compileall src tests`
  Result: Completed successfully.

## Final Status

Complete. The graph now has an explicit bounded self-reflection route from critic rejection through revision and back to critic, with verification passing.
