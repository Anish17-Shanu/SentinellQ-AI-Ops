"""HTTP/1.1 transport built on the standard library's ThreadingHTTPServer."""

from __future__ import annotations

import logging
import signal
import threading
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib.parse import parse_qs, urlparse

from bootstrap import AppContext, build_context
from config import load_settings
from logging_utils import log_event
from router import SECURITY_HEADERS, Request, Response, json_response
from util import sanitize_request_id

logger = logging.getLogger("sentineliq.server")


class SentinelHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    context: AppContext

    def __init__(self, server_address, context: AppContext) -> None:
        self.context = context
        super().__init__(server_address, RequestHandler)


class RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SentinelIQ"
    sys_version = ""

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_OPTIONS(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_PATCH(self) -> None:
        self._handle()

    def do_HEAD(self) -> None:
        self._handle(head_only=True)

    def _handle(self, head_only: bool = False) -> None:
        context = cast(SentinelHTTPServer, self.server).context
        settings = context.settings
        request_id = sanitize_request_id(self.headers.get("X-Request-Id"))
        with suppress(OSError):
            self.connection.settimeout(settings.request_timeout_seconds)

        headers = {key.lower(): value for key, value in self.headers.items()}
        if headers.get("transfer-encoding", "").lower() == "chunked":
            self.close_connection = True
            self._send_pre_router(
                Response(
                    status=411,
                    body=json_response(
                        {"error": {"code": "length_required", "message": "Chunked bodies are not supported", "request_id": request_id}}
                    ).body,
                    headers={"Connection": "close"},
                ),
                request_id,
            )
            return

        try:
            length = int(headers.get("content-length", "0") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            self._send_pre_router(
                Response(
                    status=400,
                    body=json_response(
                        {"error": {"code": "validation_error", "message": "Content-Length must be an integer", "request_id": request_id}}
                    ).body,
                ),
                request_id,
            )
            return
        if length > settings.max_body_bytes:
            self.close_connection = True
            self._send_pre_router(
                Response(
                    status=413,
                    body=json_response(
                        {
                            "error": {
                                "code": "payload_too_large",
                                "message": f"Request body exceeds {settings.max_body_bytes} bytes",
                                "request_id": request_id,
                            }
                        }
                    ).body,
                    headers={"Connection": "close"},
                ),
                request_id,
            )
            return

        body = b""
        try:
            if length:
                chunks = []
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                body = b"".join(chunks)
        except TimeoutError:
            self.close_connection = True
            self._send_pre_router(
                Response(
                    status=408,
                    body=json_response(
                        {"error": {"code": "timeout", "message": "Request body timed out", "request_id": request_id}}
                    ).body,
                    headers={"Connection": "close"},
                ),
                request_id,
            )
            return
        except OSError:
            return

        parsed = urlparse(self.path)
        client_ip = headers.get("x-forwarded-for", "").split(",")[0].strip() or (
            self.client_address[0] if self.client_address else "127.0.0.1"
        )
        request = Request(
            method="GET" if self.command == "HEAD" else self.command,
            path=parsed.path,
            query=parse_qs(parsed.query),
            headers=headers,
            body=body,
            client_ip=client_ip,
            request_id=request_id,
            scheme="https" if headers.get("x-forwarded-proto", "").lower() == "https" else "http",
        )
        response = context.router.handle(request)
        self._send(response, head_only=head_only)

    def _send_pre_router(self, response: Response, request_id: str) -> None:
        response.headers.setdefault("X-Request-Id", request_id)
        response.headers.setdefault("Cache-Control", "no-store")
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        self._send(response)

    def _send(self, response: Response, head_only: bool = False) -> None:
        try:
            self.send_response(response.status)
            if response.body:
                self.send_header("Content-Type", response.content_type)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(response.body)))
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            if response.body and not head_only:
                self.wfile.write(response.body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def log_message(self, fmt, *args) -> None:  # noqa: A003 - stdlib signature
        return

    def log_request(self, code="-", size="-") -> None:
        return


def create_server(context: AppContext, host: str | None = None, port: int | None = None) -> SentinelHTTPServer:
    settings = context.settings
    return SentinelHTTPServer(
        (host or settings.host, settings.port if port is None else port),
        context,
    )


def main() -> None:
    settings = load_settings()
    context = build_context(settings, configure_logs=True)
    server = create_server(context)

    def request_shutdown(signum, _frame) -> None:
        log_event(logger, logging.INFO, "shutdown_requested", signal=signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    for candidate in (signal.SIGINT, signal.SIGTERM):
        with suppress(ValueError, OSError):
            signal.signal(candidate, request_shutdown)

    log_event(
        logger,
        logging.INFO,
        "server_started",
        host=settings.host,
        port=settings.port,
        storage=settings.storage_backend,
        auth=context.authenticator.enabled,
        version=settings.version,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        context.repository.close()
        log_event(logger, logging.INFO, "server_stopped")


if __name__ == "__main__":
    main()
