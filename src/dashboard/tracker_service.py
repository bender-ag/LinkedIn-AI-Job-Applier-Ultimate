"""Funnel database service for job tracking and scoring."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from funnel.merge import ensure_schema
from funnel.score_match import score_match
from src.dashboard.runtime import ROOT_DIR

DB_PATH = ROOT_DIR / "data" / "funnel.db"
RESUME_PATH = ROOT_DIR / "data" / "resumes" / "resume_text.txt"

# Module-level memoization: migrate schema once per db_path per process.
# Distinct tmp db paths (tests) each migrate once.
_migrated_paths: set[str] = set()


def _ensure_schema_once(conn: sqlite3.Connection, db_path: Path) -> None:
    """
    Ensure schema is migrated, but only once per db_path.

    Uses a module-level memo to avoid repeated PRAGMA and CREATE statements.
    """
    key = str(db_path)
    if key in _migrated_paths:
        return
    ensure_schema(conn)
    _migrated_paths.add(key)


STATUSES = [
    "new",
    "interested",
    "applied",
    "interviewing",
    "rejected",
    "offer",
    "archived",
]


def _now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def _read_resume(resume_path: Path) -> str | None:
    """Read resume file; return None if missing or unreadable."""
    try:
        if resume_path.exists():
            return resume_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        pass
    return None


def get_jobs(
    db_path: Path | None = None,
    status: str | None = None,
    search: str | None = None,
) -> list[dict]:
    """
    Get all jobs from database, optionally filtered by status and search term.

    Each dict contains:
    - url, job_title, company_name, location, salary_range
    - first_seen, last_seen, status, notes, applied_date
    - tailored_resume_path, tailored_cover_path
    - kw_score (or None), band, matched + missing (keyword lists, top 15 each)
    - job_description (full text)

    Filters:
    - status: exact match (null status counts as "new")
    - search: case-insensitive substring over title, company, location

    Returns unsorted list.
    """
    if db_path is None:
        db_path = DB_PATH
    if not db_path.exists():
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Ensure schema is migrated before querying
        _ensure_schema_once(conn, db_path)
        cursor = conn.execute("SELECT * FROM jobs")
        rows = cursor.fetchall()
    finally:
        conn.close()

    # Read resume once per call
    resume_text = _read_resume(RESUME_PATH)

    jobs = []
    for row in rows:
        job_dict = dict(row)

        # Normalize status: NULL -> "new"
        job_dict["status"] = job_dict.get("status") or "new"

        # Apply filters first (before scoring)
        if status and job_dict["status"] != status:
            continue

        if search:
            search_lower = search.lower()
            found = (
                search_lower in (job_dict.get("job_title") or "").lower()
                or search_lower in (job_dict.get("company_name") or "").lower()
                or search_lower in (job_dict.get("location") or "").lower()
            )
            if not found:
                continue

        # Compute kw_score, band, matched, missing (only for rows that pass filters)
        kw_score = None
        band = ""
        matched = []
        missing = []

        if resume_text and job_dict.get("job_description"):
            match_result = score_match(resume_text, job_dict["job_description"])
            if match_result.ok:
                kw_score = match_result.score
                band = match_result.band
                matched = match_result.matched[:15]
                missing = match_result.missing[:15]

        # The UI only shows a short preview; don't ship multi-KB JDs for every
        # row. Truncate server-side (after scoring, which needs the full text).
        jd = job_dict.get("job_description") or ""
        job_dict["job_description"] = jd[:800]

        job_dict["kw_score"] = kw_score
        job_dict["band"] = band
        job_dict["matched"] = matched
        job_dict["missing"] = missing

        jobs.append(job_dict)

    return jobs


def update_job(
    url: str,
    fields: dict[str, Any],
    db_path: Path | None = None,
) -> dict:
    """
    Update a job by URL.

    Allowed fields: {status, notes, applied_date}
    Validates status in STATUSES, sets updated_at to UTC ISO.

    Returns the updated row as a dict.
    Raises KeyError if url not found, ValueError if bad status.
    """
    if db_path is None:
        db_path = DB_PATH

    # Validate allowed fields
    allowed = {"status", "notes", "applied_date"}
    updates = {k: v for k, v in fields.items() if k in allowed}

    if not updates:
        raise ValueError("No valid fields to update")

    # Validate status if provided
    if "status" in updates and updates["status"] not in STATUSES:
        raise ValueError(f"Invalid status: {updates['status']}")

    # Add updated_at
    updates["updated_at"] = _now_iso()

    if not db_path.exists():
        raise KeyError(f"Job not found: {url}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Ensure schema is migrated before querying
        _ensure_schema_once(conn, db_path)
        # Check if job exists
        existing = conn.execute("SELECT url FROM jobs WHERE url = ?", (url,)).fetchone()
        if not existing:
            raise KeyError(f"Job not found: {url}")

        # Build UPDATE query
        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [url]

        conn.execute(
            f"UPDATE jobs SET {set_clause} WHERE url = ?",
            values,
        )
        conn.commit()

        # Fetch and return updated row
        updated = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
        return dict(updated)
    finally:
        conn.close()


def status_counts(db_path: Path | None = None) -> dict[str, int]:
    """
    Get count of jobs per status, including all STATUSES (0 when absent).
    Null status counts as 'new'.
    Also includes 'total' key.
    """
    if db_path is None:
        db_path = DB_PATH

    if not db_path.exists():
        result = {status: 0 for status in STATUSES}
        result["total"] = 0
        return result

    conn = sqlite3.connect(db_path)

    try:
        # Ensure schema is migrated before querying
        _ensure_schema_once(conn, db_path)
        cursor = conn.execute("""
            SELECT COALESCE(status, 'new') as status, COUNT(*) as cnt
            FROM jobs
            GROUP BY COALESCE(status, 'new')
            """)
        counts = {row[0]: row[1] for row in cursor.fetchall()}
    finally:
        conn.close()

    result = {status: counts.get(status, 0) for status in STATUSES}
    # Total is every job, including any with an out-of-vocab status value.
    result["total"] = sum(counts.values())
    return result
