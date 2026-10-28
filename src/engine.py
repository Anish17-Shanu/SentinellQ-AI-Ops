"""Deterministic, explainable incident scoring.

The engine has no third-party dependencies and makes no network calls. Weight
configuration is optional; the defaults reproduce the original v0.1 behaviour.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from errors import ConfigError, ValidationError

DEFAULT_CRITICALITY_WEIGHT = {"low": 8.0, "medium": 18.0, "high": 30.0, "critical": 42.0}
DEFAULT_BANDS = {"P1": 85.0, "P2": 65.0, "P3": 42.0}

PLAYBOOKS = {
    "availability": [
        "Page the owning service team immediately.",
        "Shift traffic or activate a fallback if available.",
        "Start a short incident timeline and customer impact summary.",
    ],
    "security": [
        "Isolate suspicious access paths and rotate affected credentials.",
        "Preserve logs before applying mitigations.",
        "Escalate to security stakeholders for containment review.",
    ],
    "data": [
        "Freeze destructive writes until blast radius is understood.",
        "Validate backups and start data integrity checks.",
        "Communicate affected records and restore strategy early.",
    ],
    "latency": [
        "Check dependency saturation and recent deploys.",
        "Throttle expensive workloads or scale the hot path.",
        "Compare p95 latency against the last healthy window.",
    ],
}

VALID_CATEGORIES = {"availability", "security", "data", "latency"}
VALID_CRITICALITIES = {"low", "medium", "high", "critical"}
VALID_PRIORITIES = {"P1", "P2", "P3", "P4"}
PRIORITY_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}

MAX_IMPACTED_USERS = 1_000_000_000
MAX_ERROR_RATE = 100.0
MAX_LATENCY_MS = 3_600_000.0
MAX_ALERTS = 1_000_000


@dataclass(frozen=True)
class ScoreConfig:
    criticality_weights: dict = field(default_factory=lambda: dict(DEFAULT_CRITICALITY_WEIGHT))
    max_user_impact: float = 25.0
    user_impact_scale: float = 12.0
    max_error: float = 20.0
    error_rate_multiplier: float = 1.8
    max_latency: float = 16.0
    latency_ceiling_ms: float = 2000.0
    max_alerts: float = 12.0
    alert_multiplier: float = 1.1
    bands: dict = field(default_factory=lambda: dict(DEFAULT_BANDS))
    confidence_base: float = 0.55
    confidence_alert_weight: float = 0.20
    confidence_user_weight: float = 0.20
    confidence_min: float = 0.50
    confidence_max: float = 0.96

    @property
    def score_max(self) -> float:
        return (
            max(self.criticality_weights.values())
            + self.max_user_impact
            + self.max_error
            + self.max_latency
            + self.max_alerts
        )


DEFAULT_CONFIG = ScoreConfig()


def _positive(value, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"weights.{name} must be a number") from exc
    if parsed <= 0:
        raise ConfigError(f"weights.{name} must be greater than zero")
    return parsed


def load_weights(path: Path | None) -> ScoreConfig:
    if path is None or not Path(path).exists():
        return DEFAULT_CONFIG
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Unable to read weights file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("weights file must contain a JSON object")

    weights = raw.get("criticality_weights", DEFAULT_CRITICALITY_WEIGHT)
    if not isinstance(weights, dict) or set(weights) != VALID_CRITICALITIES:
        raise ConfigError("weights.criticality_weights must define low, medium, high, critical")
    weights = {key: _positive(value, f"criticality_weights.{key}") for key, value in weights.items()}

    bands = raw.get("bands", DEFAULT_BANDS)
    if not isinstance(bands, dict) or set(bands) != {"P1", "P2", "P3"}:
        raise ConfigError("weights.bands must define P1, P2, P3")
    bands = {key: _positive(value, f"bands.{key}") for key, value in bands.items()}
    if not (bands["P1"] > bands["P2"] > bands["P3"]):
        raise ConfigError("weights.bands must satisfy P1 > P2 > P3")

    return ScoreConfig(
        criticality_weights=weights,
        max_user_impact=_positive(raw.get("max_user_impact", 25.0), "max_user_impact"),
        user_impact_scale=_positive(raw.get("user_impact_scale", 12.0), "user_impact_scale"),
        max_error=_positive(raw.get("max_error", 20.0), "max_error"),
        error_rate_multiplier=_positive(raw.get("error_rate_multiplier", 1.8), "error_rate_multiplier"),
        max_latency=_positive(raw.get("max_latency", 16.0), "max_latency"),
        latency_ceiling_ms=_positive(raw.get("latency_ceiling_ms", 2000.0), "latency_ceiling_ms"),
        max_alerts=_positive(raw.get("max_alerts", 12.0), "max_alerts"),
        alert_multiplier=_positive(raw.get("alert_multiplier", 1.1), "alert_multiplier"),
        bands=bands,
    )


def load_playbooks(path: Path | None) -> dict:
    default = {"defaults": {key: list(value) for key, value in PLAYBOOKS.items()}, "service_overrides": {}}
    if path is None or not Path(path).exists():
        return default
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Unable to read playbooks file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("playbooks file must contain a JSON object")

    defaults = raw.get("defaults", default["defaults"])
    if not isinstance(defaults, dict) or set(defaults) != VALID_CATEGORIES:
        raise ConfigError("playbooks.defaults must define availability, security, data, latency")
    cleaned_defaults = {}
    for category, actions in defaults.items():
        if not isinstance(actions, list) or not actions or not all(isinstance(item, str) and item.strip() for item in actions):
            raise ConfigError(f"playbooks.defaults.{category} must be a non-empty list of strings")
        cleaned_defaults[category] = [item.strip() for item in actions]

    overrides_raw = raw.get("service_overrides", {})
    if not isinstance(overrides_raw, dict):
        raise ConfigError("playbooks.service_overrides must be an object")
    overrides: dict[str, dict[str, list[str]]] = {}
    for service, categories in overrides_raw.items():
        if not isinstance(categories, dict):
            raise ConfigError(f"playbooks.service_overrides.{service} must be an object")
        overrides[str(service)] = {}
        for category, actions in categories.items():
            if category not in VALID_CATEGORIES:
                raise ConfigError(f"Unknown playbook category: {category}")
            if not isinstance(actions, list) or not all(isinstance(item, str) and item.strip() for item in actions):
                raise ConfigError(f"playbooks.service_overrides.{service}.{category} must be a list of strings")
            overrides[str(service)][category] = [item.strip() for item in actions]
    return {"defaults": cleaned_defaults, "service_overrides": overrides}


def resolve_actions(playbooks: dict, service: str, category: str) -> list[str]:
    override = playbooks.get("service_overrides", {}).get(service, {}).get(category)
    if override:
        return list(override)
    defaults = playbooks.get("defaults", PLAYBOOKS)
    return list(defaults.get(category, defaults.get("availability", PLAYBOOKS["availability"])))


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def parse_int(value, field_name: str, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field_name} must be an integer", field=field_name) from None
    if parsed < 0:
        raise ValidationError(f"{field_name} must be non-negative", field=field_name)
    if maximum is not None and parsed > maximum:
        raise ValidationError(f"{field_name} must be <= {maximum}", field=field_name)
    return parsed


def parse_float(value, field_name: str, maximum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field_name} must be a number", field=field_name) from None
    if parsed < 0:
        raise ValidationError(f"{field_name} must be non-negative", field=field_name)
    if maximum is not None and parsed > maximum:
        raise ValidationError(f"{field_name} must be <= {maximum}", field=field_name)
    return parsed


def _text(payload: dict, key: str, default: str = "", max_length: int = 200) -> str:
    raw = payload.get(key, default)
    if raw is None:
        raw = default
    value = str(raw).strip()
    if len(value) > max_length:
        raise ValidationError(f"{key} must be at most {max_length} characters", field=key)
    return value


def normalize_incident(payload) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object")

    service = _text(payload, "service", max_length=120)
    if not service:
        raise ValidationError("service is required", field="service")
    owner = _text(payload, "owner", "platform-core", max_length=120) or "platform-core"
    environment = (_text(payload, "environment", "production", max_length=60) or "production").lower()
    criticality = (_text(payload, "criticality", "medium", max_length=20) or "medium").lower()
    category = (_text(payload, "category", "availability", max_length=20) or "availability").lower()
    description = _text(payload, "description", max_length=2000)
    priority_hint = (_text(payload, "priority_hint", max_length=4) or "").upper()

    if criticality not in VALID_CRITICALITIES:
        raise ValidationError("criticality must be one of low, medium, high, critical", field="criticality")
    if category not in VALID_CATEGORIES:
        raise ValidationError("category must be one of availability, security, data, latency", field="category")
    if priority_hint and priority_hint not in VALID_PRIORITIES:
        raise ValidationError("priority_hint must be one of P1, P2, P3, P4", field="priority_hint")

    return {
        "service": service,
        "owner": owner,
        "environment": environment,
        "description": description,
        "criticality": criticality,
        "impacted_users": parse_int(payload.get("impacted_users", 0), "impacted_users", MAX_IMPACTED_USERS),
        "error_rate": parse_float(payload.get("error_rate", 0.0), "error_rate", MAX_ERROR_RATE),
        "latency_ms": parse_float(payload.get("latency_ms", 0.0), "latency_ms", MAX_LATENCY_MS),
        "alerts": parse_int(payload.get("alerts", 0), "alerts", MAX_ALERTS),
        "category": category,
        "priority_hint": priority_hint,
    }


def _priority_for_score(weighted_total: float, bands: dict) -> str:
    if weighted_total >= bands["P1"]:
        return "P1"
    if weighted_total >= bands["P2"]:
        return "P2"
    if weighted_total >= bands["P3"]:
        return "P3"
    return "P4"


def _risk_band(priority: str) -> str:
    return {"P1": "severe", "P2": "high", "P3": "moderate"}.get(priority, "low")


def _top_factors(config: ScoreConfig, criticality_score, user_impact_score, error_score, latency_score, alert_score) -> list[str]:
    factors = []
    if criticality_score >= 0.7 * max(config.criticality_weights.values()):
        factors.append("critical service classification")
    if user_impact_score >= 0.64 * config.max_user_impact:
        factors.append("large customer impact")
    if error_score >= 0.60 * config.max_error:
        factors.append("elevated failure rate")
    if latency_score >= 0.625 * config.max_latency:
        factors.append("severe latency regression")
    if alert_score >= 0.66 * config.max_alerts:
        factors.append("sustained alert pressure")
    if not factors:
        factors.append("moderate operational instability")
    return factors


def assess_incident(incident: dict, config: ScoreConfig | None = None, playbooks: dict | None = None) -> dict:
    """Score an already-normalized incident and return the assessment fields."""

    config = config or DEFAULT_CONFIG
    criticality_score = config.criticality_weights[incident["criticality"]]
    user_impact_score = clamp(math.log10(max(incident["impacted_users"], 1)) * config.user_impact_scale, 0, config.max_user_impact)
    error_score = clamp(incident["error_rate"] * config.error_rate_multiplier, 0, config.max_error)
    latency_score = clamp((incident["latency_ms"] / config.latency_ceiling_ms) * config.max_latency, 0, config.max_latency)
    alert_score = clamp(incident["alerts"] * config.alert_multiplier, 0, config.max_alerts)

    weighted_total = round(
        criticality_score + user_impact_score + error_score + latency_score + alert_score, 2
    )
    priority = _priority_for_score(weighted_total, config.bands)
    hint = incident.get("priority_hint") or ""
    hinted = bool(hint) and PRIORITY_RANK[hint] < PRIORITY_RANK[priority]
    if hinted:
        priority = hint
    score_100 = round(clamp(weighted_total / config.score_max * 100, 0, 100), 1)

    alert_factor = clamp(incident["alerts"] / config.max_alerts, 0, 1)
    user_factor = clamp(math.log10(incident["impacted_users"] + 1) / 5, 0, 1)
    confidence = round(
        clamp(
            config.confidence_base
            + config.confidence_alert_weight * alert_factor
            + config.confidence_user_weight * user_factor,
            config.confidence_min,
            config.confidence_max,
        ),
        2,
    )
    factors = _top_factors(
        config, criticality_score, user_impact_score, error_score, latency_score, alert_score
    )
    if hinted:
        factors = [*factors, "operator priority hint"]
    actions = resolve_actions(playbooks or {"defaults": PLAYBOOKS}, incident["service"], incident["category"])

    return {
        "priority": priority,
        "score": weighted_total,
        "score_100": score_100,
        "confidence": confidence,
        "risk_band": _risk_band(priority),
        "explanation": (
            f"{incident['service']} was classified as {priority} because of {', '.join(factors)}."
            if not hinted
            else f"{incident['service']} was escalated to {priority} by the operator hint; score factors: {', '.join(factors[:-1])}."
        ),
        "factors": factors,
        "actions": deepcopy(actions),
        "dimensions": {
            "criticality": round(criticality_score, 2),
            "user_impact": round(user_impact_score, 2),
            "error_rate": round(error_score, 2),
            "latency": round(latency_score, 2),
            "alert_pressure": round(alert_score, 2),
        },
    }


def score_incident(payload, config: ScoreConfig | None = None, playbooks: dict | None = None) -> dict:
    incident = normalize_incident(payload)
    assessment = assess_incident(incident, config, playbooks)
    return {"incident": incident, **assessment}


def summarize_incidents(items, config: ScoreConfig | None = None, playbooks: dict | None = None) -> dict:
    assessments = [score_incident(item, config, playbooks) for item in items]
    summary: dict[str, Any] = {
        "total_incidents": len(assessments),
        "by_priority": {"P1": 0, "P2": 0, "P3": 0, "P4": 0},
        "max_score": 0,
        "average_score": 0,
        "services": [],
    }

    total_score = 0.0
    for assessment in assessments:
        summary["by_priority"][assessment["priority"]] += 1
        summary["max_score"] = max(summary["max_score"], assessment["score"])
        total_score += assessment["score"]
        summary["services"].append(
            {
                "service": assessment["incident"]["service"],
                "priority": assessment["priority"],
                "score": assessment["score"],
                "owner": assessment["incident"]["owner"],
            }
        )

    if assessments:
        summary["average_score"] = round(total_score / len(assessments), 2)
    return summary
