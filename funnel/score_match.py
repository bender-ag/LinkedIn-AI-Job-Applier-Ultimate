"""Resume↔JD keyword match scorer — faithful port of the TypeScript scoreMatch."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

MatchBand = Literal["strong", "partial", "weak"]

MIN_TOKENS = 8
THIN_JD_TOKENS = 60
TOP_JD_TERMS = 40
STRONG = 75
PARTIAL = 45
PHRASE_BOOST = 1.2
ACRONYM_BOOST = 1.0

STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "his",
        "i",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "she",
        "so",
        "such",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "would",
        "you",
        "your",
        "about",
        "above",
        "after",
        "again",
        "all",
        "am",
        "any",
        "because",
        "before",
        "below",
        "between",
        "both",
        "can",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "further",
        "here",
        "how",
        "if",
        "more",
        "most",
        "no",
        "nor",
        "not",
        "now",
        "off",
        "once",
        "only",
        "other",
        "out",
        "over",
        "own",
        "same",
        "should",
        "some",
        "than",
        "too",
        "under",
        "until",
        "up",
        "very",
        "while",
        "why",
    }
)

JD_FILLER: frozenset[str] = frozenset(
    {
        "experience",
        "experienced",
        "ability",
        "able",
        "year",
        "years",
        "preferred",
        "required",
        "require",
        "plus",
        "including",
        "include",
        "related",
        "strong",
        "excellent",
        "etc",
        "eg",
        "ie",
        "work",
        "working",
        "role",
        "roles",
        "team",
        "teams",
        "environment",
        "responsibilities",
        "responsibility",
        "skill",
        "skills",
        "knowledge",
        "understanding",
        "proficiency",
        "demonstrated",
        "proven",
        "must",
        "will",
        "you",
        "your",
        "our",
        "we",
        "us",
        "the",
        "and",
        "or",
        "with",
        "within",
        "across",
        "other",
        "others",
        "new",
        "using",
        "use",
        "used",
        "help",
        "various",
    }
)

EXCLUDED: frozenset[str] = STOPWORDS | JD_FILLER

_ES_SUFFIX = re.compile(r"(s|x|z|ch|sh)es$")
_DOUBLE_CONSONANT = re.compile(r"([bdfglmnprt])\1$")
_PURE_NUMBER = re.compile(r"^\d+$")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]+")
_WHITESPACE = re.compile(r"\s+")
_SPLIT_RAW = re.compile(r"[^A-Za-z0-9]+")
_ACRONYM = re.compile(r"^[A-Z0-9]+$")
_HAS_LETTER = re.compile(r"[A-Z]")


@dataclass
class MatchResult:
    score: int
    band: MatchBand
    matched: list[str]
    missing: list[str]
    ok: bool
    thin_jd: bool


def stem(token: str) -> str:
    """Lightweight aggressive suffix stripper — port of stem.ts."""
    w = token.lower()
    if len(w) < 4:
        return w

    # plurals
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("sses"):
        w = w[:-2]
    elif _ES_SUFFIX.search(w):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]

    # verb endings
    if w.endswith("ing") and len(w) > 5:
        w = w[:-3]
    elif w.endswith("ed") and len(w) > 4:
        w = w[:-2]

    # nominalizers
    if w.endswith("ment") and len(w) > 6:
        w = w[:-4]
    if w.endswith("ity") and len(w) > 5:
        w = w[:-3]
    if w.endswith("ly") and len(w) > 4:
        w = w[:-2]

    # trailing 'e' (manage->manag so it unifies with managed->manag)
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]

    # collapse doubled trailing consonant (plann->plan, runn->run)
    if _DOUBLE_CONSONANT.search(w):
        w = w[:-1]

    return w


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", _NON_ALNUM.sub(" ", text.lower())).strip()


def _tokenize(text: str) -> list[str]:
    n = _normalize(text)
    return n.split(" ") if n else []


def _is_pure_number(t: str) -> bool:
    return bool(_PURE_NUMBER.match(t))


def _is_excluded(t: str) -> bool:
    return t in EXCLUDED or _is_pure_number(t)


def _acronym_set(text: str) -> set[str]:
    out: set[str] = set()
    for raw in _SPLIT_RAW.split(text):
        if 2 <= len(raw) <= 6 and _ACRONYM.match(raw) and _HAS_LETTER.search(raw):
            out.add(raw.lower())
    return out


def _resume_token_set(text: str) -> set[str]:
    result: set[str] = set()
    acr = _acronym_set(text)
    for a in acr:
        result.add(a)
    for tok in _tokenize(text):
        if _is_excluded(tok):
            continue
        result.add(tok if tok in acr else stem(tok))
    return result


@dataclass
class _UniTerm:
    kind: Literal["uni"]
    token: str
    display: str
    weight: float


@dataclass
class _BiTerm:
    kind: Literal["bi"]
    a: str
    b: str
    display: str
    weight: float


ScoredTerm = _UniTerm | _BiTerm


def _build_jd_terms(text: str) -> list[ScoredTerm]:
    tokens = _tokenize(text)
    acr = _acronym_set(text)

    uni: dict[str, dict] = {}
    for tok in tokens:
        if _is_excluded(tok):
            continue
        is_acr = tok in acr
        token = tok if is_acr else stem(tok)
        cur = uni.get(token)
        if cur:
            cur["freq"] += 1
        else:
            uni[token] = {"display": tok, "freq": 1, "acronym": is_acr}

    bi: dict[str, dict] = {}
    for i in range(len(tokens) - 1):
        ta = tokens[i]
        tb = tokens[i + 1]
        if _is_excluded(ta) or _is_excluded(tb):
            continue
        a = stem(ta)
        b = stem(tb)
        key = a + " " + b
        cur = bi.get(key)
        if cur:
            cur["freq"] += 1
        else:
            bi[key] = {"a": a, "b": b, "display": ta + " " + tb, "freq": 1}

    terms: list[ScoredTerm] = []
    for token, v in uni.items():
        terms.append(
            _UniTerm(
                kind="uni",
                token=token,
                display=v["display"],
                weight=v["freq"] * (ACRONYM_BOOST if v["acronym"] else 1),
            )
        )
    for v in bi.values():
        terms.append(
            _BiTerm(
                kind="bi",
                a=v["a"],
                b=v["b"],
                display=v["display"],
                weight=v["freq"] * PHRASE_BOOST,
            )
        )
    terms.sort(key=lambda t: t.weight, reverse=True)
    return terms[:TOP_JD_TERMS]


def _is_matched(term: ScoredTerm, resume_set: set[str]) -> bool:
    if term.kind == "uni":
        return term.token in resume_set
    return term.a in resume_set and term.b in resume_set


def score_match(resume_text: str, jd_text: str) -> MatchResult:
    """Score resume↔JD keyword overlap. Port of TypeScript scoreMatch()."""
    thin_jd = len([t for t in _tokenize(jd_text) if not _is_excluded(t)]) < THIN_JD_TOKENS
    empty = MatchResult(score=0, band="weak", matched=[], missing=[], ok=False, thin_jd=thin_jd)
    if len(_tokenize(resume_text)) < MIN_TOKENS or len(_tokenize(jd_text)) < MIN_TOKENS:
        return empty

    jd_terms = _build_jd_terms(jd_text)
    resume_set = _resume_token_set(resume_text)

    total = 0.0
    hit = 0.0
    matched: list[dict] = []
    missing: list[dict] = []
    for t in jd_terms:
        total += t.weight
        if _is_matched(t, resume_set):
            hit += t.weight
            matched.append({"display": t.display, "weight": t.weight})
        else:
            missing.append({"display": t.display, "weight": t.weight})

    # Math.round semantics (half away from zero), not Python banker's round
    raw = (100 * hit) / total if total > 0 else 0.0
    score = int(raw + 0.5) if raw >= 0 else int(raw - 0.5)
    band: MatchBand = "strong" if score >= STRONG else "partial" if score >= PARTIAL else "weak"

    return MatchResult(
        score=score,
        band=band,
        matched=[m["display"] for m in sorted(matched, key=lambda x: x["weight"], reverse=True)],
        missing=[m["display"] for m in sorted(missing, key=lambda x: x["weight"], reverse=True)],
        ok=True,
        thin_jd=thin_jd,
    )
