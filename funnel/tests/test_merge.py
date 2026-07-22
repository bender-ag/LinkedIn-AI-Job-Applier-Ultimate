"""Tests for funnel.merge YAML -> SQLite lifecycle."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import yaml

from funnel.merge import merge_jobs


def _write_yaml(path: Path, jobs: list[dict]) -> None:
    path.write_text(yaml.dump(jobs), encoding="utf-8")


def _fetch(db: Path, url: str) -> sqlite3.Row:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
    conn.close()
    return row


def test_first_insert_sets_first_seen(tmp_path: Path):
    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"
    _write_yaml(
        yaml_path,
        [
            {
                "url": "https://example.com/job/1",
                "job_title": "Analyst",
                "company_name": "Acme",
                "location": "Remote",
                "job_description": "Do analysis",
                "interest_score": 80,
                "interest_reason": "Good fit",
                "skills": ["SQL", "Python"],
                "salary_range": "$100k",
            }
        ],
    )

    new, updated = merge_jobs(yaml_path, db_path)
    assert new == 1
    assert updated == 0

    row = _fetch(db_path, "https://example.com/job/1")
    assert row is not None
    assert row["first_seen"] == row["last_seen"]
    assert row["job_title"] == "Analyst"
    assert row["interest_score"] == 80
    assert row["digested"] == 0
    assert "SQL" in row["skills"]


def test_remerge_updates_last_seen_not_first_seen(tmp_path: Path):
    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"
    job = {
        "url": "https://example.com/job/2",
        "job_title": "Engineer",
        "company_name": "Beta",
        "job_description": "Build things",
        "interest_score": 70,
        "interest_reason": "ok",
        "skills": ["Go"],
    }
    _write_yaml(yaml_path, [job])
    merge_jobs(yaml_path, db_path)
    first = _fetch(db_path, job["url"])
    first_seen = first["first_seen"]
    time.sleep(0.01)

    job["interest_score"] = 90
    job["interest_reason"] = "better"
    job["salary_range"] = "$150k"
    job["job_description"] = "Build more things"
    _write_yaml(yaml_path, [job])
    new, updated = merge_jobs(yaml_path, db_path)
    assert new == 0
    assert updated == 1

    row = _fetch(db_path, job["url"])
    assert row["first_seen"] == first_seen
    assert row["last_seen"] >= first_seen
    assert row["interest_score"] == 90
    assert row["interest_reason"] == "better"
    assert row["salary_range"] == "$150k"
    assert row["job_description"] == "Build more things"


def test_missing_url_skipped(tmp_path: Path, caplog):
    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"
    _write_yaml(
        yaml_path,
        [
            {"job_title": "No URL", "interest_score": 99},
            {
                "url": "https://example.com/job/3",
                "job_title": "Has URL",
                "interest_score": 50,
            },
        ],
    )
    with caplog.at_level("WARNING"):
        new, updated = merge_jobs(yaml_path, db_path)
    assert new == 1
    assert updated == 0
    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert count == 1
    assert any("missing url" in r.message.lower() for r in caplog.records)


def test_missing_yaml_handled(tmp_path: Path, capsys):
    db_path = tmp_path / "funnel.db"
    missing = tmp_path / "nope.yaml"
    new, updated = merge_jobs(missing, db_path)
    assert new == 0
    assert updated == 0
    out = capsys.readouterr().out
    assert "not found" in out.lower() or "nothing to merge" in out.lower()
    assert not db_path.exists()


def test_schema_migration_adds_user_owned_columns(tmp_path: Path):
    """ensure_schema idempotently adds user-owned columns to legacy DB."""
    from funnel.merge import ensure_schema

    db_path = tmp_path / "funnel.db"

    # Create a DB with old schema (missing the new columns)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            url TEXT PRIMARY KEY,
            job_title TEXT,
            company_name TEXT,
            location TEXT,
            job_description TEXT,
            company_description TEXT,
            salary_range TEXT,
            posted_date TEXT,
            interest_score INTEGER,
            interest_reason TEXT,
            skills TEXT,
            first_seen TEXT,
            last_seen TEXT,
            digested INTEGER DEFAULT 0
        )
        """)
    conn.execute(
        """
        INSERT INTO jobs (url, job_title, company_name, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "https://example.com/job/legacy",
            "Legacy Job",
            "Old Company",
            "2026-01-01T00:00:00",
            "2026-01-02T00:00:00",
        ),
    )
    conn.commit()
    conn.close()

    # Run ensure_schema — should add missing columns
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    conn.close()

    # Verify columns exist
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("PRAGMA table_info(jobs)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()

    assert "status" in columns
    assert "notes" in columns
    assert "applied_date" in columns
    assert "tailored_resume_path" in columns
    assert "tailored_cover_path" in columns
    assert "updated_at" in columns


def test_schema_migration_idempotent(tmp_path: Path):
    """ensure_schema is idempotent — re-running adds no duplicates."""
    from funnel.merge import ensure_schema

    db_path = tmp_path / "funnel.db"

    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    conn.close()

    # Run again
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    conn.close()

    # Verify no errors and table still valid
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("PRAGMA table_info(jobs)")
    columns = list(cursor.fetchall())
    conn.close()

    # Should have exactly 20 columns (old 14 + new 6)
    assert len(columns) == 20


def test_merge_preserves_user_owned_columns(tmp_path: Path):
    """Merging existing job does NOT overwrite user-owned columns."""
    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"
    job = {
        "url": "https://example.com/job/1",
        "job_title": "Engineer",
        "company_name": "Acme",
        "job_description": "Build things",
        "interest_score": 80,
    }
    _write_yaml(yaml_path, [job])

    # First merge
    new, updated = merge_jobs(yaml_path, db_path)
    assert new == 1

    # Manually set user-owned columns
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        UPDATE jobs SET status = ?, notes = ?, applied_date = ?
        WHERE url = ?
        """,
        ("applied", "Good fit!", "2026-07-15", job["url"]),
    )
    conn.commit()
    conn.close()

    # Verify they were set
    row = _fetch(db_path, job["url"])
    assert row["status"] == "applied"
    assert row["notes"] == "Good fit!"
    assert row["applied_date"] == "2026-07-15"

    # Re-merge with changed metadata
    job["interest_score"] = 90
    job["job_description"] = "Build more things"
    _write_yaml(yaml_path, [job])
    new, updated = merge_jobs(yaml_path, db_path)
    assert updated == 1

    # User-owned columns should NOT change
    row = _fetch(db_path, job["url"])
    assert row["status"] == "applied"
    assert row["notes"] == "Good fit!"
    assert row["applied_date"] == "2026-07-15"

    # But metadata should be updated
    assert row["interest_score"] == 90
    assert row["job_description"] == "Build more things"
