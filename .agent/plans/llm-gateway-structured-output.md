# LLM Gateway Structured Output

## Goal

Add an offline-testable LLM gateway foundation for LiteLLM-backed structured output. When complete, the project should have a typed OpenAI-compatible chat client, native structured output request support, JSON prompt fallback, Pydantic validation, bounded repair retry, and safe failure behavior without requiring Docker or real model credentials in tests.

## Context

- Relevant files:
  - `RFC-Autonomous-Ads-Growth-Agent-Platform-v0.1.md`
  - `src/ads_growth_agent/config.py`
  - `config/litellm/config.yaml`
  - `.env.example`
  - `pyproject.toml`
  - `tests/`
- Current behavior:
  - Docker Compose can run LiteLLM Proxy, but application code does not yet have a model gateway client.
  - Planner, critic, and finalizer are deterministic.
  - Structured output fallback and repair behavior exists only in the HLD/RFC.
- Constraints:
  - Do not require Docker, LiteLLM, OpenAI, LangSmith API keys, or network access for tests.
  - Keep real model use behind a small client abstraction that can be tested with `httpx.MockTransport`.
  - Do not integrate live LLM behavior into the graph in this slice; first stabilize the gateway and structured output boundary.
  - Follow the ExecPlan process from `/Users/learningmachine/Documents/New project/.agent/PLANS.md`.

## Plan

- [x] Create this ExecPlan and record scope.
- [x] Add LLM message/request/result contracts.
- [x] Implement a LiteLLM/OpenAI-compatible model gateway client with timeout, auth, and model config.
- [x] Implement structured output generation with native JSON schema request, JSON prompt fallback, Pydantic validation, repair retry, and safe failure.
- [x] Add tests for successful native structured output, unsupported native fallback, invalid JSON repair, schema-invalid safe failure, and HTTP errors.
- [x] Update README with gateway behavior and offline test posture.
- [x] Run verification commands and record results.
- [x] Commit and push the completed slice.

## Decisions

- Decision: Build the gateway against OpenAI-compatible `/v1/chat/completions`.
  Reason: LiteLLM Proxy exposes an OpenAI-compatible API and this keeps provider-specific SDKs out of application code.

- Decision: Keep graph integration out of this slice.
  Reason: The gateway and validation/repair semantics should be testable before the deterministic planner/critic is swapped for model-backed nodes.

- Decision: Use `httpx.MockTransport` for tests.
  Reason: It verifies request/response behavior without Docker, network, or paid model calls.

## Discoveries

- Discovery: The current settings already contain LiteLLM base URL, API key, and default chat model.
  Evidence: `src/ads_growth_agent/config.py` exposes `litellm_base_url`, `litellm_api_key`, and `default_chat_model`.

- Discovery: The gateway can be fully tested offline.
  Evidence: `tests/test_llm_gateway.py` uses `httpx.MockTransport` to assert request headers, request payloads, fallback ordering, repair prompts, and safe failure behavior.

- Discovery: Full repository test count increased from 28 to 35.
  Evidence: `.venv/bin/python -m pytest` collected and passed 35 tests after adding LLM gateway tests.

## Verification

- [x] Command or check: `.venv/bin/python -m pytest`
  Result: Passed, 35 tests.

- [x] Command or check: `.venv/bin/python -m ruff check .`
  Result: Passed.

- [x] Command or check: `tests/test_llm_gateway.py`
  Result: Covers native JSON schema success, unsupported native fallback, invalid JSON repair, schema-invalid safe failure, HTTP 500 safe failure, auth/request payload, and empty choices.

## Final Status

Completed. The project now has an offline-testable LiteLLM/OpenAI-compatible gateway client plus structured output generation with native JSON schema, JSON prompt fallback, Pydantic validation, repair retry, and safe failure behavior. The next slice should integrate this gateway into planner or critic graph nodes behind a feature flag.
