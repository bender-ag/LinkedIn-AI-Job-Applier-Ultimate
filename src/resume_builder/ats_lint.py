"""ATS-parseability lint for generated resume PDFs."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

from pdfminer.high_level import extract_text as pdfminer_extract_text
from pypdf import PdfReader

DEFAULT_HEADERS = (
    "SUMMARY",
    "EXPERIENCE",
    "EDUCATION",
    "SKILLS",
    "PROJECTS",
    "CERTIFICATIONS",
    "ACHIEVEMENTS",
)

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_PATTERNS = (
    re.compile(r"\d{3}[-.\s]\d{3}[-.\s]\d{4}"),
    re.compile(r"\(\d{3}\)\s*\d{3}[-.\s]\d{4}"),
)
PUA_PATTERN = re.compile(r"[\uE000-\uF8FF]")
# Word's Symbol/Wingdings list bullets; present in resumes that parse fine in ATSes.
PUA_ALLOWED = frozenset({"\uf0b7", "\uf0a7"})


@dataclass
class CheckResult:
    """Outcome of a single ATS lint check."""

    name: str
    passed: bool
    detail: str


def extract_pypdf(path: str) -> str:
    """Extract text from a PDF using pypdf."""
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_pdfminer(path: str) -> str:
    """Extract text from a PDF using pdfminer.six."""
    return pdfminer_extract_text(path) or ""


def extract_pdftotext(path: str) -> str | None:
    """Extract text from a PDF using poppler's pdftotext. Returns None if binary is missing."""
    try:
        result = subprocess.run(
            ["pdftotext", path, "-"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        return None
    return result.stdout


def extract_all(path: str) -> dict[str, str]:
    """Run all available extractors and return their text output."""
    texts: dict[str, str] = {}

    pypdf_text = extract_pypdf(path)
    if pypdf_text:
        texts["pypdf"] = pypdf_text

    pdfminer_text = extract_pdfminer(path)
    if pdfminer_text:
        texts["pdfminer"] = pdfminer_text

    pdftotext_text = extract_pdftotext(path)
    if pdftotext_text:
        texts["pdftotext"] = pdftotext_text

    if len(texts) < 2:
        raise RuntimeError(
            f"Fewer than 2 extractors produced text (got {len(texts)}: {', '.join(texts) or 'none'})"
        )

    return texts


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _header_expected(texts: dict[str, str], header: str) -> bool:
    collapsed_header = header.upper()
    for text in texts.values():
        if collapsed_header in _collapse_whitespace(text).upper():
            return True
    return False


def _find_split_header_form(text: str, header: str) -> str | None:
    pattern = r"\s*".join(re.escape(char) for char in header)
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(0)
    return None


def check_text_layer(texts: dict[str, str], min_chars: int = 200) -> CheckResult:
    """Verify every parser extracted at least min_chars characters."""
    short_parsers = [
        f"{parser}: {len(text)} chars" for parser, text in texts.items() if len(text) < min_chars
    ]
    if short_parsers:
        return CheckResult(
            name="text_layer",
            passed=False,
            detail=f"Too little text from {', '.join(short_parsers)} (min {min_chars})",
        )
    return CheckResult(name="text_layer", passed=True, detail="")


def check_headers(
    texts: dict[str, str],
    headers: tuple[str, ...] = DEFAULT_HEADERS,
) -> CheckResult:
    """Verify expected section headers appear unbroken in every parser's text."""
    expected = [header for header in headers if _header_expected(texts, header)]
    failures: list[str] = []

    for header in expected:
        for parser, text in texts.items():
            if header.lower() not in text.lower():
                split_form = _find_split_header_form(text, header)
                if split_form:
                    failures.append(f"{parser}: {header} extracted as '{split_form}'")
                else:
                    failures.append(f"{parser}: {header} missing")

    if failures:
        return CheckResult(name="headers", passed=False, detail="; ".join(failures))
    return CheckResult(name="headers", passed=True, detail="")


def check_email(texts: dict[str, str]) -> CheckResult:
    """Verify an email address appears in every parser's text."""
    failures = [parser for parser, text in texts.items() if not EMAIL_PATTERN.search(text)]
    if failures:
        return CheckResult(
            name="email",
            passed=False,
            detail=f"No email found in {', '.join(failures)}",
        )
    return CheckResult(name="email", passed=True, detail="")


def check_phone(texts: dict[str, str]) -> CheckResult:
    """Verify a US phone number appears in every parser's text."""
    failures = []
    for parser, text in texts.items():
        if not any(pattern.search(text) for pattern in PHONE_PATTERNS):
            failures.append(parser)
    if failures:
        return CheckResult(
            name="phone",
            passed=False,
            detail=f"No phone number found in {', '.join(failures)}",
        )
    return CheckResult(name="phone", passed=True, detail="")


def check_profile_urls(texts: dict[str, str]) -> CheckResult:
    """Verify LinkedIn/GitHub anchor text is accompanied by URL text in the same parser."""
    checks = (
        ("linkedin", "linkedin.com/"),
        ("github", "github.com/"),
    )
    failures: list[str] = []

    for word, domain in checks:
        word_expected = any(word in text.lower() for text in texts.values())
        if not word_expected:
            continue
        for parser, text in texts.items():
            if word in text.lower() and domain not in text.lower():
                failures.append(f"{parser}: '{word}' without {domain} in text layer")

    if failures:
        return CheckResult(name="profile_urls", passed=False, detail="; ".join(failures))
    return CheckResult(name="profile_urls", passed=True, detail="")


def check_no_pua(texts: dict[str, str]) -> CheckResult:
    """Verify no Private Use Area codepoints leaked into extracted text."""
    failures = []
    for parser, text in texts.items():
        for match in PUA_PATTERN.finditer(text):
            if match.group() not in PUA_ALLOWED:
                failures.append(f"{parser}: PUA codepoint U+{ord(match.group()):04X}")
                break
    if failures:
        return CheckResult(name="no_pua", passed=False, detail="; ".join(failures))
    return CheckResult(name="no_pua", passed=True, detail="")


def check_name(texts: dict[str, str], name: str) -> CheckResult:
    """Verify the candidate name appears in the first 3 non-empty lines of every parser."""
    failures = []
    for parser, text in texts.items():
        non_empty_lines = [line.strip() for line in text.splitlines() if line.strip()]
        top_lines = non_empty_lines[:3]
        if not any(name.lower() in line.lower() for line in top_lines):
            failures.append(parser)
    if failures:
        return CheckResult(
            name="name",
            passed=False,
            detail=f"Name '{name}' not in first 3 lines from {', '.join(failures)}",
        )
    return CheckResult(name="name", passed=True, detail="")


def lint_pdf(path: str, name: str | None = None) -> list[CheckResult]:
    """Extract text from a PDF and run all applicable ATS lint checks."""
    texts = extract_all(path)
    results = [
        check_text_layer(texts),
        check_headers(texts),
        check_email(texts),
        check_phone(texts),
        check_profile_urls(texts),
        check_no_pua(texts),
    ]
    if name is not None:
        results.append(check_name(texts, name))
    return results


def _print_results(results: list[CheckResult]) -> int:
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        if result.passed:
            print(f"{status} {result.name}")
        else:
            print(f"{status} {result.name}: {result.detail}")

    passed = sum(1 for result in results if result.passed)
    total = len(results)
    all_passed = passed == total
    print(f"Summary: {passed}/{total} checks passed")
    return 0 if all_passed else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ATS lint."""
    parser = argparse.ArgumentParser(description="ATS-parseability lint for resume PDFs")
    parser.add_argument("pdf", help="Path to the resume PDF")
    parser.add_argument("--name", default=None, help="Candidate full name to verify in header")
    args = parser.parse_args(argv)

    results = lint_pdf(args.pdf, name=args.name)
    return _print_results(results)


if __name__ == "__main__":
    sys.exit(main())
