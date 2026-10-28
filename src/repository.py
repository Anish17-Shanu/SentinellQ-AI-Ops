"""Storage adapters for incidents, notification outbox, and idempotency records.

The repository is deliberately transport- and scoring-agnostic. An ``assessor``
callable supplied by the bootstrap layer fills derived fields (priority, score,
explanation) when legacy records are imported.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from util import atomic_write_text, file_lock, utc_now_iso

VALID_STATES = {"open", "acknowledged", "mitigated", "resolved"}
VALID_PRIORITIES = {"P1", "P2", "P3", "P4"}
VALID_CRITICALITIES = {"low", "medium", "high", "critical"}
VALID_CATEGORIES = {"availability", "security", "data", "latency"}
PRIORITY_ORDER = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
ENVELOPE_VERSION = 2

Assessor = Callable[[dict], dict]


class StorageError(RuntimeError):
    """Raised when persisted data is unreadable or corrupt."""


def _text(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def _int(value, default: int = 0, minimum: int = 0) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _float(value, default: float = 0.0, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def normalize_record(raw: dict, tenant_default: str, assessor: Assessor, index: int = 0) -> dict:
    """Coerce any historical/imported record into the canonical shape."""

    raw = raw if isinstance(raw, dict) else {}
    service = _text(raw.get("service"), "unknown-service")
    criticality = _text(raw.get("criticality"), "medium").lower()
    if criticality not in VALID_CRITICALITIES:
        criticality = "medium"
    category = _text(raw.get("category"), "availability").lower()
    if category not in VALID_CATEGORIES:
        category = "availability"
    state = _text(raw.get("state"), "open").lower()
    if state not in VALID_STATES:
        state = "open"
    priority = _text(raw.get("priority")).upper()
    if priority not in VALID_PRIORITIES:
        priority = ""

    created_at = _text(raw.get("created_at"), utc_now_iso())
    record: dict = {
        "id": _text(raw.get("id")) or f"seed-{uuid5(NAMESPACE_URL, f'{index}:{json.dumps(raw, sort_keys=True, default=str)}')}",
        "tenant": _text(raw.get("tenant"), tenant_default),
        "service": service,
        "owner": _text(raw.get("owner"), "platform-core"),
        "environment": _text(raw.get("environment"), "production").lower(),
        "description": _text(raw.get("description")),
        "criticality": criticality,
        "category": category,
        "impacted_users": _int(raw.get("impacted_users")),
        "error_rate": _float(raw.get("error_rate")),
        "latency_ms": _float(raw.get("latency_ms")),
        "alerts": _int(raw.get("alerts")),
        "priority_hint": _text(raw.get("priority_hint")).upper() if _text(raw.get("priority_hint")).upper() in VALID_PRIORITIES else "",
        "state": state,
        "created_at": created_at,
        "updated_at": _text(raw.get("updated_at"), created_at),
        "acknowledged_at": _text(raw.get("acknowledged_at")) or None,
        "mitigated_at": _text(raw.get("mitigated_at")) or None,
        "resolved_at": _text(raw.get("resolved_at")) or None,
        "last_seen_at": _text(raw.get("last_seen_at"), created_at),
        "alert_count": _int(raw.get("alert_count"), 1, minimum=1),
        "dedup_key": _text(raw.get("dedup_key"), f"{service}|{category}"),
        "source": _text(raw.get("source"), "seed"),
        "events": raw.get("events") if isinstance(raw.get("events"), list) else [],
    }
    if not record["events"]:
        record["events"] = [{"type": "imported", "at": created_at, "actor": "system", "detail": {}}]

    assessment = assessor(record)
    for key in ("priority", "score", "score_100", "confidence", "risk_band", "explanation", "factors", "dimensions", "actions"):
        record[key] = assessment.get(key)
    if priority:
        # Preserve an explicitly stored priority only when it matches the current assessment band.
        record["priority"] = priority
    return record


class IncidentRepository(ABC):
    @abstractmethod
    def ping(self) -> bool: ...

    @abstractmethod
    def list_incidents(self, tenant: str, **filters) -> tuple[list[dict], int]: ...

    @abstractmethod
    def get_incident(self, tenant: str, incident_id: str) -> dict | None: ...

    @abstractmethod
    def create_incident(self, tenant: str, record: dict) -> dict: ...

    @abstractmethod
    def update_incident(self, tenant: str, record: dict) -> dict: ...

    @abstractmethod
    def delete_incident(self, tenant: str, incident_id: str) -> bool: ...

    @abstractmethod
    def find_open_incident(self, tenant: str, dedup_key: str, since_iso: str) -> dict | None: ...

    @abstractmethod
    def all_incidents(self, tenant: str) -> list[dict]: ...

    @abstractmethod
    def add_outbox(self, entry: dict) -> None: ...

    @abstractmethod
    def due_outbox(self, now_iso: str, limit: int = 20) -> list[dict]: ...

    @abstractmethod
    def update_outbox(self, entry: dict) -> None: ...

    @abstractmethod
    def get_idempotent(self, tenant: str, key: str) -> dict | None: ...

    @abstractmethod
    def put_idempotent(self, tenant: str, key: str, response: dict) -> None: ...

    def close(self) -> None:
        return None


def _matches(record: dict, filters: dict) -> bool:
    for field in ("priority", "state", "category"):
        wanted = filters.get(field)
        if wanted and str(record.get(field, "")).lower() != str(wanted).lower():
            return False
    for field in ("owner", "environment"):
        wanted = filters.get(field)
        if wanted and str(record.get(field, "")).lower() != str(wanted).lower():
            return False
    search = filters.get("search")
    if search:
        needle = str(search).lower()
        haystack = " ".join(
            str(record.get(key, "")) for key in ("service", "owner", "description", "explanation")
        ).lower()
        if needle not in haystack:
            return False
    return True


def _sort_records(records: list[dict], sort: str, order: str) -> list[dict]:
    reverse = str(order).lower() != "asc"

    def priority_key(record: dict) -> tuple:
        return (PRIORITY_ORDER.get(record.get("priority", ""), 9), record.get("created_at", ""))

    def score_key(record: dict) -> tuple:
        return (_float(record.get("score")), record.get("created_at", ""))

    def field_key(record: dict) -> tuple:
        return (str(record.get(sort, "")),)

    def created_key(record: dict) -> tuple:
        return (str(record.get("created_at", "")),)

    if sort == "priority":
        key = priority_key
    elif sort == "score":
        key = score_key
    elif sort in {"updated_at", "created_at", "service", "state"}:
        key = field_key
    else:
        key = created_key
    return sorted(records, key=key, reverse=reverse)


class JsonRepository(IncidentRepository):
    def __init__(self, path: Path, assessor: Assessor, tenant_default: str = "default") -> None:
        self.path = Path(path)
        self.assessor = assessor
        self.tenant_default = tenant_default
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_initialized()

    # -- lifecycle -----------------------------------------------------
    def _lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".lock")

    def _new_envelope(self) -> dict:
        return {"version": ENVELOPE_VERSION, "incidents": [], "outbox": [], "idempotency": {}}

    def _ensure_initialized(self) -> None:
        with self._lock:
            if not self.path.exists():
                atomic_write_text(self.path, json.dumps(self._new_envelope(), indent=2))
                return
            self._load_locked(write_migration=True)

    def _load_locked(self, write_migration: bool = False) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(f"Unable to read incident store: {exc}") from exc

        if isinstance(raw, list):
            envelope = self._new_envelope()
            envelope["incidents"] = [
                normalize_record(item, self.tenant_default, self.assessor, index)
                for index, item in enumerate(raw)
            ]
            if write_migration:
                self._write_locked(envelope)
            return envelope

        if not isinstance(raw, dict):
            raise StorageError("Incident store must contain an object or a list")

        envelope = {
            "version": ENVELOPE_VERSION,
            "incidents": raw.get("incidents") if isinstance(raw.get("incidents"), list) else [],
            "outbox": raw.get("outbox") if isinstance(raw.get("outbox"), list) else [],
            "idempotency": raw.get("idempotency") if isinstance(raw.get("idempotency"), dict) else {},
        }
        migrated = False
        normalized = []
        for index, item in enumerate(envelope["incidents"]):
            if isinstance(item, dict) and item.get("id") and item.get("state") and item.get("priority"):
                normalized.append(item)
            else:
                normalized.append(normalize_record(item, self.tenant_default, self.assessor, index))
                migrated = True
        envelope["incidents"] = normalized
        if migrated and write_migration:
            self._write_locked(envelope)
        return envelope

    def _write_locked(self, envelope: dict) -> None:
        envelope["version"] = ENVELOPE_VERSION
        atomic_write_text(self.path, json.dumps(envelope, indent=2, ensure_ascii=False, default=str))

    def _read(self) -> dict:
        with self._lock:
            return self._load_locked()

    def _mutate(self, mutator: Callable[[dict], object]):
        with self._lock, file_lock(self._lock_path()):
            envelope = self._load_locked()
            result = mutator(envelope)
            self._write_locked(envelope)
            return result

    # -- incidents -----------------------------------------------------
    def ping(self) -> bool:
        with self._lock:
            self._load_locked()
        return True

    def list_incidents(self, tenant: str, **filters) -> tuple[list[dict], int]:
        envelope = self._read()
        records = [item for item in envelope["incidents"] if item.get("tenant") == tenant and _matches(item, filters)]
        records = _sort_records(records, filters.get("sort", "created_at"), filters.get("order", "desc"))
        total = len(records)
        offset = max(0, int(filters.get("offset", 0)))
        limit = max(0, int(filters.get("limit", 50)))
        return records[offset : offset + limit], total

    def get_incident(self, tenant: str, incident_id: str) -> dict | None:
        for item in self._read()["incidents"]:
            if item.get("tenant") == tenant and item.get("id") == incident_id:
                return item
        return None

    def create_incident(self, tenant: str, record: dict) -> dict:
        def mutate(envelope):
            envelope["incidents"] = [item for item in envelope["incidents"] if item.get("id") != record["id"]]
            envelope["incidents"].insert(0, record)
            return record

        return self._mutate(mutate)

    def update_incident(self, tenant: str, record: dict) -> dict:
        def mutate(envelope):
            for index, item in enumerate(envelope["incidents"]):
                if item.get("tenant") == tenant and item.get("id") == record["id"]:
                    envelope["incidents"][index] = record
                    break
            else:
                envelope["incidents"].insert(0, record)
            return record

        return self._mutate(mutate)

    def delete_incident(self, tenant: str, incident_id: str) -> bool:
        def mutate(envelope):
            before = len(envelope["incidents"])
            envelope["incidents"] = [
                item
                for item in envelope["incidents"]
                if not (item.get("tenant") == tenant and item.get("id") == incident_id)
            ]
            return len(envelope["incidents"]) < before

        return self._mutate(mutate)

    def find_open_incident(self, tenant: str, dedup_key: str, since_iso: str) -> dict | None:
        candidates = [
            item
            for item in self._read()["incidents"]
            if item.get("tenant") == tenant
            and item.get("dedup_key") == dedup_key
            and item.get("state") != "resolved"
            and str(item.get("last_seen_at", "")) >= since_iso
        ]
        candidates.sort(key=lambda item: str(item.get("last_seen_at", "")), reverse=True)
        return candidates[0] if candidates else None

    def all_incidents(self, tenant: str) -> list[dict]:
        return [item for item in self._read()["incidents"] if item.get("tenant") == tenant]

    # -- outbox / idempotency -----------------------------------------
    def add_outbox(self, entry: dict) -> None:
        def mutate(envelope):
            if any(item.get("event_key") == entry.get("event_key") for item in envelope["outbox"]):
                return None
            envelope["outbox"].append(entry)
            return None

        self._mutate(mutate)

    def due_outbox(self, now_iso: str, limit: int = 20) -> list[dict]:
        due = [
            item
            for item in self._read()["outbox"]
            if item.get("status") == "pending" and str(item.get("next_attempt_at", "")) <= now_iso
        ]
        due.sort(key=lambda item: str(item.get("next_attempt_at", "")))
        return due[:limit]

    def update_outbox(self, entry: dict) -> None:
        def mutate(envelope):
            for index, item in enumerate(envelope["outbox"]):
                if item.get("id") == entry.get("id"):
                    envelope["outbox"][index] = entry
                    return None
            envelope["outbox"].append(entry)
            return None

        self._mutate(mutate)

    def get_idempotent(self, tenant: str, key: str) -> dict | None:
        return self._read()["idempotency"].get(f"{tenant}:{key}")

    def put_idempotent(self, tenant: str, key: str, response: dict) -> None:
        def mutate(envelope):
            envelope["idempotency"][f"{tenant}:{key}"] = response
            return None

        self._mutate(mutate)


class SqliteRepository(IncidentRepository):
    def __init__(self, path: Path, assessor: Assessor, tenant_default: str = "default") -> None:
        self.path = Path(path)
        self.assessor = assessor
        self.tenant_default = tenant_default
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _init_schema(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    tenant TEXT NOT NULL,
                    service TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    category TEXT NOT NULL,
                    state TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    dedup_key TEXT NOT NULL,
                    alert_count INTEGER NOT NULL DEFAULT 1,
                    last_seen_at TEXT NOT NULL,
                    data TEXT NOT NULL
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
                    payload TEXT NOT NULL,
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
                    response TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (tenant, key)
                );
                """
            )

    def _upsert(self, connection: sqlite3.Connection, tenant: str, record: dict) -> dict:
        connection.execute(
            """
            INSERT INTO incidents (id, tenant, service, owner, environment, category, state, priority, score,
                                   created_at, updated_at, dedup_key, alert_count, last_seen_at, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                tenant=excluded.tenant, service=excluded.service, owner=excluded.owner,
                environment=excluded.environment, category=excluded.category, state=excluded.state,
                priority=excluded.priority, score=excluded.score, created_at=excluded.created_at,
                updated_at=excluded.updated_at, dedup_key=excluded.dedup_key,
                alert_count=excluded.alert_count, last_seen_at=excluded.last_seen_at, data=excluded.data
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
                json.dumps(record, ensure_ascii=False, default=str),
            ),
        )
        return record

    def ping(self) -> bool:
        with closing(self._connect()) as connection:
            connection.execute("SELECT 1").fetchone()
        return True

    def list_incidents(self, tenant: str, **filters) -> tuple[list[dict], int]:
        clauses = ["tenant = ?"]
        params: list = [tenant]
        for field in ("priority", "state", "category"):
            if filters.get(field):
                clauses.append(f"LOWER({field}) = LOWER(?)")
                params.append(filters[field])
        for field in ("owner", "environment"):
            if filters.get(field):
                clauses.append(f"LOWER({field}) = LOWER(?)")
                params.append(filters[field])
        if filters.get("search"):
            needle = f"%{filters['search']}%"
            clauses.append("(LOWER(service) LIKE LOWER(?) OR LOWER(owner) LIKE LOWER(?) OR LOWER(data) LIKE LOWER(?))")
            params.extend([needle, needle, needle])

        where = " AND ".join(clauses)
        sort = filters.get("sort", "created_at")
        order = "ASC" if str(filters.get("order", "desc")).lower() == "asc" else "DESC"
        if sort == "priority":
            order_by = f"CASE priority WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 WHEN 'P3' THEN 3 WHEN 'P4' THEN 4 ELSE 5 END {order}, created_at {order}"
        elif sort == "score":
            order_by = f"score {order}, created_at {order}"
        elif sort in {"created_at", "updated_at", "service", "state"}:
            order_by = f"{sort} {order}"
        else:
            order_by = f"created_at {order}"

        limit = max(0, int(filters.get("limit", 50)))
        offset = max(0, int(filters.get("offset", 0)))
        with closing(self._connect()) as connection:
            # where/order_by use allowlisted column names; all values are parameterized.
            total = connection.execute(f"SELECT COUNT(*) AS count FROM incidents WHERE {where}", params).fetchone()["count"]  # nosec B608
            rows = connection.execute(
                f"SELECT data FROM incidents WHERE {where} ORDER BY {order_by} LIMIT ? OFFSET ?",  # nosec B608
                [*params, limit, offset],
            ).fetchall()
        return [json.loads(row["data"]) for row in rows], int(total)

    def get_incident(self, tenant: str, incident_id: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT data FROM incidents WHERE tenant = ? AND id = ?", (tenant, incident_id)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def create_incident(self, tenant: str, record: dict) -> dict:
        with closing(self._connect()) as connection, connection:
            return self._upsert(connection, tenant, record)

    def update_incident(self, tenant: str, record: dict) -> dict:
        with closing(self._connect()) as connection, connection:
            return self._upsert(connection, tenant, record)

    def delete_incident(self, tenant: str, incident_id: str) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("DELETE FROM incidents WHERE tenant = ? AND id = ?", (tenant, incident_id))
        return cursor.rowcount > 0

    def find_open_incident(self, tenant: str, dedup_key: str, since_iso: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT data FROM incidents
                WHERE tenant = ? AND dedup_key = ? AND state != 'resolved' AND last_seen_at >= ?
                ORDER BY last_seen_at DESC LIMIT 1
                """,
                (tenant, dedup_key, since_iso),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def all_incidents(self, tenant: str) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT data FROM incidents WHERE tenant = ?", (tenant,)).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def add_outbox(self, entry: dict) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO outbox (id, tenant, incident_id, event_key, payload, status, attempts,
                                              next_attempt_at, created_at, sent_at, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry["id"],
                    entry["tenant"],
                    entry.get("incident_id"),
                    entry["event_key"],
                    json.dumps(entry.get("payload", {}), ensure_ascii=False, default=str),
                    entry.get("status", "pending"),
                    int(entry.get("attempts", 0)),
                    entry.get("next_attempt_at", entry.get("created_at", utc_now_iso())),
                    entry.get("created_at", utc_now_iso()),
                    entry.get("sent_at"),
                    entry.get("last_error"),
                ),
            )

    def due_outbox(self, now_iso: str, limit: int = 20) -> list[dict]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM outbox
                WHERE status = 'pending' AND next_attempt_at <= ?
                ORDER BY next_attempt_at LIMIT ?
                """,
                (now_iso, limit),
            ).fetchall()
        return [self._outbox_row(row) for row in rows]

    @staticmethod
    def _outbox_row(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "tenant": row["tenant"],
            "incident_id": row["incident_id"],
            "event_key": row["event_key"],
            "payload": json.loads(row["payload"]),
            "status": row["status"],
            "attempts": row["attempts"],
            "next_attempt_at": row["next_attempt_at"],
            "created_at": row["created_at"],
            "sent_at": row["sent_at"],
            "last_error": row["last_error"],
        }

    def update_outbox(self, entry: dict) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE outbox SET payload = ?, status = ?, attempts = ?, next_attempt_at = ?,
                                  sent_at = ?, last_error = ?
                WHERE id = ?
                """,
                (
                    json.dumps(entry.get("payload", {}), ensure_ascii=False, default=str),
                    entry.get("status", "pending"),
                    int(entry.get("attempts", 0)),
                    entry.get("next_attempt_at", utc_now_iso()),
                    entry.get("sent_at"),
                    entry.get("last_error"),
                    entry["id"],
                ),
            )

    def get_idempotent(self, tenant: str, key: str) -> dict | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT response FROM idempotency WHERE tenant = ? AND key = ?", (tenant, key)
            ).fetchone()
        return json.loads(row["response"]) if row else None

    def put_idempotent(self, tenant: str, key: str, response: dict) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO idempotency (tenant, key, response, created_at) VALUES (?, ?, ?, ?)",
                (tenant, key, json.dumps(response, ensure_ascii=False, default=str), utc_now_iso()),
            )


def build_repository(settings, assessor: Assessor):
    if settings.storage_backend == "sqlite":
        return SqliteRepository(settings.sqlite_path, assessor, settings.default_tenant)
    if settings.storage_backend == "postgres":
        from postgres_repository import PostgresRepository

        return PostgresRepository(settings.database_url, assessor, settings.default_tenant)
    return JsonRepository(settings.data_file, assessor, settings.default_tenant)
