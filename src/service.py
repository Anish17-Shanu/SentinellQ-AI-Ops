"""Application service: incident lifecycle, alert ingest, summaries, and outbox."""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from collections.abc import Callable
from copy import deepcopy

from engine import (
    DEFAULT_CONFIG,
    VALID_CATEGORIES,
    VALID_PRIORITIES,
    assess_incident,
    normalize_incident,
)
from errors import ConflictError, NotFoundError, ValidationError
from repository import normalize_record
from util import parse_iso, utc_now, utc_now_iso

logger = logging.getLogger("sentineliq.service")

PRIORITY_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
CRITICALITY_ORDER = ["low", "medium", "high", "critical"]
TRANSITIONS = {
    "open": {"acknowledged"},
    "acknowledged": {"mitigated", "resolved"},
    "mitigated": {"resolved"},
    "resolved": set(),
}
STATE_TIMESTAMP = {
    "acknowledged": "acknowledged_at",
    "mitigated": "mitigated_at",
    "resolved": "resolved_at",
}
MAX_EVENTS = 200
MAX_IMPORT_RECORDS = 1000


def _sanitize_actor(value) -> str:
    text = str(value or "system").strip() or "system"
    return text[:80]


def _first_number(sources: list[dict], keys: tuple[str, ...]) -> float | None:
    for source in sources:
        for key in keys:
            if key in source:
                try:
                    return float(source[key])
                except (TypeError, ValueError):
                    continue
    return None


def _infer_category(*texts) -> str:
    haystack = " ".join(str(item) for item in texts).lower()
    for category in ("security", "data", "latency"):
        if category in haystack:
            return category
    return "availability"


def parse_alertmanager(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValidationError("Alertmanager payload must be a JSON object")
    alerts = payload.get("alerts")
    if alerts is None:
        alerts = [payload]
    if not isinstance(alerts, list):
        raise ValidationError("Alertmanager payload alerts must be a list")
    if not alerts:
        raise ValidationError("Alertmanager payload contained no alerts")

    parsed = []
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        raw_labels = alert.get("labels")
        raw_annotations = alert.get("annotations")
        labels: dict = raw_labels if isinstance(raw_labels, dict) else {}
        annotations: dict = raw_annotations if isinstance(raw_annotations, dict) else {}
        sources = [labels, annotations]
        service = (
            labels.get("service")
            or labels.get("job")
            or annotations.get("service")
            or labels.get("alertname")
            or "unknown-service"
        )
        severity = str(labels.get("severity") or labels.get("criticality") or "warning").lower()
        criticality = {
            "critical": "critical",
            "page": "critical",
            "high": "high",
            "warning": "high",
            "medium": "medium",
            "info": "low",
            "low": "low",
        }.get(severity, "medium")
        category = str(labels.get("category") or "").lower()
        if category not in VALID_CATEGORIES:
            category = _infer_category(labels.get("alertname", ""), annotations.get("summary", ""), annotations.get("description", ""))
        impacted_users = _first_number(sources, ("impacted_users", "users", "impacted"))
        error_rate = _first_number(sources, ("error_rate", "errorRate", "errors"))
        latency_ms = _first_number(sources, ("latency_ms", "latency", "response_time"))
        priority_hint = str(labels.get("priority_hint") or "").upper()
        parsed.append(
            {
                "service": str(service),
                "owner": str(labels.get("owner") or labels.get("team") or "platform-core"),
                "environment": str(labels.get("environment") or labels.get("env") or "production"),
                "description": str(
                    annotations.get("description")
                    or annotations.get("summary")
                    or labels.get("alertname")
                    or "Alertmanager alert"
                ),
                "criticality": str(labels.get("criticality") or criticality).lower(),
                "category": category,
                "impacted_users": int(impacted_users or 0),
                "error_rate": float(error_rate or 0.0),
                "latency_ms": float(latency_ms or 0.0),
                "alerts": 1,
                "priority_hint": priority_hint if priority_hint in VALID_PRIORITIES else "",
            }
        )
    if not parsed:
        raise ValidationError("Alertmanager payload contained no alerts")
    return parsed


def parse_pagerduty(payload: dict) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValidationError("PagerDuty payload must be a JSON object")
    raw_event = payload.get("event")
    event: dict = raw_event if isinstance(raw_event, dict) else {}
    raw_data = event.get("data")
    data: dict = raw_data if isinstance(raw_data, dict) else {}
    if not data:
        raise ValidationError("PagerDuty payload must contain event.data")
    service_value = data.get("service")
    if isinstance(service_value, dict):
        service = service_value.get("summary") or service_value.get("name") or "unknown-service"
    else:
        service = service_value or "unknown-service"
    urgency = str(data.get("urgency") or "high").lower()
    raw_details = data.get("custom_details")
    details: dict = raw_details if isinstance(raw_details, dict) else {}
    priority_hint = str(details.get("priority_hint") or "").upper()
    description = str(data.get("title") or data.get("summary") or "PagerDuty incident")
    category = str(details.get("category") or "").lower()
    if category not in VALID_CATEGORIES:
        category = _infer_category(description)
    return [
        {
            "service": str(service),
            "owner": str(details.get("owner") or details.get("team") or "platform-core"),
            "environment": str(details.get("environment") or "production"),
            "description": description,
            "criticality": str(details.get("criticality") or ("critical" if urgency == "high" else "medium")).lower(),
            "category": category,
            "impacted_users": int(_first_number([details], ("impacted_users", "users")) or 0),
            "error_rate": float(_first_number([details], ("error_rate",)) or 0.0),
            "latency_ms": float(_first_number([details], ("latency_ms", "latency")) or 0.0),
            "alerts": 1,
            "priority_hint": priority_hint if priority_hint in VALID_PRIORITIES else "",
        }
    ]


class IncidentService:
    def __init__(
        self,
        repository,
        settings,
        score_config=None,
        playbooks=None,
        metrics=None,
        notifier=None,
        clock: Callable[[], str] = utc_now_iso,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.config = score_config or DEFAULT_CONFIG
        self.playbooks = playbooks or {"defaults": {}, "service_overrides": {}}
        self.metrics = metrics
        self.notifier = notifier
        self.clock = clock

    # -- helpers -------------------------------------------------------
    def assess(self, record: dict) -> dict:
        return assess_incident(record, self.config, self.playbooks)

    def _assessed(self, record: dict) -> dict:
        enriched = deepcopy(record)
        enriched.update(self.assess(enriched))
        return enriched

    def _inc(self, name: str, labels: dict | None = None) -> None:
        if self.metrics is not None:
            self.metrics.inc(name, labels)

    def _append_event(self, record: dict, event_type: str, actor: str, detail: dict | None = None) -> None:
        events = record.setdefault("events", [])
        events.append({"type": event_type, "at": self.clock(), "actor": _sanitize_actor(actor), "detail": detail or {}})
        if len(events) > MAX_EVENTS:
            record["events"] = events[-MAX_EVENTS:]

    def _should_notify(self, record: dict) -> bool:
        if self.notifier is None or not getattr(self.notifier, "enabled", False):
            return False
        threshold = self.settings.notify_min_priority
        return PRIORITY_RANK.get(record.get("priority", "P4"), 4) <= PRIORITY_RANK.get(threshold, 2)

    def _enqueue_notification(self, record: dict, event_type: str) -> None:
        if not self._should_notify(record):
            return
        entry = {
            "id": str(uuid.uuid4()),
            "tenant": record["tenant"],
            "incident_id": record["id"],
            "event_key": f"{record['id']}:{event_type}:{record.get('updated_at') or record.get('created_at')}",
            "payload": {
                "event": event_type,
                "incident": {
                    "id": record["id"],
                    "service": record.get("service"),
                    "owner": record.get("owner"),
                    "environment": record.get("environment"),
                    "priority": record.get("priority"),
                    "score_100": record.get("score_100"),
                    "state": record.get("state"),
                    "explanation": record.get("explanation"),
                },
            },
            "status": "pending",
            "attempts": 0,
            "next_attempt_at": self.clock(),
            "created_at": self.clock(),
            "sent_at": None,
            "last_error": None,
        }
        self.repository.add_outbox(entry)
        logger.info("notification queued", extra={"fields": {"event": event_type, "incident_id": record["id"]}})

    # -- incidents -----------------------------------------------------
    def build_record(self, tenant: str, payload: dict, actor: str, source: str) -> dict:
        incident = normalize_incident(payload)
        now = self.clock()
        record = {
            "id": str(uuid.uuid4()),
            "tenant": tenant,
            **incident,
            "state": "open",
            "created_at": now,
            "updated_at": now,
            "acknowledged_at": None,
            "mitigated_at": None,
            "resolved_at": None,
            "last_seen_at": now,
            "alert_count": 1,
            "dedup_key": f"{incident['service']}|{incident['category']}",
            "source": source,
            "events": [],
        }
        self._append_event(record, "created" if source == "api" else "ingested", actor, {"source": source})
        record.update(self.assess(record))
        return record

    def create(self, tenant: str, payload: dict, actor: str, idempotency_key: str | None = None) -> tuple[dict, bool]:
        if idempotency_key:
            existing = self.repository.get_idempotent(tenant, idempotency_key)
            if existing is not None:
                self._inc("sentineliq_idempotent_replays_total")
                return existing["record"], True
        record = self.build_record(tenant, payload, actor, "api")
        self.repository.create_incident(tenant, record)
        self._inc("sentineliq_incidents_created_total", {"priority": record["priority"]})
        if idempotency_key:
            self.repository.put_idempotent(tenant, idempotency_key, {"record": record, "created_at": record["created_at"]})
        self._enqueue_notification(record, "incident_created")
        logger.info(
            "incident created",
            extra={"fields": {"incident_id": record["id"], "priority": record["priority"], "tenant": tenant}},
        )
        return record, False

    def list(self, tenant: str, **filters) -> tuple[list[dict], int]:
        return self.repository.list_incidents(tenant, **filters)

    def get(self, tenant: str, incident_id: str) -> dict:
        record = self.repository.get_incident(tenant, incident_id)
        if record is None:
            raise NotFoundError(f"Incident {incident_id} was not found")
        return record

    def transition(self, tenant: str, incident_id: str, target_state: str, actor: str, note: str = "") -> dict:
        target = str(target_state or "").strip().lower()
        if target not in TRANSITIONS:
            raise ValidationError("state must be one of open, acknowledged, mitigated, resolved", field="state")
        record = self.get(tenant, incident_id)
        current = record.get("state", "open")
        if target == current:
            raise ConflictError(f"Incident is already {target}")
        if target not in TRANSITIONS[current]:
            raise ValidationError(f"Cannot transition from {current} to {target}", field="state")
        if len(note) > 500:
            raise ValidationError("note must be at most 500 characters", field="note")

        now = self.clock()
        record["state"] = target
        record[STATE_TIMESTAMP[target]] = now
        record["updated_at"] = now
        self._append_event(record, "state_changed", actor, {"from": current, "to": target, "note": note})
        record.update(self.assess(record))
        self.repository.update_incident(tenant, record)
        self._inc("sentineliq_incidents_transitioned_total", {"state": target})
        self._enqueue_notification(record, f"incident_{target}")
        logger.info(
            "incident transitioned",
            extra={"fields": {"incident_id": incident_id, "from": current, "to": target, "tenant": tenant}},
        )
        return record

    def delete(self, tenant: str, incident_id: str) -> None:
        if not self.repository.delete_incident(tenant, incident_id):
            raise NotFoundError(f"Incident {incident_id} was not found")
        logger.info("incident deleted", extra={"fields": {"incident_id": incident_id, "tenant": tenant}})

    # -- ingest --------------------------------------------------------
    def ingest(self, tenant: str, source: str, payload: dict, actor: str) -> dict:
        if source == "alertmanager":
            alerts = parse_alertmanager(payload)
        elif source == "pagerduty":
            alerts = parse_pagerduty(payload)
        else:
            source = "generic"
            alerts = [payload]
        results = [self._ingest_one(tenant, source, alert, actor) for alert in alerts]
        return {
            "source": source,
            "count": len(results),
            "created": sum(1 for item in results if item["action"] == "created"),
            "deduplicated": sum(1 for item in results if item["action"] == "updated"),
            "items": results,
        }

    def _ingest_one(self, tenant: str, source: str, payload: dict, actor: str) -> dict:
        incident = normalize_incident(payload)
        window = self.settings.dedup_window_seconds
        now = self.clock()
        if window > 0:
            since_iso = _add_seconds(-window)
            existing = self.repository.find_open_incident(tenant, f"{incident['service']}|{incident['category']}", since_iso)
            if existing is not None:
                return self._merge_alert(tenant, existing, incident, source, actor, now)

        record = self.build_record(tenant, payload, actor, source)
        record["alert_count"] = 1
        self.repository.create_incident(tenant, record)
        self._inc("sentineliq_incidents_created_total", {"priority": record["priority"]})
        self._inc("sentineliq_ingest_total", {"source": source, "result": "created"})
        self._enqueue_notification(record, "incident_created")
        return {"incident": record, "deduplicated": False, "action": "created", "source": source}

    def _merge_alert(self, tenant: str, existing: dict, incident: dict, source: str, actor: str, now: str) -> dict:
        previous_priority = existing.get("priority", "P4")
        record = deepcopy(existing)
        record["alert_count"] = int(record.get("alert_count", 1)) + 1
        record["last_seen_at"] = now
        record["updated_at"] = now
        record["alerts"] = max(int(record.get("alerts", 0)), int(incident["alerts"]))
        record["impacted_users"] = max(int(record.get("impacted_users", 0)), int(incident["impacted_users"]))
        record["error_rate"] = max(float(record.get("error_rate", 0.0)), float(incident["error_rate"]))
        record["latency_ms"] = max(float(record.get("latency_ms", 0.0)), float(incident["latency_ms"]))
        if CRITICALITY_ORDER.index(incident["criticality"]) > CRITICALITY_ORDER.index(record.get("criticality", "medium")):
            record["criticality"] = incident["criticality"]
        if not record.get("description"):
            record["description"] = incident["description"]
        self._append_event(
            record,
            "alert_received",
            actor,
            {"source": source, "alert_count": record["alert_count"], "description": incident["description"]},
        )
        record.update(self.assess(record))
        self.repository.update_incident(tenant, record)
        self._inc("sentineliq_ingest_total", {"source": source, "result": "deduplicated"})
        if PRIORITY_RANK.get(record["priority"], 4) < PRIORITY_RANK.get(previous_priority, 4):
            self._append_event(record, "priority_escalated", actor, {"from": previous_priority, "to": record["priority"]})
            self.repository.update_incident(tenant, record)
            self._enqueue_notification(record, "incident_escalated")
        return {"incident": record, "deduplicated": True, "action": "updated", "source": source}

    # -- reporting -----------------------------------------------------
    def summary(self, tenant: str) -> dict:
        records = self.repository.all_incidents(tenant)
        by_priority = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
        by_state = {"open": 0, "acknowledged": 0, "mitigated": 0, "resolved": 0}
        ack_durations: list[float] = []
        resolve_durations: list[float] = []
        oldest_open_age = 0.0
        scores: list[float] = []
        now = utc_now()
        services = []

        for record in records:
            priority = record.get("priority", "P4")
            state = record.get("state", "open")
            by_priority[priority] = by_priority.get(priority, 0) + 1
            by_state[state] = by_state.get(state, 0) + 1
            scores.append(float(record.get("score", 0.0)))
            created = parse_iso(record.get("created_at"))
            acknowledged = parse_iso(record.get("acknowledged_at"))
            resolved = parse_iso(record.get("resolved_at"))
            if created and acknowledged:
                ack_durations.append((acknowledged - created).total_seconds())
            if created and resolved:
                resolve_durations.append((resolved - created).total_seconds())
            if created and state != "resolved":
                oldest_open_age = max(oldest_open_age, (now - created).total_seconds())
            services.append(
                {
                    "service": record.get("service"),
                    "owner": record.get("owner"),
                    "state": state,
                    "priority": priority,
                    "score": record.get("score"),
                    "score_100": record.get("score_100"),
                    "alert_count": record.get("alert_count", 1),
                }
            )

        services.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
        normalized = [float(record.get("score_100", 0.0)) for record in records]
        return {
            "total_incidents": len(records),
            "open_incidents": len(records) - by_state.get("resolved", 0),
            "p1_open": sum(1 for item in records if item.get("priority") == "P1" and item.get("state") != "resolved"),
            "by_priority": by_priority,
            "by_state": by_state,
            "max_score": round(max(scores), 2) if scores else 0,
            "average_score": round(sum(scores) / len(scores), 2) if scores else 0,
            "max_score_100": round(max(normalized), 2) if normalized else 0,
            "average_score_100": round(sum(normalized) / len(normalized), 2) if normalized else 0,
            "mtta_seconds": round(sum(ack_durations) / len(ack_durations), 1) if ack_durations else None,
            "mttr_seconds": round(sum(resolve_durations) / len(resolve_durations), 1) if resolve_durations else None,
            "oldest_open_age_seconds": round(oldest_open_age, 1),
            "services": services[:200],
        }

    def export(self, tenant: str, fmt: str = "json") -> dict:
        records = sorted(self.repository.all_incidents(tenant), key=lambda item: str(item.get("created_at", "")), reverse=True)
        if fmt == "csv":
            buffer = io.StringIO()
            columns = [
                "id", "service", "owner", "environment", "category", "state", "priority", "score", "score_100",
                "confidence", "alert_count", "created_at", "acknowledged_at", "mitigated_at", "resolved_at", "description",
            ]
            writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for record in records:
                writer.writerow({column: record.get(column, "") for column in columns})
            return {
                "body": buffer.getvalue().encode("utf-8"),
                "content_type": "text/csv; charset=utf-8",
                "filename": f"sentineliq-incidents-{utc_now().strftime('%Y%m%d')}.csv",
            }
        if fmt != "json":
            raise ValidationError("format must be json or csv", field="format")
        payload = {
            "exported_at": self.clock(),
            "tenant": tenant,
            "count": len(records),
            "incidents": records,
        }
        return {
            "body": json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode("utf-8"),
            "content_type": "application/json",
            "filename": f"sentineliq-incidents-{utc_now().strftime('%Y%m%d')}.json",
        }

    def import_records(self, tenant: str, payload) -> dict:
        items = payload.get("incidents") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise ValidationError("Import payload must contain an incidents list", field="incidents")
        if len(items) > MAX_IMPORT_RECORDS:
            raise ValidationError(f"At most {MAX_IMPORT_RECORDS} incidents can be imported per request", field="incidents")
        created = 0
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValidationError(f"incidents[{index}] must be an object", field="incidents")
            record = normalize_record(item, tenant, self.assess, index)
            record["tenant"] = tenant
            record["source"] = record.get("source") or "import"
            self.repository.update_incident(tenant, record)
            created += 1
        logger.info("incidents imported", extra={"fields": {"count": created, "tenant": tenant}})
        return {"imported": created, "tenant": tenant}

    # -- outbox --------------------------------------------------------
    def tick(self) -> dict:
        if self.notifier is None or not getattr(self.notifier, "enabled", False):
            return {"processed": 0, "sent": 0, "failed": 0, "dead": 0}
        due = self.repository.due_outbox(self.clock(), limit=20)
        sent = failed = dead = 0
        for entry in due:
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
            try:
                self.notifier.send(entry.get("payload", {}))
            except Exception as exc:  # noqa: BLE001 - delivery failures are recorded, not raised
                entry["last_error"] = str(exc)[:300]
                failed += 1
                self._inc("sentineliq_notifications_total", {"status": "failed"})
                if entry["attempts"] >= 5:
                    entry["status"] = "dead"
                    dead += 1
                    self._inc("sentineliq_notifications_total", {"status": "dead"})
                else:
                    backoff = min(3600, 30 * (2 ** entry["attempts"]))
                    entry["next_attempt_at"] = utc_now_iso() if backoff <= 0 else _add_seconds(backoff)
            else:
                entry["status"] = "sent"
                entry["sent_at"] = self.clock()
                entry["last_error"] = None
                sent += 1
                self._inc("sentineliq_notifications_total", {"status": "sent"})
            self.repository.update_outbox(entry)
        return {"processed": len(due), "sent": sent, "failed": failed, "dead": dead}


def _add_seconds(seconds: float) -> str:
    from datetime import timedelta

    return (utc_now() + timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
