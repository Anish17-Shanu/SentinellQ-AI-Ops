import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tests", ROOT / "sdk"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from sentineliq_client import SentinelIQClient, SentinelIQError  # noqa: E402
from support import FakeNotifier, make_settings  # noqa: E402

from bootstrap import build_context  # noqa: E402
from server import create_server  # noqa: E402


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = make_settings(Path(self.tmp.name))
        self.context = build_context(settings, notifier=FakeNotifier())
        self.server = create_server(self.context, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.client = SentinelIQClient(f"http://127.0.0.1:{self.port}", timeout=10)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.context.repository.close()
        self.tmp.cleanup()

    def test_health_and_summary(self):
        self.assertEqual(self.client.health()["status"], "ok")
        summary = self.client.summary()
        self.assertEqual(summary["total_incidents"], 0)

    def test_create_get_transition(self):
        created = self.client.create_incident(
            service="payments-api",
            category="availability",
            criticality="critical",
            impacted_users=20000,
            error_rate=50,
            latency_ms=3000,
            alerts=10,
        )
        self.assertEqual(created["priority"], "P1")
        fetched = self.client.get_incident(created["id"])
        self.assertEqual(fetched["incident"]["id"], created["id"])
        updated = self.client.transition(created["id"], "acknowledged", note="paged")
        self.assertEqual(updated["state"], "acknowledged")

    def test_error_envelope(self):
        with self.assertRaises(SentinelIQError) as caught:
            self.client.get_incident("missing-id")
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(caught.exception.code, "not_found")
        self.assertTrue(caught.exception.request_id)

    def test_pagination_iterator(self):
        for index in range(3):
            self.client.create_incident(service=f"svc-{index}", category="latency")
        ids = [item["id"] for item in self.client.iter_incidents(page_size=2)]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)

    def test_ingest_dedupes(self):
        payload = {
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "HighErrorRate", "service": "checkout", "severity": "critical"},
                    "annotations": {"summary": "5xx above 10%"},
                }
            ]
        }
        first = self.client.ingest("alertmanager", payload)
        second = self.client.ingest("alertmanager", payload)
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["deduplicated"], 1)
        self.assertEqual(first["items"][0]["incident"]["id"], second["items"][0]["incident"]["id"])

    def test_metrics_text(self):
        text = self.client.metrics()
        self.assertIn("sentineliq_http_requests_total", text)


if __name__ == "__main__":
    unittest.main()
