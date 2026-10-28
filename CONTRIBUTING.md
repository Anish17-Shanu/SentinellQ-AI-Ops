# Contributing

Thanks for improving SentinelIQ. This project is intentionally small: standard library
Python, no build step for the dashboard, and a deterministic scoring engine.

## Ground rules

Every change must preserve the [hard constraints](ROADMAP.md#hard-constraints):

1. **Zero LLM / zero inference dependency.** No model calls in the core path.
2. **Zero required paid services.** Optional integrations must degrade gracefully.
3. **Offline-capable core.** API, dashboard, scoring, and storage work without internet.
4. **Portable.** Engine and repository stay platform-agnostic; transports and storage are
   adapters.

If a feature cannot meet these, it belongs in a separate adapter or is out of scope.

## Setup

```bash
git clone https://github.com/Anish17-Shanu/SentinelIQ-AI-Ops.git
cd SentinelIQ-AI-Ops
python -m pip install -r requirements-dev.txt
python src/server.py
```

Use Python 3.11 or newer. Runtime code must not add third-party imports.

## Before opening a PR

```bash
python -m ruff check src tests api scripts
python -m mypy src
python -m coverage run -m unittest discover -s tests
python -m coverage report          # must stay >= 70% overall
```

Also exercise the change manually against a running server, and for dashboard changes check
the layout at 320 px, 390 px, 768 px, and 1440 px widths with no horizontal overflow.

## Code conventions

- Standard library only in `src/`; optional drivers (e.g. `psycopg`) go in
  `requirements.txt` and must be imported lazily.
- Type hints on public functions; `mypy` with `check_untyped_defs` must pass.
- All timestamps are timezone-aware UTC via `util.utc_now()`; never use `datetime.utcnow()`.
- Errors raised to clients use `ApiError` subclasses so the JSON shape stays consistent.
- New secrets are read through `config.load_settings` and documented in `.env.example`.
- Dashboard rendering uses the `el()` DOM helper; do not introduce `innerHTML` or inline
  scripts (CSP forbids them).
- Keep comments for non-obvious *why* only.

## Tests

- Add tests next to the existing suites in `tests/`; use `tests/support.py` helpers.
- Repository changes must pass the shared contract tests against both JSON and SQLite.
- Transport changes must pass the same behavior through the `http.server` and ASGI suites.
- Prefer deterministic tests: inject clocks or use timestamps far from boundaries.

## Commit and PR style

- Reference the roadmap ID when one applies, e.g. `P3-02: dedupe alert bursts`.
- Keep commits focused; describe *why* in the message.
- Update `ROADMAP.md` in the same PR: check the box and append `— done YYYY-MM-DD`.
- Never commit data files, backups, generated `public/`, or secrets.
- Interview-prep and explanation material is deliberately excluded from this repository.

## Reporting bugs

Open an issue with the version, environment, reproduction steps, expected behavior, and
actual behavior. Security issues go through [SECURITY.md](SECURITY.md), not the tracker.

## License

By contributing you agree that your contribution is licensed under the MIT License.
