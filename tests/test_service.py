import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from support import FakeClock, FakeNotifier, incident, make_settings

from engine import assess_incident
from errors import ConflictError, NotFoundError, ValidationError
from repository import JsonRepository
from service import IncidentService


class ServiceTestCase(unittest.TestCase):
    notifier_enabled = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = make_settings(Path(self.tmp.name))
        self.repository = JsonRepository(Path(self.tmp.name) / "incidents.json", assess_incident)
        self.clock = FakeClock()
        self.notifier = FakeNotifier()
        self.service = self.build_service()

    def build_service(self, notifier="default"):
        resolved = self.notifier if notifier == "default" else notifier
        return IncidentService(
            self.repository,
            self.settings,
            metrics=None,
            notifier=resolved,
            clock=self.clock,
        )

    def tearDown(self):
        self.repository.close()
        self.tmp.cleanup()


class LifecycleTests(ServiceTestCase):
    def test_create_builds_lifecycle_record(self):
        record, replayed = self.service.create("default", incident(), "tester")
        self.assertFalse(replayed)
        self.assertEqual(record["state"], "open")
        self.assertEqual(record["priority"], "P1")
        self.assertEqual(record["tenant"], "default")
        self.assertEqual([event["type"] for event in record["events"]], ["created"])
        self.assertAlmostEqual(record["score_100"], record["score"] / 115 * 100, places=1)

    def test_idempotency_replays_the_same_record(self):
        first, replayed = self.service.create("default", incident(), "tester", "key-1")
        second, replayed = self.service.create("default", incident(), "tester", "key-1")
        self.assertTrue(replayed)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.service.list("default")[1], 1)

    def test_transition_records_timestamps_and_events(self):
        record, _ = self.service.create("default", incident(), "tester")
        incident_id = record["id"]
        self.clock.advance(120)
        acknowledged = self.service.transition("default", incident_id, "acknowledged", "oncall", "looking")
        self.assertEqual(acknowledged["state"], "acknowledged")
        self.assertEqual(acknowledged["acknowledged_at"], "2026-10-29T10:02:00Z")
        self.clock.advance(300)
        mitigated = self.service.transition("default", incident_id, "mitigated", "oncall")
        self.assertEqual(mitigated["mitigated_at"], "2026-10-29T10:07:00Z")
        self.clock.advance(600)
        resolved = self.service.transition("default", incident_id, "resolved", "oncall")
        self.assertEqual(resolved["resolved_at"], "2026-10-29T10:17:00Z")
        self.assertEqual([event["type"] for event in resolved["events"]], ["created", "state_changed", "state_changed", "state_changed"])

        summary = self.service.summary("default")
        self.assertEqual(summary["mtta_seconds"], 120.0)
        self.assertEqual(summary["mttr_seconds"], 1020.0)
        self.assertEqual(summary["open_incidents"], 0)
        self.assertEqual(summary["by_state"]["resolved"], 1)

    def test_invalid_transitions_are_rejected(self):
        record, _ = self.service.create("default", incident(), "tester")
        with self.assertRaises(ConflictError):
            self.service.transition("default", record["id"], "open", "tester")
        with self.assertRaises(ValidationError):
            self.service.transition("default", record["id"], "resolved", "tester")
        with self.assertRaises(ValidationError):
            self.service.transition("default", record["id"], "bogus", "tester")
        self.service.transition("default", record["id"], "acknowledged", "tester")
        with self.assertRaises(ConflictError):
            self.service.transition("default", record["id"], "acknowledged", "tester")

    def test_missing_incident_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.service.get("default", "missing")
        with self.assertRaises(NotFoundError):
            self.service.transition("default", "missing", "acknowledged", "tester")

    def test_delete_removes_incident(self):
        record, _ = self.service.create("default", incident(), "tester")
        self.service.delete("default", record["id"])
        with self.assertRaises(NotFoundError):
            self.service.get("default", record["id"])

    def test_priority_hint_escalates(self):
        record, _ = self.service.create(
            "default",
            incident(criticality="low", impacted_users=0, error_rate=0, latency_ms=0, alerts=0, priority_hint="P2"),
            "tester",
        )
        self.assertEqual(record["priority"], "P2")

    def test_summary_reports_normalized_average(self):
        self.service.create("default", incident(), "tester")
        self.service.create(
            "default",
            incident(service="cms", criticality="low", impacted_users=1, error_rate=0, latency_ms=0, alerts=0),
            "tester",
        )
        summary = self.service.summary("default")
        self.assertEqual(summary["total_incidents"], 2)
        self.assertEqual(summary["p1_open"], 1)
        self.assertEqual(summary["by_priority"]["P1"], 1)
        self.assertGreater(summary["average_score_100"], 0)
        self.assertLessEqual(summary["max_score_100"], 100)
        self.assertEqual(len(summary["services"]), 2)


class IngestTests(ServiceTestCase):
    def alertmanager_payload(self, severity="critical", **labels):
        base = {"alertname": "HighErrorRate", "service": "payments-api", "severity": severity}
        base.update(labels)
        return {
            "version": "4",
            "status": "firing",
            "alerts": [
                {
                    "labels": base,
                    "annotations": {"description": "5xx spike", "summary": "error budget burn"},
                    "startsAt": "2026-10-29T09:55:00Z",
                }
            ],
        }

    def test_alertmanager_ingest_creates_incident(self):
        result = self.service.ingest("default", "alertmanager", self.alertmanager_payload(), "alertmanager")
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["deduplicated"], 0)
        record = result["items"][0]["incident"]
        self.assertEqual(record["service"], "payments-api")
        self.assertEqual(record["source"], "alertmanager")
        self.assertEqual(record["events"][0]["type"], "ingested")

    def test_duplicate_alerts_merge_within_window(self):
        self.service.ingest("default", "alertmanager", self.alertmanager_payload(), "alertmanager")
        result = self.service.ingest("default", "alertmanager", self.alertmanager_payload(), "alertmanager")
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["deduplicated"], 1)
        merged = result["items"][0]["incident"]
        self.assertEqual(merged["alert_count"], 2)
        self.assertIn("alert_received", [event["type"] for event in merged["events"]])
        self.assertEqual(self.service.list("default")[1], 1)

    def test_severity_escalation_is_recorded(self):
        self.service.ingest("default", "alertmanager", self.alertmanager_payload(severity="warning"), "alertmanager")
        result = self.service.ingest("default", "alertmanager", self.alertmanager_payload(severity="critical"), "alertmanager")
        merged = result["items"][0]["incident"]
        self.assertEqual(merged["criticality"], "critical")
        self.assertIn("priority_escalated", [event["type"] for event in merged["events"]])

    def test_pagerduty_payload(self):
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {
                    "title": "Database replication lag",
                    "urgency": "high",
                    "service": {"summary": "orders-db"},
                    "custom_details": {"category": "data", "owner": "data-platform", "environment": "production"},
                },
            }
        }
        result = self.service.ingest("default", "pagerduty", payload, "pagerduty")
        record = result["items"][0]["incident"]
        self.assertEqual(record["service"], "orders-db")
        self.assertEqual(record["owner"], "data-platform")
        self.assertEqual(record["category"], "data")

    def test_generic_payload_requires_service(self):
        result = self.service.ingest("default", "generic", incident(service="custom-service"), "hook")
        self.assertEqual(result["items"][0]["incident"]["service"], "custom-service")
        with self.assertRaises(ValidationError):
            self.service.ingest("default", "alertmanager", {"alerts": []}, "alertmanager")


class ReportingTests(ServiceTestCase):
    def test_export_json_and_csv(self):
        record, _ = self.service.create("default", incident(), "tester")
        exported = self.service.export("default", "json")
        self.assertTrue(exported["content_type"].startswith("application/json"))
        payload = json.loads(exported["body"])
        self.assertEqual(payload["incidents"][0]["id"], record["id"])

        csv_export = self.service.export("default", "csv")
        text = csv_export["body"].decode("utf-8")
        self.assertTrue(text.startswith("id,service,owner"))
        self.assertIn("payments-api", text)

    def test_import_round_trip(self):
        payload = {
            "incidents": [
                incident(service="imported-a"),
                incident(service="imported-b", state="resolved", priority="P3"),
            ]
        }
        result = self.service.import_records("acme", payload)
        self.assertEqual(result["imported"], 2)
        items, total = self.service.list("acme")
        self.assertEqual(total, 2)
        self.assertEqual({item["service"] for item in items}, {"imported-a", "imported-b"})
        self.assertTrue(all(item["tenant"] == "acme" for item in items))

    def test_import_rejects_oversized_batches(self):
        with self.assertRaises(ValidationError):
            self.service.import_records("default", {"incidents": [incident()] * 1001})


class NotificationTests(ServiceTestCase):
    def test_p1_creation_is_queued_and_sent(self):
        self.service.create("default", incident(), "tester")
        result = self.service.tick()
        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertEqual(self.notifier.sent[0]["event"], "incident_created")

    def test_low_priority_is_not_queued(self):
        self.service.create(
            "default",
            incident(criticality="low", impacted_users=0, error_rate=0, latency_ms=0, alerts=0, category="latency"),
            "tester",
        )
        result = self.service.tick()
        self.assertEqual(result["processed"], 0)
        self.assertEqual(self.notifier.sent, [])

    def test_failures_mark_outbox_dead_after_five_attempts(self):
        service = self.build_service(FakeNotifier(fail=True))
        service.create("default", incident(), "tester")
        due = self.repository.due_outbox(self.clock())
        self.assertEqual(len(due), 1)
        due[0]["attempts"] = 4
        self.repository.update_outbox(due[0])
        result = service.tick()
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["dead"], 1)
        remaining = self.repository.due_outbox("2099-01-01T00:00:00Z")
        self.assertEqual(remaining, [])

    def test_tick_without_notifier_is_noop(self):
        service = self.build_service(None)
        self.assertEqual(service.tick(), {"processed": 0, "sent": 0, "failed": 0, "dead": 0})


if __name__ == "__main__":
    unittest.main()
