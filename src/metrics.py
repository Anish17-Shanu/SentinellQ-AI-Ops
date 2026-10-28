"""Prometheus-compatible request and domain metrics."""

from __future__ import annotations

import threading

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class Metrics:
    def __init__(self, version: str) -> None:
        self.version = version
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, str], int] = {}
        self._latency_counts: dict[tuple[str, str, float], int] = {}
        self._latency_sum: dict[tuple[str, str], float] = {}
        self._latency_total: dict[tuple[str, str], int] = {}
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def observe_request(self, method: str, route: str, status: int, duration_seconds: float) -> None:
        with self._lock:
            key = (method, route, f"{status // 100}xx")
            self._requests[key] = self._requests.get(key, 0) + 1
            hist_key = (method, route)
            self._latency_sum[hist_key] = self._latency_sum.get(hist_key, 0.0) + duration_seconds
            self._latency_total[hist_key] = self._latency_total.get(hist_key, 0) + 1
            for bucket in LATENCY_BUCKETS:
                if duration_seconds <= bucket:
                    bkey = (method, route, bucket)
                    self._latency_counts[bkey] = self._latency_counts.get(bkey, 0) + 1

    def inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        normalized = tuple(sorted((labels or {}).items()))
        with self._lock:
            key = (name, normalized)
            self._counters[key] = self._counters.get(key, 0.0) + value

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            requests = sorted(self._requests.items())
            latency_counts = dict(self._latency_counts)
            latency_sum = dict(self._latency_sum)
            latency_total = dict(self._latency_total)
            counters = sorted(self._counters.items())

        lines.append("# HELP sentineliq_build_info Build information")
        lines.append("# TYPE sentineliq_build_info gauge")
        lines.append(f'sentineliq_build_info{{version="{_escape(self.version)}"}} 1')
        lines.append("# HELP sentineliq_http_requests_total HTTP requests by method, route and status class")
        lines.append("# TYPE sentineliq_http_requests_total counter")
        for (method, route, status), count in requests:
            lines.append(
                f'sentineliq_http_requests_total{{method="{_escape(method)}",route="{_escape(route)}",status="{status}"}} {count}'
            )
        lines.append("# HELP sentineliq_http_request_duration_seconds HTTP request latency")
        lines.append("# TYPE sentineliq_http_request_duration_seconds histogram")
        for (method, route) in sorted(latency_total):
            base = f'method="{_escape(method)}",route="{_escape(route)}"'
            for bucket in LATENCY_BUCKETS:
                count = latency_counts.get((method, route, bucket), 0)
                lines.append(f'sentineliq_http_request_duration_seconds_bucket{{{base},le="{bucket}"}} {count}')
            total = latency_total[(method, route)]
            lines.append(f'sentineliq_http_request_duration_seconds_bucket{{{base},le="+Inf"}} {total}')
            lines.append(f"sentineliq_http_request_duration_seconds_sum{{{base}}} {latency_sum[(method, route)]:.6f}")
            lines.append(f"sentineliq_http_request_duration_seconds_count{{{base}}} {total}")
        for (name, labels), value in counters:
            if labels:
                label_text = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
                lines.append(f"{name}{{{label_text}}} {value}")
            else:
                lines.append(f"{name} {value}")
        return "\n".join(lines) + "\n"
