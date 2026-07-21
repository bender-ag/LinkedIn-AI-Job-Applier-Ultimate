"""Tests for funnel.digest daily markdown digest."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from funnel.digest import digest_jobs
from funnel.merge import ensure_schema
from funnel.score_match import score_match


def _seed_db(db_path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    for r in rows:
        conn.execute(
            """
            INSERT INTO jobs (
                url, job_title, company_name, location, job_description,
                company_description, salary_range, posted_date,
                interest_score, interest_reason, skills,
                first_seen, last_seen, digested
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r["url"],
                r.get("job_title"),
                r.get("company_name"),
                r.get("location"),
                r.get("job_description"),
                r.get("company_description"),
                r.get("salary_range"),
                r.get("posted_date"),
                r.get("interest_score", 0),
                r.get("interest_reason"),
                r.get("skills"),
                r.get("first_seen", "2026-01-01T00:00:00+00:00"),
                r.get("last_seen", "2026-01-01T00:00:00+00:00"),
                r.get("digested", 0),
            ),
        )
    conn.commit()
    conn.close()


def _digested(db_path: Path, url: str) -> int:
    conn = sqlite3.connect(db_path)
    val = conn.execute("SELECT digested FROM jobs WHERE url = ?", (url,)).fetchone()[0]
    conn.close()
    return val


RESUME = (
    "Technical Product Leader and Product Architect with eighteen years in "
    "investments technology. Promoted from Senior Business Analyst. Owned "
    "architecture, requirements, and delivery across data-heavy products. Drove "
    "roadmap and backlog prioritization, partnered with product owners, engineers, "
    "and designers, and ran Agile sprint planning and ceremonies. Translated "
    "business needs into user stories, acceptance criteria, and functional "
    "specifications. Elicited requirements through stakeholder interviews and data "
    "analysis; defined and coordinated user acceptance testing. Built data-flow "
    "diagrams and process documentation. Bridged business and engineering "
    "stakeholders. Tools: Jira, Confluence, SQL, Python, Tableau, Snowflake. "
    "Designed APIs and data models across microservices. Financial services and "
    "capital markets domain."
)

JD = (
    "Senior Business Analyst. Acme Financial Solutions provides treasury management "
    "and liquidity management to the public sector, local governments, and school "
    "districts. As the Senior Business Analyst on the LedgerMax product POD, you will "
    "own the requirements and delivery, partner with the product owner, engineers, "
    "and designers, and translate business needs into well-defined features. Thrive "
    "in an Agile team environment. Participate in sprint ceremonies, refine the "
    "product backlog with the product owner, and serve as the bridge to stakeholders. "
    "Elicit and document business requirements through stakeholder interviews and data "
    "analysis. Translate business needs into detailed user stories, acceptance "
    "criteria, and functional specifications. Define and execute user acceptance "
    "testing for each release. Create process documentation and data-flow diagrams. "
    "Strong understanding of Agile Scrum methodologies. Experience with backlog "
    "management tools such as Jira and documentation platforms such as Confluence. "
    "Understanding of software architecture concepts including APIs, microservices, "
    "and data models. Proficiency with data analysis and visualization. Seven years "
    "as a Business Analyst, preferably in financial services."
)

LOW_MATCH_JD = (
    "Warehouse associate needed for night shift packing and shipping. "
    "Must lift fifty pounds, operate forklift, and load trucks. "
    "Experience with inventory scanning guns preferred. No programming "
    "or product management required. Fast paced distribution center."
)


def test_digest_writes_markdown_and_marks_digested(tmp_path: Path):
    db = tmp_path / "funnel.db"
    out = tmp_path / "out"
    resume = tmp_path / "resume_text.txt"
    resume.write_text(RESUME, encoding="utf-8")
    _seed_db(
        db,
        [
            {
                "url": "https://example.com/a",
                "job_title": "Senior BA",
                "company_name": "Acme",
                "location": "NYC",
                "salary_range": "$140k",
                "job_description": JD,
                "interest_score": 85,
                "interest_reason": "Strong domain fit",
                "first_seen": "2026-07-01T12:00:00+00:00",
            },
            {
                "url": "https://example.com/low",
                "job_title": "Intern",
                "company_name": "Zed",
                "job_description": "short",
                "interest_score": 40,
            },
        ],
    )

    # Default min_score is 0; both undigested rows render when score floor is 0.
    n = digest_jobs(db, min_score=0, resume_path=resume, out_dir=out)
    assert n == 2

    digests = list(out.glob("digest-*.md"))
    assert len(digests) == 1
    text = digests[0].read_text(encoding="utf-8")
    assert "Senior BA" in text
    assert "Acme" in text
    assert "NYC" in text
    assert "$140k" in text
    assert "85" in text
    assert "Strong domain fit" in text
    assert "https://example.com/a" in text
    assert "Keyword score:" in text
    assert "Missing keywords:" in text
    # High kw_score job listed before low (Intern / short JD → score 0).
    assert text.index("Senior BA") < text.index("Intern")

    assert _digested(db, "https://example.com/a") == 1
    assert _digested(db, "https://example.com/low") == 1


def test_min_score_filters_kw_score(tmp_path: Path):
    db = tmp_path / "funnel.db"
    out = tmp_path / "out"
    resume = tmp_path / "resume_text.txt"
    resume.write_text(RESUME, encoding="utf-8")

    high = score_match(RESUME, JD).score
    low = score_match(RESUME, LOW_MATCH_JD).score
    assert high >= 70
    assert low < 70

    _seed_db(
        db,
        [
            {
                "url": "https://example.com/high",
                "job_title": "High Match",
                "job_description": JD,
                "interest_score": 10,  # interest_score must not gate inclusion
            },
            {
                "url": "https://example.com/lowkw",
                "job_title": "Low Match",
                "job_description": LOW_MATCH_JD,
                "interest_score": 99,
            },
        ],
    )

    n = digest_jobs(db, min_score=70, resume_path=resume, out_dir=out)
    assert n == 1
    text = list(out.glob("digest-*.md"))[0].read_text(encoding="utf-8")
    assert "High Match" in text
    assert "Low Match" not in text
    assert _digested(db, "https://example.com/high") == 1
    assert _digested(db, "https://example.com/lowkw") == 0

    # No resume → min_score ignored; both jobs included.
    out2 = tmp_path / "out2"
    conn = sqlite3.connect(db)
    conn.execute("UPDATE jobs SET digested = 0")
    conn.commit()
    conn.close()
    n2 = digest_jobs(db, min_score=70, resume_path=tmp_path / "missing.txt", out_dir=out2)
    assert n2 == 2


def test_missing_resume_note(tmp_path: Path):
    db = tmp_path / "funnel.db"
    out = tmp_path / "out"
    _seed_db(
        db,
        [
            {
                "url": "https://example.com/r",
                "job_title": "Role",
                "company_name": "Co",
                "interest_score": 90,
                "job_description": JD,
            }
        ],
    )
    n = digest_jobs(db, min_score=0, resume_path=tmp_path / "nope.txt", out_dir=out)
    assert n == 1
    text = (out / list(out.glob("digest-*.md"))[0]).read_text(encoding="utf-8")
    assert "resume_text.txt not found - keyword scoring skipped" in text
    assert "Keyword score:" not in text


def test_appends_on_second_md_run_same_day(tmp_path: Path):
    db = tmp_path / "funnel.db"
    out = tmp_path / "out"
    resume = tmp_path / "resume_text.txt"
    resume.write_text(RESUME, encoding="utf-8")
    _seed_db(
        db,
        [
            {
                "url": "https://example.com/1",
                "job_title": "First Job",
                "company_name": "A",
                "interest_score": 80,
                "job_description": JD,
            },
            {
                "url": "https://example.com/2",
                "job_title": "Second Job",
                "company_name": "B",
                "interest_score": 75,
                "job_description": JD,
                "digested": 1,
            },
        ],
    )
    digest_jobs(db, min_score=0, resume_path=resume, out_dir=out)
    digests = list(out.glob("digest-*.md"))
    assert len(digests) == 1
    path = digests[0]

    # Reset second job as undigested for another run
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE jobs SET digested = 0 WHERE url = ?",
        ("https://example.com/2",),
    )
    conn.commit()
    conn.close()

    digest_jobs(db, min_score=0, resume_path=resume, out_dir=out)
    text = path.read_text(encoding="utf-8")
    assert "First Job" in text
    assert "Second Job" in text
    assert "## Run 2" in text
    assert len(list(out.glob("digest-*.md"))) == 1


def test_html_two_runs_same_day_keeps_all_jobs(tmp_path: Path):
    from funnel import digest as digest_mod

    db = tmp_path / "funnel.db"
    resume = tmp_path / "resume_text.txt"
    resume.write_text(RESUME, encoding="utf-8")
    _seed_db(
        db,
        [
            {
                "url": "https://example.com/batch1",
                "job_title": "Batch One Role",
                "company_name": "A",
                "job_description": JD,
                "interest_score": 0,
            },
        ],
    )
    digest_jobs(db, min_score=0, resume_path=resume, out_dir=tmp_path, fmt="html")
    html1 = (tmp_path / f"digest-{digest_mod._today_str()}.html").read_text(encoding="utf-8")
    assert "Batch One Role" in html1
    # HTML must not mark digested — full board regenerates.
    assert _digested(db, "https://example.com/batch1") == 0

    _seed_db(
        db,
        [
            {
                "url": "https://example.com/batch2",
                "job_title": "Batch Two Role",
                "company_name": "B",
                "job_description": JD,
                "interest_score": 0,
            },
        ],
    )
    digest_jobs(db, min_score=0, resume_path=resume, out_dir=tmp_path, fmt="html")
    html2 = (tmp_path / f"digest-{digest_mod._today_str()}.html").read_text(encoding="utf-8")
    assert "Batch One Role" in html2
    assert "Batch Two Role" in html2


def test_zero_new_jobs(tmp_path: Path, capsys):
    db = tmp_path / "funnel.db"
    out = tmp_path / "out"
    _seed_db(
        db,
        [
            {
                "url": "https://example.com/done",
                "job_title": "Done",
                "interest_score": 95,
                "digested": 1,
            }
        ],
    )
    n = digest_jobs(db, min_score=0, resume_path=tmp_path / "r.txt", out_dir=out)
    assert n == 0
    assert "0 new jobs" in capsys.readouterr().out
    assert list(out.glob("digest-*.md")) == []


def test_html_format_renders_and_sorts(tmp_path):
    from funnel import digest as digest_mod

    db = tmp_path / "f.db"
    conn = sqlite3.connect(db)
    conn.execute(digest_mod_create_sql())
    conn.execute(
        "INSERT INTO jobs (url, job_title, company_name, job_description, interest_score,"
        " first_seen, last_seen, digested) VALUES (?,?,?,?,?,?,?,0)",
        (
            "u1",
            "React Engineer",
            "Acme",
            "react typescript node graphql",
            80,
            "2026-07-21T00:00:00+00:00",
            "2026-07-21T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    n = digest_mod.digest_jobs(
        db, min_score=0, resume_path=tmp_path / "nope.txt", out_dir=tmp_path, fmt="html"
    )
    assert n == 1
    html = (tmp_path / f"digest-{digest_mod._today_str()}.html").read_text()
    assert "<!doctype html>" in html
    assert "React Engineer" in html
    assert "u1" in html
    # HTML path must not mark digested
    assert _digested(db, "u1") == 0


def digest_mod_create_sql():
    return (
        "CREATE TABLE jobs (url TEXT PRIMARY KEY, job_title TEXT, company_name TEXT,"
        " location TEXT, job_description TEXT, company_description TEXT, salary_range TEXT,"
        " posted_date TEXT, interest_score INTEGER, interest_reason TEXT, skills TEXT,"
        " first_seen TEXT, last_seen TEXT, digested INTEGER DEFAULT 0)"
    )
