# ADR 0005 — Zero LLM and zero required paid services

**Status:** Accepted · 2026-10-29

## Context

The product promise is explainable, affordable incident triage. Adding an LLM or a required
SaaS dependency would introduce recurring cost, vendor lock-in, network dependency, and
non-deterministic behavior. The roadmap makes this a hard constraint for every phase.

## Decision

- No model calls, API keys, or inference bills in the core path.
- Every third-party integration (Slack webhook, PagerDuty ingest, Sentry, Postgres,
  serverless hosting) is an optional adapter that degrades gracefully when absent.
- The API, dashboard, scoring, storage, and CLI run with the Python standard library alone.
- Hosting can be free-tier; commercial use of platforms that forbid it (Vercel Hobby) is
  limited to demos.

## Consequences

- The default install works offline and costs nothing to run.
- Features that need a paid service must be scoped as optional and documented as such.
- Any proposal that makes a paid service mandatory is rejected or moved to non-goals.
- PostgreSQL on Vercel uses the free `psycopg` driver; it is deployment-only, not a product
  dependency.
