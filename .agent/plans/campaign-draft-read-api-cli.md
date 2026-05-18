# Campaign Draft Read API And CLI

## Goal

Make persisted campaign drafts usable after strategy generation by exposing
tenant-scoped detail and list reads through the product API and CLI.

## Scope

- Add campaign draft detail/list response contracts.
- Extend the campaign draft store protocol with `get_draft` and `list_drafts`.
- Implement Noop and PostgreSQL read behavior.
- Add `GET /campaign-drafts/{draft_id}` and `GET /campaign-drafts`.
- Add `get-campaign-draft` and `list-campaign-drafts` CLI commands.
- Add focused API/CLI/unit and Postgres integration coverage.

## Plan

- [x] Add this ExecPlan.
- [x] Add campaign draft read contracts.
- [x] Extend campaign draft stores with read methods.
- [x] Wire FastAPI dependency and protected routes.
- [x] Add CLI read commands.
- [x] Add focused tests and update integration coverage.
- [x] Update README, changelog, roadmap, and RFC notes.
- [x] Run focused and full verification.
- [x] Commit and push the slice.

## Decisions

- Decision: Default Noop store returns no drafts.
  Reason: Local deterministic behavior remains model-key-free and database-free
  unless draft persistence is explicitly enabled.
- Decision: Return the final strategy alongside draft metadata.
  Reason: Draft review needs explainability, not only the draft ID and budget.

## Verification

- [x] `.venv/bin/pytest tests/test_campaign_draft_persistence.py tests/test_auth.py`
  Result: 17 passed.
- [x] `.venv/bin/pytest`
  Result: 195 passed, 18 skipped.
- [x] `.venv/bin/ruff check .`
  Result: All checks passed.
- [x] `git diff --check`
  Result: Passed.

## Final Status

Implemented, locally verified, and committed for CI verification.
