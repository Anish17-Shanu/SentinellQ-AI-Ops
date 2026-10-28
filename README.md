# SentinelIQ AI Ops

**Explainable, deterministic incident prioritization for on-call teams.**

SentinelIQ scores incoming incidents across five business-impact dimensions, assigns a
P1–P4 priority with a written explanation and a playbook, and tracks the incident from
`open` through `acknowledged`, `mitigated`, and `resolved`. It ships a browser dashboard,
a JSON/CSV export, Alertmanager/PagerDuty ingests, API-key auth with tenants, Prometheus
metrics, and a CLI for day-to-day operations.

Built and maintained by **Anish Kumar**.

## Why this exists

Most "AI ops" tools are opaque and expensive. SentinelIQ is the opposite:

- **Zero LLM / zero AI inference.** Scoring is a transparent weighted formula. The same
  input always produces the same priority and the same explanation.
- **Zero paid services required.** The API, dashboard, scoring, and storage run on the
  Python standard library. Slack, PagerDuty, Sentry, Postgres, and hosted platforms are
  optional adapters.
- **Offline-capable.** No outbound network call is needed for the core workflow.
- **Portable.** One engine and router behind two transports (`http.server` and ASGI) and
  three storage backends (JSON, SQLite, optional Postgres).

## Quick start

Requirements: Python 3.11+ (no third-party packages).

```bash
git clone https://github.com/Anish17-Shanu/SentinelIQ-AI-Ops.git
cd SentinelIQ-AI-Ops
python src/server.py
```

Open <http://127.0.0.1:8081/dashboard/>. The API is at `http://127.0.0.1:8081`.

For a keyless, read-only public demo:

```bash
SENTINELIQ_ANONYMOUS_ROLE=viewer python src/server.py
```

Seed demo data (optional):

```bash
python scripts/cli.py seed
```

Score an incident without storing it:

```bash
curl -X POST http://127.0.0.1:8081/v1/score \
  -H "Content-Type: application/json" \
  -d '{"service":"payments-api","criticality":"critical","impacted_users":18500,"error_rate":17.2,"latency_ms":1850,"alerts":23,"category":"availability"}'
```

## API

Versioned routes live under `/v1`; the unversioned aliases remain available for
backwards compatibility. The OpenAPI 3.1 document is served at `GET /openapi.json`.

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| GET | `/health`, `/v1/health` | — | Liveness |
| GET | `/ready`, `/v1/ready` | — | Readiness (checks storage) |
| GET | `/metrics` | admin | Prometheus metrics |
| GET | `/openapi.json` | — | OpenAPI specification |
| GET | `/v1/incidents` | viewer | List with filters, sorting, pagination |
| POST | `/v1/incidents` | responder | Create (supports `Idempotency-Key`) |
| GET | `/v1/incidents/{id}` | viewer | Detail, timeline, playbook |
| DELETE | `/v1/incidents/{id}` | admin | Delete |
| POST | `/v1/incidents/{id}/transitions` | responder | Change lifecycle state |
| GET | `/v1/summary` | viewer | Totals, MTTA/MTTR, open age |
| GET | `/v1/playbooks` | viewer | Configured response playbooks |
| POST | `/v1/score` | viewer | Score without storing |
| POST | `/v1/ingest/{alertmanager\|pagerduty\|generic}` | responder | Alert ingest with dedupe |
| GET | `/v1/export?format=json\|csv` | admin | Export tenant data |
| POST | `/v1/import` | admin | Import JSON export |
| GET/POST | `/v1/cron/tick` | cron secret | Process notification outbox |

List filters: `priority`, `state`, `category`, `owner`, `environment`, `search`,
`sort` (`created_at`, `updated_at`, `score`, `priority`, `service`, `state`),
`order` (`asc`/`desc`), `limit` (1–100), `offset`.

### Authentication and tenants

Authentication is optional locally and recommended everywhere else. Configure
`SENTINELIQ_API_KEYS` as comma-separated `key:role:tenant` entries; roles are
`viewer`, `responder`, and `admin`. Keys must be at least 12 characters. Send a key as
`X-API-Key: <key>` or `Authorization: Bearer <key>`. Each key is bound to a tenant, and
all reads and writes are scoped to that tenant.

When no keys are configured, the service runs as a single anonymous tenant and logs a
warning in production. Set `SENTINELIQ_AUTH_REQUIRED=true` to make missing keys fatal.

For a public, keyless demo set `SENTINELIQ_ANONYMOUS_ROLE=viewer` and leave
`SENTINELIQ_API_KEYS` empty. Visitors can read incidents, summary, and playbooks without
a key; creates, transitions, ingest, admin routes, and `/metrics` return `403`. The default
is `admin` when no keys are configured (local behavior) and `disabled` when keys exist.
`disabled` requires keys, and `SENTINELIQ_AUTH_REQUIRED=true` cannot be combined with a
non-disabled anonymous role. Anonymous requests are scoped to `SENTINELIQ_DEFAULT_TENANT`
and are rate-limited per IP.

| Role | Can do |
| --- | --- |
| public | `/health`, `/ready`, `/openapi.json`, dashboard static files; reads too when `SENTINELIQ_ANONYMOUS_ROLE=viewer` |
| `viewer` | List/detail incidents, summary, playbooks, score preview |
| `responder` | Everything a viewer can, plus create, transition, and ingest alerts |
| `admin` | Everything a responder can, plus delete, export, import, and `/metrics` |
| `cron` | `/v1/cron/tick` using `X-Cron-Secret` (or an admin bearer token) |

An admin key is **not** required for normal use: readers need a viewer key, on-call
responders need a responder key, and only destructive/bulk/reporting operations need
admin. The dashboard itself is a public static page but its data calls require a key,
entered once under "Connection settings" and kept in `sessionStorage`.

### Incident lifecycle

`open` → `acknowledged` → `mitigated` → `resolved`. Acknowledged incidents may resolve
directly without a mitigation step, and `resolved` is terminal. Every transition records
timestamps, the actor label, and an event in the incident timeline. `/v1/summary` derives
MTTA, MTTR, and the oldest open incident from those timestamps.

### Example: create and resolve

```bash
curl -X POST http://127.0.0.1:8081/v1/incidents \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-001" \
  -d '{"service":"search-api","owner":"discovery","environment":"production","description":"5xx spike","criticality":"high","category":"availability","impacted_users":12400,"error_rate":6.8,"latency_ms":1250,"alerts":15}'

curl -X POST http://127.0.0.1:8081/v1/incidents/<id>/transitions \
  -H "Content-Type: application/json" \
  -d '{"state":"acknowledged","note":"on it"}'
```

## Python client

[`sdk/sentineliq_client.py`](sdk/sentineliq_client.py) is a single-file, stdlib-only client:

```python
from sentineliq_client import SentinelIQClient

client = SentinelIQClient("https://sentineliq.example.com", api_key="...")
incident = client.create_incident(service="checkout", category="availability", criticality="critical")
client.transition(incident["id"], "acknowledged", note="paged on-call")
for item in client.iter_incidents(page_size=100, priority="P1"):
    print(item["id"], item["score_100"])
```

Runnable examples: [`examples/quickstart.py`](examples/quickstart.py) and
[`examples/ingest_alert.py`](examples/ingest_alert.py). Point them at any instance with
`SENTINELIQ_URL` and `SENTINELIQ_API_KEY`.

## Dashboard

The dashboard is a dependency-free static page served at `/dashboard/`. It shows live
summary metrics, a filterable and sortable queue, incident details with timeline and
playbook, and lifecycle controls. Paste an API key into the connection panel; it is kept
in `sessionStorage` only. Rendering uses DOM APIs (`textContent`) — there is no
`innerHTML`, so incident text cannot inject markup. Polling pauses when the tab is hidden.

## Scoring model

Five weighted dimensions produce a raw weighted total (maximum 115) and a normalized
0–100 score:

| Dimension | Input | Default weight / behaviour |
| --- | --- | --- |
| Criticality | `low`/`medium`/`high`/`critical` | 8 / 18 / 30 / 42 points |
| User impact | `impacted_users` | `log10(users) * 12`, capped at 25 |
| Error rate | `error_rate` (percent) | `rate * 1.8`, capped at 20 |
| Latency | `latency_ms` | `(latency / 2000 ms) * 16`, capped at 16 |
| Alert pressure | `alerts` | `alerts * 1.1`, capped at 12 |

Priority bands: **P1 ≥ 85**, **P2 ≥ 65**, **P3 ≥ 42**, otherwise **P4**. A lower
`priority_hint` (e.g. `P1`) can escalate a score, never lower it. Weights and bands live in
[`config/weights.json`](config/weights.json); playbooks live in
[`config/playbooks.json`](config/playbooks.json) and support per-service overrides.

## Configuration

All configuration is environment-driven; see [`.env.example`](.env.example) for the full
annotated list. Storage selection:

| Backend | Use it for | Required driver |
| --- | --- | --- |
| `json` | Local development, single small instance | none |
| `sqlite` | Docker/containers, durable single node | none (WAL) |
| `postgres` | Serverless (Vercel), shared deployments | `psycopg[binary]` |

Legacy JSON files are migrated into the current envelope on first read. JSON writes are
atomic and cross-process locked.

## Operations CLI

```bash
python scripts/cli.py seed --count 6
python scripts/cli.py list --priority P1 --state open
python scripts/cli.py export --format csv --output incidents.csv
python scripts/cli.py import --input incidents.json
python scripts/cli.py summary
python scripts/cli.py verify-data
python scripts/cli.py backup
python scripts/cli.py restore --input backups/json-20261029T101500Z.json --yes
python scripts/cli.py tick
```

Run `python scripts/cli.py <command> --help` for options. Load testing:

```bash
python scripts/load_test.py --base-url http://127.0.0.1:8081 \
  --path /health --path /v1/incidents --requests 500 --concurrency 20 --budget-p95-ms 50
```

## Deployment

- **Docker (canonical):** `docker build -t sentineliq-ai-ops .` then
  `docker run -p 8081:8081 -v sentineliq-data:/data sentineliq-ai-ops`.
  Or `docker compose up --build`. The image runs unprivileged, with a healthcheck and a
  `/data` volume for SQLite.
- **Vercel (serverless demo):** `api/[...path].py` exposes the ASGI app; the dashboard is
  published to `public/` by `scripts/prepare_vercel.py`. Use hosted Postgres and set
  `DATABASE_URL` + `SENTINELIQ_STORAGE=postgres`. Vercel Hobby is non-commercial.
- **Netlify:** host `public/dashboard` as a static site and point
  `window.SENTINELIQ_API_BASE` at the API host.

Step-by-step recipes, free-tier limits, and a verification checklist are in
[DEPLOYMENT.md](DEPLOYMENT.md). Day-two operations are in [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check src tests api scripts sdk examples
python -m mypy src sdk
python -m bandit -q -r src -ll
python -m pip_audit -r requirements.txt
python -m coverage run -m unittest discover -s tests && python -m coverage report
python scripts/smoke_test.py --base-url http://127.0.0.1:8081
```

The suite has 126 tests covering the engine, storage contract (JSON + SQLite), HTTP
transport, ASGI parity, auth/RBAC/tenancy, ingest dedupe, outbox delivery, exports, and the
Python client. CI runs lint, types, security scans, coverage, a server smoke test, dashboard
asset checks, and a Docker image smoke test on Python 3.11–3.13.

```
src/        application (stdlib only)
api/        Vercel ASGI entrypoint
sdk/        Python client library
examples/   runnable client examples
dashboard/  static dashboard (no build step)
config/     weights and playbooks
deploy/     Prometheus alert rules
scripts/    CLI, load test, smoke test, Vercel packaging
tests/      unittest suite
docs/       architecture, runbook, onboarding, releasing, ADRs
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the request flow and storage model,
and [ROADMAP.md](ROADMAP.md) for scope and release status.

## Security

Please report vulnerabilities privately as described in [SECURITY.md](SECURITY.md). Never
expose the service without API keys, and terminate TLS at a reverse proxy.

## License

MIT — see [LICENSE](LICENSE). © 2026 Anish Kumar.
