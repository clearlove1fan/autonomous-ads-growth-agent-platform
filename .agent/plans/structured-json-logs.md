# Structured JSON Logs

## Goal

Add structured JSON logs for graph runs and local evaluation summaries. When complete, API and CLI execution should emit machine-readable log events with run ID, trace ID, advertiser ID, node path, tool counts, evaluation pass rate, and error metadata without exposing secrets or raw provider credentials.

## Context

- Relevant files:
  - `src/ads_growth_agent/config.py`
  - `src/ads_growth_agent/api.py`
  - `src/ads_growth_agent/cli.py`
  - `src/ads_growth_agent/graph.py`
  - `src/ads_growth_agent/evaluation.py`
  - `src/ads_growth_agent/observability.py`
  - `tests/`
- Current behavior:
  - Graph responses include `run_metadata`.
  - Local evaluation reports include suite pass/fail details.
  - The project depends on `python-json-logger`, but no application logging configuration or structured log events exist yet.
- Constraints:
  - Do not require Docker, Postgres, LiteLLM, LangSmith API keys, or network access.
  - Keep CLI JSON command output on stdout; logs should go to stderr.
  - Do not log raw secrets, provider API keys, or full advertiser payloads.
  - Follow the ExecPlan process from `/Users/learningmachine/Documents/New project/.agent/PLANS.md`.

## Plan

- [x] Create this ExecPlan and record scope.
- [x] Add JSON logging configuration helper.
- [x] Emit structured graph completion and graph failure events.
- [x] Emit structured local evaluation suite summary events.
- [x] Configure logging from API and CLI entrypoints.
- [x] Add tests for JSON formatter fields and log events.
- [x] Update README with logging behavior.
- [x] Run verification commands and record results.
- [x] Commit and push the completed slice.

## Decisions

- Decision: Use `python-json-logger`.
  Reason: It is already part of the selected stack and avoids inventing a custom JSON formatter.

- Decision: Send logs to stderr.
  Reason: CLI commands return structured JSON on stdout, and stderr logs preserve machine-readable command output.

- Decision: Log summary metadata only.
  Reason: Observability needs correlation and health signals, not raw advertiser text or credentials.

## Discoveries

- Discovery: `python-json-logger` is installed and exposes `jsonlogger.JsonFormatter`.
  Evidence: `.venv/bin/python` imported `pythonjsonlogger.jsonlogger.JsonFormatter` successfully.

- Discovery: The newer import path is `pythonjsonlogger.json.JsonFormatter`.
  Evidence: The old import path emitted a package deprecation warning during tests; switching to `pythonjsonlogger.json.JsonFormatter` removed it.

- Discovery: LangGraph emits a third-party pending deprecation warning on import.
  Evidence: CLI stderr initially included `LangChainPendingDeprecationWarning` from `langgraph/cache/base/__init__.py`. The graph module now filters that specific warning so stderr remains structured JSON for application logs.

- Discovery: CLI stdout and stderr remain separated.
  Evidence: `ads-growth-agent eval examples/eval_cases.json` writes the evaluation suite JSON to stdout and emits JSON log events to stderr.

## Verification

- [x] Command or check: `.venv/bin/python -m pytest`
  Result: Passed, 28 tests.

- [x] Command or check: `.venv/bin/python -m ruff check .`
  Result: Passed.

- [x] Command or check: `env PYTHONPYCACHEPREFIX=/private/tmp/ads-growth-agent-pycache .venv/bin/python -m compileall src tests`
  Result: Source and tests compiled successfully.

- [x] Command or check: `.venv/bin/ads-growth-agent eval examples/eval_cases.json >/private/tmp/eval-stdout.json 2>/private/tmp/eval-stderr.log`
  Result: Stdout parsed as evaluation JSON with pass rate `1.0`; stderr parsed as JSON log events `growth_strategy.run_completed` and `evaluation.suite_completed`.

## Final Status

Completed. API and CLI entrypoints now configure JSON logging, graph runs emit structured completion/failure events, local eval suites emit structured summary events, and tests verify parseable JSON log records. CLI stdout remains reserved for command payloads while logs are emitted to stderr.
