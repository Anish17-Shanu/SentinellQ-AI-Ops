# Architecture

SentinelIQ is a small, layered Python service. Everything in the core path uses the
standard library; optional integrations sit behind interfaces so the system still runs
offline with zero paid dependencies.

## Principles

1. **Deterministic core.** Scoring is a pure function of normalized input and configuration.
2. **Transport-neutral.** One `Router` serves both `http.server` and ASGI.
3. **Storage-neutral.** One repository contract with JSON, SQLite, and Postgres adapters.
4. **Fail closed in production.** Authentication, tenant isolation, body limits, and RBAC
   are enforced in the shared router, not in a transport.

## Component map

```
                       ┌─────────────────────────────┐
  browser dashboard ──▶│  Router (auth, RBAC, limits)│◀── Alertmanager / PagerDuty
                       └──────────────┬──────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
              IncidentService     Metrics           OpenAPI
          (lifecycle, ingest,  (Prometheus)        (spec)
           dedupe, outbox)
                    │
                    ▼
        ┌──────────────────────────────────────┐
        │  IncidentRepository (ABC)            │
        │  JSON │ SQLite │ Postgres           │
        └──────────────────────────────────────┘
                    │
                    ▼
          WebhookNotifier (optional outbox)
                    │
             Slack / generic webhook
```

| Module | Responsibility |
| --- | --- |
| `config.py` | Parse and validate environment settings |
| `engine.py` | Normalize, weight, band, explain, playbooks |
| `service.py` | Lifecycle, ingest/dedupe, idempotency, summary, export/import, outbox |
| `repository.py` | Storage contract + JSON and SQLite adapters |
| `postgres_repository.py` | Optional Postgres adapter (lazy `psycopg` import) |
| `router.py` | Routes, auth, RBAC, rate limiting, static assets, error mapping |
| `server.py` | `ThreadingHTTPServer` transport and graceful shutdown |
| `asgi.py` | ASGI transport for serverless/ASGI hosts |
| `auth.py` | API-key parsing, identity, role ranking |
| `ratelimit.py` | Token buckets keyed by API key or IP |
| `notifier.py` | Webhook delivery (Slack-compatible or generic JSON) |
| `metrics.py` | Prometheus counters and latency histograms |
| `logging_utils.py` | JSON or text structured logs with request IDs |
| `openapi.py` | OpenAPI 3.1 document |
| `sentry.py` | Optional Sentry error reporting |
| `seed.py` | Realistic demo incidents with backdated timelines |

The composition root is `bootstrap.build_context()`. It loads settings, weights, and
playbooks, builds the repository, service, authenticator, limiter, router, and optional
notifier. `api/[...path].py` exposes the lazy ASGI app.

## Request lifecycle

1. **Transport.** `server.py` reads method, path, query, headers, and a body capped by
   `Content-Length`. It rejects chunked bodies (`411`), oversized bodies (`413`), and read
   timeouts (`408`) before routing. The ASGI adapter applies the same body cap.
2. **Normalization.** `Router.handle` sanitizes/creates a request ID, decodes the path, and
   strips the `/api` prefix when used from a serverless gateway.
3. **Routing.** Static `/dashboard/*` requests are served from disk with path-traversal
   protection and ETags. Everything else matches the route table.
4. **Authorization.** `Authenticator.identify` resolves `X-API-Key` or Bearer tokens to an
   `Identity(role, tenant, label)`. `require` enforces the minimum role. The cron route
   uses a constant-time secret comparison instead of an identity.
5. **Rate limiting.** Token buckets are keyed by the authenticated key label or client IP.
   Rejections return `429` with `Retry-After` and `X-RateLimit-*` headers.
6. **Handler.** The handler calls `IncidentService`, which applies domain rules and uses the
   repository. Handlers never touch storage directly.
7. **Finalize.** Security headers, `X-Request-Id`, charset normalization, CORS, and
   `Cache-Control: no-store` for JSON are applied.
8. **Observe.** Latency and status are recorded in metrics; a structured log line records
   request ID, method, path, route, status, duration, tenant, and role.
9. **Errors.** `ApiError` subclasses map to a consistent JSON envelope. `StorageError` maps
   to `503`, and unexpected exceptions map to a generic `500` (no stack traces to clients)
   plus an optional Sentry event.

## Scoring

`normalize_incident` validates and bounds inputs: `impacted_users` ≤ 1e9, `error_rate`
≤ 100, `latency_ms` ≤ 3,600,000 ms, `alerts` ≤ 1,000,000, text fields have length caps.

`assess_incident` computes:

```
criticality  = weight[criticality]                      (8 / 18 / 30 / 42)
user_impact  = min(log10(max(users, 1)) * 12, 25)
error        = min(error_rate * 1.8, 20)
latency      = min((latency_ms / 2000) * 16, 16)
alerts       = min(alerts * 1.1, 12)
total        = sum of the five terms                    (max 115)
score_100    = total / 115 * 100
```

Priority bands: `P1 ≥ 85`, `P2 ≥ 65`, `P3 ≥ 42`, else `P4`. A `priority_hint` can only
escalate. Confidence starts at 0.55 and grows with alert pressure and user impact, capped
at 0.96. The explanation names the largest contributing dimensions, so every decision is
auditable.

Weights, caps, and bands are configuration (`config/weights.json`), validated at startup
with `P1 > P2 > P3`. Playbooks support per-service overrides, e.g. `payments-api`.

## Data model

An incident is a JSON object with stable identity and lifecycle fields:

| Field | Meaning |
| --- | --- |
| `id` | UUID assigned at creation |
| `tenant` | Owning tenant, always applied as a storage filter |
| `service`, `owner`, `environment`, `description` | Normalized text |
| `criticality`, `category`, `priority`, `priority_hint` | Classification |
| `impacted_users`, `error_rate`, `latency_ms`, `alerts`, `alert_count` | Signals |
| `score`, `score_100`, `confidence`, `risk_band`, `explanation`, `factors`, `dimensions`, `actions` | Assessment |
| `state` | `open` → `acknowledged` → `mitigated` → `resolved` |
| `created_at`, `updated_at`, `acknowledged_at`, `mitigated_at`, `resolved_at`, `last_seen_at` | UTC ISO-8601 (`Z`) |
| `dedup_key` | `service|category`, used for correlation |
| `source` | `api`, `seed`, `import`, or an ingest source |
| `events` | Append-only timeline, capped at 200 entries |

### JSON envelope (v2)

```json
{
  "version": 2,
  "incidents": [],
  "outbox": [],
  "idempotency": {}
}
```

Writes acquire a cross-process file lock, serialize the envelope to a temporary file in the
same directory, `fsync`, and atomically `os.replace` it. Readers tolerate a legacy v0.1
top-level list and migrate it on the next write. A corrupt file raises `StorageError`,
which surfaces as `503` rather than silently discarding data.

### SQLite

WAL mode, `busy_timeout`, and indexes on `(tenant, created_at)`, `(tenant, state)`,
`(tenant, priority)`, and `(tenant, dedup_key, state)`. Each operation uses a short-lived
connection with `closing()` to avoid leaked descriptors (notably on Windows). The schema is
created idempotently at startup.

### Postgres

Used by the serverless target. `psycopg` is imported lazily, so it is never required
locally. The same contract methods apply; JSON payloads are stored as `jsonb`, and indexed
columns mirror the SQLite schema.

## Workflows

### Ingest and dedupe

`POST /v1/ingest/{alertmanager|pagerduty|generic}` normalizes provider payloads into one or
more incidents. For each incident, the service looks for a non-resolved record with the same
`dedup_key` whose `last_seen_at` is inside `SENTINELIQ_DEDUP_WINDOW_SECONDS`. A match
increments `alert_count`, raises signals to the maximum of the old and new values, appends
an event, and escalates the priority if the new assessment is higher. Otherwise a new
incident is created. Responses report `created`/`deduplicated` counts and per-item actions.

### Lifecycle

Transitions validate the state machine and record the actor, note, and timestamp. Repeating
the same target state returns `409`. Every change appends an event, updates `updated_at`,
and enqueues a notification when the priority meets the notify threshold.

### Idempotency

`POST /v1/incidents` accepts an `Idempotency-Key` header (1–128 characters of
`A-Za-z0-9._:-`). Responses are stored per tenant; a replay returns the original record with
`Idempotent-Replayed: true` instead of creating a duplicate. Ingest is naturally idempotent
through deduplication: repeated alerts for the same service and category update one open
incident rather than creating more.

### Notifications and cron

Notifications are written to an outbox first, then delivered by `tick()`:

- P1/P2 by default (`SENTINELIQ_NOTIFY_MIN_PRIORITY`).
- Slack URLs get a `{"text": ...}` payload; everything else receives the raw event JSON.
- Each entry has a stable `event_key`, so retries cannot duplicate a notification.
- Failures retry up to five times with exponential backoff (30 s → 1 h), then move to
  `dead`.

`tick()` runs through `/v1/cron/tick` (protected by `SENTINELIQ_CRON_SECRET`) or
`python scripts/cli.py tick`. This keeps serverless deployments free of background threads.

### Export and import

Export returns JSON or CSV for the caller's tenant with `Content-Disposition`. Import
normalizes each record, keeps its ID when present, and re-scores it. Import is upsert
semantics and is capped at 1,000 records per request.

## Multi-tenancy

Tenant isolation is a storage-level invariant: every repository method takes a tenant and
filters on it. The router derives the tenant from the API key (or the configured default
for the anonymous role). A tenant cannot influence another tenant's data through filters,
pagination, exports, or idempotency keys.

## Deployment topology

| Target | Transport | Storage | Background work |
| --- | --- | --- | --- |
| Docker | `http.server` | SQLite on a volume | `tick()` via cron or CLI |
| Vercel | ASGI | Hosted Postgres | Vercel cron / external cron |
| Netlify | — | — | Static dashboard calling the API |

Serverless constraints drove three design choices: storage behind an interface with a
networked adapter, no reliance on in-process timers, and request bodies that finish within
the platform timeout.

## What is deliberately not here

- No LLM or ML inference — explainability and determinism are the product.
- No frontend framework or build step — the dashboard is plain HTML/CSS/JS served as static
  files.
- No message queue — the outbox table gives at-least-once delivery with a cron driver.
- No multi-region replication — a single writer per database is the supported model.
