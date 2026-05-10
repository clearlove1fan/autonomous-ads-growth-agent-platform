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
- [x] Run source/package validation.
- [x] Install/enable Docker Compose locally and validate the Compose stack.
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

- Decision: Set the Compose project name to `ads-growth-agent`.
  Reason: The workspace path contains punctuation, and an explicit project name keeps container, network, and volume names stable and readable.

- Decision: Override both `DATABASE_URL` and `LITELLM_DATABASE_URL` for the LiteLLM service.
  Reason: `.env` contains the host/local SQLAlchemy URL for app development; LiteLLM's Prisma startup path reads `DATABASE_URL` and needs a plain Compose-network `postgresql://...@postgres:5432/...` URL.

- Decision: Add a LiteLLM readiness healthcheck and make the API wait for `service_healthy`.
  Reason: The API should not report a complete local stack until the LLM gateway is actually ready to accept traffic.

## Discoveries

- Discovery: Docker CLI is installed, but Docker Compose is not available in this environment.
  Evidence: `docker --version` returned Docker 28.3.2, `docker compose version` returned `docker: unknown command: docker compose`, and `docker-compose` was not found.

- Discovery: Docker Compose can be installed and exposed to Docker CLI through Homebrew.
  Evidence: `brew install docker-compose` completed successfully, `~/.docker/config.json` was configured with `/opt/homebrew/lib/docker/cli-plugins`, and both `docker compose version` and `docker-compose version` returned Docker Compose 5.1.3.

- Discovery: Docker Desktop must be running before Compose can talk to the daemon.
  Evidence: Docker CLI was installed, but Compose commands could not start services until Docker Desktop was accepted and launched.

- Discovery: LiteLLM initially exited because it inherited the host-oriented `.env` `DATABASE_URL`.
  Evidence: `docker compose ps --all` showed `ads-growth-agent-litellm-1` exited with code 3, and `docker compose logs --tail=120 litellm` showed a Prisma database connection failure during startup. Explicitly setting the container `DATABASE_URL` fixed startup.

- Discovery: The bundled Python runtime can compile the current source tree.
  Evidence: `python3 -m compileall src tests` completed successfully with the bundled Python 3.12 runtime.

## Verification

- [x] Command or check: `env PYTHONPYCACHEPREFIX=/private/tmp/ads-growth-agent-pycache /Users/learningmachine/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m compileall src tests`
  Result: Source and tests compiled successfully.

- [x] Command or check: `/Users/learningmachine/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c 'import tomllib; tomllib.load(open("pyproject.toml", "rb")); print("pyproject_ok")'`
  Result: `pyproject.toml` parsed successfully.

- [x] Command or check: `.venv/bin/python -m pytest`
  Result: Passed, 1 test.

- [x] Command or check: `.venv/bin/python -m ruff check .`
  Result: Passed.

- [x] Command or check: `docker compose config`
  Result: Passed after installing Docker Compose. The resolved project name is `ads-growth-agent`, and the API now waits for healthy Postgres and healthy LiteLLM.

- [x] Command or check: `docker-compose config`
  Result: Passed after installing Docker Compose through Homebrew.

- [x] Command or check: `docker compose up --build -d`
  Result: Built the API image and started Postgres, LiteLLM, and API containers.

- [x] Command or check: `docker compose ps`
  Result: API, LiteLLM, and Postgres are all running and healthy.

- [x] Command or check: `curl -sS http://localhost:8000/health`
  Result: Returned `{"status":"ok","service":"ads-growth-agent","version":"0.1.0","environment":"local"}`.

- [x] Command or check: `curl -sS http://localhost:4000/health/readiness`
  Result: Returned healthy status with `db: connected` and LiteLLM version `1.82.6`.

- [x] Command or check: `curl -sS -H "Authorization: Bearer sk-local-dev-key" http://localhost:4000/v1/models`
  Result: Returned configured LiteLLM model aliases `ads-growth-chat` and `ads-growth-embedding`.

- [x] Command or check: `docker compose exec -T postgres psql -U ads_growth -d ads_growth -c "SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pg_trgm') ORDER BY extname;"`
  Result: Returned `pg_trgm` and `vector`.

## Final Status

Completed. Local runtime stack files were added, committed as `1433f1d add local runtime stack`, and pushed to GitHub. Docker Desktop and Docker Compose are now installed locally, Compose configuration validates, and the full stack has been verified with healthy API, LiteLLM, and Postgres services.
