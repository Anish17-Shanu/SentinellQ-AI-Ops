# ADR 0006 — Outbox table plus cron instead of background worker threads

**Status:** Accepted · 2026-10-29

## Context

Notifications must survive restarts and must not be lost when a webhook is down. A worker
thread inside the API process would not exist on serverless platforms, and would duplicate
work across replicas.

## Decision

Write notifications to an outbox table as part of the same request that changes an incident.
A separate `tick()` operation claims due entries, attempts delivery, records the outcome, and
schedules retries with exponential backoff. `tick()` is exposed through
`GET/POST /v1/cron/tick` behind `SENTINELIQ_CRON_SECRET`, the CLI (`scripts/cli.py tick`),
and platform cron.

Each entry carries a stable `event_key` (`incident:event:timestamp`), so retries and
overlapping ticks cannot deliver the same event twice from this system's perspective.

## Consequences

- The API request path stays fast and free of network I/O to third parties.
- Delivery works identically in containers and serverless.
- Delivery is at-least-once: a crash after sending but before marking `sent` can duplicate a
  notification; consumers should be idempotent.
- Operators must configure a scheduler; without one, notifications remain queued (never
  lost).
- Failed entries become `dead` after five attempts and require operator action.
