import http.client
import json
import threading


def _request(server, method, path, body=None, headers=None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = dict(headers or {})
    if payload is not None:
        request_headers.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=payload, headers=request_headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    parsed = json.loads(raw.decode("utf-8")) if raw else None
    return resp.status, parsed


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def test_http_mcp_handles_initialize(monkeypatch):
    monkeypatch.delenv("MEMPALACE_HTTP_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("MEMPALACE_HTTP_PATH", "/mcp")
    from mempalace.http_mcp import create_server

    server = create_server("127.0.0.1", 0)
    thread = _serve(server)
    try:
        status, body = _request(
            server,
            "POST",
            "/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert status == 200
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert body["result"]["serverInfo"]["name"] == "mempalace"


def test_http_mcp_rejects_missing_or_invalid_bearer_before_dispatch(monkeypatch):
    monkeypatch.setenv("MEMPALACE_HTTP_BEARER_TOKEN", "secret")
    monkeypatch.setenv("MEMPALACE_HTTP_PATH", "/mcp")
    from mempalace import http_mcp

    def fail_dispatch(_request):
        raise AssertionError("handle_request should not run before HTTP auth")

    monkeypatch.setattr(http_mcp.mcp_server, "handle_request", fail_dispatch)
    server = http_mcp.create_server("127.0.0.1", 0)
    thread = _serve(server)
    try:
        missing_status, missing_body = _request(
            server,
            "POST",
            "/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        invalid_status, invalid_body = _request(
            server,
            "POST",
            "/mcp",
            {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}},
            {"Authorization": "Bearer wrong"},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert missing_status == 401
    assert missing_body == {"error": "unauthorized"}
    assert invalid_status == 401
    assert invalid_body == {"error": "unauthorized"}


def test_http_mcp_accepts_valid_bearer(monkeypatch):
    monkeypatch.setenv("MEMPALACE_HTTP_BEARER_TOKEN", "secret")
    monkeypatch.setenv("MEMPALACE_HTTP_PATH", "/mcp")
    from mempalace import http_mcp

    monkeypatch.setattr(
        http_mcp.mcp_server,
        "handle_request",
        lambda request: {"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}},
    )
    server = http_mcp.create_server("127.0.0.1", 0)
    thread = _serve(server)
    try:
        status, body = _request(
            server,
            "POST",
            "/mcp",
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            {"Authorization": "Bearer secret"},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert status == 200
    assert body == {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}


def test_http_healthz_does_not_dispatch_or_open_collection(monkeypatch):
    from mempalace import http_mcp

    def fail_dispatch(_request):
        raise AssertionError("healthz should not dispatch MCP")

    def fail_collection(*_args, **_kwargs):
        raise AssertionError("healthz should not open palace collection")

    monkeypatch.setattr(http_mcp.mcp_server, "handle_request", fail_dispatch)
    monkeypatch.setattr(http_mcp.mcp_server, "_get_collection", fail_collection)
    server = http_mcp.create_server("127.0.0.1", 0)
    thread = _serve(server)
    try:
        status, body = _request(server, "GET", "/healthz")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert status == 200
    assert body == {"ok": True}


def test_mcp_entrypoint_delegates_to_http_transport(monkeypatch):
    monkeypatch.setenv("MEMPALACE_TRANSPORT", "http")
    from mempalace import http_mcp, mcp_server

    called = []
    monkeypatch.setattr(http_mcp, "main", lambda: called.append("http"))

    mcp_server.main()

    assert called == ["http"]
