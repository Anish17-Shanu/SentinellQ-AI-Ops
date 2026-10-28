# SentinelIQ AI Ops — Production Roadmap

Living checklist for taking this from portfolio demo to a real, deployable product.
Update it as work lands — see [How to use this file](#how-to-use-this-file).

**Last updated:** 2026-10-29

## Why this file exists

The project works today as an explainable scoring demo. The gap to "usable, useful, production-grade" is tracked below in five phases. Each item has a stable ID (`P1-03`) you can reference in commits, PRs, and issues.

| Phase | Goal | Items | Done |
| --- | --- | --- | --- |
| 0. Baseline | What already works (verified) | 7 | 7/7 |
| 1. Deployable | Runs anywhere, config-driven, no data loss | 13 | 13/13 |
| 2. Trustworthy | Correct, tested, safe with bad input | 13 | 13/13 |
| 3. Useful | Real on-call workflows end to end | 14 | 14/14 |
| 4. Production-grade | Secure, observable, operable | 20 | 20/20 |
| 5. Product | Sustainable as a product | 8 | 7/8 |
| **Total** | | **75** | **74/75** |

## Release mapping

| Release | Includes | Meaning |
| --- | --- | --- |
| v0.1 | Phase 0 | Demo that runs locally |
| v1.0 | Phase 1 + 2 | Deployable and trustworthy |
| v1.1 | Phase 3 | Genuinely useful for a small team |
| v2.0 | Phase 4 | Safe to run in production |
| v2.x | Phase 5 | Sustainable product |

The code and release automation are at v2.0 scope. The first hosted deployment, TLS certificate, registry push, and git tag still need external credentials or an explicit go-ahead (see [Verification status](#verification-status-2026-10-29)).

## Hard constraints

These must always hold, in every phase. Any change that would violate one is out of scope, regardless of which phase it appears in.

- **C-01 Zero LLM / zero AI inference dependency.** No model calls, API keys, or inference bills. Scoring stays deterministic and explainable.
- **C-02 Zero required paid services.** The app must run end to end on free, self-hosted, or free-tier infrastructure. Third-party integrations (Slack, PagerDuty, Sentry, hosting, managed databases) are optional adapters, never requirements, and must degrade gracefully when absent. Generic webhooks are the default integration path. Free tiers that forbid commercial use (e.g. Vercel Hobby) are acceptable for demo use only; monetizing reopens this constraint as an explicit decision, not a silent exception.
- **C-03 Offline-capable core.** API, dashboard, scoring, and storage must work with no outbound internet access.
- **C-04 Portable by design.** Engine and repository code contains no platform-specific logic. Transport (`http.server`, ASGI) and storage (JSON, SQLite, Postgres/Turso) are swappable adapters.

## Target deployment topology (decided 2026-10-29)

**Both (portable).** The Docker container is the canonical build; the serverless deployment is a public demo adapter over the same engine.

| Target | Role | Runtime | Storage |
| --- | --- | --- | --- |
| Docker container | Canonical, self-hosted | `http.server` (ASGI-capable) | JSON locally, SQLite in container |
| Vercel (free tier) | Public demo URL | Python serverless (ASGI) + static dashboard | Hosted free Postgres/Turso |
| Netlify | Static dashboard only | — | Same API host |

Rules that follow from this choice:

- Serverless has no persistent disk and no long-running process: storage must sit behind a hosted free DB, background work must move to cron, and handlers must finish inside the platform timeout.
- Vercel Hobby is non-commercial: demo and portfolio use only. Monetizing triggers the C-02 decision above.
- Both targets must pass the same smoke test on every release (`scripts/smoke_test.py`).

---

## Phase 0 — Baseline (verified 2026-10-29)

- [x] **B-01** Explainable scoring engine with five weighted dimensions and P1–P4 bands
- [x] **B-02** Six HTTP routes: `/health`, `/playbooks`, `/incidents` (GET/POST), `/summary`, `/score`
- [x] **B-03** Dashboard serves at `/dashboard/` with summary, create form, and queue
- [x] **B-04** Local JSON persistence with owner/environment/priority filters
- [x] **B-05** Engine unit tests pass
- [x] **B-06** CI compiles sources and runs tests
- [x] **B-07** Path traversal guard on dashboard asset serving

---

## Phase 1 — Deployable (v1.0)

- [x] **P1-01** Read `HOST`, `PORT`, `DATA_FILE` from environment with validation and local-friendly defaults — done 2026-10-29
- [x] **P1-02** Bind `0.0.0.0` inside containers (env-driven); `127.0.0.1` local default — done 2026-10-29
- [x] **P1-03** Normalize incidents on read so owner/environment/priority filters work on shipped data — done 2026-10-29
- [x] **P1-04** Atomic locked writes (temp file + `os.replace` + lock) so concurrent POSTs cannot lose incidents — done 2026-10-29
- [x] **P1-05** Validate `priority_hint` against P1–P4 and wire it into the dashboard — done 2026-10-29
- [x] **P1-06** Replace deprecated `datetime.utcnow()` with timezone-aware UTC timestamps — done 2026-10-29
- [x] **P1-07** Fix the README run-path typo and settle naming (`SentinelIQ`) — done 2026-10-29
- [x] **P1-08** Add `.dockerignore`, a non-root `USER`, and a `HEALTHCHECK` to the Dockerfile — done 2026-10-29
- [x] **P1-09** Verify and correct the Docker instructions in `DEPLOYMENT.md` (host port reachable) — done 2026-10-29
- [x] **P1-10** Score semantics: weighted total plus normalized 0–100 value — done 2026-10-29
- [x] **P1-11** Repository contract with JSON (local), SQLite (container), and Postgres (serverless) adapters — done 2026-10-29
- [x] **P1-12** ASGI adapter exposing the same routes, engine logic unchanged — done 2026-10-29
- [x] **P1-13** Deployment recipes for both targets, including free-tier limits — done 2026-10-29

**Gate:** Dockerfile and Compose are covered by CI's image build/smoke job (first run on the next push); ASGI adapter passes the parity suite locally; shipped incidents are filterable; concurrent-write tests pass. **Hosted** gate (reachable image on a free host + hosted DB) is pending deployment credentials.

---

## Phase 2 — Trustworthy (v1.0)

- [x] **P2-01** Repository tests: create, list/filter, persistence round-trip, concurrent writes — done 2026-10-29
- [x] **P2-02** HTTP handler tests: status codes, invalid JSON, unknown routes, oversized bodies — done 2026-10-29
- [x] **P2-03** Request body size cap (413) and socket timeouts — done 2026-10-29
- [x] **P2-04** Global exception handling: JSON 500 with `request_id`, no stack traces to clients — done 2026-10-29
- [x] **P2-05** Consistent validation error schema (`error`, `field`, `request_id`) and documentation — done 2026-10-29
- [x] **P2-06** Pagination (`limit`/`offset`) with a maximum page size — done 2026-10-29
- [x] **P2-07** Sorting and documented filter semantics — done 2026-10-29
- [x] **P2-08** Eliminate stored XSS (DOM-only rendering) and add a CSP — done 2026-10-29
- [x] **P2-09** UTC ISO-8601 everywhere; timestamp semantics documented — done 2026-10-29
- [x] **P2-10** Cache headers / ETag / 304 for dashboard assets — done 2026-10-29
- [x] **P2-11** Coverage thresholds in CI (82%, floor 70) — done 2026-10-29
- [x] **P2-12** Lint (`ruff`), types (`mypy`), security scans (`bandit`, `pip-audit`) in CI — done 2026-10-29
- [x] **P2-13** Transport parity tests across `http.server` and ASGI — done 2026-10-29

**Gate:** CI enforces lint, types, security, and coverage; malformed input never produces a 500; no `innerHTML`; both transports return identical results for the shared suite. Verified locally; CI runs on push.

---

## Phase 3 — Useful (v1.1)

- [x] **P3-01** Alert ingest endpoint accepting Alertmanager v4 and PagerDuty-style payloads — done 2026-10-29
- [x] **P3-02** Dedup/correlation on service+category within a window; increments `alert_count` — done 2026-10-29
- [x] **P3-03** Lifecycle with timestamps and actor — done 2026-10-29
- [x] **P3-04** MTTA/MTTR and open-incident age in `/summary` and the dashboard — done 2026-10-29
- [x] **P3-05** Dashboard filters (owner, environment, category, priority, state) plus search — done 2026-10-29
- [x] **P3-06** Incident detail view with timeline and interactive action checklist — done 2026-10-29
- [x] **P3-07** Create-form polish: units, inline validation, helper text, `priority_hint` — done 2026-10-29
- [x] **P3-08** Loading/empty/error states and live polling — done 2026-10-29
- [x] **P3-09** Outbound notifications (Slack/webhook) for P1/P2 with retry and idempotency — done 2026-10-29
- [x] **P3-10** Per-service/team playbook overrides in configuration — done 2026-10-29
- [x] **P3-11** Configurable scoring weights with validation and safe defaults — done 2026-10-29
- [x] **P3-12** Export/import incidents (JSON/CSV) — done 2026-10-29
- [x] **P3-13** Seed/demo data generator with realistic incidents and timelines — done 2026-10-29
- [x] **P3-14** Ops CLI: `seed`, `list`, `export`, `import`, `verify-data`, `summary`, `tick`, `backup`, `restore` — done 2026-10-29

**Gate:** an Alertmanager-style alert produces a scored incident; acknowledging and resolving updates MTTA/MTTR; the demo is reproducible with `scripts/cli.py seed`. Verified live on 2026-10-29.

---

## Phase 4 — Production-grade (v2.0)

### Security
- [x] **P4-01** Authentication for write endpoints (API keys) — done 2026-10-29
- [x] **P4-02** Role model (viewer/responder/admin) with enforcement — done 2026-10-29
- [x] **P4-03** TLS via reverse proxy (Caddy/Nginx recipes), HSTS when `SENTINELIQ_TLS=true`, CSP, nosniff, frame options — done 2026-10-29 (hosted certificate pending)
- [x] **P4-04** Locked-down CORS; no cookie auth, so CSRF is not applicable — done 2026-10-29
- [x] **P4-05** Rate limiting per key/IP with `429` and `Retry-After` — done 2026-10-29
- [x] **P4-13** Idempotency keys for `POST /incidents` and ingest — done 2026-10-29
- [x] **P4-19** Timers, retries, and notifications driven by cron `tick()`, not in-process threads — done 2026-10-29

### Operability
- [x] **P4-06** Structured JSON logging (`request_id`, method, path, status, `duration_ms`) — done 2026-10-29
- [x] **P4-07** Prometheus `/metrics`: request rate, latency histogram, incidents by priority — done 2026-10-29
- [x] **P4-08** Separate liveness and readiness endpoints — done 2026-10-29
- [x] **P4-09** Graceful shutdown on `SIGTERM` — done 2026-10-29
- [x] **P4-17** Optional Sentry error reporting plus importable Prometheus alert rules (`deploy/prometheus/sentineliq-alerts.yml`) — done 2026-10-29
- [x] **P4-18** Load test with performance budgets — done 2026-10-29
- [x] **P4-20** Cold-start and function-timeout budgets for serverless — done 2026-10-29: `scripts/cold_start.py` measures import + first `/health` in fresh subprocesses and gates at 2 s in CI (measured 207 ms import, 226 ms total p50); 512 MB, 30 s `maxDuration`, 1,000-row import cap. Hosted value is recorded after the first deploy

### Data
- [x] **P4-11** SQLite (WAL) and Postgres adapters behind the repository contract; migrations; indexes — done 2026-10-29
- [x] **P4-12** Backup/restore runbook, retention policy, documented RPO/RTO, quarterly drill — done 2026-10-29
- [x] **P4-14** Tenant/team isolation — done 2026-10-29

### Delivery
- [x] **P4-10** Env-based secret management; no secrets in repo or images — done 2026-10-29
- [x] **P4-15** Container hardening: digest-pinned base, non-root, read-only rootfs in Compose, `cap_drop: ALL`, `no-new-privileges`, resource limits — done 2026-10-29
- [x] **P4-16** CI/CD: image build/smoke in CI, `release.yml` pushes the GHCR image with SBOM and provenance, Trivy gates on CRITICAL/HIGH, and opt-in container/Vercel deploy workflows run the live smoke test — done 2026-10-29 (first registry push and hosted rollout pending credentials)

**Gate:** code-level controls verified locally (auth/RBAC/tenant tests, live 29-check API suite, restore drill, load budget). Public TLS deployment and live alerting still require hosting credentials.

---

## Phase 5 — Product (v2.x)

- [x] **P5-01** API versioning (`/v1`) and OpenAPI spec — done 2026-10-29
- [x] **P5-02** Python client library (`sdk/`) plus runnable examples (`examples/`) — done 2026-10-29
- [x] **P5-03** Docs: architecture, ADRs, runbooks, onboarding, releasing — done 2026-10-29
- [x] **P5-04** `SECURITY.md`, `CONTRIBUTING.md`, `LICENSE`, `CODE_OF_CONDUCT.md` — done 2026-10-29
- [ ] **P5-05** Release process: semver, `CHANGELOG.md`, tagged images — **in progress**: process documented in `docs/RELEASING.md`; first tag and registry push pending
- [x] **P5-06** Responsive and accessibility audit — done 2026-10-29 (1440/390/320 px, no overflow; axe-core 0 violations desktop and mobile)
- [x] **P5-07** Privacy-respecting usage telemetry — closed 2026-10-29: decision is no telemetry. The self-hosted build and the demo send nothing; revisit only if a hosted offering is ever added
- [x] **P5-08** Packaging/pricing and hosted-offering decision — closed 2026-10-29: no paid tier or packaging. Distribution is the Docker image plus a non-commercial demo, consistent with C-02

**Gate:** versioned API, client library, and onboarding docs exist; a tagged release and external developer trial are pending.

---

## Verification status (2026-10-29)

Evidence from the final local pass:

| Area | Result |
| --- | --- |
| Lint / types / security | `ruff` clean, `mypy` clean (20 files), `bandit` 0 issues, `pip-audit` no known vulnerabilities |
| Tests | 126 tests pass; coverage 82% (floor 70%) |
| Live API (auth enabled) | 29/29 checks: RBAC, tenant isolation, lifecycle, dedupe, idempotency, pagination, ETag, export/import, cron, metrics, OpenAPI |
| CLI | seed/list/summary/verify/backup/restore/tick against JSON; SQLite covered by tests |
| Dashboard | Playwright at 1440/390/320 px: 8 cards, detail panel, zero horizontal overflow, no console errors; axe-core 0 violations |
| Load | 0 failures across 1,500 requests; `/health` 942 rps p95 8.6 ms, `/v1/incidents` 484 rps p95 25.2 ms, `/v1/summary` 487 rps p95 16.1 ms |
| Smoke test | `scripts/smoke_test.py` passes with and without auth (6/6, 7/7) |
| Cold start | `scripts/cold_start.py` (3 fresh processes): 207 ms import, 226 ms total p50, 234 ms max, HTTP 200; CI gate 2 s |
| CI/CD | `ci.yml` builds and smoke-tests the image; `release.yml` (tag or manual) publishes GHCR with SBOM/provenance, Trivy gate, GitHub release; container and Vercel deploy workflows are opt-in and skip cleanly without secrets. All four workflows parse; first live run waits for a push/tag |
| Docker | Dockerfile, digest pin, and Compose hardening written; local daemon was unavailable, so CI's `docker` job builds and smoke-tests the image on push |

**Pending external credentials or accounts:**

1. Deploy the container or Vercel app and run the hosted smoke test.
2. Provision hosted Postgres/Turso and verify the serverless target against it.
3. Terminate TLS with a real certificate and confirm HSTS.
4. Set `SENTRY_DSN` if error tracking is wanted; import the Prometheus rules where metrics are scraped.
5. Approve the commit and cut `v2.0.0` (git tag triggers `release.yml`, which pushes the image) to close P5-05.

---

## Non-goals (deliberately out of scope)

- Replacing the rule-based scorer with an ML model — explainability is the differentiator
- Rewriting the API in Node/TypeScript just to run on Netlify Functions — Python-capable hosts are the supported path
- Multi-region high availability
- Native mobile app
- Full ITSM/ticketing replacement (integrate with them instead)
- Usage telemetry for the self-hosted build — no data leaves the instance

---

## How to use this file

**Status conventions**

- `- [ ]` not started
- `- [ ] **P1-03** ... — **in progress**` (keep the box unchecked, append the marker)
- `- [x] **P1-03** ... — done 2026-11-02 (#14)` (checked, with date and optional PR/commit)

**Update protocol**

1. Every PR that completes an item references its ID in the title or description (e.g. `P1-04: atomic JSON writes`).
2. When it merges, check the box and append `— done YYYY-MM-DD`.
3. New work discovered mid-flight gets a new ID in the right phase — never renumber existing IDs.
4. Keep the progress table and "Last updated" date in sync with the checkboxes.
5. Don't silently drop items: move them to Non-goals with a one-line reason instead.
6. Phase gates are release criteria — do not mark a phase complete until its gate is demonstrated.
7. Every change is checked against [Hard constraints](#hard-constraints) before merge. A violation is a bug, not a tradeoff.
8. Both deployment targets must stay green. A change that breaks the container or the serverless adapter is not done.
