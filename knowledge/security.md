
# Security Practices

## Secrets

- Never commit secrets to git. We use `git-secrets` as a pre-commit hook
  to catch accidents.
- Runtime secrets come from AWS Secrets Manager, fetched at service
  startup. No secret ever appears in an environment variable in a
  developer's shell.
- Developer AWS access is via SSO with time-bound tokens (max 4h).

## Access

- Production database access is behind a bastion host with MFA. Direct
  connections from developer machines are blocked at the VPC level.
- Read-only replicas are available for engineering queries; use those
  for anything that isn't a live incident.

## Vulnerabilities

- Dependency scanning runs nightly (Dependabot for GitHub, custom scans
  for internal registries).
- Critical vulnerabilities get a PR opened automatically; someone must
  ack within 24 business hours.
