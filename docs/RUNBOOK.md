# Runbook

Operational procedures for running SentinelIQ. Commands assume the repository root and a
Unix shell unless noted; Windows equivalents are noted where they differ.

## Service overview

| Item | Value |
| --- | --- |
| Process | `python src/server.py` (single process, threaded) |
| Default bind | `127.0.0.1:8081` (official image: `0.0.0.0:8081`) |
| Liveness | `GET /health` |
| Readiness | `GET /ready` (checks the data store) |
| Metrics | `GET /metrics` (admin key) |
| Logs | stdout; JSON when `SENTINELIQ_LOG_FORMAT=json` |
| Data | JSON file, SQLite (`/data/sentineliq.db` in Docker), or Postgres |

## Start, stop, restart

### Local

```bash
python src/server.py
```

Stop with `Ctrl+C`. `SIGTERM` and `SIGINT` trigger a graceful shutdown: the listener stops,
in-flight requests finish, and the repository closes.

### Docker

```bash
docker compose up -d --build
docker compose logs -f sentineliq
docker compose restart sentineliq
docker compose down            # keeps the named volume
```

`docker compose down -v` deletes the data volume. Treat it as destructive.

### systemd

```ini
# /etc/systemd/system/sentineliq.service
[Unit]
Description=SentinelIQ AI Ops
After=network.target

[Service]
Type=simple
User=sentineliq
WorkingDirectory=/opt/sentineliq
EnvironmentFile=/etc/sentineliq/sentineliq.env
ExecStart=/usr/bin/python3 src/server.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sentineliq
journalctl -u sentineliq -f
```

## Scheduled work

The only recurring job is the notification outbox tick. Delivery is at-least-once and
idempotent per `event_key`, so overlapping schedulers are harmless.

### cron

```cron
*/5 * * * * cd /opt/sentineliq && /usr/bin/python3 scripts/cli.py tick >> /var/log/sentineliq-tick.log 2>&1
```

Or over HTTP:

```cron
*/5 * * * * curl -fsS -H "X-Cron-Secret: $SENTINELIQ_CRON_SECRET" http://127.0.0.1:8081/v1/cron/tick > /dev/null
```

### systemd timer

```ini
# /etc/systemd/system/sentineliq-tick.service
[Unit]
Description=SentinelIQ outbox tick
[Service]
Type=oneshot
User=sentineliq
WorkingDirectory=/opt/sentineliq
ExecStart=/usr/bin/python3 scripts/cli.py tick
```

```ini
# /etc/systemd/system/sentineliq-tick.timer
[Unit]
Description=Run SentinelIQ outbox tick every five minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
```

### Windows Task Scheduler

```powershell
schtasks /Create /TN "SentinelIQ tick" /SC MINUTE /MO 5 `
  /TR "cmd /c cd /d D:\Project\SentinellQ-AI-Ops && python scripts\cli.py tick"
```

### Vercel

Set `SENTINELIQ_CRON_SECRET` and `CRON_SECRET` to the same value. Hobby cron runs once per
day; use an external scheduler for five-minute ticks. See [DEPLOYMENT.md](../DEPLOYMENT.md).

## Health checks

```bash
curl -fsS http://127.0.0.1:8081/health
curl -fsS http://127.0.0.1:8081/ready
python scripts/cli.py verify-data
```

`/health` proves the process is alive. `/ready` returns `503` when storage is unreachable.
Alert on readiness failure for two consecutive checks, not on a single blip.

## Metrics

`/metrics` requires an admin key. A minimal scrape config:

```yaml
scrape_configs:
  - job_name: sentineliq
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/sentineliq-admin-key
    static_configs:
      - targets: ["sentineliq:8081"]
```

```promql
# Request rate by route
sum by (route) (rate(sentineliq_http_requests_total[5m]))

# 5xx ratio
sum(rate(sentineliq_http_requests_total{status="5xx"}[5m]))
  / sum(rate(sentineliq_http_requests_total[5m]))

# p95 latency by route
histogram_quantile(0.95,
  sum by (le, route) (rate(sentineliq_http_request_duration_seconds_bucket[5m])))

# Incidents created by priority
sum by (priority) (increase(sentineliq_incidents_created_total[1h]))

# Rate limiting and replays
rate(sentineliq_rate_limited_total[5m])
rate(sentineliq_idempotent_replays_total[5m])

# Notification delivery outcomes
sum by (status) (rate(sentineliq_notifications_total[15m]))
```

Suggested alerts (importable rules: [`deploy/prometheus/sentineliq-alerts.yml`](../deploy/prometheus/sentineliq-alerts.yml)):

| Alert | Condition | Action |
| --- | --- | --- |
| High error ratio | 5xx ratio > 2% for 10 min | Check logs for `unhandled_error`, `storage_error` |
| Readiness failing | `/ready` 503 for 2 checks | Restore storage connectivity |
| Latency budget | p95 > 250 ms for 15 min | Inspect slow routes, DB size, rate limiting |
| Dead notifications | `sentineliq_notifications_total{status="dead"}` increases | Fix webhook URL/credentials, replay manually |
| No requests | request rate drops to zero during business hours | Check proxy, TLS certificate, process |

## Logs

Every request emits one structured line with `request_id`, `method`, `path`, `route`,
`status`, `duration_ms`, `tenant`, and `role`. Clients may send `X-Request-Id`; otherwise one
is generated and returned in the response header. To trace a user report:

```bash
grep '"request_id":"<id>"' app.log
```

Other events worth alerting on: `storage_error`, `unhandled_error`, `shutdown_requested`,
`demo data seeded`.

## Troubleshooting playbooks

### `/ready` returns 503

1. `python scripts/cli.py verify-data` — reports the backend and data problems.
2. JSON backend: confirm the file exists, is valid JSON, and is writable by the service user.
3. SQLite: check disk space and file permissions; look for `sqlite3.OperationalError` in logs.
4. Postgres: test `DATABASE_URL` connectivity and SSL mode from the host.

### Lock timeout on the JSON store

`TimeoutError: timed out acquiring lock` means a writer held the file lock for more than the
timeout, or a crashed process left an open handle. The lock is advisory and released by the
OS when the process exits. Restart the service; if the error persists, check for a stuck
process or a network-mounted data directory (unsupported — keep the data file on local disk).

### Corrupt JSON store

1. Stop the service. Do not delete the file.
2. Copy it aside: `cp data/incidents.json data/incidents.corrupt-$(date +%s).json`.
3. Restore from the newest backup: `python scripts/cli.py restore --input backups/<file> --yes`.
4. If no backup exists, validate what can be recovered with a JSON parser and import the
   `incidents` array through `python scripts/cli.py import --input <repaired>.json`.

### 429 responses

Rate limiting is working as intended. Check `X-RateLimit-Limit` and `Retry-After`. Raise
`SENTINELIQ_RATE_LIMIT_RPM`, or issue each client its own API key so buckets do not share an
IP. Multi-replica deployments need a proxy-level limiter.

### Outbox backlog or dead letters

```bash
python scripts/cli.py tick --json
```

- `failed` increments: the webhook is unreachable or rejected the payload. Verify the URL and
  test with `curl`.
- `dead`: five failed attempts. Fix the destination, then re-enqueue by updating the outbox
  record's `status` to `pending` and `attempts` to `0` in the store, or trigger a new incident
  event.

### Dashboard is blank or unauthorized

1. Open the browser console: `401` means no key; `403` means the key's role is too low.
2. Confirm the API base: `window.SENTINELIQ_API_BASE` should be empty for same-origin hosting
   and `/api` on Vercel.
3. Check CSP errors — inline scripts are intentionally blocked.

### Vercel data disappears

The deployment used JSON or SQLite on ephemeral disk. Switch to Postgres
(`SENTINELIQ_STORAGE=postgres`, `DATABASE_URL=...`).

## Data protection

### Backup policy

| Backend | Method | Suggested schedule | RPO | RTO |
| --- | --- | --- | --- | --- |
| JSON | `python scripts/cli.py backup` (locked file copy) | hourly, keep 7 days + weekly 8 weeks | ≤ 1 h | ~15 min |
| SQLite | `python scripts/cli.py backup` (online `sqlite3.backup`) | hourly, keep 7 days + weekly 8 weeks | ≤ 1 h | ~15 min |
| Postgres | Provider PITR / `pg_dump` | continuous + daily dump | provider-dependent | ~30 min |

Backups land in `backups/` by default, which is gitignored. Copy them off-host (object
storage, another volume) — a backup on the same disk does not survive disk loss.

```bash
# Nightly cron example
15 2 * * * cd /opt/sentineliq && /usr/bin/python3 scripts/cli.py backup >> /var/log/sentineliq-backup.log 2>&1
```

### Restore

```bash
python scripts/cli.py restore --input backups/sqlite-20261029T021500Z.db --yes
```

- JSON and SQLite restores copy the previous live file to `*.pre-restore-<timestamp>` first.
- Postgres restores import records with upsert semantics.
- Restart the service after a SQLite restore.

### Restore drill (required quarterly)

1. Take a backup: `python scripts/cli.py backup`.
2. Stop the service.
3. Note the current incident count: `python scripts/cli.py list --limit 1 --json | head`.
4. Restore the backup.
5. Run `python scripts/cli.py verify-data` and compare counts.
6. Start the service and run the smoke test in [DEPLOYMENT.md](../DEPLOYMENT.md).
7. Record the drill date and actual RTO in the team log.

## Key rotation

1. Generate a new key: `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Add it to `SENTINELIQ_API_KEYS` alongside the old key; restart or roll the deployment.
3. Move clients to the new key.
4. Remove the old entry and restart again.

Keys are matched by SHA-256 digest; removing one revokes it immediately.

## Upgrade and rollback

1. Read [CHANGELOG.md](../CHANGELOG.md) for breaking changes.
2. Take a backup.
3. Pull the new version and run the test suite: `python -m coverage run -m unittest discover -s tests`.
4. Restart the service; `/ready` should be `200`.
5. Run the smoke test.
6. Rollback: check out the previous tag, restore the backup if the schema changed, restart.

The JSON envelope migrates forward automatically and refuses files with a newer version.
Downgrades across a schema change require the pre-upgrade backup.

## Capacity notes

- JSON storage rewrites the whole envelope per mutation. Keep it under a few thousand
  incidents; move to SQLite or Postgres beyond that.
- SQLite is a single-writer database; throughput is fine for small teams, not for heavy
  ingest bursts.
- The in-process token bucket is per replica. Use a shared limiter or proxy for horizontal
  scaling.
- Serverless cold starts are the main latency risk on Vercel; keep payloads small and avoid
  large imports in a single request.

## Incident response checklist (for SentinelIQ itself)

1. Confirm scope: `/health`, `/ready`, dashboard, and metrics.
2. Capture evidence: request IDs from clients, recent deploy time, log window.
3. Mitigate: roll back the deploy, restore storage, or disable the failing integration
   (clear `SENTINELIQ_NOTIFY_WEBHOOK_URL` and restart).
4. Preserve data: take a backup before any repair.
5. Communicate status in the team channel; note the customer impact.
6. Follow up with a postmortem, a roadmap item, and a regression test.
