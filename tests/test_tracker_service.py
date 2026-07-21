"""Tests for tracker_service — job tracking and scoring."""

from __future__ import annotations

from pathlib import Path

from funnel.merge import merge_jobs
from src.dashboard.tracker_service import (
    STATUSES,
    get_jobs,
    status_counts,
    update_job,
)


def _write_yaml_jobs(path: Path, jobs: list[dict]) -> None:
    """Write test jobs to YAML file."""
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(jobs), encoding="utf-8")


def _write_resume(path: Path, text: str) -> None:
    """Write resume text to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_get_jobs_empty_db(tmp_path: Path):
    """get_jobs on non-existent DB returns empty list."""
    db_path = tmp_path / "funnel.db"
    jobs = get_jobs(db_path=db_path)
    assert jobs == []


def test_get_jobs_with_migration(tmp_path: Path):
    """get_jobs exercises the migration path (new columns added)."""
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
                "job_description": "Python Django backend engineer",
                "interest_score": 80,
            }
        ],
    )

    # Merge to create DB
    new, updated = merge_jobs(yaml_path, db_path)
    assert new == 1

    # Fetch jobs — migration should have added status, notes, etc.
    jobs = get_jobs(db_path=db_path)
    assert len(jobs) == 1
    job = jobs[0]

    # Check migrated columns exist and have defaults
    assert job["status"] == "new"  # Default status
    assert job["notes"] == ""
    assert job["applied_date"] is None
    assert job["tailored_resume_path"] is None
    assert job["tailored_cover_path"] is None
    # updated_at might be None if not set by merge


def test_get_jobs_computes_kw_score_with_resume(tmp_path: Path):
    """get_jobs computes kw_score, band, missing when resume exists."""
    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"
    resume_path = tmp_path / "resume.txt"

    resume_text = (
        "Python expert with Django, REST APIs, PostgreSQL, "
        "AWS, Docker, Kubernetes experience. Senior engineer."
    )
    _write_resume(resume_path, resume_text)

    _write_yaml_jobs(
        yaml_path,
        [
            {
                "url": "https://example.com/job/1",
                "job_title": "Backend Engineer",
                "company_name": "Acme",
                "job_description": (
                    "We seek a Python Django developer with "
                    "REST API, PostgreSQL, Docker skills. "
                    "Kubernetes a plus. AWS experience required."
                ),
                "interest_score": 80,
            }
        ],
    )

    merge_jobs(yaml_path, db_path)

    # Patch the resume path for testing
    import src.dashboard.tracker_service as ts

    original_resume_path = ts.RESUME_PATH
    ts.RESUME_PATH = resume_path

    try:
        jobs = get_jobs(db_path=db_path)
        assert len(jobs) == 1
        job = jobs[0]

        # Should have computed score
        assert job["kw_score"] is not None
        assert isinstance(job["kw_score"], int)
        assert job["band"] in ["strong", "partial", "weak"]
        assert isinstance(job["missing"], list)
    finally:
        ts.RESUME_PATH = original_resume_path


def test_get_jobs_no_score_without_resume(tmp_path: Path):
    """get_jobs returns None for kw_score when resume is missing."""
    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"
    missing_resume = tmp_path / "missing.txt"

    _write_yaml_jobs(
        yaml_path,
        [
            {
                "url": "https://example.com/job/1",
                "job_title": "Engineer",
                "company_name": "Acme",
                "job_description": "Python and Django",
            }
        ],
    )

    merge_jobs(yaml_path, db_path)

    # Patch to non-existent resume path
    import src.dashboard.tracker_service as ts

    original_resume_path = ts.RESUME_PATH
    ts.RESUME_PATH = missing_resume

    try:
        jobs = get_jobs(db_path=db_path)
        job = jobs[0]
        assert job["kw_score"] is None
        assert job["band"] == ""
        assert job["missing"] == []
    finally:
        ts.RESUME_PATH = original_resume_path


def test_get_jobs_filters_by_status(tmp_path: Path):
    """get_jobs status filter works."""
    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"

    _write_yaml_jobs(
        yaml_path,
        [
            {
                "url": "https://example.com/job/1",
                "job_title": "Job A",
                "company_name": "Acme",
                "job_description": "test",
            },
            {
                "url": "https://example.com/job/2",
                "job_title": "Job B",
                "company_name": "Beta",
                "job_description": "test",
            },
        ],
    )

    merge_jobs(yaml_path, db_path)

    # All jobs default to "new"
    all_jobs = get_jobs(db_path=db_path)
    assert len(all_jobs) == 2

    # Filter by status="new"
    new_jobs = get_jobs(db_path=db_path, status="new")
    assert len(new_jobs) == 2

    # Update one to "interested"
    update_job("https://example.com/job/1", {"status": "interested"}, db_path=db_path)

    # Filter again
    new_jobs = get_jobs(db_path=db_path, status="new")
    assert len(new_jobs) == 1
    assert new_jobs[0]["job_title"] == "Job B"

    interested_jobs = get_jobs(db_path=db_path, status="interested")
    assert len(interested_jobs) == 1
    assert interested_jobs[0]["job_title"] == "Job A"


def test_get_jobs_filters_by_search(tmp_path: Path):
    """get_jobs search filter works (case-insensitive substring)."""
    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"

    _write_yaml_jobs(
        yaml_path,
        [
            {
                "url": "https://example.com/job/1",
                "job_title": "Python Engineer",
                "company_name": "Acme Inc",
                "location": "San Francisco",
                "job_description": "test",
            },
            {
                "url": "https://example.com/job/2",
                "job_title": "Ruby Developer",
                "company_name": "Beta Corp",
                "location": "New York",
                "job_description": "test",
            },
        ],
    )

    merge_jobs(yaml_path, db_path)

    # Search title
    python_jobs = get_jobs(db_path=db_path, search="python")
    assert len(python_jobs) == 1
    assert python_jobs[0]["job_title"] == "Python Engineer"

    # Search company
    beta_jobs = get_jobs(db_path=db_path, search="beta")
    assert len(beta_jobs) == 1
    assert beta_jobs[0]["company_name"] == "Beta Corp"

    # Search location
    sf_jobs = get_jobs(db_path=db_path, search="san")
    assert len(sf_jobs) == 1
    assert sf_jobs[0]["location"] == "San Francisco"


def test_update_job_happy_path(tmp_path: Path):
    """update_job updates allowed fields and sets updated_at."""
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

    # Update
    updated = update_job(
        "https://example.com/job/1",
        {"status": "applied", "notes": "Good fit", "applied_date": "2026-07-21"},
        db_path=db_path,
    )

    assert updated["status"] == "applied"
    assert updated["notes"] == "Good fit"
    assert updated["applied_date"] == "2026-07-21"
    assert updated["updated_at"] is not None


def test_update_job_invalid_status_raises(tmp_path: Path):
    """update_job raises ValueError on invalid status."""
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

    try:
        update_job(
            "https://example.com/job/1",
            {"status": "invalid_status"},
            db_path=db_path,
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "invalid" in str(e).lower()


def test_update_job_unknown_url_raises(tmp_path: Path):
    """update_job raises KeyError on unknown url."""
    db_path = tmp_path / "funnel.db"

    try:
        update_job("https://unknown.com", {"status": "applied"}, db_path=db_path)
        assert False, "Should have raised KeyError"
    except KeyError:
        pass


def test_update_job_ignores_unknown_fields(tmp_path: Path):
    """update_job only updates allowed fields; ignores others."""
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

    # Try to update non-allowed field (e.g., job_title)
    updated = update_job(
        "https://example.com/job/1",
        {
            "status": "applied",
            "job_title": "HACKED",  # Should be ignored
            "notes": "test",
        },
        db_path=db_path,
    )

    # Status and notes should be updated
    assert updated["status"] == "applied"
    assert updated["notes"] == "test"

    # But job_title should NOT be changed
    assert updated["job_title"] == "Engineer"


def test_status_counts_empty_db(tmp_path: Path):
    """status_counts on empty DB returns 0 for all statuses."""
    db_path = tmp_path / "funnel.db"
    counts = status_counts(db_path=db_path)

    for status in STATUSES:
        assert counts[status] == 0
    assert counts["total"] == 0


def test_status_counts_with_jobs(tmp_path: Path):
    """status_counts tallies jobs per status, null->new."""
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
            {
                "url": "https://example.com/job/3",
                "job_title": "C",
                "company_name": "Acme",
                "job_description": "test",
            },
        ],
    )

    merge_jobs(yaml_path, db_path)

    # All default to status=NULL -> "new"
    counts = status_counts(db_path=db_path)
    assert counts["new"] == 3
    assert counts["total"] == 3

    # Update two to different statuses
    update_job("https://example.com/job/1", {"status": "applied"}, db_path=db_path)
    update_job("https://example.com/job/2", {"status": "interviewing"}, db_path=db_path)

    counts = status_counts(db_path=db_path)
    assert counts["new"] == 1
    assert counts["applied"] == 1
    assert counts["interviewing"] == 1
    assert counts["total"] == 3
