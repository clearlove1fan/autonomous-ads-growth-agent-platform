# Feedback Loop Command Center

Add an event-rooted operator command center that combines feedback-loop summary,
timeline, and stage-aware next commands so a user can tell what to do next
without manually translating status into API or CLI calls.

## Scope

- Add typed command-center contracts for stage-aware operator commands.
- Add a pure builder that composes existing summary and timeline projections.
- Add FastAPI and CLI read surfaces.
- Extend unit, API/CLI, auth, and persisted product-loop verification.
- Sync README, RFC/HLD, roadmap, and changelog.

## Checklist

- [x] Create this ExecPlan.
- [x] Add command-center contracts and builder.
- [x] Wire API and CLI command-center endpoints.
- [x] Add focused tests and product-loop verifier assertions.
- [x] Update docs.
- [x] Run focused tests, full tests, ruff, compile check, and diff check.
- [ ] Commit, push, and verify CI.

## Decisions

- Decision: Keep the command center read-only.
  Reason: v0.1 still requires human review and does not mutate live campaign
  state.
- Decision: Include concrete API paths and CLI command arrays instead of only
  natural-language next actions.
  Reason: Product users need actionable affordances, not just status text.
- Decision: Compose from summary and timeline instead of persisting another
  table.
  Reason: This is an operator read model over existing audit records.

## Verification

- [x] Command center chooses the correct primary command for review, revision,
  execution, dry-run, handoff, and post-handoff stages.
- [x] API command center mirrors builder output and headers.
- [x] CLI command center mirrors API shape.
- [x] Persisted product-loop verifier covers command-center reads.
- [x] Focused tests for command-center unit/API/CLI/auth paths passed with 13 passed.
- [x] Full pytest: `.venv/bin/pytest` passed with 288 passed, 20 skipped.
- [x] Ruff: `.venv/bin/ruff check .` passed.
- [x] Compile check: `.venv/bin/python -m py_compile scripts/verify_persisted_product_loop.py src/ads_growth_agent/feedback_loop_command_center.py` passed.
- [x] `git diff --check` passed.
