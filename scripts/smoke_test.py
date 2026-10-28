"""Release smoke test: verifies a running instance (container, Vercel, or local).

    python scripts/smoke_test.py --base-url https://sentineliq.example.com --api-key "$KEY"

Exits 1 on the first failed check. Requires no third-party packages.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from sentineliq_client import SentinelIQClient, SentinelIQError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="SentinelIQ smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    client = SentinelIQClient(args.base_url, api_key=args.api_key, timeout=args.timeout)
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, fn) -> None:
        try:
            detail = fn()
            checks.append((name, True, detail))
            print(f"PASS {name}: {detail}")
        except (SentinelIQError, KeyError, ValueError) as exc:
            checks.append((name, False, str(exc)))
            print(f"FAIL {name}: {exc}")

    check("health", lambda: client.health()["status"])
    check("ready", lambda: f"storage={client.ready()['storage']}")
    check("openapi", lambda: f"{len(client.request('GET', '/openapi.json')['paths'])} paths")
    check("summary", lambda: f"{client.summary()['total_incidents']} incidents")
    check("incidents", lambda: f"{client.list_incidents(limit=1)['total']} total")
    check("dashboard", lambda: f"{len(client.request('GET', '/dashboard/'))} bytes")
    if args.api_key:
        check("metrics", lambda: f"{len(client.metrics())} bytes")

    failed = [name for name, ok, _ in checks if not ok]
    if failed:
        print(f"\n{len(failed)} check(s) failed: {', '.join(failed)}")
        return 1
    print(f"\nAll {len(checks)} checks passed against {args.base_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
