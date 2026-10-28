# ADR 0003 — Repository interface with JSON, SQLite, and Postgres adapters

**Status:** Accepted · 2026-10-29

## Context

Local development should need no database. Containers need durable storage on a volume.
Serverless has no durable disk and needs a networked database. Data must not be lost when
multiple requests write concurrently.

## Decision

Define `IncidentRepository` as the storage contract (incidents, outbox, idempotency) and
implement three adapters:

- **JSON**: versioned envelope, cross-process lock, atomic `os.replace` writes, legacy
  migration, WAL-like read safety via locking.
- **SQLite**: WAL mode, indexes matching every filter and dedupe path.
- **Postgres**: lazy `psycopg` import, `jsonb` payloads, same indexes.

Tenant scoping is part of every method signature, making isolation a storage invariant.

## Consequences

- The default install has zero runtime dependencies.
- One contract test suite runs against JSON and SQLite; Postgres shares behavior by design.
- JSON rewrites the whole file per mutation, so it is capped to small datasets by
  operational guidance.
- SQLite remains single-writer; heavy ingest should move to Postgres.
- Schema changes require forward migration logic and an envelope version bump.
