# LLM Critic Feature Flag

## Goal

Add an opt-in LLM critic path to the LangGraph workflow. By default the deterministic critic remains active and all local tests/evals stay offline-safe. When `USE_LLM_CRITIC=true`, the critic node asks the LiteLLM gateway for a structured `CritiqueReport`, validates it with Pydantic, enforces a minimum score/pass gate, and records safe structured failure if the model cannot produce a valid critique.

## Context

- Relevant files:
  - `src/ads_growth_agent/graph.py` contains the critic node and finalizer.
  - `src/ads_growth_agent/llm.py` contains structured output generation with native JSON schema, JSON prompt fallback, and repair.
  - `src/ads_growth_agent/config.py` contains feature flags and model settings.
  - `tests/test_graph_workflow.py` already covers deterministic and LLM planner graph behavior.
  - `README.md` and `.env.example` document feature flags.
- Current behavior:
  - The critic is deterministic and always returns a passing `CritiqueReport` with score `8.1`.
  - Local evaluation expects `strategy.critique.passed` to be true.
  - LLM planner is already opt-in via `USE_LLM_PLANNER`.
- Constraints:
  - Do not require Docker or live model credentials for default tests.
  - Keep the critic output strictly Pydantic-validated.
  - Do not execute any real ad spend or live campaign mutations.
  - Treat low-score or failed critiques as safe workflow failures for this slice rather than auto-launching a revision loop.

## Plan

- [x] Add settings and documentation for an opt-in LLM critic mode.
- [x] Add critic prompt construction using tool outputs and draft strategy artifacts.
- [x] Wire the graph critic node to choose deterministic or LLM critique by feature flag.
- [x] Enforce a minimum critic score/pass gate before finalization.
- [x] Add tests for default deterministic mode, LLM critic success, failed critique safe failure, and gateway failure safe failure.
- [x] Run lint, tests, compile checks.
- [x] Commit and push verified changes.

## Decisions

- Decision: Keep `USE_LLM_CRITIC=false` by default.
  Reason: The local product demo and eval suite should not depend on a live model.
- Decision: Gate finalization on `CritiqueReport.passed` and a configurable minimum score.
  Reason: The critic is meant to be a quality gate, not decorative commentary.
- Decision: Defer automatic revision loops to a later slice.
  Reason: The current graph does not yet have a meaningful replanning/revision node; adding the critic as a validated gate first creates a safer foundation.

## Discoveries

- Discovery: Local evaluation already checks `strategy.critique.passed`.
  Evidence: `evaluate_strategy_completeness` includes `critique_passed` in `src/ads_growth_agent/evaluation.py`.
- Discovery: Settings now include `use_llm_critic` and `llm_critic_min_score`.
  Evidence: `src/ads_growth_agent/config.py` and `.env.example` define the new env-driven controls.
- Discovery: Successful LLM critic output remains state metadata, not an executable tool result.
  Evidence: `_llm_critic_node` stores structured output attempts under `artifacts["critic"]`.
- Discovery: Failed LLM critic output is represented as a structured `llm_critic` failure.
  Evidence: `_critic_failure_tool_result` appends a failed `ToolResult` before raising `StrategyGenerationError`.
- Discovery: Graph workflow tests pass for deterministic, LLM planner, and LLM critic paths.
  Evidence: `.venv/bin/python -m pytest tests/test_graph_workflow.py` reported 12 passed.
- Discovery: Full verification passes after the LLM critic integration.
  Evidence: Full pytest reports 43 passed; ruff reports all checks passed; compileall completes successfully.

## Verification

- [x] `.venv/bin/python -m pytest`
  Result: 43 passed.
- [x] `.venv/bin/python -m ruff check .`
  Result: All checks passed.
- [x] `.venv/bin/python -m compileall src tests`
  Result: Completed successfully.

## Final Status

Implementation and verification are complete. The working tree is ready to commit and push.
Changes were committed and pushed in `6dba74f add llm critic feature flag`.
