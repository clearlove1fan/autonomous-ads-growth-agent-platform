# Advertiser Memory Read API And CLI

## Goal

Make long-term advertiser memory visible and auditable through tenant-scoped
product surfaces. Users should be able to list an advertiser's memories and
inspect a specific memory source after feedback ingestion or seed loading.

## Scope

- Add advertiser memory detail/list response contracts.
- Extend the advertiser memory store protocol with read methods.
- Implement Noop and PostgreSQL read behavior.
- Add protected FastAPI routes under `/advertisers/{advertiser_id}/memories`.
- Add CLI commands for listing and fetching advertiser memories.
- Add focused API/CLI/unit and Postgres integration coverage.
- Update product docs and roadmap notes.

## Plan

- [x] Add this ExecPlan.
- [x] Add advertiser memory read contracts.
- [x] Extend advertiser memory stores with read methods.
- [x] Wire FastAPI dependency and protected routes.
- [x] Add CLI read commands.
- [x] Add focused tests and update integration coverage.
- [x] Update README, changelog, roadmap, and RFC notes.
- [x] Run focused and full verification.
- [x] Commit and push the slice.

## Decisions

- Decision: Scope memory reads by `tenant_id` and `advertiser_id`.
  Reason: Advertiser memory is sensitive business context and should not be
  addressable across advertiser boundaries by source ID alone.
- Decision: Use `source_id` as the public identifier and keep `memory_id` as
  database identity in the response.
  Reason: Strategy sources already cite memory `source_id`, so users can jump
  from a strategy citation to the underlying memory.

## Verification

- [x] Focused tests.
  Result: `.venv/bin/pytest tests/test_advertiser_memory_read_api_cli.py tests/test_advertiser_memory_persistence.py tests/test_auth.py` passed with 17 passed.
- [x] Full pytest.
  Result: `.venv/bin/pytest` passed with 201 passed and 18 skipped.
- [x] Ruff.
  Result: `.venv/bin/ruff check ...` passed for touched source and test files.
- [x] `git diff --check`.
  Result: Passed.

## Discoveries

- Discovery: Live Postgres verification could not be run in this local session
  because Docker Desktop's daemon socket was not active.
  Evidence: `docker compose up -d postgres` failed with `dial unix
  /Users/learningmachine/.docker/run/docker.sock: connect: no such file or
  directory`. The Postgres integration test was updated and remains covered by
  CI.

## Final Status

Implemented and locally verified. Local live Postgres execution was blocked by
an inactive Docker daemon, so the updated integration assertion is left for CI's
Postgres job.
