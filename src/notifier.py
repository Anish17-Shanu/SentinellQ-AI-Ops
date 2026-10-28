"""Outbound webhook notifications (Slack-compatible or generic JSON)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from errors import ConfigError


class WebhookNotifier:
    def __init__(self, url: str, timeout: float = 5.0) -> None:
        if url and not (url.startswith("http://") or url.startswith("https://")):
            raise ConfigError("SENTINELIQ_NOTIFY_WEBHOOK_URL must start with http:// or https://")
        self.url = url
        self.timeout = timeout
        self.is_slack = "hooks.slack.com" in url

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def send(self, event: dict) -> None:
        if not self.enabled:
            return
        payload = {"text": self._slack_text(event)} if self.is_slack else event
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "SentinelIQ/1.0"},
            method="POST",
        )
        try:
            # Scheme is restricted to http(s) in __init__.
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # nosec B310
                if response.status >= 300:
                    raise RuntimeError(f"webhook returned HTTP {response.status}")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"webhook delivery failed: {exc}") from exc

    @staticmethod
    def _slack_text(event: dict) -> str:
        incident = event.get("incident", {})
        return (
            f"*[{incident.get('priority', '?')}] {incident.get('service', 'unknown')}* "
            f"({event.get('event', 'event')}) - {incident.get('explanation', '')}"
        )
