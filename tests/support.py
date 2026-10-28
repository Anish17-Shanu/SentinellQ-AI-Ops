"""Shared fixtures for the test suite."""

from __future__ import annotations

import copy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT / "src", ROOT / "tests"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from config import load_settings  # noqa: E402

BASE_INCIDENT = {
    "service": "payments-api",
    "owner": "payments",
    "environment": "production",
    "criticality": "critical",
    "impacted_users": 18000,
    "error_rate": 18.0,
    "latency_ms": 2100,
    "alerts": 20,
    "category": "availability",
    "description": "Checkout authorization failures",
}


def make_settings(tmp_path, **overrides):
    environ = {
        "SENTINELIQ_ENV": "test",
        "SENTINELIQ_DATA_FILE": str(Path(tmp_path) / "incidents.json"),
        "SENTINELIQ_SQLITE_PATH": str(Path(tmp_path) / "incidents.db"),
        "SENTINELIQ_RATE_LIMIT_RPM": "0",
        "SENTINELIQ_AUTO_SEED": "false",
        "SENTINELIQ_WEIGHTS_FILE": str(ROOT / "config" / "weights.json"),
        "SENTINELIQ_PLAYBOOKS_FILE": str(ROOT / "config" / "playbooks.json"),
    }
    environ.update({key: str(value) for key, value in overrides.items()})
    return load_settings(environ)


def incident(**overrides):
    payload = copy.deepcopy(BASE_INCIDENT)
    payload.update(overrides)
    return payload


class FakeNotifier:
    enabled = True

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict] = []

    def send(self, event: dict) -> None:
        if self.fail:
            raise RuntimeError("webhook unavailable")
        self.sent.append(event)


class FakeClock:
    def __init__(self, start: str = "2026-10-29T10:00:00Z") -> None:
        self.value = datetime.fromisoformat(start.replace("Z", "+00:00"))

    def __call__(self) -> str:
        return self.value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def advance(self, seconds: int) -> None:
        self.value = self.value + timedelta(seconds=seconds)
