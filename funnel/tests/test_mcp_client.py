"""Tests for funnel.mcp_client."""

from __future__ import annotations

from funnel.mcp_client import McpClient, McpError, parse_sse


def test_parse_sse_real_sample():
    sample = 'event: message\ndata: {"result":{"x":1},"jsonrpc":"2.0","id":1}\n'
    messages = parse_sse(sample)
    assert messages == [{"result": {"x": 1}, "jsonrpc": "2.0", "id": 1}]


def test_call_tool_returns_concatenated_text(monkeypatch):
    client = McpClient(url="http://example.test/mcp", api_key="test-key")

    def fake_post(payload):
        assert payload["method"] == "tools/call"
        assert payload["params"]["name"] == "take_snapshot"
        return {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": {"content": [{"type": "text", "text": "hello"}]},
        }

    monkeypatch.setattr(client, "_post", fake_post)
    assert client.call_tool("take_snapshot", {}) == "hello"


def test_call_tool_raises_on_jsonrpc_error(monkeypatch):
    client = McpClient(url="http://example.test/mcp", api_key="k")

    def fake_post(payload):
        return {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "boom"}}

    monkeypatch.setattr(client, "_post", fake_post)
    try:
        client.call_tool("x", {})
        assert False, "expected McpError"
    except McpError as exc:
        assert "boom" in str(exc) or "error" in str(exc).lower() or exc.args


def test_connect_retries_then_succeeds(monkeypatch):
    client = McpClient(url="http://example.test/mcp", api_key="k")
    calls = {"n": 0}

    def flaky_post(payload):
        if payload.get("method") == "initialize":
            calls["n"] += 1
            if calls["n"] < 2:
                raise McpError("No valid session ID provided")
        return None

    monkeypatch.setattr(client, "_post", flaky_post)
    monkeypatch.setattr("funnel.mcp_client.time.sleep", lambda *_: None)
    client.connect(retries=3, backoff=0)
    assert calls["n"] == 2  # failed once, succeeded on the second


def test_connect_raises_after_exhausting_retries(monkeypatch):
    client = McpClient(url="http://example.test/mcp", api_key="k")

    def always_fail(payload):
        raise McpError("busy")

    monkeypatch.setattr(client, "_post", always_fail)
    monkeypatch.setattr("funnel.mcp_client.time.sleep", lambda *_: None)
    try:
        client.connect(retries=2, backoff=0)
        assert False, "expected McpError"
    except McpError:
        pass


def test_close_sends_delete_and_clears_session(monkeypatch):
    client = McpClient(url="http://example.test/mcp", api_key="k")
    client._session_id = "sess-123"
    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout=None):
        captured["method"] = request.method
        captured["session"] = request.headers.get("Mcp-session-id")
        return FakeResp()

    monkeypatch.setattr("funnel.mcp_client.urllib.request.urlopen", fake_urlopen)
    client.close()
    assert captured["method"] == "DELETE"
    assert captured["session"] == "sess-123"
    assert client._session_id is None
    client.close()  # idempotent, no-op when session already cleared


def test_context_manager_connects_and_closes(monkeypatch):
    client = McpClient(url="http://example.test/mcp", api_key="k")
    events = []
    monkeypatch.setattr(client, "connect", lambda: events.append("connect"))
    monkeypatch.setattr(client, "close", lambda: events.append("close"))
    with client as c:
        assert c is client
    assert events == ["connect", "close"]
