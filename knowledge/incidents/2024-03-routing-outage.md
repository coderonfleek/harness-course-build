
# Incident: Routing Engine Outage, 2024-03-14

## Summary

The routing engine returned 503s for approximately 47 minutes during
the afternoon peak (14:12–14:59 GMT). Affected ~30% of dispatches
during the window.

## Root Cause

A Kafka consumer in the routing engine had a slow-processing bug
introduced in the previous week's deploy. Lag accumulated silently
until the consumer group's max.poll.interval.ms threshold was
exceeded, at which point Kafka evicted consumers and triggered a
rebalance storm. The rebalances took the routing engine offline.

## Resolution

Rolled back to the previous week's routing engine deploy. Kafka
consumer group re-stabilized within 8 minutes of rollback.

## Follow-ups

- Added consumer lag alerting at 30% of the max.poll threshold (not
  just at hard failure)
- Load test now includes a slow-consumer scenario
- The specific bug (an unbounded retry loop on transient DB errors)
  was fixed and re-deployed within 2 days
