# Changelog

All notable changes to SentinelIQ are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `SENTINELIQ_ANONYMOUS_ROLE` (`disabled`, `viewer`, `responder`, or `admin`) controls
  requests without an API key. `viewer` makes a public instance keyless and read-only;
  writes and admin routes return `403`. Defaults preserve existing behavior: `admin`
  without keys, `disabled` with keys.

### Changed

- The runtime image no longer ships `pip`. The server is stdlib-only, and pip's vendored
  `msgpack`, `setuptools`, and `urllib3` copies were the only `HIGH` findings in the
  Trivy release scan.

## [2.0.0] — 2026-10-29

First production-grade release. Implements the roadmap through Phase 4 (security, operability
and data) and the Phase 5 documentation set.

### Added

- Incident lifecycle: `open` → `acknowledged` → `mitigated` → `resolved`, with timestamps,
  actors, notes, and an append-only timeline.
- MTTA, MTTR, open-incident age, and priority/state breakdowns in `GET /v1/summary` and the
  dashboard.
- Alert ingest for Alertmanager v4, PagerDuty, and generic payloads, with windowed
  deduplication, alert counts, and automatic escalation.
- Outbox-based notifications for Slack-compatible and generic webhooks, with retries,
  exponential backoff, dead-letter state, and a cron tick endpoint.
- Per-service playbook overrides and configurable scoring weights with startup validation.
- JSON/CSV export and JSON import, capped at 1,000 records per request.
- API-key authentication (`key:role:tenant`) and viewer/responder/admin RBAC, enforced per
  route, with tenant-scoped storage.
- Token-bucket rate limiting per key or IP with `429`, `Retry-After`, and `X-RateLimit-*`.
- Idempotency keys for `POST /v1/incidents` with replay responses.
- Request body cap (`413`), socket timeouts (`408`), consistent error envelopes, and a
  last-resort `500` handler without stack traces.
- Prometheus `/metrics` with request counters, latency histograms, and domain counters.
- Structured JSON or text logs with request IDs; optional Sentry error reporting.
- Separate `/health` and `/ready` probes; graceful shutdown on `SIGINT`/`SIGTERM`.
- ETag/`304` and cache headers for static dashboard assets; path-traversal protection.
- Versioned `/v1` routes with legacy aliases, plus an OpenAPI 3.1 document.
- Storage adapters: JSON envelope v2 with atomic cross-process-locked writes and legacy
  migration, SQLite with WAL and indexes, and optional Postgres for serverless.
- ASGI adapter and Vercel entrypoint sharing the same router and engine.
- Operations CLI (`seed`, `list`, `export`, `import`, `summary`, `verify-data`, `backup`,
  `restore`, `tick`).
- Single-file, stdlib-only Python client (`sdk/sentineliq_client.py`) with pagination helpers
  and runnable examples for creating incidents and ingesting alerts.
- Release smoke test (`scripts/smoke_test.py`) usable against any deployment.
- Importable Prometheus alert rules for errors, latency, readiness, notifications, and
  rate limiting.
- Load tester with p50/p95/p99 and latency-budget gates.
- Hardened Docker image (digest-pinned base, non-root, healthcheck, `/data` volume), plus a
  Compose file with read-only rootfs, dropped capabilities, and resource limits.
- Documentation: architecture, runbook, onboarding, releasing, ADRs, security policy,
  contributing guide, code of conduct, and deployment recipes for Docker, Vercel, and Netlify.
- CI runs ruff, mypy, bandit, pip-audit, coverage (≥70%), a live server smoke test, dashboard
  asset checks, a Docker image build/smoke job, and a serverless cold-start budget on Python
  3.11–3.13.
- Tag-driven release workflow: builds and pushes the GHCR image with SBOM and provenance,
  fails on unfixed CRITICAL/HIGH Trivy findings, and creates the GitHub release.
- Opt-in deploy workflows for the container host and Vercel; both run the release smoke test
  against the live URL and skip cleanly when credentials are absent.
- `scripts/cold_start.py` measures ASGI import and first-request latency in fresh processes
  (207 ms import, 226 ms total locally) and is gated at 2 s in CI.

### Changed

- Priority is computed from a normalized 0–100 score; the raw weighted total (max 115) and
  normalized value are both exposed.
- Dashboard rewritten as a dependency-free, CSP-safe client with filters, search, sorting,
  pagination, detail timeline, playbook actions, lifecycle controls, and live polling.
- All timestamps are timezone-aware UTC in ISO-8601 with a `Z` suffix.
- README and deployment guide rewritten for the supported topologies.

### Fixed

- Concurrent JSON writes can no longer lose incidents (atomic replace + file lock).
- Malformed input consistently returns `400`/`413`/`503` instead of a `500`.
- Dashboard no longer stores user data in HTML (`textContent` only) and no longer leaks
  horizontal overflow at mobile widths.
- HEAD requests are served correctly and report the same content length as GET.
- Legacy `data/incidents.json` records are normalized so owner/environment filters work.
- Vercel entrypoint resolves `src/` from the project root.

### Security

- CSP, `nosniff`, frame denial, referrer policy, permissions policy, and optional HSTS on all
  responses.
- Deny-by-default CORS with explicit origin allowlists.
- API keys held only as SHA-256 digests; cron uses a constant-time secret comparison.
- Request bodies bounded before parsing; static paths confined to the dashboard directory.

## [1.0.0] — 2026-10-29

Initial public baseline: deterministic five-dimension scoring, explainable priorities,
playbook actions, local JSON persistence, a basic dashboard, and unit tests.
