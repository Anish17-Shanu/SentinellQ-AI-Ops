"""Optional Postgres adapter (requires the ``psycopg`` driver).

This adapter lets the serverless deployment use a hosted free-tier Postgres
(Neon, Supabase, Railway, ...). It is optional: the default build has no runtime
dependencies, and SQLite/JSON remain the supported local backends.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from errors import ConfigError
from repository import (
    IncidentRepository,
    _float,
    _int,
    normalize_record,
)
from util import utc_now_iso

try:  # pragma: no cover - exercised only when a driver is installed
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    service TEXT NOT NULL,
    owner TEXT NOT NULL,
    environment TEXT NOT NULL,
    category TEXT NOT NULL,
    state TEXT NOT NULL,
    priority TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    alert_count INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL,
    data JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_tenant_created ON incidents(tenant, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_incidents_tenant_state ON incidents(tenant, state);
CREATE INDEX IF NOT EXISTS idx_incidents_tenant_priority ON incidents(tenant, priority);
CREATE INDEX IF NOT EXISTS idx_incidents_tenant_dedup ON incidents(tenant, dedup_key, state);
CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    incident_id TEXT,
    event_key TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON outbox(status, next_attempt_at);
CREATE TABLE IF NOT EXISTS idempotency (
    tenant TEXT NOT NULL,
    key TEXT NOT NULL,
    response JSONB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant, key)
);
"""


class PostgresRepository(IncidentRepository):
    def __init__(self, dsn: str, assessor: Callable[[dict], dict], tenant_default: str = "default") -> None:
        if psycopg is None:
            raise ConfigError(
                "SENTINELIQ_STORAGE=postgres requires the optional 'psycopg' package (pip install psycopg[binary])"
            )
        self.dsn = dsn
        self.assessor = assessor
        self.tenant_default = tenant_default
        self._init_schema()

    def _connect(self):
        return psycopg.connect(self.dsn, autocommit=False)

    def _init_schema(self) -> None:
        with closing(self._connect()) as connection, connection, connection.cursor() as cursor:
            cursor.execute(SCHEMA)

    def ping(self) -> bool:
        with closing(self._connect()) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True

    @staticmethod
    def _upsert(cursor, tenant: str, record: dict) -> dict:
        cursor.execute(
            """
            INSERT INTO incidents (id, tenant, service, owner, environment, category, state, priority, score,
                                   created_at, updated_at, dedup_key, alert_count, last_seen_at, data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (id) DO UPDATE SET
                tenant=EXCLUDED.tenant, service=EXCLUDED.service, owner=EXCLUDED.owner,
                environment=EXCLUDED.environment, category=EXCLUDED.category, state=EXCLUDED.state,
                priority=EXCLUDED.priority, score=EXCLUDED.score, created_at=EXCLUDED.created_at,
                updated_at=EXCLUDED.updated_at, dedup_key=EXCLUDED.dedup_key,
                alert_count=EXCLUDED.alert_count, last_seen_at=EXCLUDED.last_seen_at, data=EXCLUDED.data
            """,
            (
                record["id"],
                tenant,
                record.get("service", ""),
                record.get("owner", ""),
                record.get("environment", ""),
                record.get("category", ""),
                record.get("state", "open"),
                record.get("priority", ""),
                _float(record.get("score")),
                record.get("created_at", ""),
                record.get("updated_at", ""),
                record.get("dedup_key", ""),
                _int(record.get("alert_count"), 1, minimum=1),
                record.get("last_seen_at", ""),
                json.dumps(record, default=str),
            ),
        )
        return record

    def list_incidents(self, tenant: str, **filters) -> tuple[list[dict], int]:
        clauses = ["tenant = %s"]
        params: list = [tenant]
        for field in ("priority", "state", "category", "owner", "environment"):
            if filters.get(field):
                clauses.append(f"LOWER({field}) = LOWER(%s)")
                params.append(filters[field])
        if filters.get("search"):
            needle = f"%{filters['search']}%"
            clauses.append("(LOWER(service) LIKE LOWER(%s) OR LOWER(owner) LIKE LOWER(%s) OR LOWER(data::text) LIKE LOWER(%s))")
            params.extend([needle, needle, needle])
        where = " AND ".join(clauses)
        sort = filters.get("sort", "created_at")
        order = "ASC" if str(filters.get("order", "desc")).lower() == "asc" else "DESC"
        if sort == "priority":
            order_by = f"CASE priority WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 WHEN 'P4' THEN 4 ELSE 5 END {order}"
        elif sort == "score":
            order_by = f"score {order}"
        elif sort in {"created_at", "updated_at", "service", "state"}:
            order_by = f"{sort} {order}"
        else:
            order_by = f"created_at {order}"
        limit = max(0, int(filters.get("limit", 50)))
        offset = max(0, int(filters.get("offset", 0)))
        with closing(self._connect()) as connection, connection.cursor() as cursor:
            # where/order_by use allowlisted column names; all values are parameterized.
            cursor.execute(f"SELECT COUNT(*) FROM incidents WHERE {where}", params)  # nosec B608
            total = int(cursor.fetchone()[0])
            cursor.execute(
                f"SELECT data FROM incidents WHERE {where} ORDER BY {order_by} LIMIT %s OFFSET %s",  # nosec B608
                [*params, limit, offset],
            )
            rows = cursor.fetchall()
        return [row[0] for row in rows], total

    def get_incident(self, tenant: str, incident_id: str) -> dict | None:
        with closing(self._connect()) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT data FROM incidents WHERE tenant = %s AND id = %s", (tenant, incident_id))
            row = cursor.fetchone()
        return row[0] if row else None

    def create_incident(self, tenant: str, record: dict) -> dict:
        with closing(self._connect()) as connection, connection, connection.cursor() as cursor:
            return self._upsert(cursor, tenant, record)

    def update_incident(self, tenant: str, record: dict) -> dict:
        with closing(self._connect()) as connection, connection, connection.cursor() as cursor:
            return self._upsert(cursor, tenant, record)

    def delete_incident(self, tenant: str, incident_id: str) -> bool:
        with closing(self._connect()) as connection, connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM incidents WHERE tenant = %s AND id = %s", (tenant, incident_id))
            return cursor.rowcount > 0

    def find_open_incident(self, tenant: str, dedup_key: str, since_iso: str) -> dict | None:
        with closing(self._connect()) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    SELECT data FROM incidents
                    WHERE tenant = %s AND dedup_key = %s AND state != 'resolved' AND last_seen_at >= %s
                    ORDER BY last_seen_at DESC LIMIT 1
                    """,
                (tenant, dedup_key, since_iso),
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def all_incidents(self, tenant: str) -> list[dict]:
        with closing(self._connect()) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT data FROM incidents WHERE tenant = %s", (tenant,))
            return [row[0] for row in cursor.fetchall()]

    def add_outbox(self, entry: dict) -> None:
        with closing(self._connect()) as connection, connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    INSERT INTO outbox (id, tenant, incident_id, event_key, payload, status, attempts,
                                        next_attempt_at, created_at, sent_at, last_error)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_key) DO NOTHING
                    """,
                (
                    entry["id"],
                    entry["tenant"],
                    entry.get("incident_id"),
                    entry["event_key"],
                    json.dumps(entry.get("payload", {}), default=str),
                    entry.get("status", "pending"),
                    int(entry.get("attempts", 0)),
                    entry.get("next_attempt_at", entry.get("created_at", utc_now_iso())),
                    entry.get("created_at", utc_now_iso()),
                    entry.get("sent_at"),
                    entry.get("last_error"),
                ),
            )

    def due_outbox(self, now_iso: str, limit: int = 20) -> list[dict]:
        with closing(self._connect()) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    SELECT id, tenant, incident_id, event_key, payload, status, attempts,
                           next_attempt_at, created_at, sent_at, last_error
                    FROM outbox WHERE status = 'pending' AND next_attempt_at <= %s
                    ORDER BY next_attempt_at LIMIT %s
                    """,
                (now_iso, limit),
            )
            rows = cursor.fetchall()
        keys = ("id", "tenant", "incident_id", "event_key", "payload", "status", "attempts", "next_attempt_at", "created_at", "sent_at", "last_error")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def update_outbox(self, entry: dict) -> None:
        with closing(self._connect()) as connection, connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    UPDATE outbox SET payload = %s::jsonb, status = %s, attempts = %s,
                                      next_attempt_at = %s, sent_at = %s, last_error = %s
                    WHERE id = %s
                    """,
                (
                    json.dumps(entry.get("payload", {}), default=str),
                    entry.get("status", "pending"),
                    int(entry.get("attempts", 0)),
                    entry.get("next_attempt_at", utc_now_iso()),
                    entry.get("sent_at"),
                    entry.get("last_error"),
                    entry["id"],
                ),
            )

    def get_idempotent(self, tenant: str, key: str) -> dict | None:
        with closing(self._connect()) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT response FROM idempotency WHERE tenant = %s AND key = %s", (tenant, key))
            row = cursor.fetchone()
        return row[0] if row else None

    def put_idempotent(self, tenant: str, key: str, response: dict) -> None:
        with closing(self._connect()) as connection, connection, connection.cursor() as cursor:
            cursor.execute(
                """
                    INSERT INTO idempotency (tenant, key, response, created_at) VALUES (%s, %s, %s::jsonb, %s)
                    ON CONFLICT (tenant, key) DO UPDATE SET response = EXCLUDED.response
                    """,
                (tenant, key, json.dumps(response, default=str), utc_now_iso()),
            )


def import_legacy_json(path: Path, repository: IncidentRepository, tenant: str, assessor) -> int:
    """Utility used by the CLI to copy a legacy JSON store into Postgres/SQLite."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw if isinstance(raw, list) else raw.get("incidents", [])
    count = 0
    for index, item in enumerate(items):
        record = normalize_record(item, tenant, assessor, index)
        repository.create_incident(tenant, record)
        count += 1
    return count
