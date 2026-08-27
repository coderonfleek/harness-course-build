
# ADR-001: Monorepo

## Status

Accepted, 2022-08-15.

## Context

We started with four separate repos (one per service). Cross-service
changes required coordinated PRs across repos, which slowed us down and
made rollbacks painful.

## Decision

Move all four services and the dashboard into a single monorepo. Use
per-directory ownership (CODEOWNERS) to preserve team boundaries.

## Consequences

- Cross-service changes are now atomic (one PR, one revert if needed)
- CI runs longer (full test suite on every PR) — mitigated with
  bazel-based test selection
- Onboarding is simpler (one clone, one setup script)
- Historical git history is preserved via git subtree merges
