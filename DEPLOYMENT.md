# Deployment

SentinelIQ runs as a single Python process with no third-party packages. This guide covers
the two supported targets from the roadmap plus the static-dashboard option.

| Target | Role | Transport | Storage |
| --- | --- | --- | --- |
| Docker container | Canonical, self-hosted | `http.server` (ASGI-capable) | SQLite on a volume (or JSON) |
| Vercel (Hobby) | Public demo / portfolio | ASGI serverless function | Hosted Postgres |
| Netlify | Static dashboard only | — | Same API host |

Both targets must pass the [smoke test](#smoke-test) on every release.

---

## 1. Local

```bash
python src/server.py
```

- Dashboard: <http://127.0.0.1:8081/dashboard/>
- API: <http://127.0.0.1:8081>
- Data: `data/incidents.json` (seeded with demo incidents)
- Seeding: `python scripts/cli.py seed`

Environment variables are optional locally; see [`.env.example`](.env.example).

---

## 2. Docker (canonical)

The image is unprivileged (`UID 10001`), uses SQLite at `/data/sentineliq.db`, and ships a
`HEALTHCHECK` against `/health`.

```bash
docker build -t sentineliq-ai-ops .
docker run -d --name sentineliq \
  -p 8081:8081 \
  -v sentineliq-data:/data \
  -e SENTINELIQ_API_KEYS="viewer-key-please-change:viewer:default,responder-key-please-change:responder:default,admin-key-please-change:admin:default" \
  -e SENTINELIQ_AUTH_REQUIRED=true \
  sentineliq-ai-ops
```

Or with Compose:

```bash
docker compose up --build
```

Verify:

```bash
docker inspect --format '{{.State.Health.Status}}' sentineliq   # healthy
curl -fsS http://127.0.0.1:8081/ready
```

Notes:

- The default `HOST` inside the image is `0.0.0.0` and `PORT` is `8081`. Publishing the
  container port is what makes it reachable from the host: `-p 8081:8081`.
- SQLite lives on the named volume. Without `-v /data`, data disappears when the container
  is removed.
- `SENTINELIQ_ENV=production` is set in the image, so logs are JSON unless overridden.
- To use the JSON backend instead, mount a directory and set
  `SENTINELIQ_STORAGE=json SENTINELIQ_DATA_FILE=/data/incidents.json`.
- Container hardening used by this image: non-root user, no build toolchain, no secrets
  baked in, healthcheck, read-only app directory. For stronger isolation add
  `--read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges` and keep
  `/data` writable.

---

## 3. Vercel (serverless demo)

`api/[...path].py` is the ASGI entrypoint. `vercel.json` runs
`scripts/prepare_vercel.py` at build time, which copies the dashboard into `public/` and
points it at the `/api` prefix.

### 3.1 Create a hosted Postgres

Any free-tier Postgres works (Neon, Supabase, Railway). Copy the pooled connection string;
it must allow SSL, e.g. `postgresql://user:pass@host/db?sslmode=require`.

Serverless functions have no durable disk, so **do not use JSON or SQLite on Vercel**.

### 3.2 Configure environment variables

| Variable | Example | Why |
| --- | --- | --- |
| `SENTINELIQ_ENV` | `production` | JSON logs, production warnings |
| `SENTINELIQ_STORAGE` | `postgres` | Persistent hosted database |
| `DATABASE_URL` | `postgresql://...` | Connection string |
| `SENTINELIQ_API_KEYS` | `key:role:tenant,...` | Auth and tenant isolation — required for a public URL |
| `SENTINELIQ_AUTH_REQUIRED` | `true` | Fail closed |
| `SENTINELIQ_CRON_SECRET` | long random string | Protects `/v1/cron/tick` |
| `CRON_SECRET` | same value | Vercel sends it automatically to cron invocations |
| `SENTINELIQ_NOTIFY_WEBHOOK_URL` | optional Slack webhook | Outbound notifications |
| `SENTINELIQ_TLS` | `true` | Emit HSTS behind Vercel's TLS |

The Python runtime installs [`requirements.txt`](requirements.txt), which contains only
the optional `psycopg` driver.

### 3.3 Deploy

```bash
npm i -g vercel
vercel link
vercel env add SENTINELIQ_API_KEYS production
# ...add the remaining variables...
vercel --prod
```

Dashboard: `https://<project>.vercel.app/dashboard/`
API: `https://<project>.vercel.app/api/v1/health`

### 3.4 Cron on Hobby

`vercel.json` defines a **daily** `/api/v1/cron/tick` job, which is the Hobby plan limit.
For more frequent outbox delivery use an external scheduler (cron-job.org, GitHub Actions
schedule, or your own cron) that calls:

```bash
curl -fsS -H "X-Cron-Secret: $SENTINELIQ_CRON_SECRET" \
  https://<project>.vercel.app/api/v1/cron/tick
```

If `SENTINELIQ_CRON_SECRET` is unset the endpoint deliberately returns 404.

### 3.5 Serverless limits to plan for

- **Cold starts:** ASGI import plus first request measured at ~0.23 s locally (Python 3.14,
  warm filesystem) and gated at 2 s in CI by `scripts/cold_start.py`. The function is
  configured with 512 MB and a 30 s maximum duration; record the hosted value once after
  the first deploy.
- **Function timeout:** large imports should be split. A single request handles at most
  1,000 records (`MAX_IMPORT_RECORDS`).
- **No background threads after a response:** notification retries run through the cron
  endpoint, not in-process.
- **Hobby terms:** non-commercial use only. Monetizing requires revisiting the constraint
  in [ROADMAP.md](ROADMAP.md).

---

## 4. Netlify (static dashboard only)

Netlify is supported only for hosting the dashboard. Python functions are not part of this
path.

1. Copy the `dashboard/` folder into a site repository (or set it as the publish
   directory).
2. Edit `dashboard/config.js` so requests reach your API:

   ```js
   window.SENTINELIQ_API_BASE = "https://your-api-host.example.com";
   ```

3. Add the Netlify site origin to `SENTINELIQ_CORS_ORIGINS` on the API host, for example
   `SENTINELIQ_CORS_ORIGINS=https://sentineliq.netlify.app`.
4. Users must enter an API key in the dashboard connection panel.

Do not use `scripts/prepare_vercel.py` for this target: it sets the API base to `/api`,
which only exists on Vercel.

---

## 5. TLS and reverse proxy

Terminate TLS in front of the container and forward the original scheme. Set
`SENTINELIQ_TLS=true` so responses include HSTS.

**Caddy**

```caddyfile
sentineliq.example.com {
    reverse_proxy 127.0.0.1:8081
}
```

**Nginx**

```nginx
server {
    listen 443 ssl http2;
    server_name sentineliq.example.com;

    ssl_certificate     /etc/letsencrypt/live/sentineliq.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sentineliq.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 30s;
    }
}
```

Rate limiting is in-process. If you run several replicas, add a proxy-level limiter or a
shared limiter, because each replica has its own token buckets.

---

## 6. Free-tier limits (as of 2026-10)

| Concern | Docker on a free VM | Vercel Hobby | Netlify |
| --- | --- | --- | --- |
| Persistent disk | Volume, yes | No | No |
| Background work | In-process + cron | Cron only (daily on Hobby) | n/a |
| Function timeout | n/a | 30 s configured maximum | n/a |
| Cold starts | None | Yes | n/a |
| Commercial use | Allowed | **Not allowed** | Allowed (static) |
| Storage | SQLite file | Hosted Postgres (free tier) | n/a |

---

## 7. Smoke test

Run after every deployment:

```bash
python scripts/smoke_test.py --base-url https://your-host --api-key "$ADMIN_KEY"
```

Equivalent manual checks:

```bash
BASE=https://your-host

curl -fsS "$BASE/health"
curl -fsS "$BASE/ready"
curl -fsS "$BASE/dashboard/" -o /dev/null -w "dashboard %{http_code}\n"
curl -fsS "$BASE/openapi.json" -o /dev/null -w "openapi %{http_code}\n"

KEY="your-responder-key"
curl -fsS -H "X-API-Key: $KEY" "$BASE/v1/incidents?limit=1"

curl -fsS -X POST "$BASE/v1/score" \
  -H "Content-Type: application/json" \
  -d '{"service":"smoke","criticality":"high","impacted_users":1000,"error_rate":2,"latency_ms":500,"alerts":3}'

# Authenticated write, transition, and cleanup
ID=$(curl -fsS -X POST "$BASE/v1/incidents" -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"service":"smoke-test","criticality":"medium","impacted_users":10,"error_rate":0.1,"latency_ms":100,"alerts":1}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['id'])")
curl -fsS -X POST "$BASE/v1/incidents/$ID/transitions" -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"state":"acknowledged"}'
curl -fsS -X POST "$BASE/v1/incidents/$ID/transitions" -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" -d '{"state":"resolved"}'
```

Expected: `200` for health/ready/openapi, `200` for the dashboard, `201` for create, and
`200` for both transitions. A `401` means the key is missing; `403` means the role is too
low; `404` on cron means the cron secret is not configured.

---

## 8. Backup and restore

Backups are covered in [docs/RUNBOOK.md](docs/RUNBOOK.md):

```bash
python scripts/cli.py backup                     # JSON/SQLite: consistent snapshot
python scripts/cli.py restore --input <file> --yes
```

For Postgres, use the provider's automated backups (Neon/Supabase) or
`pg_dump`; the CLI's Postgres backup path writes a logical JSON export.

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Connection refused from the host | Bound to `127.0.0.1` inside the container | Keep `HOST=0.0.0.0` in containers and publish the port |
| `401` on every write | Key missing or shorter than 12 characters | Send `X-API-Key`; check startup config errors |
| `403` | Role below requirement (e.g. viewer writing) | Use a responder or admin key |
| `429` with `Retry-After` | Rate limit | Raise `SENTINELIQ_RATE_LIMIT_RPM` or wait |
| `503` from `/ready` | Storage unreachable | Check `DATABASE_URL`, disk permissions, or DB limits |
| `404` from `/v1/cron/tick` | `SENTINELIQ_CRON_SECRET` unset | Set it and resend the same value |
| Dashboard loads but lists nothing | Wrong API base or missing key | Check `config.js`; enter a key in the connection panel |
| Vercel data resets | JSON/SQLite on ephemeral disk | Switch to Postgres |

---

## 10. Automated deploys (GitHub Actions)

Pushing a `v*` tag runs [`release.yml`](.github/workflows/release.yml): it re-runs the gates,
pushes a signed-by-provenance GHCR image with an SBOM, scans it with Trivy, and creates the
GitHub release. The two deploy workflows are manual dispatches so a rollout is always an
explicit decision.

| Workflow | Deploys | Required secrets |
| --- | --- | --- |
| [`deploy-container.yml`](.github/workflows/deploy-container.yml) | Pulls the tag on an SSH host and runs `docker compose up -d --no-build` | `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PATH`, `DEPLOY_SSH_KEY`, optional `DEPLOY_KNOWN_HOSTS`, `DEPLOY_BASE_URL`, `DEPLOY_ADMIN_KEY` |
| [`deploy-vercel.yml`](.github/workflows/deploy-vercel.yml) | `vercel pull/build/deploy --prod` | `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `SENTINELIQ_BASE_URL`, `SENTINELIQ_ADMIN_KEY` |

Both workflows run `scripts/smoke_test.py` against the live URL after the rollout and fail the
job on any unexpected status. If the secrets are absent the job exits with a notice instead of
failing, so forks stay green. Roll out the container first, then the serverless demo; see
[docs/RELEASING.md](docs/RELEASING.md) for the full checklist.
