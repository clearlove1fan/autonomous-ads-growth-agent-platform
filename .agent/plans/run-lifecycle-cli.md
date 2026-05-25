# Run Lifecycle CLI

## Goal

Close the Phase 2 local-operations gap by exposing persisted run detail,
retry, and resume flows through the CLI as well as the API. This keeps the
local MVP debuggable from the same command-line surface used for demo, eval,
and operator recovery.

## Scope

- Add shared run-lifecycle validation helpers for stored-brief resume and
  retry eligibility.
- Add `ads-growth-agent get-run`.
- Add `ads-growth-agent resume-run`.
- Add `ads-growth-agent retry-run`.
- Add unit coverage for CLI success paths and core guard behavior.
- Update README, RFC, roadmap, and changelog.

## Non-Goals

- No new retry queue or DLQ service.
- No production IAM/RBAC change.
- No native partitioning or replica routing.
- No change to API semantics beyond sharing validation logic.

## Acceptance Criteria

- CLI `get-run` returns the same persisted run detail contract as the API.
- CLI `resume-run` reuses the original run ID, strategy ID, and trace ID when
  regenerating from stored run metadata.
- CLI `retry-run` creates a fresh execution from an explicit brief file and
  rejects non-failed runs.
- Existing API retry/resume tests continue to pass.

## Verification

- [x] Focused run lifecycle CLI/API tests pass: `39 passed`.
- [x] Full suite passes: `315 passed, 21 skipped`.
- [x] Ruff, py_compile, and diff check pass.
