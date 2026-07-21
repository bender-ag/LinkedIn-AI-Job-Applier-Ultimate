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


def _job_scored(row: sqlite3.Row, resume_text: str | None) -> dict:
    """Collect the display fields for one job, including the deterministic score."""
    data = {
        "title": row["job_title"] or "(no title)",
        "company": row["company_name"] or "(no company)",
        "location": row["location"] or "",
        "salary": row["salary_range"] or "",
        "llm_score": row["interest_score"],
        "reason": row["interest_reason"] or "",
        "url": row["url"],
        "first_seen": row["first_seen"] or "",
        "kw_score": None,
        "band": "",
        "missing": [],
    }
    if resume_text is not None:
        result = score_match(resume_text, row["job_description"] or "")
        data["kw_score"] = result.score
        data["band"] = result.band
        data["missing"] = result.missing[:MISSING_KEYWORDS_CAP]
    return data


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_html(jobs: list[dict], day: str, resume_note: str | None) -> str:
    """Self-contained, theme-aware HTML digest, sorted by keyword score desc."""
    ordered = sorted(jobs, key=lambda j: (j["kw_score"] is None, -(j["kw_score"] or 0)))
    cards = []
    for j in ordered:
        badge = (
            f'<span class="band band-{_esc(j["band"])}">{j["kw_score"]} · {_esc(j["band"])}</span>'
            if j["kw_score"] is not None
            else ""
        )
        meta = " · ".join(
            x for x in [_esc(j["company"]), _esc(j["location"]), _esc(j["salary"])] if x
        )
        missing = (
            '<div class="missing"><b>Missing keywords:</b> '
            + ", ".join(_esc(m) for m in j["missing"])
            + "</div>"
            if j["missing"]
            else ""
        )
        reason = f'<div class="reason">{_esc(j["reason"])}</div>' if j["reason"] else ""
        cards.append(
            f'<article class="job"><div class="jobhead"><h2><a href="{_esc(j["url"])}" '
            f'target="_blank" rel="noopener">{_esc(j["title"])}</a></h2>{badge}</div>'
            f'<div class="meta">{meta}</div>{reason}{missing}'
            f'<div class="seen">first seen {_esc(j["first_seen"][:19])}</div></article>'
        )
    note = f'<p class="note">{_esc(resume_note)}</p>' if resume_note else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job digest — {_esc(day)}</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--muted:#666;--card:#f7f6f4;--rule:#e2ded8;--accent:#9e3b2d;--link:#1a4f8a}}
@media(prefers-color-scheme:dark){{:root{{--bg:#16181c;--fg:#e8e6e3;--muted:#9a9a9a;--card:#1f2229;--rule:#33363d;--accent:#e0765f;--link:#7aa7d9}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:24px}}
.wrap{{max-width:760px;margin:0 auto}}h1{{font-size:20px;margin:0 0 4px}}.sub{{color:var(--muted);margin:0 0 20px;font-size:13px}}
.note{{background:var(--card);border:1px solid var(--rule);border-radius:8px;padding:8px 12px;color:var(--muted);font-size:13px}}
.job{{background:var(--card);border:1px solid var(--rule);border-radius:10px;padding:14px 16px;margin:0 0 12px}}
.jobhead{{display:flex;justify-content:space-between;align-items:baseline;gap:12px}}
.job h2{{font-size:16px;margin:0}}.job a{{color:var(--link);text-decoration:none}}.job a:hover{{text-decoration:underline}}
.band{{font-weight:700;font-size:12px;white-space:nowrap;padding:2px 8px;border-radius:20px;border:1px solid var(--rule)}}
.band-strong{{color:#1a7f37;border-color:#1a7f37}}.band-partial{{color:var(--accent);border-color:var(--accent)}}.band-weak{{color:var(--muted)}}
.meta{{color:var(--muted);font-size:13px;margin:4px 0}}.reason{{font-size:13px;margin:6px 0}}
.missing{{font-size:13px;margin:6px 0;color:var(--fg)}}.seen{{color:var(--muted);font-size:11px;margin-top:8px}}
</style></head><body><div class="wrap">
<h1>Job digest — {_esc(day)}</h1><p class="sub">{len(ordered)} new role{'s' if len(ordered)!=1 else ''}, sorted by match score</p>
{note}
{''.join(cards)}
</div></body></html>"""


def digest_jobs(
    db_path: Path,
    min_score: int = DEFAULT_MIN_SCORE,
    resume_path: Path = DEFAULT_RESUME,
    out_dir: Path = DEFAULT_OUT_DIR,
    fmt: str = "md",
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

        if fmt == "html":
            # HTML always renders the full current batch (no incremental append).
            path = out_dir / f"digest-{_today_str()}.html"
            jobs = [_job_scored(row, resume_text) for row in rows]
            path.write_text(render_html(jobs, _today_str(), resume_note), encoding="utf-8")
        else:
            path = _digest_path(out_dir)
            body = "\n".join(_format_job(row, resume_text) for row in rows)
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
    parser.add_argument("--format", choices=["md", "html"], default="md", dest="fmt")
    args = parser.parse_args(argv)

    digest_jobs(args.db, args.min_score, args.resume, args.out_dir, fmt=args.fmt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
