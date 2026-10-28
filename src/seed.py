"""Realistic demo data for local development and container bootstrapping."""

from __future__ import annotations

from datetime import timedelta

from util import utc_now

SAMPLES = [
    {
        "service": "payments-api",
        "owner": "payments",
        "environment": "production",
        "description": "Card authorization failures spiking after deploy 2026.10.29-3.",
        "criticality": "critical",
        "category": "availability",
        "impacted_users": 18500,
        "error_rate": 17.2,
        "latency_ms": 1850,
        "alerts": 23,
        "priority_hint": "P1",
    },
    {
        "service": "identity-gateway",
        "owner": "identity",
        "environment": "production",
        "description": "Credential stuffing pattern detected against the login endpoint.",
        "criticality": "critical",
        "category": "security",
        "impacted_users": 6200,
        "error_rate": 8.4,
        "latency_ms": 980,
        "alerts": 11,
    },
    {
        "service": "analytics-worker",
        "owner": "data",
        "environment": "staging",
        "description": "Batch aggregation queue depth growing; results delayed.",
        "criticality": "medium",
        "category": "latency",
        "impacted_users": 900,
        "error_rate": 2.1,
        "latency_ms": 2400,
        "alerts": 6,
    },
    {
        "service": "search-api",
        "owner": "discovery",
        "environment": "production",
        "description": "Search latency and 5xx rate elevated on the primary cluster.",
        "criticality": "high",
        "category": "availability",
        "impacted_users": 12400,
        "error_rate": 6.8,
        "latency_ms": 1250,
        "alerts": 15,
    },
    {
        "service": "billing-worker",
        "owner": "payments",
        "environment": "production",
        "description": "Invoice export wrote partial records before failing validation.",
        "criticality": "high",
        "category": "data",
        "impacted_users": 3100,
        "error_rate": 4.2,
        "latency_ms": 700,
        "alerts": 7,
    },
    {
        "service": "cdn-edge",
        "owner": "platform-core",
        "environment": "production",
        "description": "Origin health checks intermittent in the EU region.",
        "criticality": "low",
        "category": "latency",
        "impacted_users": 350,
        "error_rate": 0.6,
        "latency_ms": 320,
        "alerts": 3,
    },
]


def _iso(moment) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def seed_demo(service, tenant: str, count: int = 6) -> list[dict]:
    created = []
    now = utc_now()
    for index, sample in enumerate(SAMPLES[: max(1, min(count, len(SAMPLES)))]):
        record, _ = service.create(tenant, sample, "seed")
        created_at = now - timedelta(hours=(index + 1) * 3)
        record["created_at"] = _iso(created_at)
        record["last_seen_at"] = record["created_at"]
        record["updated_at"] = record["created_at"]
        events = [{"type": "created", "at": record["created_at"], "actor": "seed", "detail": {"source": "seed"}}]

        if index % 4 == 1:
            acknowledged = created_at + timedelta(minutes=20)
            record["state"] = "acknowledged"
            record["acknowledged_at"] = _iso(acknowledged)
            record["updated_at"] = record["acknowledged_at"]
            events.append({"type": "state_changed", "at": record["acknowledged_at"], "actor": "seed", "detail": {"from": "open", "to": "acknowledged"}})
        elif index % 4 == 2:
            acknowledged = created_at + timedelta(minutes=15)
            mitigated = created_at + timedelta(minutes=40)
            record["state"] = "mitigated"
            record["acknowledged_at"] = _iso(acknowledged)
            record["mitigated_at"] = _iso(mitigated)
            record["updated_at"] = record["mitigated_at"]
            events.append({"type": "state_changed", "at": record["acknowledged_at"], "actor": "seed", "detail": {"from": "open", "to": "acknowledged"}})
            events.append({"type": "state_changed", "at": record["mitigated_at"], "actor": "seed", "detail": {"from": "acknowledged", "to": "mitigated"}})
        elif index % 4 == 3:
            acknowledged = created_at + timedelta(minutes=10)
            resolved = created_at + timedelta(minutes=90)
            record["state"] = "resolved"
            record["acknowledged_at"] = _iso(acknowledged)
            record["mitigated_at"] = _iso(created_at + timedelta(minutes=45))
            record["resolved_at"] = _iso(resolved)
            record["updated_at"] = record["resolved_at"]
            events.append({"type": "state_changed", "at": record["acknowledged_at"], "actor": "seed", "detail": {"from": "open", "to": "acknowledged"}})
            events.append({"type": "state_changed", "at": record["mitigated_at"], "actor": "seed", "detail": {"from": "acknowledged", "to": "mitigated"}})
            events.append({"type": "state_changed", "at": record["resolved_at"], "actor": "seed", "detail": {"from": "mitigated", "to": "resolved"}})

        record["events"] = events
        record.update(service.assess(record))
        service.repository.update_incident(tenant, record)
        created.append(record)
    return created
