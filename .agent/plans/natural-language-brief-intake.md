# Natural Language Brief Intake

## Goal

Let a single advertiser start from a plain-language goal instead of hand-writing
strict `AdvertiserBrief` JSON. The system should parse the text into a typed
brief, expose the parsed assumptions, and optionally generate the full growth
strategy from that text in one API/CLI call.

## Context

- Relevant files:
  - `src/ads_growth_agent/contracts.py`
  - `src/ads_growth_agent/config.py`
  - `src/ads_growth_agent/brief_intake.py`
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/cli.py`
  - `src/ads_growth_agent/health.py`
  - `tests/test_strategy_api_cli.py`
  - `tests/test_health.py`
  - `README.md`
- Current behavior:
  - `POST /growth-strategies` and `ads-growth-agent plan` require a fully
    structured JSON advertiser brief.
  - LiteLLM gateway and structured output helpers already exist.
  - LLM planner/critic are feature-flagged, but the first user-facing intake
    step is still too developer-oriented.
- Constraints:
  - Default local and CI behavior must not require external model calls.
  - The natural-language path must still produce a strict `AdvertiserBrief`
    before strategy generation.
  - When LLM intake is enabled, failed structured extraction should fall back to
    local heuristic intake when enough information can be inferred.

## Plan

- [x] Create this ExecPlan and define the product-facing intake slice.
- [x] Add contracts for text intake request/response and strategy-from-text.
- [x] Add heuristic and optional LiteLLM-backed brief extraction.
- [x] Add API routes for parsing text and generating strategy from text.
- [x] Add CLI commands for parsing text and planning from text.
- [x] Update readiness logic for optional LLM intake.
- [x] Add unit tests for heuristic, LLM, API, CLI, and readiness behavior.
- [x] Update README with the new user-facing path.
- [~] Run verification, commit, push, and watch CI.

## Decisions

- Decision: Default to heuristic intake and make LLM extraction opt-in through
  `USE_LLM_BRIEF_INTAKE=true`.
  Reason: One-person local usage and CI should work without external API keys,
  while production-style demos can use LiteLLM.
- Decision: Add both parse-only and generate-from-text endpoints.
  Reason: Parse-only lets the user inspect/correct inferred brief fields;
  generate-from-text provides the shortest path to a usable strategy.

## Discoveries

- Discovery: The repository already had LiteLLM gateway, structured output,
  and feature-flagged LLM planner/critic support.
  Evidence: `src/ads_growth_agent/graph.py` includes `use_llm_planner`,
  `use_llm_critic`, and `generate_structured_output` integration.
- Discovery: Budget extraction must prioritize explicit currency syntax before
  generic "budget" syntax.
  Evidence: The first targeted test run parsed `$2000 budget ... 14 days` as
  `14.00`; the parser now checks `$2000`, `2000 USD`, and `2000 美元` before
  `budget: 2000`.

## Verification

- [x] Targeted tests:
  `.venv/bin/python -m pytest tests/test_brief_intake.py tests/test_strategy_api_cli.py tests/test_health.py tests/e2e/test_product_smoke.py`
  Result: 41 passed.
- [x] Full unit suite: `.venv/bin/python -m pytest`
  Result: 164 passed, 18 skipped.
- [x] Ruff: `.venv/bin/ruff check .`
  Result: All checks passed.
- [ ] CI:

## Final Status

Implementation complete locally. Plain-language brief intake is available
through API and CLI, with deterministic heuristic parsing by default and
LiteLLM-backed structured extraction behind `USE_LLM_BRIEF_INTAKE=true`. Commit,
push, and CI watch remain.
