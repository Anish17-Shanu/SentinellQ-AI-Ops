"""Minimal stdlib-only client for the SentinelIQ HTTP API.

Copy this file into your project or add ``sdk/`` to ``sys.path``:

    from sentineliq_client import SentinelIQClient

    client = SentinelIQClient("https://sentineliq.example.com", api_key="...")
    client.create_incident(service="checkout", category="availability", criticality="critical")
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any


class SentinelIQError(RuntimeError):
    """Raised for non-2xx responses; carries the API error envelope."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.request_id = request_id
        self.details = details or {}


class SentinelIQClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8081", api_key: str | None = None, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # -- transport -----------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        if self.api_key:
            request_headers.setdefault("Authorization", f"Bearer {self.api_key}")
        request = urllib.request.Request(self.base_url + path, data=data, headers=request_headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            payload = _parse_json(raw)
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise SentinelIQError(
                str(error.get("message") or f"HTTP {exc.code}"),
                status=exc.code,
                code=error.get("code"),
                request_id=error.get("request_id"),
                details=error.get("details"),
            ) from exc
        except urllib.error.URLError as exc:
            raise SentinelIQError(f"cannot reach {self.base_url}: {exc.reason}") from exc
        if content_type.startswith("application/json"):
            return _parse_json(raw)
        return raw.decode("utf-8", errors="replace")

    # -- routes --------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self.request("GET", "/health")

    def ready(self) -> dict[str, Any]:
        return self.request("GET", "/v1/ready")

    def summary(self) -> dict[str, Any]:
        return self.request("GET", "/v1/summary")

    def list_incidents(self, **filters: Any) -> dict[str, Any]:
        params = {key: value for key, value in filters.items() if value not in (None, "")}
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        return self.request("GET", f"/v1/incidents{query}")

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/incidents/{incident_id}")

    def create_incident(self, **incident: Any) -> dict[str, Any]:
        return self.request("POST", "/v1/incidents", incident)

    def transition(self, incident_id: str, state: str, note: str = "") -> dict[str, Any]:
        return self.request("POST", f"/v1/incidents/{incident_id}/transitions", {"state": state, "note": note})

    def score(self, **incident: Any) -> dict[str, Any]:
        return self.request("POST", "/v1/score", incident)

    def ingest(self, source: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"/v1/ingest/{source}", payload)

    def export(self, fmt: str = "json") -> Any:
        return self.request("GET", f"/v1/export?format={fmt}")

    def import_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        return self.request("POST", "/v1/import", {"incidents": records})

    def metrics(self) -> str:
        return self.request("GET", "/metrics")

    def iter_incidents(self, page_size: int = 50, **filters: Any) -> Iterator[dict[str, Any]]:
        offset = 0
        while True:
            page = self.list_incidents(limit=page_size, offset=offset, **filters)
            items = page.get("items", [])
            yield from items
            offset += len(items)
            if not items or offset >= int(page.get("total", 0)):
                break


def _parse_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
