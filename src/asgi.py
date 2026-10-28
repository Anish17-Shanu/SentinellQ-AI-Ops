"""ASGI adapter for serverless hosts (Vercel) and ASGI servers."""

from __future__ import annotations

import asyncio
import logging
import threading
from urllib.parse import parse_qs

from bootstrap import build_context
from router import SECURITY_HEADERS, Request, Response, json_response
from util import sanitize_request_id

logger = logging.getLogger("sentineliq.asgi")


async def _send_response(send, response: Response) -> None:
    headers: list[tuple[bytes, bytes]] = []
    if response.body:
        headers.append((b"content-type", response.content_type.encode("latin-1")))
    for name, value in response.headers.items():
        headers.append((name.lower().encode("latin-1"), value.encode("latin-1")))
    headers.append((b"content-length", str(len(response.body)).encode("latin-1")))
    await send({"type": "http.response.start", "status": response.status, "headers": headers})
    await send({"type": "http.response.body", "body": response.body})


def _error_response(status: int, code: str, message: str, request_id: str) -> Response:
    response = json_response({"error": {"code": code, "message": message, "request_id": request_id}}, status=status)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    response.headers["X-Request-Id"] = request_id
    response.headers["Cache-Control"] = "no-store"
    return response


def build_asgi_app(context):
    async def app(scope, receive, send) -> None:
        if scope.get("type") == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope.get("type") != "http":
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        request_id = sanitize_request_id(headers.get("x-request-id"))
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > context.settings.max_body_bytes:
                await _send_response(
                    send,
                    _error_response(
                        413,
                        "payload_too_large",
                        f"Request body exceeds {context.settings.max_body_bytes} bytes",
                        request_id,
                    ),
                )
                return
            if not message.get("more_body", False):
                break

        client = scope.get("client") or ("127.0.0.1", 0)
        client_ip = headers.get("x-forwarded-for", "").split(",")[0].strip() or client[0]
        path = scope.get("path", "/")
        override = parse_qs(scope.get("query_string", b"").decode("latin-1")).get("__path")
        if override:
            path = override[0]
        request = Request(
            method=scope.get("method", "GET"),
            path=path,
            query=parse_qs(scope.get("query_string", b"").decode("latin-1")),
            headers=headers,
            body=bytes(body),
            client_ip=client_ip,
            request_id=request_id,
            scheme=scope.get("scheme", "http"),
        )
        response = await asyncio.to_thread(context.router.handle, request)
        await _send_response(send, response)

    return app


class LazyASGIApp:
    """Builds the application on first request so imports stay side-effect free."""

    def __init__(self) -> None:
        self._app = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._app is None:
            with self._lock:
                if self._app is None:
                    self._app = build_asgi_app(build_context(configure_logs=True))
        return self._app

    async def __call__(self, scope, receive, send) -> None:
        app = self._ensure()
        await app(scope, receive, send)


app = LazyASGIApp()
