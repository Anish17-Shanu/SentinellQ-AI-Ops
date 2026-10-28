import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from support import FakeNotifier, incident, make_settings

from asgi import build_asgi_app
from bootstrap import build_context
from router import Request

KEYS = "viewer-key-123456:viewer:acme,admin-key-123456:admin:acme"


class AsgiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = make_settings(
            Path(self.tmp.name),
            SENTINELIQ_API_KEYS=KEYS,
            SENTINELIQ_AUTH_REQUIRED="true",
        )
        self.context = build_context(settings, notifier=FakeNotifier())
        self.app = build_asgi_app(self.context)

    def tearDown(self):
        self.context.repository.close()
        self.tmp.cleanup()

    def call(self, method, path, body=b"", headers=None, chunks=None, query=b""):
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query,
            "headers": [(key.lower().encode("latin-1"), value.encode("latin-1")) for key, value in (headers or {}).items()],
            "client": ("127.0.0.1", 4321),
            "scheme": "http",
        }
        if chunks is None:
            messages = [{"type": "http.request", "body": body, "more_body": False}]
        else:
            messages = [
                {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
                for index, chunk in enumerate(chunks)
            ]
        sent = []

        async def receive():
            return messages.pop(0) if messages else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        asyncio.run(self.app(scope, receive, send))
        start = next(message for message in sent if message["type"] == "http.response.start")
        body_bytes = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
        headers_out = {key.decode("latin-1"): value.decode("latin-1") for key, value in start["headers"]}
        return start["status"], headers_out, body_bytes

    def direct(self, method, path, body=b"", headers=None, query=None):
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(path)
        request = Request(
            method=method,
            path=parsed.path,
            query=query if query is not None else parse_qs(parsed.query),
            headers={key.lower(): value for key, value in (headers or {}).items()},
            body=body,
            client_ip="127.0.0.1",
        )
        response = self.context.router.handle(request)
        return response.status, response.body

    def test_health_matches_router(self):
        status, headers, body = self.call("GET", "/health")
        direct_status, direct_body = self.direct("GET", "/health")
        self.assertEqual(status, direct_status)
        self.assertEqual(json.loads(body)["status"], json.loads(direct_body)["status"])
        self.assertEqual(headers["x-content-type-options"], "nosniff")

    def test_summary_requires_auth_and_matches(self):
        status, _, _ = self.call("GET", "/v1/summary")
        self.assertEqual(status, 401)
        headers = {"Authorization": "Bearer admin-key-123456"}
        status, _, body = self.call("GET", "/v1/summary", headers=headers)
        direct_status, direct_body = self.direct("GET", "/v1/summary", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(status, direct_status)
        self.assertEqual(json.loads(body), json.loads(direct_body))

    def test_api_prefix_is_supported(self):
        headers = {"X-API-Key": "admin-key-123456"}
        status, _, body = self.call("GET", "/api/v1/incidents", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["total"], 0)

    def test_query_override_matches_router(self):
        status, _, body = self.call("GET", "/v1/incidents", query=b"__path=/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")

    def test_create_and_read_round_trip(self):
        headers = {"Content-Type": "application/json", "X-API-Key": "admin-key-123456"}
        status, _, body = self.call("POST", "/v1/incidents", json.dumps(incident()).encode(), headers)
        self.assertEqual(status, 201)
        record = json.loads(body)
        status, _, body = self.call("GET", f"/v1/incidents/{record['id']}", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["incident"]["priority"], "P1")

    def test_invalid_json_is_rejected(self):
        headers = {"Content-Type": "application/json", "X-API-Key": "admin-key-123456"}
        status, _, body = self.call("POST", "/v1/incidents", b"{nope", headers)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["field"], "body")

    def test_unknown_route(self):
        status, _, body = self.call("GET", "/missing")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"]["code"], "not_found")

    def test_oversized_chunked_body_is_rejected(self):
        headers = {"Content-Type": "application/json", "X-API-Key": "admin-key-123456"}
        chunks = [b"x" * 100000, b"y" * 100000, b"z" * 100000]
        status, _, body = self.call("POST", "/v1/incidents", headers=headers, chunks=chunks)
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["error"]["code"], "payload_too_large")

    def test_dashboard_asset(self):
        status, headers, body = self.call("GET", "/dashboard/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"))
        self.assertIn(b"SentinelIQ", body)

    def test_lifespan_does_not_crash(self):
        sent = []

        async def receive():
            if not sent:
                return {"type": "lifespan.startup"}
            return {"type": "lifespan.shutdown"}

        async def send(message):
            sent.append(message)

        asyncio.run(self.app({"type": "lifespan"}, receive, send))
        self.assertEqual(
            [message["type"] for message in sent],
            ["lifespan.startup.complete", "lifespan.shutdown.complete"],
        )


if __name__ == "__main__":
    unittest.main()
