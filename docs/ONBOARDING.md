# Onboarding

A guided path from a clean checkout to a confident first change. Budget about an hour.

## 1. What you are working on

SentinelIQ takes incident signals, scores them with a deterministic rule-based engine, and
tracks them through an on-call lifecycle. It is deliberately small: standard library Python,
a static dashboard, and swappable storage.

Read in this order:

1. [README](../README.md) — features and quick start
2. [ROADMAP](../ROADMAP.md) — what is shipped and what is next
3. [docs/ARCHITECTURE.md](ARCHITECTURE.md) — request flow, data model, scoring
4. `src/engine.py` — the scoring function and configuration validation
5. `src/service.py` — lifecycle, ingest, idempotency, outbox
6. `src/router.py` — routes, auth, limits, static assets

## 2. Run it

```bash
python --version                 # need 3.11+
python src/server.py
```

Open <http://127.0.0.1:8081/dashboard/>. If the queue is empty:

```bash
python scripts/cli.py seed
```

Try the API:

```bash
curl -s http://127.0.0.1:8081/v1/summary | python -m json.tool
curl -s http://127.0.0.1:8081/openapi.json | python -m json.tool | head -40
```

## 3. Run the tests

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Then the same checks CI runs:

```bash
python -m ruff check src tests api scripts
python -m mypy src
python -m coverage run -m unittest discover -s tests && python -m coverage report
```

If everything passes, you have a working baseline.

## 4. Make a first change

Good starter tasks:

- Add a filter to the dashboard (follow the existing `filters` object and query builder).
- Add a playbook override in `config/playbooks.json` and confirm it in
  `GET /v1/playbooks`.
- Add a scoring test that documents an edge case (zero users, huge alert counts).
- Add a metric counter to a service path and assert it in a test.

Workflow:

1. Find or create the matching ID in [ROADMAP.md](../ROADMAP.md).
2. Write the test first where practical; use `tests/support.py` helpers.
3. Implement the smallest change that passes.
4. Run lint, types, coverage, and the relevant tests.
5. For UI changes, check 320/390/768/1440 px widths and the browser console.
6. Update the roadmap checkbox with the date.

## 5. Understand the guardrails

- **No third-party runtime imports in `src/`.** Optional drivers are lazy imports listed in
  `requirements.txt`.
- **No LLM calls, ever.** Scoring must stay deterministic and explainable.
- **UTC only.** Use `util.utc_now()`; timestamps serialize as ISO-8601 with `Z`.
- **Errors are typed.** Raise `ValidationError`, `NotFoundError`, `ConflictError`,
  `AuthError`, or `ForbiddenError`; the router renders the JSON envelope.
- **Tenant isolation is a storage invariant.** Never filter incidents in the router or
  service by tenant manually; pass the tenant to every repository call.
- **Dashboard is CSP-safe.** No inline scripts, no `innerHTML`; build nodes with `el()`.
- **Both transports must behave the same.** Add HTTP tests to `tests/test_http.py`, and check
  parity in `tests/test_asgi.py` when routing or headers change.

## 6. Where things live

| I want to... | Look at |
| --- | --- |
| Change scoring weights | `config/weights.json`, `src/engine.py` |
| Change response playbooks | `config/playbooks.json` |
| Add an endpoint | `src/router.py` route table + handler, then `tests/test_http.py` |
| Change lifecycle rules | `src/service.py` `transition`, state sets in `src/repository.py` |
| Add a storage backend | Implement `IncidentRepository` in `src/repository.py` style |
| Change the dashboard | `dashboard/index.html`, `dashboard/app.js`, `dashboard/style.css` |
| Change configuration | `src/config.py` + `.env.example` + docs |
| Add an operational task | `scripts/cli.py` |
| Deploy | `Dockerfile`, `vercel.json`, [DEPLOYMENT.md](../DEPLOYMENT.md) |

## 7. Troubleshooting first run

| Problem | Fix |
| --- | --- |
| `Address already in use` | Set `PORT=8082` or stop the other process |
| Tests import errors | Run from the repository root; discovery needs `-s tests` |
| Data looks stale | Delete `.tmp` test data or point `SENTINELIQ_DATA_FILE` elsewhere |
| Dashboard empty | Seed data; check the API key in the connection panel |
| `mypy` errors after adding code | Annotate new dicts/lists or use explicit locals |

## 8. Ask for help

Open an issue with the command you ran, the output, and the roadmap ID you are working on.
Security questions go through [SECURITY.md](../SECURITY.md).
