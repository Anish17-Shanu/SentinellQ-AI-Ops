"""Ingest an Alertmanager-style payload and show deduplication.

    python examples/ingest_alert.py

The second send lands inside SENTINELIQ_DEDUP_WINDOW_SECONDS and updates the
same incident, incrementing alert_count instead of creating a duplicate.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from sentineliq_client import SentinelIQClient  # noqa: E402

ALERT = {
    "version": "4",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "CheckoutHighErrorRate",
                "service": "checkout",
                "severity": "critical",
                "environment": "production",
            },
            "annotations": {
                "summary": "Checkout 5xx above 10%",
                "description": "Error budget burn rate is 14x for the last 5 minutes.",
            },
        }
    ],
}


def main() -> None:
    client = SentinelIQClient(
        os.getenv("SENTINELIQ_URL", "http://127.0.0.1:8081"),
        api_key=os.getenv("SENTINELIQ_API_KEY"),
    )
    first = client.ingest("alertmanager", ALERT)
    incident_id = first["items"][0]["incident"]["id"]
    print("first ingest:", first["created"], "created,", first["deduplicated"], "deduplicated")

    second = client.ingest("alertmanager", ALERT)
    print("second ingest:", second["created"], "created,", second["deduplicated"], "deduplicated")

    detail = client.get_incident(incident_id)
    incident = detail.get("incident", detail)
    print("incident:", incident["id"], incident["priority"], "alerts:", incident.get("alert_count"))


if __name__ == "__main__":
    main()
