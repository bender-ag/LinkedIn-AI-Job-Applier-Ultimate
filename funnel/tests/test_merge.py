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
