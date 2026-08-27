
# Code Conventions

## Python

- Formatter: `black` with default settings
- Linter: `ruff` with our custom rule set (see `pyproject.toml`)
- Type hints required on all public function signatures
- Test framework: `pytest` with fixtures (no `setUp` methods)
- Package layout: `src/` for library code, `tests/` for tests

## TypeScript

- Formatter: `prettier` with our shared config
- Linter: `eslint` with `@acme/eslint-config`
- All new code in strict mode
- Component structure: functional components with hooks (no class components)

## Pull Requests

- Every PR needs at least one approving review from someone outside the
  author's immediate sub-team
- PRs must be under 400 lines of diff; split larger changes into stacked PRs
- CI must pass before merge; no exceptions
- Squash-merge to main; the squash message becomes the commit message
