"""Measure ASGI cold start: module import plus the first request through the lazy app.

    python scripts/cold_start.py --runs 3 --budget-ms 2000

Each run uses a fresh subprocess and a temporary JSON store, so no warm state leaks
between samples. Exits 1 when the slowest run exceeds the budget.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = r"""
import asyncio
import json
import sys
import time

sys.path.insert(0, sys.argv[1])
started = time.perf_counter()
import asgi
imported = time.perf_counter()

captured = {}


async def main():
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 0),
        "scheme": "http",
    }
    await asgi.app(scope, receive, send)


request_started = time.perf_counter()
asyncio.run(main())
finished = time.perf_counter()

print(json.dumps({
    "import_ms": (imported - started) * 1000,
    "first_request_ms": (finished - request_started) * 1000,
    "total_ms": (finished - started) * 1000,
    "status": captured.get("status"),
}))
"""


def run_once() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "SENTINELIQ_ENV": "test",
            "SENTINELIQ_STORAGE": "json",
            "SENTINELIQ_DATA_FILE": str(Path(tmp) / "incidents.json"),
            "SENTINELIQ_AUTO_SEED": "false",
            "SENTINELIQ_RATE_LIMIT_RPM": "0",
            "SENTINELIQ_WEIGHTS_FILE": str(ROOT / "config" / "weights.json"),
            "SENTINELIQ_PLAYBOOKS_FILE": str(ROOT / "config" / "playbooks.json"),
        }
        completed = subprocess.run(
            [sys.executable, "-c", PROBE, str(ROOT / "src")],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )
    if completed.returncode != 0:
        raise SystemExit(f"cold-start probe failed:\n{completed.stderr}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description="ASGI cold-start measurement")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--budget-ms", type=float, default=2000.0)
    args = parser.parse_args()

    samples = [run_once() for _ in range(max(1, args.runs))]
    totals = [sample["total_ms"] for sample in samples]
    imports = [sample["import_ms"] for sample in samples]
    statuses = {sample["status"] for sample in samples}
    print(
        json.dumps(
            {
                "runs": len(samples),
                "import_ms_p50": round(statistics.median(imports), 1),
                "total_ms_p50": round(statistics.median(totals), 1),
                "total_ms_max": round(max(totals), 1),
                "budget_ms": args.budget_ms,
                "statuses": sorted(statuses),
            }
        )
    )
    if statuses != {200}:
        print("cold-start probe did not return 200", file=sys.stderr)
        return 1
    if max(totals) > args.budget_ms:
        print(f"cold start exceeded budget: {max(totals):.1f} ms > {args.budget_ms:.1f} ms", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
