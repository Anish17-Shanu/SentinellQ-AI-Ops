"""SentinelIQ AI Ops server created by Anish Kumar."""

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine import PLAYBOOKS, score_incident, summarize_incidents
from repository import IncidentRepository


HOST = "127.0.0.1"
PORT = 8081
REPOSITORY = IncidentRepository()
DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"


class AppHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path):
        body = file_path.read_bytes()
        mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length)
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            raise ValueError("Invalid JSON payload")

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path in ("/dashboard", "/dashboard/"):
            self._send_file(DASHBOARD_DIR / "index.html")
            return
        if parsed.path.startswith("/dashboard/"):
            requested = parsed.path.replace("/dashboard/", "", 1)
            file_path = (DASHBOARD_DIR / requested).resolve()
            if file_path.exists() and file_path.is_file() and str(file_path).startswith(str(DASHBOARD_DIR.resolve())):
                self._send_file(file_path)
                return
            self._send_json({"error": "Asset not found"}, status=404)
            return

        if parsed.path == "/health":
            incidents = REPOSITORY.list()
            summary = summarize_incidents(incidents)
            self._send_json(
                {
                    "service": "SentinelIQ AI Ops",
                    "status": "ok",
                    "repository_count": summary["total_incidents"],
                    "highest_priority_count": summary["by_priority"]["P1"],
                }
            )
            return

        if parsed.path == "/playbooks":
            self._send_json({"items": PLAYBOOKS})
            return

        if parsed.path == "/incidents":
            incidents = REPOSITORY.list(
                priority=query.get("priority", [""])[0],
                owner=query.get("owner", [""])[0],
                environment=query.get("environment", [""])[0],
            )
            self._send_json(
                {
                    "count": len(incidents),
                    "items": [{"input": item, "assessment": score_incident(item)} for item in incidents],
                }
            )
            return

        if parsed.path == "/summary":
            incidents = REPOSITORY.list()
            self._send_json(summarize_incidents(incidents))
            return

        self._send_json(
            {
                "service": "SentinelIQ AI Ops",
                "status": "running",
                "routes": ["/health", "/playbooks", "/incidents", "/summary", "/score"],
            }
        )

    def do_POST(self):
        if self.path == "/score":
            try:
                payload = self._read_json()
                self._send_json(score_incident(payload))
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        if self.path == "/incidents":
            try:
                created = REPOSITORY.create(self._read_json())
                self._send_json({"created": created, "assessment": score_incident(created)}, status=201)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
            return

        self._send_json({"error": "Not found"}, status=404)

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"SentinelIQ AI Ops listening on http://{HOST}:{PORT}")
    server.serve_forever()