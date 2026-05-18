# Curated Phase 1 Demo

## Goal

Add a small, repeatable demo verifier for the v0.1 Phase 1 MVP so a reviewer can
run one command and see the important product signals without reading the full
raw JSON response.

## Scope

- Keep `ads-growth-agent demo` as the raw product demo command.
- Add a curated verifier that runs the real CLI demo path and validates key
  product-contract signals.
- Document the expected human-readable output excerpt.
- Keep the verifier deterministic and model-key-free.

## Plan

- [x] Add this ExecPlan.
- [x] Add `scripts/verify_phase1_demo.py`.
- [x] Add an expected-output excerpt under `examples/`.
- [x] Add e2e coverage for the curated verifier.
- [x] Update README and roadmap references.
- [x] Run focused and full verification.
- [ ] Commit and push the slice.

## Decisions

- Decision: The verifier invokes the Typer CLI app in-process.
  Reason: This still exercises the CLI boundary while avoiding shell PATH and
  generated executable differences across local and CI environments.
- Decision: Validate contract fields instead of snapshotting the full JSON.
  Reason: `run_id` and `trace_id` are intentionally dynamic; the demo should
  validate stable product behavior rather than brittle identifiers.

## Verification

- [x] `.venv/bin/python scripts/verify_phase1_demo.py`
  Result: Passed and printed the expected reviewer-friendly summary.
- [x] `.venv/bin/pytest tests/e2e/test_phase1_demo_script.py`
  Result: 1 passed.
- [x] `.venv/bin/pytest`
  Result: 175 passed, 18 skipped.
- [x] `.venv/bin/ruff check .`
  Result: Passed.
- [x] `git diff --check`
  Result: Passed.

## Final Status

Implemented and locally verified. The slice is ready to commit and push.
