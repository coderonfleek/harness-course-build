
# ADR-002: Kafka for Inter-Service Messaging

## Status

Accepted, 2023-02-03. Supersedes an earlier proposal to use RabbitMQ.

## Context

Growing volume of routing events (>10k/sec at peak) plus a need for
event replay when downstream services fell behind or needed to backfill.

## Decision

Adopt Kafka as the primary message bus between services. Managed via
AWS MSK.

## Consequences

- Handles peak load with headroom to spare
- Event replay is straightforward (consumer offsets)
- Operational complexity higher than RabbitMQ would have been
- Team invested in Kafka expertise; retention decisions require careful
  cost analysis (storage cost scales with retention window)
