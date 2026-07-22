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


# ── Fakes for the tailor_job orchestration test (no real LLM/Playwright) ──
class _FakeStyleManager:
    def set_styles_directory(self, d):
        pass

    def get_styles(self):
        return {"Default": ("style_default.css", "author")}


class _FakeResumeManager:
    def __init__(self, api_key, style_manager, resume_generator):
        self.style_manager = style_manager
        self.selected_style = None

    def choose_style(self):
        self.selected_style = "Default"

    def choose_default_style(self):
        self.selected_style = "Default"

    async def pdf_base64(self):
        import base64

        return base64.b64encode(b"PDFBYTES").decode()


class _FakeResumeGenerator:
    def __init__(self, gpt, anonymizer):
        self.gpt = gpt
        self.anonymizer = anonymizer


class _FakeGPTAnswerer:
    instances: list = []

    def __init__(self, api_key=None, proxy=None):
        self.set_resume_args = None
        self.set_job_arg = None
        _FakeGPTAnswerer.instances.append(self)

    def set_resume(self, structured, text):
        self.set_resume_args = (structured, text)

    def set_job(self, job):
        self.set_job_arg = job

    def write_cover_letter(self):
        return "Dear team, [ANON] here."


class _FakeAnonymizer:
    instances: list = []

    def __init__(self, structured):
        self.resume_anonymized = structured
        self.deanon_called = False
        _FakeAnonymizer.instances.append(self)

    def anonymize_personal_information(self):
        pass

    def anonymize_text(self, text):
        return text + " [ANON]"

    def deanonymize_text(self, text):
        self.deanon_called = True
        return text.replace("[ANON]", "[REAL]")


def test_tailor_job_sync_orchestration(tmp_path: Path, monkeypatch):
    """_tailor_job_sync wires anonymize→LLM→de-anonymize, writes artifacts, persists paths."""
    import src.dashboard.tailor_service as tsvc

    tracker_service._migrated_paths.clear()
    _FakeGPTAnswerer.instances.clear()
    _FakeAnonymizer.instances.clear()

    # Seed a funnel DB with one job.
    yaml_path = tmp_path / "jobs.yaml"
    db_path = tmp_path / "funnel.db"
    _write_yaml_jobs(
        yaml_path,
        [
            {
                "url": "https://example.com/job/1",
                "job_title": "Senior Engineer",
                "company_name": "Acme",
                "job_description": "Build things",
            }
        ],
    )
    merge_jobs(yaml_path, db_path)

    # Fake ROOT_DIR / tailored dir + résumé source + secrets.
    monkeypatch.setattr(tsvc, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(tsvc, "TAILORED_DIR", tmp_path / "tailored")
    (tmp_path / ".env").write_text("llm_api_key=test-key\n", encoding="utf-8")
    resume_dir = tmp_path / "data" / "resumes"
    resume_dir.mkdir(parents=True)
    (resume_dir / "resume_text.txt").write_text("Real Name, engineer.", encoding="utf-8")
    import yaml as _yaml

    (resume_dir / "structured_resume.yaml").write_text(
        _yaml.safe_dump({"personal_information": {"first_name": "Real"}}),
        encoding="utf-8",
    )

    # Swap heavy collaborators for fakes (patched on their source modules, since
    # tailor_job imports them lazily by name).
    monkeypatch.setattr("src.job_manager.resume_anonymizer.ResumeAnonymizer", _FakeAnonymizer)
    monkeypatch.setattr("src.llm.llm_manager.GPTAnswerer", _FakeGPTAnswerer)
    monkeypatch.setattr("src.resume_builder.resume_generator.ResumeGenerator", _FakeResumeGenerator)
    monkeypatch.setattr("src.resume_builder.resume_manager.ResumeManager", _FakeResumeManager)
    monkeypatch.setattr("src.resume_builder.style_manager.StyleManager", _FakeStyleManager)

    try:
        result = tsvc._tailor_job_sync("https://example.com/job/1", db_path=db_path)

        # Artifacts written under the tailored dir.
        slug_dir = (tmp_path / "tailored").glob("*")
        out_dir = next(slug_dir)
        assert (out_dir / "resume.pdf").read_bytes() == b"PDFBYTES"
        # Cover letter was de-anonymized on the way out.
        assert (out_dir / "cover_letter.md").read_text(encoding="utf-8") == (
            "Dear team, [REAL] here."
        )
        assert _FakeAnonymizer.instances[-1].deanon_called is True

        # Résumé text handed to the LLM was anonymized first.
        structured, text = _FakeGPTAnswerer.instances[-1].set_resume_args
        assert text.endswith("[ANON]")
        assert _FakeGPTAnswerer.instances[-1].set_job_arg["company_name"] == "Acme"

        # Paths persisted (ROOT-relative) onto the row and returned.
        assert result["tailored_resume_path"].startswith("tailored/")
        assert result["tailored_resume_path"].endswith("/resume.pdf")
        assert result["tailored_cover_path"].endswith("/cover_letter.md")
        assert (
            _fetch_job_row("https://example.com/job/1", db_path)["tailored_resume_path"]
            == result["tailored_resume_path"]
        )
    finally:
        tracker_service._migrated_paths.clear()


def test_tailor_job_sync_raises_jobnotfound_for_unknown_url(tmp_path: Path):
    """_tailor_job_sync raises JobNotFound (not KeyError) for a missing job."""
    from src.dashboard.tailor_service import JobNotFound, _tailor_job_sync

    db_path = tmp_path / "funnel.db"
    yaml_path = tmp_path / "jobs.yaml"
    _write_yaml_jobs(
        yaml_path,
        [{"url": "https://example.com/job/1", "job_title": "A", "job_description": "x"}],
    )
    merge_jobs(yaml_path, db_path)

    try:
        _tailor_job_sync("https://example.com/nope", db_path=db_path)
        assert False, "Should have raised JobNotFound"
    except JobNotFound:
        pass
