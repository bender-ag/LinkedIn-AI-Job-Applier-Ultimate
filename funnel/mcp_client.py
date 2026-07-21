"""Minimal streamable-HTTP MCP client for the Chrome DevTools bridge."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


class McpError(Exception):
    """Raised on HTTP or JSON-RPC errors from the MCP bridge."""


def parse_sse(text: str) -> list[dict]:
    """Parse SSE body; return JSON objects from all ``data:`` lines."""
    messages: list[dict] = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].lstrip()
        if not payload:
            continue
        messages.append(json.loads(payload))
    return messages


class McpClient:
    """Small MCP client: initialize handshake + tools/call over SSE."""

    DEFAULT_URL = "http://host.docker.internal:8814/mcp"

    def __init__(self, url: str | None = None, api_key: str | None = None) -> None:
        self.url = url or os.environ.get("BROWSER_MCP_URL") or self.DEFAULT_URL
        self.api_key = api_key if api_key is not None else os.environ.get("RESEARCH_BROWSER_KEY")
        self._session_id: str | None = None
        self._next_id = 1

    def connect(self, *, retries: int = 3, backoff: float = 1.0) -> None:
        """POST initialize (with retry), capture session id, send initialized.

        The Chrome DevTools bridge allows one active session; if another client
        holds it, initialize is refused. Retry a few times so a briefly-busy
        bridge (e.g. a just-finished run releasing its session) succeeds.
        """
        init_payload = {
            "jsonrpc": "2.0",
            "id": self._alloc_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                # clientInfo.version is REQUIRED by the MCP initialize schema;
                # omitting it makes the server reject the request as non-initialize
                # ("No valid session ID provided").
                "clientInfo": {"name": "funnel", "version": "0.1.0"},
            },
        }
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                self._post(init_payload)
                last_exc = None
                break
            except McpError as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(backoff * attempt)
        if last_exc is not None:
            raise last_exc

        notify_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        self._post(notify_payload)

    def close(self) -> None:
        """Best-effort session termination (HTTP DELETE); safe to call twice."""
        if not self._session_id:
            return
        headers = {"Accept": "application/json, text/event-stream"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        headers["Mcp-Session-Id"] = self._session_id
        request = urllib.request.Request(self.url, headers=headers, method="DELETE")
        try:
            with urllib.request.urlopen(request, timeout=10):
                pass
        except (urllib.error.HTTPError, urllib.error.URLError, OSError):
            pass  # server may not support DELETE; releasing our handle is enough
        finally:
            self._session_id = None

    def __enter__(self) -> McpClient:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call an MCP tool; return concatenated ``content[*].text``."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._alloc_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        message = self._post(payload)
        if message is None:
            raise McpError("empty response from tools/call")
        if "error" in message:
            raise McpError(message["error"])
        result = message.get("result") or {}
        content = result.get("content") or []
        parts: list[str] = []
        for item in content:
            text = item.get("text")
            if text is not None:
                parts.append(text)
        return "".join(parts)

    def _alloc_id(self) -> int:
        req_id = self._next_id
        self._next_id += 1
        return req_id

    def _post(self, payload: dict[str, Any]) -> dict | None:
        """POST JSON-RPC; parse SSE; return the last JSON-RPC message (or None)."""
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self._session_id = session
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise McpError(err_body) from exc
        except urllib.error.URLError as exc:
            raise McpError(str(exc)) from exc

        messages = parse_sse(raw)
        if not messages:
            # Non-SSE JSON body (some servers reply plain JSON-RPC)
            stripped = raw.strip()
            if stripped.startswith("{"):
                return json.loads(stripped)
            return None
        return messages[-1]
