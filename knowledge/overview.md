
# Acme Corp Engineering Overview

Acme Corp is a mid-size logistics platform. We move packages between
warehouses and last-mile carriers for 300+ retail clients. The engineering
team owns four core services: the routing engine, the tracking API, the
warehouse coordination service, and the client-facing dashboard.

Our stack is Python (services) and TypeScript (dashboard). We run on
AWS. The primary datastore is Postgres; hot paths hit a Redis cache.
Message passing between services goes through Kafka.
