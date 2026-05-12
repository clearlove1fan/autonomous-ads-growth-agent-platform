# Contributing

This repository treats `main` as the stable demo branch. Use short-lived
`codex/*` or `feature/*` branches for implementation work, then merge through a
pull request.

## Required Checks

Pull requests should pass these GitHub Actions jobs before merge:

- `lint`
- `unit`
- `e2e-smoke`
- `postgres-integration`
- `release-readiness`

`main` branch protection should require the same checks and at least one pull
request approval before merge. Repository settings enforce this; this file
records the expected policy for reviewers and future setup.

## Dependency Lock

`requirements-lock.txt` is the v0.1 reproducibility lock. Refresh it from a
clean Python 3.11 virtual environment:

```bash
python -m pip install -e ".[dev]"
python -m pip list --format=freeze --exclude-editable --exclude pip > requirements-lock.txt
pytest
ruff check .
```

Dependency lock changes should be deliberate and reviewed like source changes.

## Release Notes

Before tagging a milestone such as `v0.1.0`, update `CHANGELOG.md` with the
user-visible or architecture-visible changes and record the verification that
passed for the release candidate.
