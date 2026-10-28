import json
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from support import incident

from engine import assess_incident, normalize_incident
from repository import JsonRepository, SqliteRepository, StorageError


def make_record(service="payments-api", tenant="default", second=0, **overrides):
    normalized = normalize_incident(incident(service=service, **overrides))
    assessed = assess_incident(normalized)
    stamp = f"2026-10-29T10:00:{second:02d}Z"
    return {
        "id": str(uuid.uuid4()),
        "tenant": tenant,
        **normalized,
        "state": overrides.get("state", "open"),
        "created_at": stamp,
        "updated_at": stamp,
        "acknowledged_at": None,
        "mitigated_at": None,
        "resolved_at": None,
        "last_seen_at": stamp,
        "alert_count": overrides.get("alert_count", 1),
        "dedup_key": f"{normalized['service']}|{normalized['category']}",
        "source": "test",
        "events": [],
        **assessed,
    }


class RepositoryContractMixin:
    backend = None

    def build(self, path):
        raise NotImplementedError

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.build(Path(self.tmp.name) / self.filename)

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def test_create_and_get_round_trip(self):
        record = make_record()
        self.repo.create_incident("default", record)
        loaded = self.repo.get_incident("default", record["id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["service"], "payments-api")
        self.assertEqual(loaded["priority"], "P1")
        self.assertEqual(loaded["events"], [])

    def test_tenant_isolation(self):
        self.repo.create_incident("acme", make_record(tenant="acme"))
        self.repo.create_incident("globex", make_record(tenant="globex", service="billing-worker"))
        items, total = self.repo.list_incidents("acme")
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["tenant"], "acme")
        self.assertIsNone(self.repo.get_incident("globex", items[0]["id"]))

    def test_filters(self):
        self.repo.create_incident("default", make_record(service="payments-api", owner="payments", environment="production", second=1))
        self.repo.create_incident("default", make_record(service="cms", owner="content", environment="staging", criticality="low", second=2))
        self.repo.create_incident(
            "default",
            make_record(service="billing-worker", owner="payments", environment="production", category="data", state="resolved", second=3),
        )
        cases = [
            ({"owner": "payments"}, 2),
            ({"environment": "staging"}, 1),
            ({"category": "data"}, 1),
            ({"state": "resolved"}, 1),
            ({"priority": "P1"}, 2),
            ({"search": "billing"}, 1),
            ({"owner": "payments", "environment": "production"}, 2),
        ]
        for filters, expected in cases:
            with self.subTest(filters=filters):
                items, total = self.repo.list_incidents("default", **filters)
                self.assertEqual(total, expected)
                self.assertEqual(len(items), expected)

    def test_sorting_and_pagination(self):
        for index in range(5):
            record = make_record(service=f"svc-{index}", second=index)
            record["score"] = float(index * 10)
            self.repo.create_incident("default", record)
        items, total = self.repo.list_incidents("default", sort="score", order="desc", limit=2, offset=0)
        self.assertEqual(total, 5)
        self.assertEqual([item["score"] for item in items], [40.0, 30.0])
        items, _ = self.repo.list_incidents("default", sort="score", order="desc", limit=2, offset=4)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["score"], 0.0)

    def test_update_and_delete(self):
        record = make_record()
        self.repo.create_incident("default", record)
        record["state"] = "acknowledged"
        self.repo.update_incident("default", record)
        self.assertEqual(self.repo.get_incident("default", record["id"])["state"], "acknowledged")
        self.assertTrue(self.repo.delete_incident("default", record["id"]))
        self.assertIsNone(self.repo.get_incident("default", record["id"]))
        self.assertFalse(self.repo.delete_incident("default", record["id"]))

    def test_find_open_incident_respects_window(self):
        record = make_record()
        self.repo.create_incident("default", record)
        found = self.repo.find_open_incident("default", record["dedup_key"], "2026-10-29T00:00:00Z")
        self.assertIsNotNone(found)
        missing = self.repo.find_open_incident("default", "other|service", "2026-10-29T00:00:00Z")
        self.assertIsNone(missing)

    def test_outbox_deduplicates_event_key(self):
        base = {
            "id": "outbox-1",
            "tenant": "default",
            "incident_id": "inc-1",
            "event_key": "inc-1:incident_created:2026-10-29T10:00:00Z",
            "payload": {"event": "incident_created"},
            "status": "pending",
            "attempts": 0,
            "next_attempt_at": "2026-10-29T10:00:00Z",
            "created_at": "2026-10-29T10:00:00Z",
            "sent_at": None,
            "last_error": None,
        }
        self.repo.add_outbox(dict(base))
        duplicate = dict(base, id="outbox-2")
        self.repo.add_outbox(duplicate)
        due = self.repo.due_outbox("2026-10-29T11:00:00Z")
        self.assertEqual(len(due), 1)
        entry = due[0]
        entry["status"] = "sent"
        entry["sent_at"] = "2026-10-29T11:00:01Z"
        self.repo.update_outbox(entry)
        self.assertEqual(self.repo.due_outbox("2026-10-29T12:00:00Z"), [])

    def test_idempotency_store_is_tenant_scoped(self):
        self.repo.put_idempotent("default", "key-1", {"record": {"id": "1"}})
        self.assertEqual(self.repo.get_idempotent("default", "key-1")["record"]["id"], "1")
        self.assertIsNone(self.repo.get_idempotent("other", "key-1"))
        self.assertIsNone(self.repo.get_idempotent("default", "key-2"))

    def test_ping(self):
        self.assertTrue(self.repo.ping())


class JsonRepositoryTests(RepositoryContractMixin, unittest.TestCase):
    filename = "incidents.json"

    def build(self, path):
        return JsonRepository(path, assess_incident)

    def test_legacy_list_is_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "service": "legacy-api",
                            "criticality": "high",
                            "impacted_users": 500,
                            "error_rate": 2.0,
                            "latency_ms": 250,
                            "alerts": 3,
                            "category": "security",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            repo = JsonRepository(path, assess_incident)
            items, total = repo.list_incidents("default")
            repo.close()
            self.assertEqual(total, 1)
            self.assertEqual(items[0]["state"], "open")
            self.assertEqual(items[0]["priority"], "P3")
            self.assertEqual(items[0]["tenant"], "default")
            envelope = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(envelope["version"], 2)
            self.assertIsInstance(envelope["incidents"], list)

    def test_corrupt_store_raises_storage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(StorageError):
                JsonRepository(path, assess_incident)

    def test_parallel_creates_do_not_lose_records(self):
        records = [make_record(service=f"svc-{index}") for index in range(16)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda item: self.repo.create_incident("default", item), records))
        items, total = self.repo.list_incidents("default", limit=100)
        self.assertEqual(total, 16)
        self.assertEqual(len({item["id"] for item in items}), 16)


class SqliteRepositoryTests(RepositoryContractMixin, unittest.TestCase):
    filename = "incidents.db"

    def build(self, path):
        return SqliteRepository(path, assess_incident)

    def test_wal_mode_is_enabled(self):
        import sqlite3
        from contextlib import closing

        with closing(sqlite3.connect(self.repo.path)) as connection:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")


if __name__ == "__main__":
    unittest.main()
