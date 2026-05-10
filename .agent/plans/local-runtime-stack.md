# Local Runtime Stack

## Goal

Add a production-like local runtime stack for the project: FastAPI service, PostgreSQL with pgvector, and LiteLLM Proxy. When complete, a developer should be able to start the core infrastructure with Docker Compose and hit the API health endpoint locally.

## Context

- Relevant files:
  - `README.md`
  - `.env.example`
  - `pyproject.toml`
  - `src/ads_growth_agent/api.py`
  - `RFC-Autonomous-Ads-Growth-Agent-Platform-v0.1.md`
- Current behavior:
  - The repo has a FastAPI `/health` endpoint, CLI health command, package metadata, CI, and HLD/RFC.
  - There is no Dockerfile, Docker Compose stack, Postgres initialization, or LiteLLM Proxy config yet.
- Constraints:
  - Keep this step focused on local runtime infrastructure.
  - Do not implement agent graph, tool registry, or database schema migrations in this step.
  - Follow the ExecPlan process in `.agent/plans/`.

## Plan

- [x] Add Dockerfile and Docker ignore rules for the FastAPI service.
- [x] Add Docker Compose services for API, PostgreSQL with pgvector, and LiteLLM Proxy.
- [x] Add Postgres extension initialization and LiteLLM Proxy config.
- [x] Update environment example and README local stack instructions.
- [x] Run non-network validation where possible and record any blocked checks.
- [x] Commit and push the local runtime stack.

## Decisions

- Decision: Use `compose.yaml` rather than the legacy `docker-compose.yml` name.
  Reason: It is the current Docker Compose default file name and keeps the root clean.

- Decision: Use `pgvector/pgvector:pg16` for local Postgres.
  Reason: It provides PostgreSQL with the `vector` extension available at startup, matching the HLD.

- Decision: Use `docker.litellm.ai/berriai/litellm:main-latest` for LiteLLM Proxy.
  Reason: This matches the LiteLLM official Docker quick start image.

- Decision: Keep LiteLLM Proxy config minimal in v0.1.
  Reason: The goal is to establish the model gateway boundary first; provider fallback routing can be expanded once model credentials and eval cases are in place.

## Discoveries

- Discovery: Docker CLI is installed, but Docker Compose is not available in this environment.
  Evidence: `docker --version` returned Docker 28.3.2, `docker compose version` returned `docker: unknown command: docker compose`, and `docker-compose` was not found.

- Discovery: The bundled Python runtime can compile the current source tree.
  Evidence: `python3 -m compileall src tests` completed successfully with the bundled Python 3.12 runtime.

## Verification

- [x] Command or check: `env PYTHONPYCACHEPREFIX=/private/tmp/ads-growth-agent-pycache /Users/learningmachine/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m compileall src tests`
  Result: Source and tests compiled successfully.

- [x] Command or check: `/Users/learningmachine/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c 'import tomllib; tomllib.load(open("pyproject.toml", "rb")); print("pyproject_ok")'`
  Result: `pyproject.toml` parsed successfully.

- [x] Command or check: `docker compose config`
  Result: Blocked because Docker Compose v2 plugin is not installed in the current environment.

- [x] Command or check: `docker-compose config`
  Result: Blocked because legacy `docker-compose` is not installed in the current environment.

## Final Status

Completed. Local runtime stack files were added, committed as `1433f1d add local runtime stack`, and pushed to GitHub. Compose startup still needs to be verified in an environment with Docker Compose v2 installed.
