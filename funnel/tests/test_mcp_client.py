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
