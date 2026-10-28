# ADR 0004 — API keys bound to roles and tenants

**Status:** Accepted · 2026-10-29

## Context

Write endpoints and multi-team data must not be open on a public deployment. Full OIDC or
session auth adds dependencies and complexity; the roadmap allows API keys first.

## Decision

Configure static API keys as `key:role:tenant` entries in `SENTINELIQ_API_KEYS`. Hash each
key with SHA-256 at startup and keep only digests in memory. Resolve `X-API-Key` or
`Authorization: Bearer` to an `Identity(role, tenant, label)`. Enforce minimum roles per
route: viewer for reads, responder for writes and ingest, admin for delete/export/import/
metrics. When auth is disabled, run as one anonymous admin in the configured default tenant
and warn in production.

Cron uses a separate constant-time secret (`SENTINELIQ_CRON_SECRET`), not an API key.

## Consequences

- No user database, password storage, or session management.
- Tenants are isolated by the storage layer, not by request parsing.
- Rotation is a config change plus restart; there is no self-service UI.
- Keys are long-lived credentials; deployment guidance requires TLS and strong random keys.
- OIDC/session auth can be added later as another authenticator without changing routes.
