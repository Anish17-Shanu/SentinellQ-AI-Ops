# ADR 0002 — One transport-neutral router behind two transports

**Status:** Accepted · 2026-10-29

## Context

The canonical deployment is a long-running container, while the public demo runs as a
serverless function. Duplicating routing, auth, and validation for each transport would let
behavior drift, and the roadmap requires both targets to pass the same smoke test.

## Decision

Implement all routing, authentication, rate limiting, validation, error mapping, and static
file serving once in `src/router.py`, operating on a transport-neutral `Request`/`Response`
pair. `src/server.py` adapts `http.server`; `src/asgi.py` adapts ASGI. Neither transport
contains domain logic.

## Consequences

- A single test suite covers behavior; a small parity suite checks both adapters.
- New endpoints and headers are implemented once.
- Transport-specific concerns (body size pre-checks, socket timeouts, HEAD handling) stay in
  thin adapters.
- The router's synchronous design is wrapped in `asyncio.to_thread` for ASGI; fine for the
  workload, but it is not an async-native stack.
