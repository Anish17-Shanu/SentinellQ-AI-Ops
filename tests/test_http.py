import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from support import FakeNotifier, incident, make_settings

from bootstrap import build_context
from server import create_server


class HttpServerCase(unittest.TestCase):
    env = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = make_settings(Path(self.tmp.name), **self.env)
        self.context = build_context(settings, notifier=FakeNotifier())
        self.server = create_server(self.context, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.context.repository.close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        payload = body
        if isinstance(body, (dict, list)):
            payload = json.dumps(body)
            headers = {"Content-Type": "application/json", **(headers or {})}
        connection.request(method, path, body=payload, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        parsed = None
        if raw and response_headers.get("content-type", "").startswith("application/json"):
            parsed = json.loads(raw)
        result = (response.status, response_headers, parsed, raw)
        connection.close()
        return result

    def create_incident(self, **overrides):
        status, _, payload, _ = self.request("POST", "/v1/incidents", incident(**overrides))
        self.assertEqual(status, 201, payload)
        return payload


class CoreRouteTests(HttpServerCase):
    def test_health_and_security_headers(self):
        status, headers, payload, _ = self.request("GET", "/health", headers={"X-Request-Id": "req-123"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(headers["x-request-id"], "req-123")
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["cache-control"], "no-store")

    def test_root_lists_routes(self):
        status, _, payload, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("/v1/incidents", payload["routes"])
        self.assertEqual(payload["docs"], "/openapi.json")

    def test_incident_crud_flow(self):
        record = self.create_incident()
        incident_id = record["id"]
        self.assertEqual(record["priority"], "P1")

        status, _, payload, _ = self.request("GET", f"/v1/incidents/{incident_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["incident"]["id"], incident_id)
        self.assertEqual(len(payload["playbook"]), 3)

        status, _, payload, _ = self.request(
            "POST", f"/v1/incidents/{incident_id}/transitions", {"state": "acknowledged", "note": "on it"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "acknowledged")

        status, _, payload, _ = self.request("GET", "/v1/summary")
        self.assertEqual(payload["open_incidents"], 1)
        self.assertEqual(payload["by_state"]["acknowledged"], 1)

        status, _, _, _ = self.request("DELETE", f"/v1/incidents/{incident_id}")
        self.assertEqual(status, 204)
        status, _, _, _ = self.request("GET", f"/v1/incidents/{incident_id}")
        self.assertEqual(status, 404)

    def test_legacy_aliases_work(self):
        status, _, _, _ = self.request("GET", "/incidents")
        self.assertEqual(status, 200)
        status, _, payload, _ = self.request("GET", "/summary")
        self.assertEqual(status, 200)
        self.assertIn("total_incidents", payload)

    def test_head_returns_no_body(self):
        status, headers, _, raw = self.request("HEAD", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"")
        _, _, _, get_raw = self.request("GET", "/health")
        self.assertEqual(headers["content-length"], str(len(get_raw)))

    def test_unknown_route(self):
        status, _, payload, _ = self.request("GET", "/nope")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")

    def test_unknown_method(self):
        status, _, _, _ = self.request("PUT", "/v1/incidents")
        self.assertEqual(status, 404)


class ValidationTests(HttpServerCase):
    def test_invalid_json(self):
        status, _, payload, _ = self.request("POST", "/v1/incidents", b"{bad", {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "validation_error")
        self.assertEqual(payload["error"]["field"], "body")
        self.assertTrue(payload["error"]["request_id"])

    def test_missing_service(self):
        status, _, payload, _ = self.request("POST", "/v1/incidents", {"owner": "ops"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["field"], "service")

    def test_invalid_priority_filter(self):
        status, _, payload, _ = self.request("GET", "/v1/incidents?priority=P9")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["field"], "priority")

    def test_invalid_pagination(self):
        for query in ("limit=0", "limit=101", "limit=abc", "offset=-1", "sort=bogus", "order=sideways"):
            with self.subTest(query=query):
                status, _, _, _ = self.request("GET", f"/v1/incidents?{query}")
                self.assertEqual(status, 400)

    def test_oversized_body(self):
        import socket

        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as connection:
            connection.sendall(
                b"POST /v1/incidents HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 300000\r\n\r\n"
            )
            raw = b""
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                raw += chunk
        self.assertIn(b"413", raw.split(b"\r\n", 1)[0])
        self.assertIn(b"payload_too_large", raw)
        self.assertIn(b"X-Content-Type-Options: nosniff", raw)

    def test_chunked_body_is_rejected(self):
        import socket

        # http.client sends the chunk terminator after the server has already
        # closed the socket, which surfaces as a Windows connection abort.
        # Raw headers keep the assertion on the server response deterministic.
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as connection:
            connection.sendall(
                b"POST /v1/incidents HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
            raw = b""
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                raw += chunk
        self.assertIn(b"411", raw.split(b"\r\n", 1)[0])
        self.assertIn(b"length_required", raw)

    def test_idempotent_create_replays(self):
        headers = {"Idempotency-Key": "test-key-1"}
        first = self.request("POST", "/v1/incidents", incident(), headers)
        second = self.request("POST", "/v1/incidents", incident(), headers)
        self.assertEqual(first[0], 201)
        self.assertEqual(second[0], 201)
        self.assertEqual(second[1].get("idempotent-replayed"), "true")
        self.assertEqual(first[2]["id"], second[2]["id"])

    def test_bad_idempotency_key(self):
        status, _, payload, _ = self.request(
            "POST", "/v1/incidents", incident(), {"Idempotency-Key": "bad key with spaces"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["field"], "Idempotency-Key")


class StaticAssetTests(HttpServerCase):
    def test_dashboard_index(self):
        status, headers, _, raw = self.request("GET", "/dashboard/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"))
        self.assertIn(b"SentinelIQ", raw)

    def test_etag_supports_conditional_get(self):
        status, headers, _, _ = self.request("GET", "/dashboard/style.css")
        self.assertEqual(status, 200)
        etag = headers["etag"]
        status, _, _, raw = self.request("GET", "/dashboard/style.css", headers={"If-None-Match": etag})
        self.assertEqual(status, 304)
        self.assertEqual(raw, b"")

    def test_traversal_is_blocked(self):
        status, _, _, _ = self.request("GET", "/dashboard/../src/config.py")
        self.assertEqual(status, 404)
        status, _, _, _ = self.request("GET", "/dashboard/%2e%2e/src/config.py")
        self.assertEqual(status, 404)

    def test_similar_prefix_is_not_static(self):
        status, _, _, _ = self.request("GET", "/dashboardfoo")
        self.assertEqual(status, 404)

    def test_config_and_favicon_are_served(self):
        for path in ("/dashboard/config.js", "/dashboard/app.js", "/dashboard/favicon.svg"):
            with self.subTest(path=path):
                status, _, _, _ = self.request("GET", path)
                self.assertEqual(status, 200)


class ApiPrefixTests(HttpServerCase):
    def test_api_prefix_is_stripped(self):
        status, _, payload, _ = self.request("GET", "/api/v1/summary")
        self.assertEqual(status, 200)
        self.assertIn("total_incidents", payload)
        status, _, payload, _ = self.request("POST", "/api/v1/incidents", incident())
        self.assertEqual(status, 201)


class RateLimitTests(HttpServerCase):
    env = {"SENTINELIQ_RATE_LIMIT_RPM": "2"}

    def test_requests_are_limited(self):
        statuses = [self.request("GET", "/v1/summary")[0] for _ in range(3)]
        self.assertEqual(statuses, [200, 200, 429])
        status, headers, payload, _ = self.request("GET", "/v1/summary")
        self.assertEqual(status, 429)
        self.assertIn("retry-after", headers)
        self.assertEqual(payload["error"]["code"], "rate_limited")


class AuthTests(HttpServerCase):
    env = {"SENTINELIQ_API_KEYS": "viewer-key-123456:viewer:acme,responder-key-123456:responder:acme,admin-key-123456:admin:globex"}

    def test_missing_key(self):
        status, headers, payload, _ = self.request("GET", "/v1/incidents")
        self.assertEqual(status, 401)
        self.assertEqual(headers["www-authenticate"], "Bearer")
        self.assertEqual(payload["error"]["code"], "unauthorized")

    def test_viewer_can_read_but_not_write(self):
        headers = {"X-API-Key": "viewer-key-123456"}
        status, _, _, _ = self.request("GET", "/v1/incidents", headers=headers)
        self.assertEqual(status, 200)
        status, _, payload, _ = self.request("POST", "/v1/incidents", incident(), headers)
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "forbidden")

    def test_responder_can_write(self):
        status, _, _, _ = self.request("POST", "/v1/incidents", incident(), {"X-API-Key": "responder-key-123456"})
        self.assertEqual(status, 201)

    def test_admin_can_read_metrics_and_export(self):
        headers = {"Authorization": "Bearer admin-key-123456"}
        for path in ("/metrics", "/v1/export?format=json"):
            with self.subTest(path=path):
                status, _, _, _ = self.request("GET", path, headers=headers)
                self.assertEqual(status, 200)

    def test_health_stays_public(self):
        status, _, _, _ = self.request("GET", "/health")
        self.assertEqual(status, 200)

    def test_tenants_are_isolated(self):
        self.request("POST", "/v1/incidents", incident(), {"X-API-Key": "responder-key-123456"})
        status, _, payload, _ = self.request("GET", "/v1/incidents", headers={"X-API-Key": "admin-key-123456"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 0)


class CorsTests(HttpServerCase):
    env = {"SENTINELIQ_CORS_ORIGINS": "*"}

    def test_preflight_and_response_headers(self):
        status, headers, _, _ = self.request(
            "OPTIONS",
            "/v1/incidents",
            headers={"Origin": "https://ops.example.com", "Access-Control-Request-Method": "POST"},
        )
        self.assertEqual(status, 204)
        self.assertEqual(headers["access-control-allow-origin"], "*")
        self.assertIn("X-API-Key", headers["access-control-allow-headers"])

        status, headers, _, _ = self.request("GET", "/health", headers={"Origin": "https://ops.example.com"})
        self.assertEqual(headers["access-control-allow-origin"], "*")
        self.assertEqual(headers["vary"], "Origin")


class IngestAndExportTests(HttpServerCase):
    def test_alertmanager_ingest_and_dedupe(self):
        payload = {
            "alerts": [
                {
                    "labels": {"alertname": "LatencyHigh", "service": "search-api", "severity": "critical", "category": "latency"},
                    "annotations": {"description": "p99 over 2s"},
                }
            ]
        }
        status, _, first, _ = self.request("POST", "/v1/ingest/alertmanager", payload)
        self.assertEqual(status, 201)
        status, _, second, _ = self.request("POST", "/v1/ingest/alertmanager", payload)
        self.assertEqual(status, 200)
        self.assertEqual(second["deduplicated"], 1)

    def test_unknown_ingest_source(self):
        status, _, _, _ = self.request("POST", "/v1/ingest/unknown", {})
        self.assertEqual(status, 404)

    def test_export_csv(self):
        self.create_incident()
        status, headers, _, raw = self.request("GET", "/v1/export?format=csv")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["content-disposition"])
        self.assertIn(b"payments-api", raw)

    def test_cron_requires_secret(self):
        status, _, _, _ = self.request("GET", "/v1/cron/tick")
        self.assertEqual(status, 404)

    def test_import(self):
        payload = {"incidents": [incident(service="imported-http")]}
        status, _, result, _ = self.request("POST", "/v1/import", payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["imported"], 1)


class CronTests(HttpServerCase):
    env = {"SENTINELIQ_CRON_SECRET": "cron-secret-123"}

    def test_tick_with_secret(self):
        status, _, payload, _ = self.request("GET", "/v1/cron/tick", headers={"X-Cron-Secret": "cron-secret-123"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["processed"], 0)
        status, _, _, _ = self.request("GET", "/v1/cron/tick", headers={"X-Cron-Secret": "wrong"})
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
