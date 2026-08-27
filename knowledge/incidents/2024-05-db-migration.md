
# Incident: Tracking DB Migration Delay, 2024-05-22

## Summary

A planned migration of the tracking service's Postgres from an older
db.r5 instance to a db.r6i.2xlarge took 3h longer than planned,
leaving the tracking API in read-only mode during the window.

## Root Cause

The migration plan assumed a snapshot restore would complete within
90 minutes based on the last-quarter's data volume. Data had grown
~2.5x since then, and the actual restore took 4.5 hours. Additionally,
one downstream service had hardcoded the old endpoint in a config file
that was missed in the migration checklist.

## Resolution

Waited for the restore to complete. Rolled the affected downstream
service with the corrected endpoint. Total customer-facing impact
was the 3h read-only window (writes queued and drained cleanly
afterwards).

## Follow-ups

- DB migrations now include a mandatory dry-run against a current
  snapshot to estimate actual restore times
- Endpoint discovery for downstream services moved from static config
  to service discovery (Consul)
- Migration checklist template updated to explicitly list all consumers
  of the migrated service
