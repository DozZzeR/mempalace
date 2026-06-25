"""Native HTTP transport for the MemPalace MCP JSON-RPC server."""

from __future__ import annotations

import json
import logging
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import mcp_server

logger = logging.getLogger("mempalace_mcp.http")


def _http_host() -> str:
    return os.environ.get("MEMPALACE_HTTP_HOST", "").strip() or "127.0.0.1"


def _http_port() -> int:
    raw = os.environ.get("MEMPALACE_HTTP_PORT", "").strip() or "8000"
    try:
        port = int(raw)
    except ValueError:
        raise SystemExit(f"MEMPALACE_HTTP_PORT must be an integer, got {raw!r}") from None
    if port <= 0 or port > 65535:
        raise SystemExit(f"MEMPALACE_HTTP_PORT must be between 1 and 65535, got {port}")
    return port


def _http_path() -> str:
    path = os.environ.get("MEMPALACE_HTTP_PATH", "").strip() or "/mcp"
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _bearer_token() -> str:
    return os.environ.get("MEMPALACE_HTTP_BEARER_TOKEN", "").strip()


def _authorized(headers) -> bool:
    token = _bearer_token()
    if not token:
        return True
    return headers.get("Authorization", "") == f"Bearer {token}"


def _json_response(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _empty_response(handler: BaseHTTPRequestHandler, status: HTTPStatus) -> None:
    handler.send_response(status)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


class MempalaceHttpMcpHandler(BaseHTTPRequestHandler):
    server_version = "MemPalaceHTTPMCP/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        logger.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        if self.path == "/healthz":
            _json_response(self, HTTPStatus.OK, {"ok": True})
            return
        _empty_response(self, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != _http_path():
            _empty_response(self, HTTPStatus.NOT_FOUND)
            return
        if not _authorized(self.headers):
            _json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "invalid content length"})
            return
        try:
            raw_body = self.rfile.read(length)
            request = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _json_response(
                self,
                HTTPStatus.BAD_REQUEST,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                },
            )
            return
        response = mcp_server.handle_request(request)
        if response is None:
            _empty_response(self, HTTPStatus.ACCEPTED)
            return
        _json_response(self, HTTPStatus.OK, response)


def create_server(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    address = (host or _http_host(), port if port is not None else _http_port())
    return ThreadingHTTPServer(address, MempalaceHttpMcpHandler)


def main() -> None:
    os.environ.pop("PYTHONPATH", None)
    mcp_server._restore_stdout()
    mcp_server._refresh_vector_disabled_flag()
    mcp_server._maybe_eager_warmup_embedder()
    server = create_server()
    host, port = server.server_address[:2]
    logger.info("MemPalace native HTTP MCP starting on http://%s:%s%s", host, port, _http_path())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
