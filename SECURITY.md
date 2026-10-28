# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.x | Yes |

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Report them privately to the
maintainer, Anish Kumar, through GitHub's private vulnerability reporting on
<https://github.com/Anish17-Shanu/SentinelIQ-AI-Ops/security/advisories/new>, or by direct
message if you already have a private channel.

Include:

- the affected version or commit,
- reproduction steps or a proof of concept,
- the impact you believe is possible,
- any suggested remediation.

You can expect an acknowledgement within 72 hours and a remediation plan within 7 days.
Please allow time for a fix before public disclosure.

## Security model

SentinelIQ is a single-process HTTP service that stores incidents in JSON, SQLite, or
Postgres. It does not execute user-supplied code, make LLM calls, or fetch remote URLs on
behalf of users.

### Authentication

- API keys are configured through `SENTINELIQ_API_KEYS` as `key:role:tenant` entries.
- Only SHA-256 digests are held in memory; the raw key is never written to disk or logs.
- Keys must be at least 12 characters. Generate them with at least 128 bits of entropy,
  for example `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- Roles: `viewer` (read), `responder` (read + write + ingest), `admin` (delete, export,
  import, metrics). Authorization is enforced per route.
- Tenants are bound to keys. Every repository query filters by the key's tenant, so one
  team cannot read another team's incidents.

### Transport

- TLS is expected to terminate at a reverse proxy or platform edge. Set
  `SENTINELIQ_TLS=true` to emit HSTS.
- Security headers on every response: CSP (no inline script), `nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, restrictive
  `Permissions-Policy`, and COOP.
- CORS is deny-by-default. Configure exact origins in `SENTINELIQ_CORS_ORIGINS`; avoid
  `*` in production.
- The dashboard stores the API key in `sessionStorage` only and never writes it to the DOM.

### Input handling

- JSON bodies are capped by `SENTINELIQ_MAX_BODY_BYTES` (default 256 KiB) and oversized
  requests return `413`.
- Invalid JSON and invalid fields return `400` with a consistent
  `{error: {code, message, field, request_id}}` schema.
- Unknown routes and static path traversal attempts return `404`; the static handler
  resolves paths and rejects anything outside the dashboard directory.
- Incident text is rendered with DOM `textContent`; the dashboard contains no `innerHTML`
  sinks.
- All timestamps are UTC ISO-8601 (`Z` suffix).

### Secrets

- Never commit `.env`, API keys, webhook URLs, `DATABASE_URL`, or `SENTRY_DSN`.
  `.gitignore` excludes `.env*` except `.env.example`.
- Docker images contain no secrets; inject them at runtime.
- Rotate a key by replacing its entry in `SENTINELIQ_API_KEYS` and restarting. Old keys
  stop working immediately.

### Denial of service

- Per-key/per-IP token buckets return `429` with `Retry-After`.
- Request sockets have a read timeout, and handler threads are bounded by the platform.
- The outbox is processed in small batches and retries with exponential backoff.

## Deployment checklist

- [ ] `SENTINELIQ_API_KEYS` set, `SENTINELIQ_AUTH_REQUIRED=true`
- [ ] TLS terminated in front, `SENTINELIQ_TLS=true`
- [ ] `SENTINELIQ_CRON_SECRET` set if cron is enabled
- [ ] CORS limited to known origins
- [ ] `/metrics` and export/import restricted to admins
- [ ] Backups configured and a restore drill completed
- [ ] Logs shipped somewhere durable; alerts on 5xx rate and readiness failures
- [ ] Rate limits sized for expected traffic

## Known limitations

- Rate limits are in-process; multi-replica deployments need a shared limiter.
- API keys are static; there is no built-in rotation UI or OIDC integration.
- The service is not designed for hostile multi-tenant internet exposure without a proxy
  and network controls; treat a public deployment as a demo.
