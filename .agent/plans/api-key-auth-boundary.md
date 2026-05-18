# API Key Auth Boundary

## Goal

Add the first local authentication boundary for product API endpoints without
breaking deterministic local demos or health probes.

## Scope

- Default behavior remains unauthenticated with `AUTH_MODE=none`.
- `AUTH_MODE=api_key` requires a configured `ADS_GROWTH_API_KEY`.
- Product endpoints accept either `X-API-Key` or `Authorization: Bearer ...`.
- Health endpoints remain public for local checks and container probes.
- This is not full production IAM, JWT validation, RBAC, or per-tenant
  authorization.

## Plan

- [x] Add this ExecPlan.
- [x] Add auth settings to `Settings` and `.env.example`.
- [x] Add a reusable FastAPI auth dependency.
- [x] Protect product API endpoints while leaving health endpoints public.
- [x] Add auth tests for disabled mode, missing credentials, invalid
  credentials, valid API key, valid bearer token, and missing server-side key.
- [x] Update README, RFC, roadmap, and changelog.
- [x] Run focused and full verification.
- [x] Commit and push the slice.

## Decisions

- Decision: Keep auth disabled by default.
  Reason: Local demos, CI, and CLI workflows must remain deterministic and
  model-key-free without extra setup.
- Decision: Use a shared static API key before JWT.
  Reason: It creates a real API boundary with minimal infrastructure while
  keeping production IAM as a future step.
- Decision: Keep health endpoints public.
  Reason: Liveness/readiness probes should not require application credentials
  in the local stack.

## Verification

- [x] `.venv/bin/pytest tests/test_auth.py`
  Result: 8 passed.
- [x] `.venv/bin/pytest`
  Result: 184 passed, 18 skipped.
- [x] `.venv/bin/ruff check .`
  Result: All checks passed.
- [x] `git diff --check`
  Result: Passed.

## Final Status

Implemented, locally verified, and committed for CI verification.
