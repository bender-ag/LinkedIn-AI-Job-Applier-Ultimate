"""Tests for sweep_service — sweep-run tracking + bridge-guarded launch."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from funnel.merge import merge_jobs
from src.dashboard import sweep_service as ss
from src.dashboard import tracker_service


def _seed_db(tmp_path: Path) -> Path:
    """Create a funnel.db with the sweeps table (via a merge)."""
    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"
    yaml_path.write_text(
        yaml.dump([{"url": "https://x/1", "job_title": "A", "job_description": "x"}]),
        encoding="utf-8",
    )
    merge_jobs(yaml_path, db_path)
    return db_path


# ── sweeps table lifecycle ──
def test_get_sweeps_empty_db(tmp_path: Path):
    assert ss.get_sweeps(db_path=tmp_path / "nope.db") == []


def test_sweep_row_lifecycle(tmp_path: Path):
    tracker_service._migrated_paths.clear()
    db_path = _seed_db(tmp_path)

    sweep_id = ss._insert_sweep(db_path, ["software engineer", "frontend"])
    running = ss.get_sweeps(db_path)[0]
    assert running["status"] == "running"
    assert running["id"] == sweep_id

    ss._finish_sweep(db_path, sweep_id, status="done", collected=9, new_count=4)
    done = ss.get_sweeps(db_path)[0]
    assert done["status"] == "done"
    assert done["collected"] == 9
    assert done["new_count"] == 4
    assert done["finished_at"] is not None
    tracker_service._migrated_paths.clear()


# ── bridge guard ──
def test_probe_bridge_raises_when_connect_fails(monkeypatch):
    class _FailClient:
        def connect(self, retries=1):
            raise RuntimeError("connection refused")

        def close(self):
            pass

    monkeypatch.setattr("funnel.mcp_client.McpClient", _FailClient)
    with pytest.raises(ss.BridgeUnavailable):
        ss.probe_browser_bridge()


def test_probe_bridge_ok_when_connect_succeeds(monkeypatch):
    closed = {"v": False}

    class _OkClient:
        def connect(self, retries=1):
            return None

        def close(self):
            closed["v"] = True

    monkeypatch.setattr("funnel.mcp_client.McpClient", _OkClient)
    ss.probe_browser_bridge()  # should not raise
    assert closed["v"] is True  # probe cleans up its session


# ── start_sweep guards (no subprocess launched) ──
def test_start_sweep_raises_when_already_running(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(ss, "get_sweep_status", lambda db_path=None: {"running": True})
    with pytest.raises(ss.SweepAlreadyRunning):
        ss.start_sweep(db_path=tmp_path / "funnel.db")


def test_start_sweep_raises_when_bridge_unavailable(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(ss, "get_sweep_status", lambda db_path=None: {"running": False})

    def _boom():
        raise ss.BridgeUnavailable("no bridge")

    monkeypatch.setattr(ss, "probe_browser_bridge", _boom)
    # Fail if a subprocess is ever launched.
    monkeypatch.setattr(
        ss.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("subprocess launched despite bridge guard"),
    )
    with pytest.raises(ss.BridgeUnavailable):
        ss.start_sweep(db_path=tmp_path / "funnel.db")


# ── stop_sweep ──
def test_stop_sweep_false_when_nothing_running(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(ss, "SWEEP_PROC_FILE", tmp_path / "sweep_process.json")
    assert ss.stop_sweep(db_path=tmp_path / "funnel.db") is False


# ── watcher parses progress + finalizes the row ──
class _FakeStdout:
    def __init__(self, lines):
        self._it = iter(lines)

    def readline(self):
        return next(self._it, b"")


class _FakeProc:
    def __init__(self, lines, rc=0):
        self.stdout = _FakeStdout(lines)
        self._rc = rc

    def wait(self):
        return self._rc


def test_watch_sweep_parses_and_finishes(monkeypatch, tmp_path: Path):
    tracker_service._migrated_paths.clear()
    db_path = _seed_db(tmp_path)
    monkeypatch.setattr(ss, "SWEEP_PROC_FILE", tmp_path / "sweep_process.json")
    monkeypatch.setattr(ss, "SWEEP_LOG_FILE", tmp_path / "sweep.log")

    sweep_id = ss._insert_sweep(db_path, [])
    proc = _FakeProc(
        [
            b"[1/3] collected 9 jobs across 3 queries\n",
            b"[2/3] merged: 4 new, 5 updated\n",
            b"[3/3] digested 4 new jobs -> /artifacts\n",
        ],
        rc=0,
    )
    ss._watch_sweep(proc, sweep_id, db_path)

    row = ss.get_sweeps(db_path)[0]
    assert row["status"] == "done"
    assert row["collected"] == 9
    assert row["new_count"] == 4
    tracker_service._migrated_paths.clear()


def test_watch_sweep_marks_failed_on_nonzero_exit(monkeypatch, tmp_path: Path):
    tracker_service._migrated_paths.clear()
    db_path = _seed_db(tmp_path)
    monkeypatch.setattr(ss, "SWEEP_PROC_FILE", tmp_path / "sweep_process.json")
    monkeypatch.setattr(ss, "SWEEP_LOG_FILE", tmp_path / "sweep.log")

    sweep_id = ss._insert_sweep(db_path, [])
    ss._watch_sweep(_FakeProc([b"boom\n"], rc=1), sweep_id, db_path)
    assert ss.get_sweeps(db_path)[0]["status"] == "failed"
    tracker_service._migrated_paths.clear()


# ── PID-reuse guard + orphan reconciliation (review fixes #1/#5) ──
def test_sweep_pid_alive_false_for_dead_pid():
    # A pid that (almost certainly) isn't running.
    assert ss._sweep_pid_alive(2_000_000_000) is False
    assert ss._sweep_pid_alive(None) is False


def test_get_sweep_status_reconciles_orphaned_running_row(monkeypatch, tmp_path):
    tracker_service._migrated_paths.clear()
    db_path = _seed_db(tmp_path)
    proc_file = tmp_path / "sweep_process.json"
    monkeypatch.setattr(ss, "SWEEP_PROC_FILE", proc_file)

    # A 'running' row whose process is gone, with a stale proc file pointing at
    # a dead pid — the classic "watcher died on restart" orphan.
    sweep_id = ss._insert_sweep(db_path, [])
    ss._write_json(proc_file, {"pid": 2_000_000_000, "sweep_id": sweep_id, "started_at": "t"})

    status = ss.get_sweep_status(db_path)

    assert status["running"] is False
    assert status["latest"]["status"] == "failed"  # row reconciled
    assert not proc_file.exists()  # process file cleared
    tracker_service._migrated_paths.clear()


# ── Popen failure finalizes the row (review fix #3) ──
def test_start_sweep_marks_row_failed_on_popen_error(monkeypatch, tmp_path):
    tracker_service._migrated_paths.clear()
    db_path = _seed_db(tmp_path)
    monkeypatch.setattr(ss, "SWEEP_PROC_FILE", tmp_path / "sweep_process.json")
    monkeypatch.setattr(ss, "probe_browser_bridge", lambda: None)

    def _boom(*a, **k):
        raise OSError("uv not found")

    monkeypatch.setattr(ss.subprocess, "Popen", _boom)

    try:
        ss.start_sweep(db_path=db_path)
        assert False, "expected OSError"
    except OSError:
        pass

    # The inserted row must be finalized, not left 'running'.
    assert ss.get_sweeps(db_path)[0]["status"] == "failed"
    tracker_service._migrated_paths.clear()


# ── sweep health assessment (dashboard heartbeat) ──
from datetime import datetime, timedelta, timezone  # noqa: E402

_NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def _health_status(latest=None, running=False):
    return {"running": running, "latest": latest}


def _sweep_row(status="done", collected=5, new_count=3, hours_ago=1.0):
    return {
        "status": status,
        "collected": collected,
        "new_count": new_count,
        "finished_at": (_NOW - timedelta(hours=hours_ago)).isoformat(),
    }


def test_health_running():
    h = ss.assess_sweep_health(_health_status(running=True), now=_NOW)
    assert h["state"] == "running"
    assert h["level"] == "idle"


def test_health_never_when_no_sweep():
    h = ss.assess_sweep_health(_health_status(latest=None), now=_NOW)
    assert h["state"] == "never"
    assert h["level"] == "idle"


def test_health_ok_for_recent_successful_sweep():
    h = ss.assess_sweep_health(_health_status(latest=_sweep_row()), now=_NOW)
    assert h["state"] == "ok"
    assert h["level"] == "ok"
    assert h["collected"] == 5
    assert h["new_count"] == 3


def test_health_empty_when_zero_collected():
    h = ss.assess_sweep_health(
        _health_status(latest=_sweep_row(collected=0, new_count=0)), now=_NOW
    )
    assert h["state"] == "empty"
    assert h["level"] == "warn"


def test_health_stale_when_last_success_is_old():
    h = ss.assess_sweep_health(
        _health_status(latest=_sweep_row(hours_ago=30)), now=_NOW, stale_after_hours=26
    )
    assert h["state"] == "stale"
    assert h["level"] == "warn"


def test_health_failed():
    h = ss.assess_sweep_health(_health_status(latest=_sweep_row(status="failed")), now=_NOW)
    assert h["state"] == "failed"
    assert h["level"] == "error"


def test_health_stopped_is_idle():
    h = ss.assess_sweep_health(_health_status(latest=_sweep_row(status="stopped")), now=_NOW)
    assert h["state"] == "stopped"
    assert h["level"] == "idle"


def test_health_age_uses_naive_local_default_now():
    # Regression: sweep timestamps are naive-local (runtime._now_iso), so the
    # DEFAULT `now` must be naive-local too — otherwise a fresh sweep reads the
    # UTC offset ("5h ago") instead of ~just-now. Exercises the default path (no
    # `now` passed), unlike the tz-aware cases above.
    finished = datetime.now().isoformat(timespec="seconds")
    latest = {"status": "done", "collected": 7, "new_count": 2, "finished_at": finished}
    h = ss.assess_sweep_health({"running": False, "latest": latest})
    assert h["state"] == "ok"
    assert h["age_hours"] is not None
    assert h["age_hours"] < 0.1  # ~just now, not a whole-hour timezone offset
