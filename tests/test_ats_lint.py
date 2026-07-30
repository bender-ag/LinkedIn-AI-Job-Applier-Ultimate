"""Test suite for src/resume_builder/ats_lint.py"""

from unittest.mock import patch

import pytest

from src.resume_builder import ats_lint


def _full_resume_text(
    *,
    summary_header: str = "SUMMARY",
    experience_header: str = "EXPERIENCE",
    education_header: str = "EDUCATION",
    email: str = "jane.doe@example.com",
    phone: str = "555-123-4567",
    linkedin: str = "linkedin.com/in/janedoe",
    github: str = "github.com/janedoe",
    name: str = "Jane Doe",
    extra: str = "",
) -> str:
    """Build a long-enough resume-like text blob for lint checks."""
    body = (
        f"{name}\n"
        f"{email} | {phone}\n"
        f"{linkedin} | {github}\n"
        f"\n"
        f"{summary_header}\n"
        f"Experienced software engineer with a track record of building reliable systems. "
        f"Skilled in Python, distributed systems, and developer tooling across multiple domains. "
        f"Comfortable leading projects end to end from design through deployment and operations. "
        f"\n"
        f"{experience_header}\n"
        f"Senior Engineer, Example Corp, 2020-Present\n"
        f"Built and maintained backend services used by millions of users every day worldwide. "
        f"\n"
        f"{education_header}\n"
        f"B.S. Computer Science, Example University, 2016\n"
        f"{extra}"
    )
    return body


class TestCheckHeaders:
    def test_all_clean_passes(self):
        text = _full_resume_text()
        texts = {"pypdf": text, "pdfminer": text}
        result = ats_lint.check_headers(texts)
        assert result.passed

    def test_split_header_fails_and_names_parser(self):
        clean = _full_resume_text()
        broken = _full_resume_text(summary_header="S U M M A R Y")
        texts = {"pypdf": clean, "pdfminer": broken}
        result = ats_lint.check_headers(texts)
        assert not result.passed
        assert "pdfminer" in result.detail
        assert "SUMMARY" in result.detail

    def test_absent_header_not_required(self):
        text = _full_resume_text(education_header="EDUCATION")
        text = text.replace("EDUCATION", "ACADEMIC BACKGROUND")
        texts = {"pypdf": text, "pdfminer": text}
        result = ats_lint.check_headers(texts)
        assert result.passed


class TestCheckEmail:
    def test_present_in_all_passes(self):
        text = _full_resume_text()
        texts = {"pypdf": text, "pdfminer": text}
        assert ats_lint.check_email(texts).passed

    def test_missing_from_one_parser_fails(self):
        text = _full_resume_text()
        texts = {"pypdf": text, "pdfminer": text.replace("jane.doe@example.com", "")}
        result = ats_lint.check_email(texts)
        assert not result.passed
        assert "pdfminer" in result.detail


class TestCheckPhone:
    def test_present_in_all_passes(self):
        text = _full_resume_text()
        texts = {"pypdf": text, "pdfminer": text}
        assert ats_lint.check_phone(texts).passed

    def test_missing_from_one_parser_fails(self):
        text = _full_resume_text()
        texts = {"pypdf": text, "pdfminer": text.replace("555-123-4567", "")}
        result = ats_lint.check_phone(texts)
        assert not result.passed
        assert "pdfminer" in result.detail


class TestCheckProfileUrls:
    def test_urls_present_passes(self):
        text = _full_resume_text()
        texts = {"pypdf": text, "pdfminer": text}
        assert ats_lint.check_profile_urls(texts).passed

    def test_anchor_without_url_fails(self):
        text = _full_resume_text(linkedin="LinkedIn")
        texts = {"pypdf": text, "pdfminer": text}
        result = ats_lint.check_profile_urls(texts)
        assert not result.passed
        assert "linkedin" in result.detail.lower()

    def test_neither_word_nor_domain_passes(self):
        text = _full_resume_text(linkedin="", github="")
        text = text.replace("linkedin.com/in/janedoe | github.com/janedoe", "")
        texts = {"pypdf": text, "pdfminer": text}
        assert ats_lint.check_profile_urls(texts).passed


class TestCheckNoPua:
    def test_pua_character_fails(self):
        text = _full_resume_text(extra="\uf095")
        texts = {"pypdf": text, "pdfminer": text}
        result = ats_lint.check_no_pua(texts)
        assert not result.passed
        assert "PUA" in result.detail

    def test_word_bullet_pua_allowed(self):
        text = _full_resume_text(extra="\uf0b7 bullet one \uf0a7 bullet two")
        texts = {"pypdf": text, "pdfminer": text}
        assert ats_lint.check_no_pua(texts).passed

    def test_allowed_bullet_does_not_mask_icon_glyph(self):
        text = _full_resume_text(extra="\uf0b7 item \uf095 phone-icon")
        texts = {"pypdf": text, "pdfminer": text}
        result = ats_lint.check_no_pua(texts)
        assert not result.passed
        assert "U+F095" in result.detail


class TestCheckName:
    def test_name_on_first_line_passes(self):
        text = _full_resume_text()
        texts = {"pypdf": text, "pdfminer": text}
        assert ats_lint.check_name(texts, "Jane Doe").passed

    def test_name_absent_fails(self):
        text = _full_resume_text(name="Someone Else")
        texts = {"pypdf": text, "pdfminer": text}
        result = ats_lint.check_name(texts, "Jane Doe")
        assert not result.passed

    def test_name_none_skipped_in_lint_pdf(self):
        with patch.object(ats_lint, "extract_all", return_value={"pypdf": "x" * 250}):
            results = ats_lint.lint_pdf("dummy.pdf", name=None)
        names = [result.name for result in results]
        assert "name" not in names


class TestCheckTextLayer:
    def test_short_text_fails(self):
        texts = {"pypdf": "short", "pdfminer": "also short"}
        result = ats_lint.check_text_layer(texts)
        assert not result.passed


class TestExtractPdftotext:
    def test_returns_none_when_binary_missing(self):
        with patch("src.resume_builder.ats_lint.subprocess.run", side_effect=FileNotFoundError):
            assert ats_lint.extract_pdftotext("resume.pdf") is None


class TestExtractAll:
    def test_raises_when_fewer_than_two_extractors(self, monkeypatch):
        monkeypatch.setattr(ats_lint, "extract_pypdf", lambda _path: "only one")
        monkeypatch.setattr(ats_lint, "extract_pdfminer", lambda _path: "")
        monkeypatch.setattr(ats_lint, "extract_pdftotext", lambda _path: None)

        with pytest.raises(RuntimeError, match="Fewer than 2 extractors"):
            ats_lint.extract_all("resume.pdf")
