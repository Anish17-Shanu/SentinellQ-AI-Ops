"""Transport-neutral HTTP router shared by the http.server and ASGI adapters."""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from auth import Identity
from engine import VALID_CATEGORIES, VALID_PRIORITIES, score_incident
from errors import (
    ApiError,
    AuthError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from logging_utils import log_event
from openapi import build_openapi
from repository import VALID_STATES, StorageError
from util import sanitize_request_id

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
}
SORT_FIELDS = {"created_at", "updated_at", "score", "priority", "service", "state"}
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
STATIC_CACHE = {"style.css": "public, max-age=300", "app.js": "public, max-age=300"}


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes = b""
    client_ip: str = "127.0.0.1"
    request_id: str = ""
    scheme: str = "http"

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def query_one(self, name: str, default: str = "") -> str:
        values = self.query.get(name)
        return values[0] if values else default

    def parse_json(self) -> dict:
        if not self.body:
            return {}
        try:
            payload = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("Request body must be valid JSON", field="body") from exc
        if not isinstance(payload, dict):
            raise ValidationError("Request body must be a JSON object", field="body")
        return payload


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


def json_response(payload, status: int = 200, headers: dict | None = None) -> Response:
    return Response(
        status=status,
        body=json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
        headers=headers or {},
    )


def text_response(text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> Response:
    return Response(status=status, body=text.encode("utf-8"), content_type=content_type)


def _compile_path(template: str) -> re.Pattern:
    parts = re.split(r"(\{[^}]+\})", template)
    pattern = "".join("([^/]+)" if part.startswith("{") and part.endswith("}") else re.escape(part) for part in parts)
    return re.compile(f"^{pattern}$")


class Router:
    def __init__(self, settings, service, authenticator, limiter, metrics) -> None:
        self.settings = settings
        self.service = service
        self.authenticator = authenticator
        self.limiter = limiter
        self.metrics = metrics
        self.logger = logging.getLogger("sentineliq.http")
        self.dashboard_dir = Path(settings.dashboard_dir)
        self._routes = self._build_routes()

    # -- routing table -------------------------------------------------
    def _build_routes(self) -> list[dict]:
        routes: list[dict] = []

        def add(method: str, template: str, handler: str, role: str | None) -> None:
            routes.append(
                {
                    "method": method,
                    "regex": _compile_path(template),
                    "template": template,
                    "handler": handler,
                    "role": role,
                    "metric_route": template,
                }
            )

        def add_versioned(method: str, path: str, handler: str, role: str | None = "viewer") -> None:
            add(method, f"/v1{path}", handler, role)
            routes.append(
                {
                    "method": method,
                    "regex": _compile_path(path),
                    "template": path,
                    "handler": handler,
                    "role": role,
                    "metric_route": f"/v1{path}",
                    "legacy": True,
                }
            )

        add("GET", "/", "root", None)
        add("GET", "/health", "health", None)
        add("GET", "/v1/health", "health", None)
        add("GET", "/ready", "ready", None)
        add("GET", "/v1/ready", "ready", None)
        add("GET", "/openapi.json", "openapi", None)
        add("GET", "/metrics", "metrics_endpoint", "admin")

        add_versioned("GET", "/incidents", "list_incidents")
        add_versioned("POST", "/incidents", "create_incident", "responder")
        add_versioned("GET", "/incidents/{incident_id}", "get_incident")
        add_versioned("DELETE", "/incidents/{incident_id}", "delete_incident", "admin")
        add_versioned("POST", "/incidents/{incident_id}/transitions", "transition_incident", "responder")
        add_versioned("GET", "/summary", "summary")
        add_versioned("GET", "/playbooks", "playbooks")
        add_versioned("POST", "/score", "score")
        add("POST", "/v1/ingest/{source}", "ingest", "responder")
        add("GET", "/v1/export", "export", "admin")
        add("POST", "/v1/import", "import_incidents", "admin")
        add("GET", "/v1/cron/tick", "cron_tick", "cron")
        add("POST", "/v1/cron/tick", "cron_tick", "cron")
        return routes

    def _match(self, method: str, path: str):
        for route in self._routes:
            if route["method"] != method:
                continue
            match = route["regex"].match(path)
            if match:
                return route, match
        return None, None

    # -- entrypoint ----------------------------------------------------
    def handle(self, request: Request) -> Response:
        started = time.perf_counter()
        request.request_id = sanitize_request_id(request.request_id or request.header("x-request-id"))
        path = unquote(request.path)
        if path.startswith("/api/") and path not in {"/api/health"}:
            stripped = path[4:]
            if self._match(request.method, stripped)[0]:
                path = stripped
        elif path == "/api":
            path = "/"

        metric_route = "unmatched"
        identity: Identity | None = None
        status = 500
        try:
            if request.method == "OPTIONS":
                response = self._preflight(request)
                metric_route = "options"
            elif path == "/dashboard" or path.startswith("/dashboard/"):
                response = self._static(request, path)
                metric_route = "static"
            else:
                route, match = self._match(request.method, path)
                if route is None:
                    raise NotFoundError(f"No route for {request.method} {path}")
                metric_route = route["metric_route"]
                identity = self.authenticator.identify(request.headers)
                role = route["role"]
                response_headers: dict[str, str] = {}
                if role == "cron":
                    self._authorize_cron(request)
                else:
                    if role is not None:
                        identity = self.authenticator.require(identity, role)
                    self._apply_rate_limit(request, identity, response_headers)
                handler = getattr(self, route["handler"])
                response = handler(request, identity, match)
                response.headers.update(response_headers)
            status = response.status
            return self._finalize(request, response, identity)
        except RateLimitError as exc:
            response = json_response(exc.to_payload(request.request_id), exc.status)
            response.headers["Retry-After"] = str(exc.retry_after)
            status = response.status
            self.metrics.inc("sentineliq_rate_limited_total")
            return self._finalize(request, response, identity)
        except ApiError as exc:
            response = json_response(exc.to_payload(request.request_id), exc.status)
            if exc.status == 401:
                response.headers["WWW-Authenticate"] = "Bearer"
            status = response.status
            return self._finalize(request, response, identity)
        except StorageError as exc:
            log_event(self.logger, logging.ERROR, "storage_error", request_id=request.request_id, error=str(exc))
            response = json_response(
                {"error": {"code": "storage_error", "message": "Storage is unavailable", "request_id": request.request_id}},
                status=503,
            )
            status = 503
            return self._finalize(request, response, identity)
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            log_event(self.logger, logging.ERROR, "unhandled_error", request_id=request.request_id, error=repr(exc))
            from sentry import report_exception

            report_exception(self.settings.sentry_dsn, exc, request.request_id)
            response = json_response(
                {"error": {"code": "internal_error", "message": "Internal server error", "request_id": request.request_id}},
                status=500,
            )
            status = 500
            return self._finalize(request, response, identity)
        finally:
            duration = time.perf_counter() - started
            self.metrics.observe_request(request.method, metric_route, status, duration)
            log_event(
                self.logger,
                logging.INFO,
                "request",
                request_id=request.request_id,
                method=request.method,
                path=request.path,
                route=metric_route,
                status=status,
                duration_ms=round(duration * 1000, 2),
                tenant=identity.tenant if identity else None,
                role=identity.role if identity else None,
                ip=request.client_ip,
            )

    # -- cross-cutting -------------------------------------------------
    def _origin_allowed(self, request: Request) -> bool:
        origin = request.header("origin")
        if not origin:
            return False
        return "*" in self.settings.cors_origins or origin in self.settings.cors_origins

    def _preflight(self, request: Request) -> Response:
        response = Response(status=204, body=b"", content_type="text/plain; charset=utf-8")
        response.headers["Allow"] = "GET, POST, DELETE, OPTIONS"
        if self._origin_allowed(request):
            origin = request.header("origin")
            response.headers["Access-Control-Allow-Origin"] = "*" if "*" in self.settings.cors_origins else origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = (
                "Authorization, X-API-Key, Content-Type, Idempotency-Key, X-Request-Id, X-Cron-Secret"
            )
            response.headers["Access-Control-Max-Age"] = "600"
            response.headers["Vary"] = "Origin"
        return response

    def _apply_rate_limit(self, request: Request, identity: Identity | None, headers: dict) -> None:
        key = identity.label if identity and identity.authenticated else f"ip:{request.client_ip}"
        result = self.limiter.check(key)
        if result.limit:
            headers["X-RateLimit-Limit"] = str(result.limit)
            headers["X-RateLimit-Remaining"] = str(result.remaining)
        if not result.allowed:
            raise RateLimitError(result.retry_after)

    def _authorize_cron(self, request: Request) -> None:
        expected = self.settings.cron_secret
        if not expected:
            raise NotFoundError("Cron endpoint is not configured")
        presented = request.header("x-cron-secret")
        if not presented:
            authorization = request.header("authorization", "")
            if authorization.lower().startswith("bearer "):
                presented = authorization[7:].strip()
        if not presented or not hmac.compare_digest(presented, expected):
            raise AuthError("Invalid cron secret")

    def _finalize(self, request: Request, response: Response, identity: Identity | None) -> Response:
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if self.settings.tls_enabled:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers["X-Request-Id"] = request.request_id
        if response.content_type and "charset" not in response.content_type:
            response.content_type = f"{response.content_type}; charset=utf-8"
        if self._origin_allowed(request):
            origin = request.header("origin")
            response.headers.setdefault("Access-Control-Allow-Origin", "*" if "*" in self.settings.cors_origins else origin)
            response.headers.setdefault("Vary", "Origin")
        if response.content_type.startswith("application/json"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # -- static assets -------------------------------------------------
    def _static(self, request: Request, path: str) -> Response:
        relative = path[len("/dashboard") :].lstrip("/") or "index.html"
        base = self.dashboard_dir.resolve()
        candidate = (base / relative).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            raise NotFoundError("Asset not found") from None
        if not candidate.is_file():
            raise NotFoundError("Asset not found")
        body = candidate.read_bytes()
        mime = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in {"application/javascript", "application/json"}:
            mime = f"{mime}; charset=utf-8"
        stat = candidate.stat()
        etag = f'"{int(stat.st_mtime)}-{stat.st_size}"'
        headers = {"ETag": etag}
        cache = STATIC_CACHE.get(candidate.name, "no-cache")
        headers["Cache-Control"] = cache
        if request.header("if-none-match") == etag:
            return Response(status=304, body=b"", content_type=mime, headers=headers)
        return Response(status=200, body=body, content_type=mime, headers=headers)

    # -- handlers ------------------------------------------------------
    def root(self, request, identity, match) -> Response:
        return json_response(
            {
                "service": "SentinelIQ AI Ops",
                "version": self.settings.version,
                "status": "running",
                "docs": "/openapi.json",
                "dashboard": "/dashboard/",
                "routes": ["/v1/incidents", "/v1/summary", "/v1/score", "/v1/ingest/{source}", "/v1/playbooks"],
            }
        )

    def health(self, request, identity, match) -> Response:
        return json_response({"service": "SentinelIQ AI Ops", "status": "ok", "version": self.settings.version})

    def ready(self, request, identity, match) -> Response:
        try:
            self.service.repository.ping()
        except Exception:  # noqa: BLE001 - readiness must never raise
            return json_response({"status": "not_ready"}, status=503)
        return json_response({"status": "ready", "storage": self.settings.storage_backend})

    def metrics_endpoint(self, request, identity, match) -> Response:
        return text_response(self.metrics.render(), content_type="text/plain; version=0.0.4; charset=utf-8")

    def openapi(self, request, identity, match) -> Response:
        return json_response(build_openapi(self.settings))

    def playbooks(self, request, identity, match) -> Response:
        return json_response(self.service.playbooks)

    def summary(self, request, identity, match) -> Response:
        tenant = identity.tenant if identity else self.settings.default_tenant
        return json_response(self.service.summary(tenant))

    def list_incidents(self, request, identity, match) -> Response:
        tenant = identity.tenant if identity else self.settings.default_tenant
        filters = self._parse_filters(request)
        items, total = self.service.list(tenant, **filters)
        return json_response(
            {
                "count": len(items),
                "total": total,
                "limit": filters["limit"],
                "offset": filters["offset"],
                "items": items,
            }
        )

    def get_incident(self, request, identity, match) -> Response:
        tenant = identity.tenant if identity else self.settings.default_tenant
        record = self.service.get(tenant, match.group(1))
        return json_response(
            {
                "incident": record,
                "timeline": record.get("events", []),
                "playbook": self.service.assess(record).get("actions", []),
            }
        )

    def create_incident(self, request, identity, match) -> Response:
        tenant = identity.tenant if identity else self.settings.default_tenant
        actor = identity.label if identity else "anonymous"
        key = request.header("idempotency-key") or None
        if key and not IDEMPOTENCY_PATTERN.match(key):
            raise ValidationError("Idempotency-Key must be 1-128 characters of A-Z, a-z, 0-9, . _ : -", field="Idempotency-Key")
        record, replayed = self.service.create(tenant, request.parse_json(), actor, key)
        response = json_response(record, status=201)
        if replayed:
            response.headers["Idempotent-Replayed"] = "true"
        return response

    def transition_incident(self, request, identity, match) -> Response:
        tenant = identity.tenant if identity else self.settings.default_tenant
        payload = request.parse_json()
        if "state" not in payload:
            raise ValidationError("state is required", field="state")
        note = payload.get("note", "")
        if note is not None and not isinstance(note, str):
            raise ValidationError("note must be a string", field="note")
        record = self.service.transition(
            tenant, match.group(1), payload["state"], identity.label if identity else "anonymous", note or ""
        )
        return json_response(record)

    def delete_incident(self, request, identity, match) -> Response:
        tenant = identity.tenant if identity else self.settings.default_tenant
        self.service.delete(tenant, match.group(1))
        return Response(status=204, body=b"", content_type="text/plain; charset=utf-8")

    def score(self, request, identity, match) -> Response:
        return json_response(
            score_incident(request.parse_json(), self.service.config, self.service.playbooks)
        )

    def ingest(self, request, identity, match) -> Response:
        source = match.group(1).lower()
        if source not in {"alertmanager", "pagerduty", "generic"}:
            raise NotFoundError(f"Unknown ingest source: {source}")
        tenant = identity.tenant if identity else self.settings.default_tenant
        actor = identity.label if identity else "anonymous"
        result = self.service.ingest(tenant, source, request.parse_json(), actor)
        return json_response(result, status=201 if result["created"] else 200)

    def export(self, request, identity, match) -> Response:
        tenant = identity.tenant if identity else self.settings.default_tenant
        fmt = request.query_one("format", "json").lower()
        exported = self.service.export(tenant, fmt)
        return Response(
            status=200,
            body=exported["body"],
            content_type=exported["content_type"],
            headers={"Content-Disposition": f'attachment; filename="{exported["filename"]}"'},
        )

    def import_incidents(self, request, identity, match) -> Response:
        tenant = identity.tenant if identity else self.settings.default_tenant
        return json_response(self.service.import_records(tenant, request.parse_json()))

    def cron_tick(self, request, identity, match) -> Response:
        return json_response(self.service.tick())

    # -- parsing -------------------------------------------------------
    def _parse_filters(self, request: Request) -> dict:
        limit = self._query_int(request, "limit", 50, 1, 100)
        offset = self._query_int(request, "offset", 0, 0, 10_000_000)
        sort = request.query_one("sort", "created_at").lower()
        if sort not in SORT_FIELDS:
            raise ValidationError(f"sort must be one of {', '.join(sorted(SORT_FIELDS))}", field="sort")
        order = request.query_one("order", "desc").lower()
        if order not in {"asc", "desc"}:
            raise ValidationError("order must be asc or desc", field="order")
        priority = request.query_one("priority").upper()
        if priority and priority not in VALID_PRIORITIES:
            raise ValidationError("priority must be one of P1, P2, P3, P4", field="priority")
        state = request.query_one("state").lower()
        if state and state not in VALID_STATES:
            raise ValidationError(f"state must be one of {', '.join(sorted(VALID_STATES))}", field="state")
        category = request.query_one("category").lower()
        if category and category not in VALID_CATEGORIES:
            raise ValidationError(f"category must be one of {', '.join(sorted(VALID_CATEGORIES))}", field="category")
        return {
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "order": order,
            "priority": priority,
            "state": state,
            "category": category,
            "owner": request.query_one("owner"),
            "environment": request.query_one("environment"),
            "search": request.query_one("search")[:200],
        }

    @staticmethod
    def _query_int(request: Request, name: str, default: int, minimum: int, maximum: int) -> int:
        raw = request.query_one(name)
        if raw == "":
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValidationError(f"{name} must be an integer", field=name) from exc
        if value < minimum or value > maximum:
            raise ValidationError(f"{name} must be between {minimum} and {maximum}", field=name)
        return value
