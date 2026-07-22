"""LLM cost readers over ``logs/llm_api_calls.yaml``.

The funnel itself makes no LLM calls (collection is scraping + the deterministic
``funnel.score_match``), so every entry in ``llm_api_calls.yaml`` comes from the
tracker's Tailor action or the standalone apply bot. Each YAML document is one
``LLMCall`` (see ``src/pydantic_models/log_models.py``) and carries a ``job_url``
stamped by ``GPTAnswerer.set_job``, which lets us attribute cost per job.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from config.constants import LOG_DIR
from src.dashboard.runtime import ROOT_DIR

LLM_CALLS_FILE = ROOT_DIR / LOG_DIR / "llm_api_calls.yaml"


def _iter_call_docs(calls_log: Path):
    """Yield each dict document from the multi-document YAML call log."""
    if not calls_log.exists():
        return
    with calls_log.open(encoding="utf-8") as f:
        for doc in yaml.safe_load_all(f):
            if isinstance(doc, dict):
                yield doc


def llm_totals(calls_log: Path | None = None) -> dict[str, Any]:
    """
    Aggregate the entire call log.

    Returns ``{"calls", "total_tokens", "total_cost", "total_time_seconds"}``.
    Mirrors the old dashboard's ``_read_llm_totals`` so the tracker can reuse the
    LLM Calls / LLM Cost tile styling.
    """
    if calls_log is None:
        calls_log = LLM_CALLS_FILE

    calls, total_tokens, total_cost, total_time = 0, 0, 0.0, 0.0
    for doc in _iter_call_docs(calls_log):
        calls += 1
        total_tokens += doc.get("total_tokens") or 0
        total_cost += doc.get("total_cost") or 0.0
        total_time += doc.get("response_time_seconds") or 0.0

    return {
        "calls": calls,
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 6),
        "total_time_seconds": round(total_time, 3),
    }


def job_cost_map(calls_log: Path | None = None) -> dict[str, dict[str, Any]]:
    """
    Aggregate call cost per ``job_url``.

    Returns ``{job_url: {"calls": int, "cost": float, "tokens": int}}``.
    Documents with an empty/missing ``job_url`` are skipped (they can't be
    attributed to a tracked job).
    """
    if calls_log is None:
        calls_log = LLM_CALLS_FILE

    by_job: dict[str, dict[str, Any]] = {}
    for doc in _iter_call_docs(calls_log):
        job_url = doc.get("job_url") or ""
        if not job_url:
            continue
        entry = by_job.setdefault(job_url, {"calls": 0, "cost": 0.0, "tokens": 0})
        entry["calls"] += 1
        entry["cost"] += doc.get("total_cost") or 0.0
        entry["tokens"] += doc.get("total_tokens") or 0

    for entry in by_job.values():
        entry["cost"] = round(entry["cost"], 6)

    return by_job
