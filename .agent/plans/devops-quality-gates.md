# DevOps Quality Gates

## Goal

Turn the project quality expectations into repeatable repository-level gates. The work should make CI/CD, branch workflow, dependency reproducibility, deterministic end-to-end validation, and release readiness explicit instead of relying on local developer discipline.

## Context

- Relevant files:
  - `.github/workflows/ci.yml`
  - `pyproject.toml`
  - `README.md`
  - `RFC-Autonomous-Ads-Growth-Agent-Platform-v0.1.md`
  - `PROJECT-MATURITY-ROADMAP.md`
  - `tests/`
- Current behavior:
  - A basic GitHub Actions workflow exists and runs package install, `ruff check .`, and `pytest` on pull requests and pushes to `main`.
  - Project dependencies are specified with lower bounds in `pyproject.toml`.
  - No `requirements-lock.txt`, `poetry.lock`, `uv.lock`, or equivalent lock file is committed.
  - The RFC now defines target branch, PR, dependency-lock, CI, and release gates.
  - The roadmap now tracks engineering workflow and quality gates as Phase 1.5.
- Constraints:
  - Do not change runtime behavior as part of planning-only updates.
  - Keep default local behavior deterministic and model-key-free.
  - Avoid introducing a packaging migration unless dependency locking cannot be handled cleanly with the current `pyproject.toml` setup.
  - Treat GitHub branch protection as a repository setting task; document it even if it cannot be fully enforced from local files.

## Plan

- [x] Add this ExecPlan.
- [ ] Decide the lock generation command and lock file format for v0.1.
- [ ] Generate and commit `requirements-lock.txt` or an equivalent accepted lock file.
- [ ] Update install instructions so CI/demo setup can use the lock file.
- [ ] Split GitHub Actions into readable quality gates for lint, unit tests, deterministic E2E smoke, and integration/release verification.
- [ ] Add a deterministic API or CLI E2E smoke test that runs without external model keys.
- [ ] Add CI coverage for the deterministic E2E smoke test.
- [ ] Document branch strategy, PR review expectations, required checks, and release tagging in README or contributor notes.
- [ ] Configure or document GitHub `main` branch protection with required checks and PR approval.
- [ ] Add changelog/release verification expectations for v0.1 demo milestones.
- [ ] Run targeted tests, full tests, ruff, and the deterministic E2E smoke path.

## Decisions

- Decision: Use GitHub Actions as the v0.1 CI runner.
  Reason: The repository is already GitHub-backed, and a basic workflow exists.

- Decision: Use a committed lock file for v0.1 instead of relying only on lower-bound dependency ranges.
  Reason: Lower bounds are fine for package metadata but do not make local demos or CI reproducible.

- Decision: Treat deterministic E2E smoke as a separate quality gate.
  Reason: Unit tests and mock-heavy contract tests can pass while the actual product boundary is broken.

- Decision: Keep production deployment out of v0.1 automation.
  Reason: The project is still draft-only and lacks production auth, rate limits, and full operational controls.

## Discoveries

- Discovery: A basic GitHub Actions workflow already exists.
  Evidence: `.github/workflows/ci.yml` installs the package, runs `ruff check .`, and runs `pytest`.

- Discovery: No dependency lock file is currently committed.
  Evidence: `rg --files -g '*lock*' -g 'requirements*.txt' -g 'uv.lock' -g 'poetry.lock' -g 'Pipfile.lock'` returned no files.

- Discovery: The RFC previously had a technical test plan but no explicit automated execution plan.
  Evidence: Section 14.2 listed test areas, while CI/CD, dependency locking, branch protection, and release gates were absent or not launch-blocking.

## Verification

- [ ] Targeted pytest:
  Result:
- [ ] Full pytest:
  Result:
- [ ] Ruff:
  Result:
- [ ] Deterministic E2E smoke:
  Result:
- [ ] CI workflow review:
  Result:
- [ ] Branch protection review:
  Result:

## Final Status

Planned. RFC and roadmap now include the missing DevOps and engineering quality gates; implementation remains pending.
