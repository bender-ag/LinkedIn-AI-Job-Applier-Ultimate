"""Sweep orchestration for the tracker.

Launches the full ``funnel.sweep`` (collect → merge → digest) as a subprocess,
guarded by a browser-bridge probe, tracks it via a process file, and records
each run in the ``sweeps`` table for the History tab.

The container's headless browser is Cloudflare-blocked, so the funnel collector
drives the desktop browser through the MCP bridge. The bridge supports *multiple*
concurrent MCP sessions (verified 2026-07-22), so a live Claude Code ``browser``
session does NOT block a sweep. Before launching we only probe that the bridge is
reachable (a lightweight ``McpClient.connect()``); if it's down we refuse with a
clear message rather than starting a sweep that would fail mid-collection.

Coordination: the sweep drives its own tab (the collector opens a fresh page).
Because all sessions share one Chrome, hand-driving the *same* tabs while a sweep
runs can interleave on the globally selected page — avoid navigating the research
browser during a sweep.

Reuses runtime.py's process-management helpers; keeps its own process file so a
sweep and the (legacy) apply bot never clobber each other's state.
"""

from __future__ import annotations

import json
import re
import signal
import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Any

from config.logger_config import logger
from src.dashboard.runtime import (
    DASHBOARD_DIR,
    ROOT_DIR,
    _now_iso,
    _read_json,
    _signal_process_tree,
    _write_json,
    is_process_running,
)
from src.dashboard.tracker_service import DB_PATH, _ensure_schema_once

SWEEP_PROC_FILE = DASHBOARD_DIR / "sweep_process.json"
SWEEP_LOG_FILE = DASHBOARD_DIR / "sweep.log"
QUERIES_CONFIG = ROOT_DIR / "funnel" / "queries.yaml"

# Progress markers printed by funnel.sweep's [1/3]/[2/3]/[3/3] stages.
_COLLECTED_RE = re.compile(r"\[1/3\]\s+collected\s+(\d+)")
_MERGED_RE = re.compile(r"\[2/3\]\s+merged:\s+(\d+)\s+new")

# Serializes the check-and-launch in start_sweep so two concurrent requests
# can't both pass the "already running" check and launch two sweeps.
_start_lock = threading.Lock()


class BridgeUnavailable(RuntimeError):
    """The browser bridge can't be acquired for a sweep."""


class SweepAlreadyRunning(RuntimeError):
    """A sweep was requested while one is already running."""


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open the funnel DB with a generous busy timeout (the sweep subprocess
    writes the same file concurrently — see also WAL mode in ensure_schema)."""
    return sqlite3.connect(db_path, timeout=30.0)


def _sweep_pid_alive(pid: int | None) -> bool:
    """
    True only if ``pid`` is running AND is our ``funnel.sweep`` process.

    Guards against PID reuse: after an orphaned process file, the recorded pid
    may have been reassigned to an unrelated process. On Linux we confirm via
    ``/proc/<pid>/cmdline``; elsewhere we fall back to bare liveness.
    """
    if not is_process_running(pid):
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True
    return b"funnel.sweep" in cmdline


# ── Browser-bridge guard ──
def probe_browser_bridge() -> None:
    """Raise BridgeUnavailable if the browser MCP bridge can't be reached.

    The bridge is multi-session, so a live Claude Code ``browser`` session is not a
    problem; this only fails when the bridge itself is down/unreachable.
    """
    try:
        from funnel.mcp_client import McpClient
    except Exception as exc:  # pragma: no cover - import guard
        raise BridgeUnavailable(f"MCP client unavailable: {exc}") from exc

    client = McpClient()
    try:
        client.connect(retries=1)
    except Exception as exc:
        raise BridgeUnavailable(
            "Browser bridge is unreachable. Start the desktop browser bridge "
            "(`./service.sh browser` on the host) and retry."
        ) from exc
    finally:
        try:
            client.close()
        except Exception:
            pass


# ── sweeps table helpers ──
def _load_queries() -> list:
    """Read the query list the sweep will run (for the History record)."""
    try:
        import yaml

        with QUERIES_CONFIG.open(encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("queries") or []
    except Exception:
        # Best-effort: the query list is only a display field on the sweep row.
        return []


def _insert_sweep(db_path: Path, queries: list) -> int:
    conn = _connect(db_path)
    try:
        _ensure_schema_once(conn, db_path)
        cur = conn.execute(
            "INSERT INTO sweeps (started_at, queries_json, status) VALUES (?, ?, 'running')",
            (_now_iso(), json.dumps(queries)),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _finish_sweep(
    db_path: Path,
    sweep_id: int,
    *,
    status: str,
    collected: int | None = None,
    new_count: int | None = None,
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE sweeps SET finished_at = ?, status = ?, collected = ?, "
            "new_count = ? WHERE id = ?",
            (_now_iso(), status, collected, new_count, sweep_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_sweeps(db_path: Path | None = None, limit: int = 50) -> list[dict]:
    """Return sweep-run history, newest first."""
    if db_path is None:
        db_path = DB_PATH
    if not db_path.exists():
        return []
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema_once(conn, db_path)
        rows = conn.execute("SELECT * FROM sweeps ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ── process file ──
def _clear_sweep_proc() -> None:
    try:
        SWEEP_PROC_FILE.unlink()
    except OSError:
        pass


# ── run lifecycle ──
def _watch_sweep(process: subprocess.Popen, sweep_id: int, db_path: Path) -> None:
    """Tee the sweep's output, parse progress, finalize the sweep row on exit."""
    collected: int | None = None
    new_count: int | None = None
    SWEEP_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SWEEP_LOG_FILE.open("a", encoding="utf-8") as log:
        assert process.stdout is not None
        for raw_line in iter(process.stdout.readline, b""):
            line = raw_line.decode("utf-8", errors="replace")
            log.write(line)
            log.flush()
            m = _COLLECTED_RE.search(line)
            if m:
                collected = int(m.group(1))
            m = _MERGED_RE.search(line)
            if m:
                new_count = int(m.group(1))

    returncode = process.wait()
    info = _read_json(SWEEP_PROC_FILE, {})
    if info.get("stop_requested"):
        status = "stopped"
    elif returncode == 0:
        status = "done"
    else:
        status = "failed"
    _finish_sweep(db_path, sweep_id, status=status, collected=collected, new_count=new_count)
    _clear_sweep_proc()
    logger.info(
        "Sweep %s finished: status=%s collected=%s new=%s", sweep_id, status, collected, new_count
    )


def _reconcile_orphan(db_path: Path, sweep_id: int | None) -> None:
    """
    Recover from a stranded sweep: mark a still-'running' row failed and clear
    the process file. Called when the recorded pid is no longer our live sweep
    (watcher died on a server restart, or the pid was reused).
    """
    if sweep_id is not None and db_path.exists():
        conn = _connect(db_path)
        try:
            _ensure_schema_once(conn, db_path)
            conn.execute(
                "UPDATE sweeps SET status = 'failed', finished_at = ? "
                "WHERE id = ? AND status = 'running'",
                (_now_iso(), sweep_id),
            )
            conn.commit()
        finally:
            conn.close()
    _clear_sweep_proc()


def get_sweep_status(db_path: Path | None = None) -> dict[str, Any]:
    """Current sweep process state + the latest sweep row."""
    if db_path is None:
        db_path = DB_PATH
    info = _read_json(SWEEP_PROC_FILE, {})
    pid = info.get("pid")
    running = _sweep_pid_alive(pid)
    if info and not running:
        # Orphaned process file (watcher died / pid reused): reconcile so a
        # stale 'running' row + process file can't wedge the tracker forever.
        _reconcile_orphan(db_path, info.get("sweep_id"))
        info = {}
    latest = get_sweeps(db_path, limit=1)
    return {
        "running": running,
        "pid": pid if running else None,
        "sweep_id": info.get("sweep_id"),
        "started_at": info.get("started_at"),
        "latest": latest[0] if latest else None,
    }


def start_sweep(db_path: Path | None = None) -> dict[str, Any]:
    """
    Launch a guarded full sweep.

    Raises:
        SweepAlreadyRunning: a sweep is already in progress.
        BridgeUnavailable: the browser bridge can't be acquired.
    """
    if db_path is None:
        db_path = DB_PATH

    # Hold the lock across check → probe → launch → record so two concurrent
    # requests can't both pass the running check.
    with _start_lock:
        if get_sweep_status(db_path)["running"]:
            raise SweepAlreadyRunning("A sweep is already running.")

        probe_browser_bridge()  # raises BridgeUnavailable

        sweep_id = _insert_sweep(db_path, _load_queries())
        try:
            # Inherits the parent environment (PATH for `uv`); no changes needed.
            process = subprocess.Popen(
                ["uv", "run", "python", "-m", "funnel.sweep", "--db", str(db_path)],
                cwd=ROOT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception:
            # Don't leave the just-inserted row stuck 'running' if launch fails.
            _finish_sweep(db_path, sweep_id, status="failed")
            raise
        _write_json(
            SWEEP_PROC_FILE,
            {"pid": process.pid, "sweep_id": sweep_id, "started_at": _now_iso()},
        )
        threading.Thread(
            target=_watch_sweep, args=(process, sweep_id, db_path), daemon=True
        ).start()
        return get_sweep_status(db_path)


def stop_sweep(db_path: Path | None = None) -> bool:
    """Signal a running sweep to stop. Returns False if none was running."""
    if db_path is None:
        db_path = DB_PATH
    info = _read_json(SWEEP_PROC_FILE, {})
    pid = info.get("pid")
    if not is_process_running(pid):
        _clear_sweep_proc()
        return False
    info["stop_requested"] = True
    _write_json(SWEEP_PROC_FILE, info)
    _signal_process_tree(pid, signal.SIGTERM)
    return True
