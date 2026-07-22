"""Tests for tailor_service — per-job tailoring helpers."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from funnel.merge import merge_jobs
from src.dashboard import tracker_service
from src.dashboard.tailor_service import (
    _fetch_job_row,
    _rel_to_root,
    _slug,
    _store_tailor_paths,
)


def _write_yaml_jobs(path: Path, jobs: list[dict]) -> None:
    """Write test jobs to YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(jobs), encoding="utf-8")


def test_slug_creates_filesystem_safe_name(tmp_path: Path):
    """_slug generates a filesystem-safe name from company/title and URL."""
    job = {
        "company_name": "Acme Corp",
        "job_title": "Senior Python Engineer",
        "url": "https://example.com/job/1",
    }

    slug = _slug(job)

    # Should be lowercase, hyphen-separated, no special chars
    assert re.match(r"^[a-z0-9-]+$", slug), f"Invalid slug: {slug}"
    # Should contain company and title fragments
    assert "acme" in slug or "corp" in slug
    assert "python" in slug or "engineer" in slug
    assert "senior" in slug or "engineer" in slug


def test_slug_is_stable_for_same_url():
    """_slug returns the same value for the same URL."""
    job = {
        "company_name": "Acme Corp",
        "job_title": "Engineer",
        "url": "https://example.com/job/1",
    }

    slug1 = _slug(job)
    slug2 = _slug(job)

    assert slug1 == slug2


def test_slug_differs_for_different_urls():
    """_slug differs when the URL changes, even with same company/title."""
    job1 = {
        "company_name": "Acme",
        "job_title": "Engineer",
        "url": "https://example.com/job/1",
    }
    job2 = {
        "company_name": "Acme",
        "job_title": "Engineer",
        "url": "https://example.com/job/2",
    }

    slug1 = _slug(job1)
    slug2 = _slug(job2)

    assert slug1 != slug2


def test_slug_handles_missing_company_and_title():
    """_slug gracefully handles missing company_name or job_title."""
    job = {"url": "https://example.com/job/1"}

    slug = _slug(job)

    assert isinstance(slug, str)
    assert len(slug) > 0
    assert re.match(r"^[a-z0-9-]+$", slug)


def test_fetch_job_row_returns_dict_for_known_url(tmp_path: Path):
    """_fetch_job_row returns a dict for a known URL."""
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

    row = _fetch_job_row("https://example.com/job/1", db_path)

    assert row is not None
    assert isinstance(row, dict)
    assert row["url"] == "https://example.com/job/1"
    assert row["job_title"] == "Engineer"


def test_fetch_job_row_returns_none_for_unknown_url(tmp_path: Path):
    """_fetch_job_row returns None for an unknown URL."""
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

    row = _fetch_job_row("https://example.com/job/nonexistent", db_path)

    assert row is None


def test_store_tailor_paths_updates_row(tmp_path: Path):
    """_store_tailor_paths updates both path columns and sets updated_at."""
    # Clear the memo to avoid cross-test contamination
    tracker_service._migrated_paths.clear()

    try:
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

        updated = _store_tailor_paths(
            "https://example.com/job/1",
            "data/output/tailored/job-1/resume.pdf",
            "data/output/tailored/job-1/cover_letter.md",
            db_path=db_path,
        )

        assert updated["tailored_resume_path"] == "data/output/tailored/job-1/resume.pdf"
        assert updated["tailored_cover_path"] == "data/output/tailored/job-1/cover_letter.md"
        assert updated["updated_at"] is not None
    finally:
        tracker_service._migrated_paths.clear()


def test_store_tailor_paths_does_not_affect_other_rows(tmp_path: Path):
    """_store_tailor_paths only updates the specified URL, not others."""
    # Clear the memo to avoid cross-test contamination
    tracker_service._migrated_paths.clear()

    try:
        yaml_path = tmp_path / "jobs.yaml"
        db_path = tmp_path / "funnel.db"

        _write_yaml_jobs(
            yaml_path,
            [
                {
                    "url": "https://example.com/job/1",
                    "job_title": "Engineer A",
                    "company_name": "Acme",
                    "job_description": "test",
                },
                {
                    "url": "https://example.com/job/2",
                    "job_title": "Engineer B",
                    "company_name": "Beta",
                    "job_description": "test",
                },
            ],
        )

        merge_jobs(yaml_path, db_path)

        # Update only job 1
        _store_tailor_paths(
            "https://example.com/job/1",
            "data/output/tailored/job-1/resume.pdf",
            "data/output/tailored/job-1/cover_letter.md",
            db_path=db_path,
        )

        # Verify job 2 is untouched
        job2 = _fetch_job_row("https://example.com/job/2", db_path)
        assert job2["tailored_resume_path"] is None
        assert job2["tailored_cover_path"] is None
    finally:
        tracker_service._migrated_paths.clear()


def test_store_tailor_paths_raises_keyerror_for_unknown_url(tmp_path: Path):
    """_store_tailor_paths raises KeyError when the URL is unknown."""
    # Clear the memo to avoid cross-test contamination
    tracker_service._migrated_paths.clear()

    try:
        db_path = tmp_path / "funnel.db"

        try:
            _store_tailor_paths(
                "https://unknown.com/job/1",
                "data/output/tailored/job-1/resume.pdf",
                "data/output/tailored/job-1/cover_letter.md",
                db_path=db_path,
            )
            assert False, "Should have raised KeyError"
        except KeyError as e:
            assert "not found" in str(e).lower()
    finally:
        tracker_service._migrated_paths.clear()


def test_rel_to_root_makes_path_relative(tmp_path: Path, monkeypatch):
    """_rel_to_root returns path relative to ROOT_DIR."""
    from src.dashboard import tailor_service

    monkeypatch.setattr(tailor_service, "ROOT_DIR", tmp_path)

    abs_path = tmp_path / "data" / "output" / "tailored" / "job-1" / "resume.pdf"
    result = _rel_to_root(abs_path)

    assert result == "data/output/tailored/job-1/resume.pdf"


def test_rel_to_root_uses_forward_slashes(tmp_path: Path, monkeypatch):
    """_rel_to_root always uses forward slashes, even on Windows."""
    from src.dashboard import tailor_service

    monkeypatch.setattr(tailor_service, "ROOT_DIR", tmp_path)

    abs_path = tmp_path / "data" / "output" / "resume.pdf"
    result = _rel_to_root(abs_path)

    # Result should use forward slashes (pathlib normalizes)
    assert "/" in result or result == "data/output/resume.pdf"
    assert "\\" not in result
