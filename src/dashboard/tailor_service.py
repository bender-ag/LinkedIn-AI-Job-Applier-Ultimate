"""Per-job résumé + cover-letter tailoring for the tracker.

Wires the existing résumé builder + ``GPTAnswerer`` against a single ``funnel.db``
job row: generates a tailored résumé PDF and a cover letter, writes them under
``data/output/tailored/<slug>/``, and persists the paths back onto the row
(``tailored_resume_path`` / ``tailored_cover_path`` — columns already present in
the funnel schema).

Heavy LLM / résumé-builder / Playwright imports are deferred into
``tailor_job`` so importing this module (and the tracker service) stays cheap and
test-friendly. The DB-persist step is factored into ``_store_tailor_paths`` so it
can be tested without invoking any LLM.

Anonymization follows ``.claude/rules/security.md``: personal data is replaced
before every LLM call and restored afterwards (the résumé builder de-anonymizes
the HTML; we de-anonymize the cover-letter text here).

Note: the résumé read here (``data/resumes/resume_text.txt``) is the generated
source of truth per ``rules.local.md`` — regenerating it from the Obsidian note
is a session/pre-run step, not this handler's responsibility.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

import anyio.to_thread

from config.constants import RESUME_DIR
from config.logger_config import logger
from src.dashboard.runtime import ROOT_DIR
from src.dashboard.tracker_service import DB_PATH, _ensure_schema_once, _now_iso

TAILORED_DIR = ROOT_DIR / "data" / "output" / "tailored"


class JobNotFound(Exception):
    """Raised when a tailor request targets a URL not present in the funnel DB."""


def _slug(job: dict[str, Any]) -> str:
    """Build a filesystem-safe, stable directory name for a job's artifacts."""
    base = f"{job.get('company_name') or 'company'}-{job.get('job_title') or 'role'}"
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:60] or "job"
    # Stable per-URL suffix so re-tailoring the same job reuses its directory and
    # distinct jobs never collide on identical company/title text.
    suffix = hashlib.sha1((job.get("url") or "").encode("utf-8")).hexdigest()[:8]
    return f"{base}-{suffix}"


def _fetch_job_row(url: str, db_path: Path) -> dict[str, Any] | None:
    """Return a single job row as a dict, or None if absent."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema_once(conn, db_path)
        row = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _store_tailor_paths(
    url: str,
    resume_path: str,
    cover_path: str,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """
    Persist tailored artifact paths (ROOT-relative) onto a job row.

    Returns the updated row as a dict. Raises KeyError if the URL is unknown.
    This is intentionally LLM-free so it can be unit-tested directly.
    """
    if db_path is None:
        db_path = DB_PATH

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_schema_once(conn, db_path)
        existing = conn.execute("SELECT url FROM jobs WHERE url = ?", (url,)).fetchone()
        if not existing:
            raise KeyError(f"Job not found: {url}")
        conn.execute(
            "UPDATE jobs SET tailored_resume_path = ?, tailored_cover_path = ?, "
            "updated_at = ? WHERE url = ?",
            (resume_path, cover_path, _now_iso(), url),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
        return dict(updated)
    finally:
        conn.close()


def _rel_to_root(path: Path) -> str:
    """Return a path relative to ROOT_DIR (for storage + the file-serving route)."""
    return str(path.resolve().relative_to(ROOT_DIR.resolve()))


def _tailor_job_sync(url: str, db_path: Path | None = None) -> dict[str, Any]:
    """
    Synchronous tailoring implementation (blocking LLM + PDF work).

    Run off the event loop via :func:`tailor_job`. Returns the updated job row
    (dict) including ROOT-relative ``tailored_resume_path`` /
    ``tailored_cover_path``.

    Raises:
        JobNotFound: the URL is not in the DB.
        FileNotFoundError: the résumé source text is missing.
        RuntimeError: no LLM API key is configured.
    """
    # Deferred heavy imports — keep module import cheap and test-friendly.
    import dotenv

    from src.job_manager.resume_anonymizer import ResumeAnonymizer
    from src.llm.llm_manager import GPTAnswerer
    from src.pydantic_models.prompt_models import ResumeStructure
    from src.resume_builder.resume_generator import ResumeGenerator
    from src.resume_builder.resume_manager import ResumeManager
    from src.resume_builder.style_manager import StyleManager
    from src.utils.utils import load_yaml_file, save_yaml_file

    if db_path is None:
        db_path = DB_PATH

    job = _fetch_job_row(url, db_path)
    if job is None:
        raise JobNotFound(f"Job not found: {url}")

    # ── Secrets ──
    secrets = dotenv.dotenv_values(ROOT_DIR / ".env")
    llm_api_key = secrets.get("llm_api_key", "") or ""
    llm_proxy = secrets.get("llm_proxy", "") or ""
    if not llm_api_key:
        raise RuntimeError("No LLM API key configured (set llm_api_key in .env)")

    # ── Résumé source (generated from the Obsidian note; never hand-edited) ──
    resume_dir = ROOT_DIR / RESUME_DIR
    resume_text_file = resume_dir / "resume_text.txt"
    if not resume_text_file.exists():
        raise FileNotFoundError(f"Résumé text not found: {resume_text_file}")
    resume_text = resume_text_file.read_text(encoding="utf-8")

    structured_file = resume_dir / "structured_resume.yaml"
    try:
        resume_structured = load_yaml_file(structured_file)
    except Exception as exc:
        if not str(exc).startswith("File not found"):
            raise
        # Derive the structured résumé once (an LLM call) and cache it.
        parsed = GPTAnswerer(llm_api_key, llm_proxy).parse_resume(resume_text)
        resume_structured = ResumeStructure(**parsed).model_dump()
        save_yaml_file(structured_file, resume_structured)

    # ── Anonymize before any LLM sees the résumé (security.md) ──
    anonymizer = ResumeAnonymizer(resume_structured)
    anonymizer.anonymize_personal_information()
    resume_structured_anon = anonymizer.resume_anonymized
    resume_text_anon = anonymizer.anonymize_text(resume_text)

    gpt = GPTAnswerer(llm_api_key, llm_proxy)
    gpt.set_resume(resume_structured_anon, resume_text_anon)
    # set_job stamps job_url/title/company onto every subsequent cost-log entry.
    gpt.set_job(job)

    out_dir = TAILORED_DIR / _slug(job)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Résumé PDF (builder de-anonymizes the HTML internally) ──
    resume_generator = ResumeGenerator(gpt, anonymizer)
    resume_manager = ResumeManager(llm_api_key, StyleManager(), resume_generator)
    resume_manager.choose_style()
    if getattr(resume_manager, "selected_style", None) is None:
        resume_manager.choose_default_style()
    # Guard a mistyped RESUME_STYLE: an unknown style would otherwise KeyError
    # deep in PDF generation (get_style_path). Fall back to the default.
    if resume_manager.selected_style not in resume_manager.style_manager.get_styles():
        logger.warning(
            "Configured resume style %r is not available; using default style.",
            resume_manager.selected_style,
        )
        resume_manager.choose_default_style()
    # pdf_base64() is async and self-contained (HTML_to_PDF starts/stops its own
    # Playwright), so a fresh loop in this worker thread is safe.
    pdf_base64 = asyncio.run(resume_manager.pdf_base64())
    resume_path = out_dir / "resume.pdf"
    resume_path.write_bytes(base64.b64decode(pdf_base64))

    # ── Cover letter (anonymized to the LLM, restored on the way out) ──
    cover_anon = gpt.write_cover_letter()
    cover_text = anonymizer.deanonymize_text(cover_anon)
    cover_path = out_dir / "cover_letter.md"
    cover_path.write_text(cover_text, encoding="utf-8")

    return _store_tailor_paths(
        url,
        _rel_to_root(resume_path),
        _rel_to_root(cover_path),
        db_path=db_path,
    )


async def tailor_job(url: str, db_path: Path | None = None) -> dict[str, Any]:
    """
    Generate a tailored résumé PDF + cover letter for one job and persist paths.

    Offloads the blocking LLM + PDF work to a worker thread so the dashboard's
    event loop stays responsive during the (30–90s) tailor. Returns the updated
    job row (dict) with ROOT-relative ``tailored_resume_path`` /
    ``tailored_cover_path``.

    Raises JobNotFound / FileNotFoundError / RuntimeError (see _tailor_job_sync).
    """
    return await anyio.to_thread.run_sync(functools.partial(_tailor_job_sync, url, db_path))
