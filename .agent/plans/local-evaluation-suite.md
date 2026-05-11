# Local Evaluation Suite

## Goal

Add a deterministic local evaluation suite for the agent platform. When complete, developers should be able to run curated advertiser briefs through the LangGraph workflow and receive structured evaluation reports for budget consistency, tool use correctness, strategy completeness, draft-only safety, and observability metadata.

## Context

- Relevant files:
  - `RFC-Autonomous-Ads-Growth-Agent-Platform-v0.1.md`
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/strategy.py`
  - `src/ads_growth_agent/cli.py`
  - `examples/fitness_app_brief.json`
  - `tests/`
- Current behavior:
  - The graph workflow returns a validated `GrowthStrategyResponse` with `strategy`, `tool_results`, `node_path`, and `run_metadata`.
  - There is no evaluator module, eval dataset, eval CLI command, or aggregate pass-rate output yet.
- Constraints:
  - Do not require Docker, Postgres, LiteLLM, LangSmith API keys, or network access.
  - Keep evaluators deterministic and programmatic.
  - Avoid LLM-as-judge in this slice; that can be added after the local evaluator contract is stable.
  - Follow the ExecPlan process from `/Users/learningmachine/Documents/New project/.agent/PLANS.md`.

## Plan

- [x] Create this ExecPlan and record scope.
- [x] Add Pydantic evaluation contracts and deterministic evaluator functions.
- [x] Add curated local eval cases for multiple advertiser categories.
- [x] Add a CLI command for running the local evaluation suite.
- [x] Add tests for passing cases and evaluator failure cases.
- [x] Update README with eval usage.
- [x] Run verification commands and record results.
- [x] Commit and push the completed slice.

## Decisions

- Decision: Start with deterministic evaluators instead of LLM-as-judge.
  Reason: Budget math, tool coverage, safety, and observability can be scored programmatically and should not depend on model variability.

- Decision: Store the first eval dataset in `examples/eval_cases.json`.
  Reason: The cases are small, human-readable, and useful for CLI demos before a database-backed or LangSmith-hosted dataset exists.

- Decision: Treat local evaluation as a contract for future LangSmith dataset replay.
  Reason: The same `EvaluationReport` shape can later be uploaded or compared through LangSmith without changing core evaluator semantics.

## Discoveries

- Discovery: The RFC already defines the evaluator dimensions for v0.1.
  Evidence: RFC section 14.1 lists plan quality, tool use correctness, budget correctness, safety, regression, and trace/eval behavior.

- Discovery: The current deterministic graph can pass a three-case local eval dataset.
  Evidence: `.venv/bin/ads-growth-agent eval examples/eval_cases.json` returned `total_cases=3`, `passed_cases=3`, and `pass_rate=1.0`.

- Discovery: The local evaluator failure path catches budget-contract regressions.
  Evidence: `tests/test_evaluation.py` lowers the eval case budget below the generated strategy budget and verifies `budget_consistency` fails.

## Verification

- [x] Command or check: `.venv/bin/python -m pytest`
  Result: Passed, 26 tests, with one third-party LangGraph/LangChain pending deprecation warning.

- [x] Command or check: `.venv/bin/python -m ruff check .`
  Result: Passed.

- [x] Command or check: `.venv/bin/ads-growth-agent eval examples/eval_cases.json`
  Result: Returned `3 3 1.0` for total cases, passed cases, and pass rate.

## Final Status

Completed. The project now has a deterministic local evaluation suite, three curated eval cases, an `ads-growth-agent eval` CLI command, and tests covering passing reports plus a budget regression failure case. The next slice should connect these evaluator contracts to LangSmith dataset upload/replay or add structured JSON logs for evaluation summaries.
