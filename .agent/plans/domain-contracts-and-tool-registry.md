# Domain Contracts and Tool Registry

## Goal

Create the first product-level contract layer for the Autonomous Ads Growth Agent Platform. When complete, the project should validate advertiser briefs, represent strategy/tool contracts with Pydantic models, execute mock ads tools only through an internal typed registry, and expose a deterministic API/CLI path that returns a structured growth strategy without launching real campaigns.

## Context

- Relevant files:
  - `RFC-Autonomous-Ads-Growth-Agent-Platform-v0.1.md`
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/cli.py`
  - `src/ads_growth_agent/config.py`
  - `tests/test_health.py`
  - `pyproject.toml`
- Current behavior:
  - The repo has a FastAPI `/health` endpoint, CLI health command, Docker Compose stack, LiteLLM config, and HLD/RFC.
  - There are no domain schemas, tool registry, mock ads tools, product strategy endpoint, or strategy tests yet.
- Constraints:
  - Do not execute real ad platform actions in v0.1.
  - Keep this slice deterministic and testable without live LLM, LangSmith, Postgres, or Docker.
  - Model/tool execution must follow the HLD principle: the LLM may propose structured intent later, but the platform validates and executes tools.
  - Follow the ExecPlan process from `/Users/learningmachine/Documents/New project/.agent/PLANS.md`.

## Plan

- [x] Create this ExecPlan and record scope.
- [x] Implement Pydantic v2 domain contracts for advertiser brief, tasks, tool intents/results, critique, and final strategy.
- [x] Implement budget correctness validation.
- [x] Implement an internal typed tool registry and deterministic mock ads tools.
- [x] Expose a first API and CLI product path for generating a mock growth strategy.
- [x] Add schema, budget, registry, API, and CLI tests.
- [x] Run verification commands and record results.
- [ ] Commit and push the completed slice.

## Decisions

- Decision: Start with deterministic mock planning instead of live LLM calls.
  Reason: The project needs stable contracts and safety boundaries before model behavior is introduced.

- Decision: Keep LangGraph integration out of this slice.
  Reason: LangGraph nodes should consume already-stable schemas and tool registry APIs; this keeps the next graph slice cleaner and easier to test.

- Decision: Keep all mock tools non-mutating draft/recommendation tools.
  Reason: v0.1 must not perform live ad launch, spend changes, or irreversible external actions.

- Decision: Add `POST /growth-strategies` with a `GrowthStrategyRequest` wrapper around `AdvertiserBrief`.
  Reason: The request wrapper leaves room for future run options, trace metadata, and evaluation flags without breaking the brief contract.

- Decision: Allow the CLI `plan` command to accept either a raw brief JSON object or `{"brief": ...}`.
  Reason: Raw brief files are easier for examples, while the wrapped shape matches the API request contract.

## Discoveries

- Discovery: The current codebase only contains health surfaces and runtime infrastructure.
  Evidence: `src/ads_growth_agent/api.py` and `src/ads_growth_agent/cli.py` expose health/version behavior only.

- Discovery: The RFC already defines the target contract names and typed tool registry direction.
  Evidence: RFC section 10.2 lists `AdvertiserBrief`, `AgentTask`, `ToolResult`, `CritiqueReport`, and `FinalGrowthStrategy`; section 11 selects Pydantic v2 and internal typed tool registry.

- Discovery: The first test run passed behavior but failed lint on Python idioms and formatting.
  Evidence: `pytest` passed 17 tests, while `ruff` required `StrEnum`, a module-level Typer argument default, import sorting, and long-line fixes.

- Discovery: Final Docker status re-check was not available during this slice because elevated command execution hit the Codex usage limit.
  Evidence: `docker compose ps --all` approval was rejected by the environment. This does not block the slice because the work is deterministic local Python code and Docker services were previously stopped.

## Verification

- [x] Command or check: `.venv/bin/python -m pytest`
  Result: Passed, 17 tests.

- [x] Command or check: `.venv/bin/python -m ruff check .`
  Result: Passed after fixing lint issues.

- [x] Command or check: `.venv/bin/ads-growth-agent plan examples/fitness_app_brief.json`
  Result: Returned structured JSON with `FinalGrowthStrategy`, five successful tool results, budget allocations totaling `2000.00`, and draft-only campaign action metadata.

- [x] Command or check: `env PYTHONPYCACHEPREFIX=/private/tmp/ads-growth-agent-pycache .venv/bin/python -m compileall src tests`
  Result: Source and tests compiled successfully.

## Final Status

Completed. The domain contracts, typed tool registry, deterministic mock tools, API/CLI strategy path, example brief, and focused tests are implemented and verified. The next implementation slice should connect these contracts to a LangGraph StateGraph workflow.
