import math
from copy import deepcopy


CRITICALITY_WEIGHT = {"low": 8, "medium": 18, "high": 30, "critical": 42}
ALLOWED_CATEGORIES = {"availability", "security", "data", "latency"}

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


def clamp(value, lower, upper):
    return max(lower, min(value, upper))


def parse_int(value, field_name):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be an integer")
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return parsed


def parse_float(value, field_name):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a number")
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return parsed


def normalize_incident(payload):
    service = str(payload.get("service", "")).strip()
    owner = str(payload.get("owner", "platform-core")).strip() or "platform-core"
    environment = str(payload.get("environment", "production")).strip().lower() or "production"
    criticality = str(payload.get("criticality", "medium")).strip().lower() or "medium"
    category = str(payload.get("category", "availability")).strip().lower() or "availability"
    description = str(payload.get("description", "")).strip()

    if not service:
        raise ValueError("service is required")
    if criticality not in CRITICALITY_WEIGHT:
        raise ValueError("criticality must be one of low, medium, high, critical")
    if category not in ALLOWED_CATEGORIES:
        raise ValueError("category must be one of availability, security, data, latency")

    return {
        "service": service,
        "owner": owner,
        "environment": environment,
        "description": description,
        "criticality": criticality,
        "impacted_users": parse_int(payload.get("impacted_users", 0), "impacted_users"),
        "error_rate": parse_float(payload.get("error_rate", 0.0), "error_rate"),
        "latency_ms": parse_float(payload.get("latency_ms", 0.0), "latency_ms"),
        "alerts": parse_int(payload.get("alerts", 0), "alerts"),
        "category": category,
    }


def _priority_for_score(weighted_total):
    if weighted_total >= 85:
        return "P1"
    if weighted_total >= 65:
        return "P2"
    if weighted_total >= 42:
        return "P3"
    return "P4"


def _top_factors(criticality_score, user_impact_score, error_score, latency_score, alert_score):
    factors = []
    if criticality_score >= 30:
        factors.append("critical service classification")
    if user_impact_score >= 16:
        factors.append("large customer impact")
    if error_score >= 12:
        factors.append("elevated failure rate")
    if latency_score >= 10:
        factors.append("severe latency regression")
    if alert_score >= 8:
        factors.append("sustained alert pressure")
    if not factors:
        factors.append("moderate operational instability")
    return factors


def score_incident(payload):
    incident = normalize_incident(payload)

    criticality_score = CRITICALITY_WEIGHT[incident["criticality"]]
    user_impact_score = clamp(math.log10(max(incident["impacted_users"], 1)) * 12, 0, 25)
    error_score = clamp(incident["error_rate"] * 1.8, 0, 20)
    latency_score = clamp((incident["latency_ms"] / 2000) * 16, 0, 16)
    alert_score = clamp(incident["alerts"] * 1.1, 0, 12)

    weighted_total = round(criticality_score + user_impact_score + error_score + latency_score + alert_score, 2)
    priority = _priority_for_score(weighted_total)
    confidence = round(
        clamp(
            0.48 + 0.12 + min(incident["alerts"], 12) / 100 + min(incident["impacted_users"], 20000) / 100000,
            0.52,
            0.96,
        ),
        2,
    )
    factors = _top_factors(criticality_score, user_impact_score, error_score, latency_score, alert_score)

    return {
        "incident": incident,
        "priority": priority,
        "score": weighted_total,
        "confidence": confidence,
        "risk_band": "severe" if priority == "P1" else "high" if priority == "P2" else "moderate" if priority == "P3" else "low",
        "explanation": f"{incident['service']} was classified as {priority} because of {', '.join(factors)}.",
        "actions": deepcopy(PLAYBOOKS.get(incident["category"], PLAYBOOKS["availability"])),
        "dimensions": {
            "criticality": round(criticality_score, 2),
            "user_impact": round(user_impact_score, 2),
            "error_rate": round(error_score, 2),
            "latency": round(latency_score, 2),
            "alert_pressure": round(alert_score, 2),
        },
    }


def summarize_incidents(items):
    assessments = [score_incident(item) for item in items]
    summary = {
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
