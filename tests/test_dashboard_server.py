import asyncio

from fastapi.testclient import TestClient

from src.dashboard import server
from src.dashboard.server import app

client = TestClient(app)


def test_index_serves_tracker_page():
    response = client.get("/")

    assert response.status_code == 200
    assert "Job Funnel Tracker" in response.text


def test_ops_serves_dashboard_page():
    response = client.get("/ops")

    assert response.status_code == 200
    assert "Operations Dashboard" in response.text
    assert '<body class="dashboard-loading" aria-busy="true">' in response.text
    assert 'id="dashboard-loading"' in response.text
    assert "Loading dashboard data" in response.text


def test_run_detail_page_serves_dashboard_page():
    response = client.get("/runs/run-1")

    assert response.status_code == 200
    assert "Operations Dashboard" in response.text


def test_summary_endpoint_returns_summary(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.get_summary",
        lambda: {"run": {"status": "idle"}, "totals": {"applied": 3}, "last_run": {}},
    )

    response = client.get("/api/summary")

    assert response.status_code == 200
    assert response.json()["totals"]["applied"] == 3


def test_runs_endpoint_returns_history(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.get_run_history",
        lambda: [{"run_id": "run-1", "status": "completed", "jobs": {"applied": 2}}],
    )

    response = client.get("/api/runs")

    assert response.status_code == 200
    assert response.json()["runs"][0]["run_id"] == "run-1"


def test_run_events_endpoint_returns_filtered_history(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.get_run_events",
        lambda run_id, limit=200: [{"run_id": run_id, "type": "run_started"}],
    )

    response = client.get("/api/runs/run-9/events?limit=10")

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-9"
    assert response.json()["events"][0]["run_id"] == "run-9"


def test_run_detail_endpoint_returns_combined_payload(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.get_run_detail",
        lambda run_id: {
            "run": {"run_id": run_id},
            "jobs": [{"status": "applied"}],
            "events": [{"type": "run_started"}],
            "screenshots": [{"path": "data/output/dashboard/screenshots/run-9/example.png"}],
        },
    )

    response = client.get("/api/runs/run-9")

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-9"
    assert response.json()["run"]["run_id"] == "run-9"
    assert response.json()["jobs"][0]["status"] == "applied"
    assert response.json()["screenshots"][0]["path"].endswith("example.png")


def test_run_export_returns_downloadable_json(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.get_run_detail",
        lambda run_id: {"run": {"run_id": run_id}, "jobs": [], "events": [], "screenshots": []},
    )

    response = client.get("/api/runs/run-9/export")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="run-9.json"'
    assert response.json()["run_id"] == "run-9"
    assert response.json()["run"]["run_id"] == "run-9"


def test_run_screenshots_endpoint_returns_history(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.get_run_screenshots",
        lambda run_id: [
            {"run_id": run_id, "path": "data/output/dashboard/screenshots/run-9/example.png"}
        ],
    )

    response = client.get("/api/runs/run-9/screenshots")

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-9"
    assert response.json()["screenshots"][0]["path"].endswith("example.png")


def test_run_jobs_endpoint_returns_filtered_jobs(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.get_run_jobs_payload",
        lambda run_id, status=None, search=None: {
            "run_id": run_id,
            "jobs": [{"run_id": run_id, "status": status, "job_title": search or "CTO"}],
            "filtered_count": 1,
            "total_count": 3,
        },
    )

    response = client.get("/api/runs/run-9/jobs?status=applied&search=cto")

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-9"
    assert response.json()["jobs"][0]["status"] == "applied"
    assert response.json()["jobs"][0]["job_title"] == "cto"
    assert response.json()["filtered_count"] == 1
    assert response.json()["total_count"] == 3


def test_jobs_endpoint_passes_filters(monkeypatch):
    captured = {}

    def fake_get_jobs_payload(status=None, search=None):
        captured["status"] = status
        captured["search"] = search
        return {
            "jobs": [{"status": status, "job_title": search}],
            "filtered_count": 1,
            "total_count": 4,
        }

    monkeypatch.setattr("src.dashboard.server.get_jobs_payload", fake_get_jobs_payload)

    response = client.get("/api/jobs?status=applied&search=cto")

    assert response.status_code == 200
    assert captured == {"status": "applied", "search": "cto"}
    assert response.json()["jobs"][0]["job_title"] == "cto"
    assert response.json()["filtered_count"] == 1
    assert response.json()["total_count"] == 4


def test_search_config_update_returns_400_on_error(monkeypatch):
    def fake_update(_config):
        raise ValueError("bad config")

    monkeypatch.setattr("src.dashboard.server.update_search_config", fake_update)

    response = client.put("/api/config/search", json={"config": {}})

    assert response.status_code == 400
    assert response.json()["detail"] == "bad config"


def test_app_config_update_returns_config(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.update_app_config",
        lambda config: {**config, "HEADLESS_MODE": False},
    )

    response = client.put("/api/config/app", json={"config": {"HEADLESS_MODE": False}})

    assert response.status_code == 200
    assert response.json()["app"]["HEADLESS_MODE"] is False


def test_start_control_returns_process(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.start_bot_process",
        lambda: {"pid": 4321, "run_id": "run-1"},
    )
    monkeypatch.setattr(
        "src.dashboard.server.get_summary",
        lambda: {"run": {"status": "starting"}, "totals": {}, "last_run": {}},
    )

    response = client.post("/api/control/start")

    assert response.status_code == 200
    assert response.json()["process"]["pid"] == 4321


def test_force_stop_returns_terminated_state(monkeypatch):
    monkeypatch.setattr("src.dashboard.server.terminate_running_process", lambda: True)
    monkeypatch.setattr(
        "src.dashboard.server.get_summary",
        lambda: {"run": {"status": "stopped"}, "totals": {}, "last_run": {}},
    )

    response = client.post("/api/control/stop?force=true")

    assert response.status_code == 200
    assert response.json()["terminated"] is True


def test_screenshot_returns_404_when_missing(monkeypatch):
    class MissingPath:
        def exists(self):
            return False

    monkeypatch.setattr("src.dashboard.server.LATEST_SCREENSHOT_FILE", MissingPath())

    response = client.get("/api/screenshot")

    assert response.status_code == 404
    assert response.json()["available"] is False


def test_screenshot_file_returns_404_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("src.dashboard.server.ROOT_DIR", tmp_path)

    response = client.get(
        "/api/screenshot-file?path=data/output/dashboard/screenshots/run-1/missing.png"
    )

    assert response.status_code == 404


def test_screenshot_file_returns_file(tmp_path, monkeypatch):
    screenshot = (
        tmp_path / "data" / "output" / "dashboard" / "screenshots" / "run-1" / "example.png"
    )
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(b"fake-image")
    monkeypatch.setattr("src.dashboard.server.ROOT_DIR", tmp_path)

    response = client.get(
        "/api/screenshot-file?path=data/output/dashboard/screenshots/run-1/example.png"
    )

    assert response.status_code == 200
    assert response.content == b"fake-image"


def test_screenshot_returns_stable_byte_snapshot(tmp_path, monkeypatch):
    screenshot = tmp_path / "latest.png"
    screenshot.write_bytes(b"first-image")
    monkeypatch.setattr("src.dashboard.server.LATEST_SCREENSHOT_FILE", screenshot)

    response = client.get("/api/screenshot")

    screenshot.write_bytes(b"updated-image")
    assert response.status_code == 200
    assert response.content == b"first-image"


def test_event_stream_sends_initial_snapshot(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.get_live_state",
        lambda: {"snapshot": {"run_status": "running"}, "events": []},
    )
    monkeypatch.setattr(
        "src.dashboard.server.get_run_events",
        lambda run_id, limit=120: [{"run_id": run_id, "type": "run_started"}],
    )
    monkeypatch.setattr("src.dashboard.server.latest_event_position", lambda: 0)
    monkeypatch.setattr(
        "src.dashboard.server.read_events_since_for_run", lambda position, run_id: ([], position)
    )

    class MockRequest:
        async def is_disconnected(self):
            return False

    async def read_first_chunk():
        response = await server.stream_events(MockRequest(), run_id="run-1")
        first_chunk = await response.body_iterator.__anext__()
        await response.body_iterator.aclose()
        return first_chunk

    first_chunk = asyncio.run(read_first_chunk())

    assert "event: snapshot" in first_chunk
    assert '"selected_run_id":"run-1"' in first_chunk


def test_event_stream_stops_when_client_disconnects(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.get_live_state",
        lambda: {"snapshot": {"run_status": "running"}, "events": []},
    )
    monkeypatch.setattr("src.dashboard.server.latest_event_position", lambda: 0)
    monkeypatch.setattr("src.dashboard.server.read_events_since", lambda position: ([], position))

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr("src.dashboard.server.asyncio.sleep", fake_sleep)

    class DisconnectingRequest:
        def __init__(self):
            self.calls = 0

        async def is_disconnected(self):
            self.calls += 1
            return self.calls > 1

    async def consume_stream():
        response = await server.stream_events(DisconnectingRequest(), run_id=None)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(consume_stream())

    assert len(chunks) == 1
    assert "event: snapshot" in chunks[0]


def test_tracker_llm_stats_returns_stats(monkeypatch):
    """GET /api/tracker/llm-stats returns LLM aggregation."""
    monkeypatch.setattr(
        "src.dashboard.server.llm_totals",
        lambda: {
            "calls": 5,
            "total_tokens": 10000,
            "total_cost": 0.05,
            "total_time_seconds": 12.5,
        },
    )

    response = client.get("/api/tracker/llm-stats")

    assert response.status_code == 200
    body = response.json()
    assert body["calls"] == 5
    assert body["total_tokens"] == 10000
    assert body["total_cost"] == 0.05
    assert body["total_time_seconds"] == 12.5


def test_tracker_tailor_job_success(monkeypatch):
    """POST /api/tracker/jobs/tailor succeeds and calls tailor_job."""
    from unittest.mock import AsyncMock

    async_mock = AsyncMock(
        return_value={
            "url": "https://example.com/job/1",
            "job_title": "Engineer",
            "tailored_resume_path": "data/output/tailored/job-1/resume.pdf",
            "tailored_cover_path": "data/output/tailored/job-1/cover_letter.md",
        }
    )
    monkeypatch.setattr("src.dashboard.server.tailor_job", async_mock)

    response = client.post("/api/tracker/jobs/tailor", json={"url": "https://example.com/job/1"})

    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "https://example.com/job/1"
    assert body["tailored_resume_path"] == "data/output/tailored/job-1/resume.pdf"
    assert async_mock.called
    # Verify the URL was passed to tailor_job
    call_args = async_mock.call_args
    assert (
        "https://example.com/job/1" in call_args[0]
        or call_args.kwargs.get("url") == "https://example.com/job/1"
    )


def test_tracker_tailor_job_returns_404_on_job_not_found(monkeypatch):
    """POST /api/tracker/jobs/tailor returns 404 when tailor_job raises JobNotFound."""
    from unittest.mock import AsyncMock

    from src.dashboard.tailor_service import JobNotFound

    async_mock = AsyncMock(
        side_effect=JobNotFound("Job not found: https://example.com/job/unknown")
    )
    monkeypatch.setattr("src.dashboard.server.tailor_job", async_mock)

    response = client.post(
        "/api/tracker/jobs/tailor", json={"url": "https://example.com/job/unknown"}
    )

    assert response.status_code == 404


def test_tracker_tailor_job_returns_400_on_filenotfound(monkeypatch):
    """POST /api/tracker/jobs/tailor returns 400 when tailor_job raises FileNotFoundError."""
    from unittest.mock import AsyncMock

    async_mock = AsyncMock(side_effect=FileNotFoundError("Resume file not found"))
    monkeypatch.setattr("src.dashboard.server.tailor_job", async_mock)

    response = client.post("/api/tracker/jobs/tailor", json={"url": "https://example.com/job/1"})

    assert response.status_code == 400


def test_tracker_tailor_job_returns_400_on_runtimeerror(monkeypatch):
    """POST /api/tracker/jobs/tailor returns 400 when tailor_job raises RuntimeError."""
    from unittest.mock import AsyncMock

    async_mock = AsyncMock(side_effect=RuntimeError("No LLM API key configured"))
    monkeypatch.setattr("src.dashboard.server.tailor_job", async_mock)

    response = client.post("/api/tracker/jobs/tailor", json={"url": "https://example.com/job/1"})

    assert response.status_code == 400


def test_tracker_file_blocks_path_traversal(monkeypatch, tmp_path):
    """GET /api/tracker/file rejects paths outside TAILORED_DIR."""
    monkeypatch.setattr("src.dashboard.server.ROOT_DIR", tmp_path)
    monkeypatch.setattr(
        "src.dashboard.server.TAILORED_DIR", tmp_path / "data" / "output" / "tailored"
    )

    response = client.get("/api/tracker/file?path=../../etc/passwd")

    assert response.status_code == 400


def test_tracker_file_returns_404_for_missing_file(monkeypatch, tmp_path):
    """GET /api/tracker/file returns 404 when file doesn't exist."""
    monkeypatch.setattr("src.dashboard.server.ROOT_DIR", tmp_path)
    monkeypatch.setattr(
        "src.dashboard.server.TAILORED_DIR", tmp_path / "data" / "output" / "tailored"
    )

    response = client.get("/api/tracker/file?path=data/output/tailored/job-1/missing.pdf")

    assert response.status_code == 404


def test_tracker_file_serves_pdf(monkeypatch, tmp_path):
    """GET /api/tracker/file serves a PDF file with correct content-type."""
    root_dir = tmp_path
    tailored_dir = root_dir / "data" / "output" / "tailored"
    tailored_dir.mkdir(parents=True, exist_ok=True)

    pdf_file = tailored_dir / "job-1" / "resume.pdf"
    pdf_file.parent.mkdir(parents=True, exist_ok=True)
    pdf_file.write_bytes(b"PDF content here")

    monkeypatch.setattr("src.dashboard.server.ROOT_DIR", root_dir)
    monkeypatch.setattr("src.dashboard.server.TAILORED_DIR", tailored_dir)

    response = client.get("/api/tracker/file?path=data/output/tailored/job-1/resume.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == b"PDF content here"


def test_tracker_file_serves_markdown(monkeypatch, tmp_path):
    """GET /api/tracker/file serves a markdown file with correct content-type."""
    root_dir = tmp_path
    tailored_dir = root_dir / "data" / "output" / "tailored"
    tailored_dir.mkdir(parents=True, exist_ok=True)

    md_file = tailored_dir / "job-1" / "cover_letter.md"
    md_file.parent.mkdir(parents=True, exist_ok=True)
    md_file.write_text("# Cover Letter\n\nHello!", encoding="utf-8")

    monkeypatch.setattr("src.dashboard.server.ROOT_DIR", root_dir)
    monkeypatch.setattr("src.dashboard.server.TAILORED_DIR", tailored_dir)

    response = client.get("/api/tracker/file?path=data/output/tailored/job-1/cover_letter.md")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert response.content == b"# Cover Letter\n\nHello!"


# ── Sweep routes (Phase 3) ──
def test_tracker_sweep_status_returns_state(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.get_sweep_status",
        lambda: {
            "running": False,
            "pid": None,
            "sweep_id": None,
            "started_at": None,
            "latest": None,
        },
    )
    response = client.get("/api/tracker/sweep")
    assert response.status_code == 200
    assert response.json()["running"] is False


def test_tracker_sweeps_history(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.get_sweeps",
        lambda limit=50: [{"id": 1, "status": "done", "collected": 9, "new_count": 4}],
    )
    response = client.get("/api/tracker/sweeps")
    assert response.status_code == 200
    body = response.json()
    assert body["sweeps"][0]["id"] == 1


def test_tracker_sweep_start_success(monkeypatch):
    monkeypatch.setattr(
        "src.dashboard.server.start_sweep",
        lambda: {"running": True, "pid": 123, "sweep_id": 7, "started_at": "t", "latest": None},
    )
    response = client.post("/api/tracker/sweep/start")
    assert response.status_code == 200
    assert response.json()["running"] is True


def test_tracker_sweep_start_returns_409_when_running(monkeypatch):
    from src.dashboard.sweep_service import SweepAlreadyRunning

    def _boom():
        raise SweepAlreadyRunning("A sweep is already running.")

    monkeypatch.setattr("src.dashboard.server.start_sweep", _boom)
    response = client.post("/api/tracker/sweep/start")
    assert response.status_code == 409


def test_tracker_sweep_start_returns_503_when_bridge_unavailable(monkeypatch):
    from src.dashboard.sweep_service import BridgeUnavailable

    def _boom():
        raise BridgeUnavailable("Browser bridge is unavailable or busy.")

    monkeypatch.setattr("src.dashboard.server.start_sweep", _boom)
    response = client.post("/api/tracker/sweep/start")
    assert response.status_code == 503
    assert "bridge" in response.json()["detail"].lower()


def test_tracker_sweep_stop(monkeypatch):
    monkeypatch.setattr("src.dashboard.server.stop_sweep", lambda: True)
    response = client.post("/api/tracker/sweep/stop")
    assert response.status_code == 200
    assert response.json() == {"stopped": True}
