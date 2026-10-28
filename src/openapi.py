"""OpenAPI 3.1 description of the public API."""

from __future__ import annotations

from config import Settings

SECURITY: list[dict[str, list[str]]] = [{"bearerAuth": []}, {"apiKeyAuth": []}]

ERROR_RESPONSE = {
    "description": "Error response",
    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}},
}


def _json(schema: str, description: str = "Successful response") -> dict:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema}"}}},
    }


def _filters() -> list[dict]:
    return [
        {"$ref": "#/components/parameters/Limit"},
        {"$ref": "#/components/parameters/Offset"},
        {"$ref": "#/components/parameters/Sort"},
        {"$ref": "#/components/parameters/Order"},
        {"$ref": "#/components/parameters/Priority"},
        {"$ref": "#/components/parameters/State"},
        {"$ref": "#/components/parameters/Category"},
        {"$ref": "#/components/parameters/Owner"},
        {"$ref": "#/components/parameters/Environment"},
        {"$ref": "#/components/parameters/Search"},
    ]


def build_openapi(settings: Settings) -> dict:
    incident_path = "/v1/incidents/{incident_id}"
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "SentinelIQ AI Ops",
            "version": settings.version,
            "description": (
                "Explainable, deterministic incident prioritization. All scoring is local, "
                "rule-based, and requires no LLM or paid service."
            ),
        },
        "servers": [{"url": "/", "description": "Current deployment"}],
        "tags": [
            {"name": "incidents"},
            {"name": "scoring"},
            {"name": "ingest"},
            {"name": "operations"},
        ],
        "security": SECURITY,
        "paths": {
            "/v1/health": {
                "get": {
                    "tags": ["operations"],
                    "summary": "Liveness probe",
                    "security": [],
                    "responses": {"200": _json("Health")},
                }
            },
            "/v1/ready": {
                "get": {
                    "tags": ["operations"],
                    "summary": "Readiness probe (checks the data store)",
                    "security": [],
                    "responses": {"200": _json("Health"), "503": _json("Health", "Storage unavailable")},
                }
            },
            "/v1/incidents": {
                "get": {
                    "tags": ["incidents"],
                    "summary": "List incidents with filtering and pagination",
                    "parameters": _filters(),
                    "responses": {"200": _json("IncidentList")},
                },
                "post": {
                    "tags": ["incidents"],
                    "summary": "Create an incident",
                    "parameters": [{"$ref": "#/components/parameters/IdempotencyKey"}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/IncidentInput"}}},
                    },
                    "responses": {"201": _json("Incident"), "400": ERROR_RESPONSE, "401": ERROR_RESPONSE},
                },
            },
            incident_path: {
                "get": {
                    "tags": ["incidents"],
                    "summary": "Get an incident with timeline and playbook",
                    "parameters": [{"$ref": "#/components/parameters/IncidentId"}],
                    "responses": {"200": _json("IncidentDetail"), "404": ERROR_RESPONSE},
                },
                "delete": {
                    "tags": ["incidents"],
                    "summary": "Delete an incident (admin)",
                    "parameters": [{"$ref": "#/components/parameters/IncidentId"}],
                    "responses": {"204": {"description": "Deleted"}, "404": ERROR_RESPONSE},
                },
            },
            f"{incident_path}/transitions": {
                "post": {
                    "tags": ["incidents"],
                    "summary": "Transition an incident (acknowledge, mitigate, resolve)",
                    "parameters": [{"$ref": "#/components/parameters/IncidentId"}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/TransitionRequest"}}},
                    },
                    "responses": {"200": _json("Incident"), "409": ERROR_RESPONSE},
                }
            },
            "/v1/summary": {
                "get": {
                    "tags": ["incidents"],
                    "summary": "Fleet summary with MTTA/MTTR",
                    "responses": {"200": _json("Summary")},
                }
            },
            "/v1/playbooks": {
                "get": {
                    "tags": ["incidents"],
                    "summary": "Configured response playbooks",
                    "responses": {"200": _json("Playbooks")},
                }
            },
            "/v1/score": {
                "post": {
                    "tags": ["scoring"],
                    "summary": "Score an incident without storing it",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/IncidentInput"}}},
                    },
                    "responses": {"200": _json("ScoreResult"), "400": ERROR_RESPONSE},
                }
            },
            "/v1/ingest/{source}": {
                "post": {
                    "tags": ["ingest"],
                    "summary": "Ingest Alertmanager, PagerDuty, or generic alerts",
                    "parameters": [
                        {
                            "name": "source",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string", "enum": ["alertmanager", "pagerduty", "generic"]},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object", "additionalProperties": True}}},
                    },
                    "responses": {"201": _json("IngestResult"), "200": _json("IngestResult"), "400": ERROR_RESPONSE},
                }
            },
            "/v1/export": {
                "get": {
                    "tags": ["operations"],
                    "summary": "Export incidents (admin)",
                    "parameters": [
                        {
                            "name": "format",
                            "in": "query",
                            "schema": {"type": "string", "enum": ["json", "csv"], "default": "json"},
                        }
                    ],
                    "responses": {"200": {"description": "Exported data"}},
                }
            },
            "/v1/import": {
                "post": {
                    "tags": ["operations"],
                    "summary": "Import incidents (admin)",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ImportRequest"}}},
                    },
                    "responses": {"200": _json("ImportResult")},
                }
            },
            "/v1/cron/tick": {
                "post": {
                    "tags": ["operations"],
                    "summary": "Run scheduled work (notification retries); requires cron secret",
                    "security": [{"cronSecret": []}, {"bearerAuth": []}],
                    "responses": {"200": _json("TickResult"), "401": ERROR_RESPONSE},
                }
            },
            "/metrics": {
                "get": {
                    "tags": ["operations"],
                    "summary": "Prometheus metrics (admin)",
                    "responses": {"200": {"description": "Prometheus text exposition"}},
                }
            },
            "/openapi.json": {
                "get": {
                    "tags": ["operations"],
                    "summary": "This document",
                    "security": [],
                    "responses": {"200": {"description": "OpenAPI document"}},
                }
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"},
                "apiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                "cronSecret": {"type": "apiKey", "in": "header", "name": "X-Cron-Secret"},
            },
            "parameters": {
                "IncidentId": {"name": "incident_id", "in": "path", "required": True, "schema": {"type": "string"}},
                "IdempotencyKey": {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": False,
                    "schema": {"type": "string", "maxLength": 128},
                    "description": "Replaying the same key returns the original record",
                },
                "Limit": {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}},
                "Offset": {"name": "offset", "in": "query", "schema": {"type": "integer", "minimum": 0, "default": 0}},
                "Sort": {
                    "name": "sort",
                    "in": "query",
                    "schema": {"type": "string", "enum": ["created_at", "updated_at", "score", "priority", "service", "state"], "default": "created_at"},
                },
                "Order": {"name": "order", "in": "query", "schema": {"type": "string", "enum": ["asc", "desc"], "default": "desc"}},
                "Priority": {"name": "priority", "in": "query", "schema": {"type": "string", "enum": ["P1", "P2", "P3", "P4"]}},
                "State": {"name": "state", "in": "query", "schema": {"type": "string", "enum": ["open", "acknowledged", "mitigated", "resolved"]}},
                "Category": {"name": "category", "in": "query", "schema": {"type": "string", "enum": ["availability", "security", "data", "latency"]}},
                "Owner": {"name": "owner", "in": "query", "schema": {"type": "string"}},
                "Environment": {"name": "environment", "in": "query", "schema": {"type": "string"}},
                "Search": {"name": "search", "in": "query", "schema": {"type": "string", "maxLength": 200}},
            },
            "schemas": {
                "IncidentInput": {
                    "type": "object",
                    "required": ["service"],
                    "properties": {
                        "service": {"type": "string", "maxLength": 120},
                        "owner": {"type": "string", "maxLength": 120, "default": "platform-core"},
                        "environment": {"type": "string", "default": "production"},
                        "description": {"type": "string", "maxLength": 2000},
                        "criticality": {"type": "string", "enum": ["low", "medium", "high", "critical"], "default": "medium"},
                        "category": {"type": "string", "enum": ["availability", "security", "data", "latency"], "default": "availability"},
                        "impacted_users": {"type": "integer", "minimum": 0, "default": 0},
                        "error_rate": {"type": "number", "minimum": 0, "maximum": 100, "default": 0},
                        "latency_ms": {"type": "number", "minimum": 0, "default": 0},
                        "alerts": {"type": "integer", "minimum": 0, "default": 0},
                        "priority_hint": {"type": "string", "enum": ["", "P1", "P2", "P3", "P4"], "default": ""},
                    },
                },
                "ScoreResult": {
                    "type": "object",
                    "properties": {
                        "incident": {"type": "object"},
                        "priority": {"type": "string"},
                        "score": {"type": "number", "description": "Weighted total (0..115 by default)"},
                        "score_100": {"type": "number", "description": "Normalized 0..100 score"},
                        "confidence": {"type": "number"},
                        "risk_band": {"type": "string"},
                        "explanation": {"type": "string"},
                        "factors": {"type": "array", "items": {"type": "string"}},
                        "actions": {"type": "array", "items": {"type": "string"}},
                        "dimensions": {"type": "object"},
                    },
                },
                "Incident": {
                    "type": "object",
                    "description": "Stored incident with assessment fields, events, and tenant scope.",
                    "additionalProperties": True,
                },
                "IncidentList": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"},
                        "total": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "offset": {"type": "integer"},
                        "items": {"type": "array", "items": {"$ref": "#/components/schemas/Incident"}},
                    },
                },
                "IncidentDetail": {
                    "type": "object",
                    "properties": {
                        "incident": {"$ref": "#/components/schemas/Incident"},
                        "timeline": {"type": "array", "items": {"type": "object"}},
                        "playbook": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "TransitionRequest": {
                    "type": "object",
                    "required": ["state"],
                    "properties": {
                        "state": {"type": "string", "enum": ["acknowledged", "mitigated", "resolved"]},
                        "note": {"type": "string", "maxLength": 500},
                    },
                },
                "Summary": {
                    "type": "object",
                    "properties": {
                        "total_incidents": {"type": "integer"},
                        "open_incidents": {"type": "integer"},
                        "p1_open": {"type": "integer"},
                        "by_priority": {"type": "object"},
                        "by_state": {"type": "object"},
                        "max_score": {"type": "number"},
                        "average_score": {"type": "number"},
                        "max_score_100": {"type": "number"},
                        "average_score_100": {"type": "number"},
                        "mtta_seconds": {"type": ["number", "null"]},
                        "mttr_seconds": {"type": ["number", "null"]},
                        "oldest_open_age_seconds": {"type": "number"},
                        "services": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "IngestResult": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "count": {"type": "integer"},
                        "created": {"type": "integer"},
                        "deduplicated": {"type": "integer"},
                        "items": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "ImportRequest": {
                    "type": "object",
                    "properties": {"incidents": {"type": "array", "items": {"type": "object"}}},
                },
                "ImportResult": {
                    "type": "object",
                    "properties": {"imported": {"type": "integer"}, "tenant": {"type": "string"}},
                },
                "TickResult": {
                    "type": "object",
                    "properties": {
                        "processed": {"type": "integer"},
                        "sent": {"type": "integer"},
                        "failed": {"type": "integer"},
                        "dead": {"type": "integer"},
                    },
                },
                "Health": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "status": {"type": "string"},
                        "version": {"type": "string"},
                    },
                },
                "Playbooks": {
                    "type": "object",
                    "properties": {"defaults": {"type": "object"}, "service_overrides": {"type": "object"}},
                },
                "ErrorEnvelope": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "message": {"type": "string"},
                                "field": {"type": "string"},
                                "request_id": {"type": "string"},
                                "details": {"type": "object"},
                            },
                        }
                    },
                },
            },
        },
    }
