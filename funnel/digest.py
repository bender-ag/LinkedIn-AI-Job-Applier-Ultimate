"""Digest undigested high-score jobs into a daily markdown file."""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from funnel.score_match import score_match

DEFAULT_DB = Path("data/funnel.db")
DEFAULT_RESUME = Path("data/resumes/resume_text.txt")
DEFAULT_OUT_DIR = Path("/artifacts")
DEFAULT_MIN_SCORE = 70
MISSING_KEYWORDS_CAP = 15


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_hm() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M")


def _digest_path(out_dir: Path, day: str | None = None) -> Path:
    return out_dir / f"digest-{day or _today_str()}.md"


def _next_run_heading(existing: str) -> str:
    """Return '## Run N (HH:MM)' for an append section."""
    runs = re.findall(r"^## Run (\d+)", existing, flags=re.MULTILINE)
    n = max(int(x) for x in runs) + 1 if runs else 2
    return f"## Run {n} ({_now_hm()})"


def _format_job(
    row: sqlite3.Row,
    resume_text: str | None,
) -> str:
    title = row["job_title"] or "(no title)"
    company = row["company_name"] or "(no company)"
    location = row["location"] or ""
    salary = row["salary_range"]
    llm_score = row["interest_score"]
    reason = row["interest_reason"] or ""
    url = row["url"]
    first_seen = row["first_seen"] or ""

    lines = [
        f"### {title}",
        f"- **Company:** {company}",
        f"- **Location:** {location}" if location else None,
        f"- **Salary:** {salary}" if salary else None,
        f"- **LLM score:** {llm_score}" + (f" — {reason}" if reason else ""),
    ]
    lines = [ln for ln in lines if ln is not None]

    if resume_text is not None:
        jd = row["job_description"] or ""
        result = score_match(resume_text, jd)
        missing = result.missing[:MISSING_KEYWORDS_CAP]
        lines.append(f"- **Keyword score:** {result.score} ({result.band})")
        if missing:
            lines.append(f"- **Missing keywords:** {', '.join(missing)}")
        else:
            lines.append("- **Missing keywords:** (none)")

    lines.append(f"- **URL:** {url}")
    lines.append(f"- **First seen:** {first_seen}")
    lines.append("")
    return "\n".join(lines)


def digest_jobs(
    db_path: Path,
    min_score: int = DEFAULT_MIN_SCORE,
    resume_path: Path = DEFAULT_RESUME,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> int:
    """Write digest markdown for undigested jobs. Returns count written."""
    if not db_path.exists():
        print("0 new jobs")
        return 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE digested = 0 AND interest_score >= ?
            ORDER BY interest_score DESC
            """,
            (min_score,),
        ).fetchall()

        if not rows:
            print("0 new jobs")
            return 0

        resume_text: str | None = None
        resume_note: str | None = None
        if resume_path.exists():
            resume_text = resume_path.read_text(encoding="utf-8")
        else:
            resume_note = "resume_text.txt not found - keyword scoring skipped"

        out_dir.mkdir(parents=True, exist_ok=True)
        path = _digest_path(out_dir)
        body_parts = [_format_job(row, resume_text) for row in rows]
        body = "\n".join(body_parts)

        if path.exists():
            existing = path.read_text(encoding="utf-8")
            heading = _next_run_heading(existing)
            section = f"\n{heading}\n\n"
            if resume_note:
                section += f"{resume_note}\n\n"
            section += body
            path.write_text(existing + section, encoding="utf-8")
        else:
            header = f"# Job digest — {_today_str()}\n\n"
            if resume_note:
                header += f"{resume_note}\n\n"
            path.write_text(header + body, encoding="utf-8")

        urls = [row["url"] for row in rows]
        conn.executemany(
            "UPDATE jobs SET digested = 1 WHERE url = ?",
            [(u,) for u in urls],
        )
        conn.commit()
    finally:
        conn.close()

    print(f"{len(rows)} new jobs")
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Digest undigested high-score jobs")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--min-score", type=int, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--resume", type=Path, default=DEFAULT_RESUME)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    digest_jobs(args.db, args.min_score, args.resume, args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
