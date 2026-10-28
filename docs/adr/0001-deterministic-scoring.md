# ADR 0001 — Deterministic weighted scoring instead of ML

**Status:** Accepted · 2026-10-29

## Context

On-call prioritization needs to be trusted by the people acting on it. An ML model would
require training data, inference infrastructure, ongoing cost, and would produce decisions
that are hard to justify in a postmortem. The roadmap also mandates zero LLM dependency and
zero required paid services.

## Decision

Score incidents with a fixed weighted formula over five dimensions: criticality, user
impact, error rate, latency, and alert pressure. Normalize to 0–100, map to P1–P4 bands,
and emit a human-readable explanation naming the largest contributors. Configuration lives
in `config/weights.json` and is validated at startup.

## Consequences

- Every score is reproducible and auditable; tests are exact.
- No training data, inference runtime, or per-request cost.
- Operators can tune weights without a deployment.
- The model cannot discover non-linear patterns on its own; new signals must be added
  explicitly as dimensions.
- A `priority_hint` exists as an operator override that can only escalate.
