"""End-to-end quickstart: score, create, acknowledge, and summarize.

Run against a local server:
    python src/server.py
    python examples/quickstart.py

Set SENTINELIQ_URL and SENTINELIQ_API_KEY for a deployed instance.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from sentineliq_client import SentinelIQClient  # noqa: E402


def main() -> None:
    client = SentinelIQClient(
        os.getenv("SENTINELIQ_URL", "http://127.0.0.1:8081"),
        api_key=os.getenv("SENTINELIQ_API_KEY"),
    )
    print("health:", client.health())

    preview = client.score(
        service="payments-api",
        criticality="critical",
        impacted_users=12000,
        error_rate=41,
        latency_ms=2400,
        alerts=3,
    )
    print(f"score preview: {preview['score_100']}/100 -> {preview['priority']} ({preview['risk_band']})")

    incident = client.create_incident(
        service="payments-api",
        owner="payments",
        environment="production",
        criticality="critical",
        category="availability",
        impacted_users=12000,
        error_rate=41,
        latency_ms=2400,
        description="Checkout 500s after deploy",
    )
    print(f"created: {incident['id']} {incident['priority']} score={incident['score_100']}")

    acknowledged = client.transition(incident["id"], "acknowledged", note="on-call paged")
    print(f"state: {acknowledged['state']} at {acknowledged['acknowledged_at']}")

    summary = client.summary()
    print(
        "summary:",
        {
            "total": summary["total_incidents"],
            "open": summary["open_incidents"],
            "p1_open": summary["p1_open"],
            "mtta_seconds": summary["mtta_seconds"],
        },
    )


if __name__ == "__main__":
    main()
