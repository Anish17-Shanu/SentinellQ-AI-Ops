#!/usr/bin/env python3
"""Stdlib load tester with latency percentiles and performance-budget gates.

Examples:
    python scripts/load_test.py --base-url http://127.0.0.1:8081 --path /health --path /v1/incidents \
        --requests 500 --concurrency 20 --budget-p95-ms 50

Start the server with a high SENTINELIQ_RATE_LIMIT_RPM (or 0 to disable) when
load testing, otherwise 429 responses will be counted as failures.
"""

from __future__ import annotations

import argparse
import http.client
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class Sample:
    latencies: list[float] = field(default_factory=list)
    statuses: dict[int, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    elapsed: float = 0.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _request_once(connection: http.client.HTTPConnection, method: str, path: str, body: bytes | None, headers: dict):
    started = time.perf_counter()
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    response.read()
    return (time.perf_counter() - started) * 1000.0, response.status


def run_scenario(
    base_url: str,
    path: str,
    method: str,
    body: bytes | None,
    headers: dict[str, str],
    requests: int,
    concurrency: int,
    timeout: float,
    warmup: int,
) -> Sample:
    parts = urlsplit(base_url)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    connection_class = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection

    warm_connection = connection_class(host, port, timeout=timeout)
    try:
        for _ in range(warmup):
            _request_once(warm_connection, method, path, body, headers)
    except OSError:
        pass
    finally:
        warm_connection.close()

    sample = Sample()
    work: queue.Queue[int] = queue.Queue()
    for index in range(requests):
        work.put(index)
    lock = threading.Lock()

    def worker() -> None:
        connection = connection_class(host, port, timeout=timeout)
        try:
            while True:
                try:
                    work.get_nowait()
                except queue.Empty:
                    return
                try:
                    latency, status = _request_once(connection, method, path, body, headers)
                except Exception as exc:  # noqa: BLE001 - network failures are reported, not raised
                    with lock:
                        sample.errors.append(f"{type(exc).__name__}: {exc}")
                    connection.close()
                    connection = connection_class(host, port, timeout=timeout)
                    continue
                with lock:
                    sample.latencies.append(latency)
                    sample.statuses[status] = sample.statuses.get(status, 0) + 1
                if status == 429 or status >= 500:
                    connection.close()
                    connection = connection_class(host, port, timeout=timeout)
        finally:
            connection.close()

    started = time.perf_counter()
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, concurrency))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.perf_counter() - started
    sample.elapsed = elapsed
    return sample


def _report(path: str, sample: Sample) -> dict:
    latencies = sample.latencies
    elapsed = sample.elapsed
    failures = sum(count for status, count in sample.statuses.items() if status >= 400)
    result = {
        "path": path,
        "requests": len(latencies) + len(sample.errors),
        "ok": len(latencies) - failures,
        "failures": failures,
        "errors": len(sample.errors),
        "statuses": {str(key): value for key, value in sorted(sample.statuses.items())},
        "requests_per_second": round(len(latencies) / elapsed, 1) if elapsed else 0.0,
        "min_ms": round(min(latencies), 2) if latencies else 0.0,
        "mean_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
        "p50_ms": round(_percentile(latencies, 0.50), 2),
        "p95_ms": round(_percentile(latencies, 0.95), 2),
        "p99_ms": round(_percentile(latencies, 0.99), 2),
        "max_ms": round(max(latencies), 2) if latencies else 0.0,
        "error_details": sample.errors[:5],
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SentinelIQ load test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--path", action="append", help="path to exercise (repeatable, default /health)")
    parser.add_argument("--method", default="GET", choices=["GET", "POST"])
    parser.add_argument("--body", help="JSON request body for POST scenarios")
    parser.add_argument("--api-key", help="bearer token sent with every request")
    parser.add_argument("--requests", type=int, default=300, help="measured requests per path")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--budget-p95-ms", type=float, default=None, help="fail when p95 exceeds this value")
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = parser.parse_args(argv)

    paths = args.path or ["/health"]
    headers = {"Accept": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    body = None
    if args.body:
        body = args.body.encode("utf-8")
        headers["Content-Type"] = "application/json"

    results = []
    for path in paths:
        sample = run_scenario(
            args.base_url,
            path,
            args.method,
            body,
            headers,
            args.requests,
            args.concurrency,
            args.timeout,
            args.warmup,
        )
        results.append(_report(path, sample))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for result in results:
            print(f"\n{args.method} {result['path']}")
            print(
                f"  requests={result['requests']} ok={result['ok']} failures={result['failures']} "
                f"errors={result['errors']} rps={result['requests_per_second']}"
            )
            print(
                f"  latency ms: min={result['min_ms']} mean={result['mean_ms']} p50={result['p50_ms']} "
                f"p95={result['p95_ms']} p99={result['p99_ms']} max={result['max_ms']}"
            )
            print(f"  statuses: {result['statuses']}")
            if result["error_details"]:
                print(f"  sample errors: {result['error_details']}")

    failed = any(result["failures"] or result["errors"] for result in results)
    over_budget = args.budget_p95_ms is not None and any(result["p95_ms"] > args.budget_p95_ms for result in results)
    if over_budget:
        print(f"\nFAIL: p95 exceeded budget of {args.budget_p95_ms} ms", file=sys.stderr)
    elif failed:
        print("\nFAIL: requests failed; check statuses and error details", file=sys.stderr)
    else:
        print("\nPASS: all requests succeeded and latency is within budget.")
    return 1 if failed or over_budget else 0


if __name__ == "__main__":
    raise SystemExit(main())
