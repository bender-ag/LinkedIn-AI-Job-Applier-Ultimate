"""Tests for tracker routes in src.dashboard.server."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from funnel.merge import merge_jobs
from src.dashboard.server import app


def _write_yaml_jobs(path: Path, jobs: list[dict]) -> None:
    """Write test jobs to YAML file."""
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(jobs), encoding="utf-8")


@pytest.fixture
def client():
    """Return FastAPI test client."""
    return TestClient(app)


def test_get_tracker_index_returns_200(client, tmp_path, monkeypatch):
    """GET / returns tracker.html with 200."""
    import src.dashboard.server as server

    tracker_html_path = tmp_path / "tracker.html"
    tracker_html_path.write_text("<html><body>tracker</body></html>")

    monkeypatch.setattr(server, "STATIC_DIR", tmp_path)

    response = client.get("/")
    assert response.status_code == 200
    assert "tracker" in response.text


def test_get_ops_returns_200(client, tmp_path, monkeypatch):
    """GET /ops returns index.html with 200."""
    import src.dashboard.server as server

    index_html_path = tmp_path / "index.html"
    index_html_path.write_text("<html><body>ops dashboard</body></html>")

    monkeypatch.setattr(server, "STATIC_DIR", tmp_path)

    response = client.get("/ops")
    assert response.status_code == 200
    assert "ops dashboard" in response.text


def test_get_tracker_jobs_empty(client, tmp_path, monkeypatch):
    """GET /api/tracker/jobs returns empty list when DB missing."""
    import src.dashboard.tracker_service as ts

    db_path = tmp_path / "funnel.db"
    monkeypatch.setattr(ts, "DB_PATH", db_path)

    response = client.get("/api/tracker/jobs")
    assert response.status_code == 200
    data = response.json()
    assert data["jobs"] == []


def test_get_tracker_jobs_with_data(client, tmp_path, monkeypatch):
    """GET /api/tracker/jobs returns jobs from database."""
    import src.dashboard.tracker_service as ts

    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"

    _write_yaml_jobs(
        yaml_path,
        [
            {
                "url": "https://example.com/job/1",
                "job_title": "Engineer",
                "company_name": "Acme",
                "location": "Remote",
                "job_description": "Python backend",
            }
        ],
    )

    merge_jobs(yaml_path, db_path)
    monkeypatch.setattr(ts, "DB_PATH", db_path)

    response = client.get("/api/tracker/jobs")
    assert response.status_code == 200
    data = response.json()
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["job_title"] == "Engineer"


def test_get_tracker_jobs_filters_by_status(client, tmp_path, monkeypatch):
    """GET /api/tracker/jobs?status=X filters by status."""
    import src.dashboard.tracker_service as ts

    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"

    _write_yaml_jobs(
        yaml_path,
        [
            {
                "url": "https://example.com/job/1",
                "job_title": "A",
                "company_name": "Acme",
                "job_description": "test",
            },
            {
                "url": "https://example.com/job/2",
                "job_title": "B",
                "company_name": "Beta",
                "job_description": "test",
            },
        ],
    )

    merge_jobs(yaml_path, db_path)

    # Update one to applied
    from src.dashboard.tracker_service import update_job

    update_job("https://example.com/job/1", {"status": "applied"}, db_path=db_path)

    monkeypatch.setattr(ts, "DB_PATH", db_path)

    response = client.get("/api/tracker/jobs?status=applied")
    assert response.status_code == 200
    data = response.json()
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["job_title"] == "A"


def test_get_tracker_jobs_filters_by_search(client, tmp_path, monkeypatch):
    """GET /api/tracker/jobs?search=X filters by search term."""
    import src.dashboard.tracker_service as ts

    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"

    _write_yaml_jobs(
        yaml_path,
        [
            {
                "url": "https://example.com/job/1",
                "job_title": "Python Engineer",
                "company_name": "Acme",
                "location": "Remote",
                "job_description": "test",
            },
            {
                "url": "https://example.com/job/2",
                "job_title": "Go Developer",
                "company_name": "Beta",
                "location": "Remote",
                "job_description": "test",
            },
        ],
    )

    merge_jobs(yaml_path, db_path)
    monkeypatch.setattr(ts, "DB_PATH", db_path)

    response = client.get("/api/tracker/jobs?search=python")
    assert response.status_code == 200
    data = response.json()
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["job_title"] == "Python Engineer"


def test_patch_tracker_job_success(client, tmp_path, monkeypatch):
    """PATCH /api/tracker/jobs updates job and returns 200."""
    import src.dashboard.tracker_service as ts

    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"

    _write_yaml_jobs(
        yaml_path,
        [
            {
                "url": "https://example.com/job/1",
                "job_title": "Engineer",
                "company_name": "Acme",
                "job_description": "test",
            }
        ],
    )

    merge_jobs(yaml_path, db_path)
    monkeypatch.setattr(ts, "DB_PATH", db_path)

    response = client.patch(
        "/api/tracker/jobs",
        json={"url": "https://example.com/job/1", "status": "applied", "notes": "Good fit"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "applied"
    assert data["notes"] == "Good fit"


def test_patch_tracker_job_bad_status(client, tmp_path, monkeypatch):
    """PATCH /api/tracker/jobs with bad status returns 400."""
    import src.dashboard.tracker_service as ts

    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"

    _write_yaml_jobs(
        yaml_path,
        [
            {
                "url": "https://example.com/job/1",
                "job_title": "Engineer",
                "company_name": "Acme",
                "job_description": "test",
            }
        ],
    )

    merge_jobs(yaml_path, db_path)
    monkeypatch.setattr(ts, "DB_PATH", db_path)

    response = client.patch(
        "/api/tracker/jobs",
        json={"url": "https://example.com/job/1", "status": "invalid_status"},
    )
    assert response.status_code == 400


def test_patch_tracker_job_unknown_url(client, tmp_path, monkeypatch):
    """PATCH /api/tracker/jobs with unknown url returns 404."""
    import src.dashboard.tracker_service as ts

    db_path = tmp_path / "funnel.db"
    monkeypatch.setattr(ts, "DB_PATH", db_path)

    response = client.patch(
        "/api/tracker/jobs",
        json={"url": "https://unknown.com", "status": "applied"},
    )
    assert response.status_code == 404


def test_get_tracker_summary(client, tmp_path, monkeypatch):
    """GET /api/tracker/summary returns status counts."""
    import src.dashboard.tracker_service as ts

    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"

    _write_yaml_jobs(
        yaml_path,
        [
            {
                "url": "https://example.com/job/1",
                "job_title": "A",
                "company_name": "Acme",
                "job_description": "test",
            },
            {
                "url": "https://example.com/job/2",
                "job_title": "B",
                "company_name": "Acme",
                "job_description": "test",
            },
        ],
    )

    merge_jobs(yaml_path, db_path)

    from src.dashboard.tracker_service import update_job

    update_job("https://example.com/job/1", {"status": "applied"}, db_path=db_path)

    monkeypatch.setattr(ts, "DB_PATH", db_path)

    response = client.get("/api/tracker/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["new"] == 1
    assert data["applied"] == 1
    assert data["total"] == 2
