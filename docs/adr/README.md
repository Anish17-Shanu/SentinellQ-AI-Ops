# Architecture Decision Records

Short records of decisions that shape the codebase. Format: context, decision,
consequences. Add a new ADR rather than rewriting an accepted one.

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-deterministic-scoring.md) | Deterministic weighted scoring instead of ML | Accepted |
| [0002](0002-transport-neutral-router.md) | One router behind two transports | Accepted |
| [0003](0003-storage-adapters.md) | Repository interface with JSON, SQLite, Postgres | Accepted |
| [0004](0004-api-keys-and-tenants.md) | API keys bound to roles and tenants | Accepted |
| [0005](0005-zero-paid-services.md) | Zero LLM and zero required paid services | Accepted |
| [0006](0006-outbox-over-background-threads.md) | Outbox table plus cron instead of worker threads | Accepted |
