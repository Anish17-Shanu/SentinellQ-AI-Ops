"""Minimal, optional Sentry error reporting (only active when SENTRY_DSN is set)."""

from __future__ import annotations

import json
import threading
import urllib.request
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse


def _endpoint(dsn: str) -> tuple[str, str] | None:
    parsed = urlparse(dsn)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.username:
        return None
    project = parsed.path.strip("/")
    if not project:
        return None
    return f"{parsed.scheme}://{parsed.hostname}{':' + str(parsed.port) if parsed.port else ''}/api/{project}/store/", parsed.username


def report_exception(dsn: str, exc: BaseException, request_id: str) -> None:
    if not dsn:
        return
    thread = threading.Thread(target=_send, args=(dsn, repr(exc), type(exc).__name__, request_id), daemon=True)
    thread.start()


def _send(dsn: str, message: str, exc_type: str, request_id: str) -> None:
    target = _endpoint(dsn)
    if target is None:
        return
    url, key = target
    payload = {
        "event_id": uuid.uuid4().hex,
        "timestamp": datetime.now(UTC).isoformat(),
        "level": "error",
        "platform": "python",
        "logger": "sentineliq",
        "exception": {"values": [{"type": exc_type, "value": message[:2000]}]},
        "tags": {"request_id": request_id},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Sentry-Auth": f"Sentry sentry_version=7, sentry_key={key}, sentry_client=sentineliq/1.0",
        },
        method="POST",
    )
    try:
        # _endpoint() only returns http(s) URLs.
        urllib.request.urlopen(request, timeout=3).close()  # nosec B310
    except Exception:  # noqa: BLE001 - telemetry must never break requests
        return
