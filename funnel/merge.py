"""YAML -> SQLite lifecycle merge for interesting jobs."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_YAML = Path("data/output/interesting_jobs.yaml")
DEFAULT_DB = Path("data/funnel.db")

CREATE_JOBS_SQL = """
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
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _skills_json(skills: Any) -> str | None:
    if skills is None:
        return None
    if isinstance(skills, str):
        return skills
    return json.dumps(skills)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_JOBS_SQL)
    conn.commit()

    # Idempotent migration: add missing columns to jobs table
    cursor = conn.execute("PRAGMA table_info(jobs)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    new_columns = [
        ("status", "TEXT DEFAULT 'new'"),
        ("notes", "TEXT DEFAULT ''"),
        ("applied_date", "TEXT"),
        ("tailored_resume_path", "TEXT"),
        ("tailored_cover_path", "TEXT"),
        ("updated_at", "TEXT"),
    ]

    for col_name, col_def in new_columns:
        if col_name not in existing_columns:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_def}")

    # Sweep-run history (for the tracker's History tab).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sweeps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT,
            finished_at TEXT,
            queries_json TEXT,
            collected INTEGER,
            new_count INTEGER,
            status TEXT
        )
        """)
    conn.commit()


def merge_jobs(yaml_path: Path, db_path: Path) -> tuple[int, int]:
    """Upsert jobs from YAML into SQLite. Returns (new_count, updated_count)."""
    if not yaml_path.exists():
        print(f"YAML file not found: {yaml_path} — nothing to merge")
        return 0, 0

    try:
        with yaml_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        print(f"Could not read YAML ({yaml_path}): {exc} — nothing to merge")
        return 0, 0

    if data is None:
        print(f"Empty YAML file: {yaml_path} — nothing to merge")
        return 0, 0

    if not isinstance(data, list):
        print(f"Malformed YAML (expected list): {yaml_path} — nothing to merge")
        return 0, 0

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        now = _now_iso()
        new_count = 0
        updated_count = 0

        for entry in data:
            if not isinstance(entry, dict):
                logger.warning("Skipping non-dict YAML entry: %r", entry)
                continue
            url = entry.get("url")
            if not url:
                logger.warning("Skipping job with missing url: %r", entry.get("job_title"))
                continue

            row = conn.execute("SELECT url FROM jobs WHERE url = ?", (url,)).fetchone()
            skills = _skills_json(entry.get("skills"))
            fields = (
                entry.get("job_title"),
                entry.get("company_name"),
                entry.get("location"),
                entry.get("job_description"),
                entry.get("company_description"),
                entry.get("salary_range"),
                entry.get("posted_date"),
                entry.get("interest_score"),
                entry.get("interest_reason"),
                skills,
            )

            if row is None:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        url, job_title, company_name, location, job_description,
                        company_description, salary_range, posted_date,
                        interest_score, interest_reason, skills,
                        first_seen, last_seen, digested
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (url, *fields, now, now),
                )
                new_count += 1
            else:
                # Update only metadata, never overwrite user-owned columns
                # (status, notes, applied_date, tailored_resume_path, tailored_cover_path, updated_at)
                conn.execute(
                    """
                    UPDATE jobs SET
                        job_title = ?,
                        company_name = ?,
                        location = ?,
                        job_description = ?,
                        company_description = ?,
                        salary_range = ?,
                        posted_date = ?,
                        interest_score = ?,
                        interest_reason = ?,
                        skills = ?,
                        last_seen = ?
                    WHERE url = ?
                    """,
                    (*fields, now, url),
                )
                updated_count += 1

        conn.commit()
    finally:
        conn.close()

    print(f"{new_count} new, {updated_count} updated")
    return new_count, updated_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge interesting_jobs YAML into SQLite")
    parser.add_argument("--yaml", type=Path, default=DEFAULT_YAML, help="Path to YAML input")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to SQLite database")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    merge_jobs(args.yaml, args.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
