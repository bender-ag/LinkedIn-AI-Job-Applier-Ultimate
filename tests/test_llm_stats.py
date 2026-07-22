"""Tests for llm_stats — LLM cost aggregation."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.dashboard.llm_stats import job_cost_map, llm_totals


def _write_yaml_call_log(path: Path, docs: list[dict]) -> None:
    """Write test LLM call docs to a multi-document YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for doc in docs:
            f.write("---\n")
            yaml.safe_dump(doc, f)


def test_llm_totals_missing_file_returns_zeros():
    """llm_totals on missing file returns all zeros."""
    result = llm_totals(Path("/nonexistent/path.yaml"))

    assert result == {
        "calls": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
        "total_time_seconds": 0.0,
    }


def test_llm_totals_aggregates_single_doc(tmp_path: Path):
    """llm_totals sums a single LLM call."""
    log_path = tmp_path / "calls.yaml"
    _write_yaml_call_log(
        log_path,
        [
            {
                "total_tokens": 1000,
                "total_cost": 0.005,
                "response_time_seconds": 2.5,
                "job_url": "https://example.com/job/1",
            }
        ],
    )

    result = llm_totals(log_path)

    assert result["calls"] == 1
    assert result["total_tokens"] == 1000
    assert result["total_cost"] == 0.005
    assert result["total_time_seconds"] == 2.5


def test_llm_totals_aggregates_multiple_docs(tmp_path: Path):
    """llm_totals sums multiple LLM calls."""
    log_path = tmp_path / "calls.yaml"
    _write_yaml_call_log(
        log_path,
        [
            {
                "total_tokens": 1000,
                "total_cost": 0.005,
                "response_time_seconds": 2.5,
                "job_url": "https://example.com/job/1",
            },
            {
                "total_tokens": 2000,
                "total_cost": 0.010,
                "response_time_seconds": 3.0,
                "job_url": "https://example.com/job/2",
            },
            {
                "total_tokens": 500,
                "total_cost": 0.0025,
                "response_time_seconds": 1.5,
                "job_url": "https://example.com/job/1",
            },
        ],
    )

    result = llm_totals(log_path)

    assert result["calls"] == 3
    assert result["total_tokens"] == 3500
    assert result["total_cost"] == 0.0175
    assert result["total_time_seconds"] == 7.0


def test_llm_totals_rounds_cost_to_6_decimals(tmp_path: Path):
    """llm_totals rounds total_cost to 6 decimal places."""
    log_path = tmp_path / "calls.yaml"
    _write_yaml_call_log(
        log_path,
        [
            {
                "total_tokens": 100,
                "total_cost": 0.001234567,
                "response_time_seconds": 1.0,
            },
            {
                "total_tokens": 100,
                "total_cost": 0.002234567,
                "response_time_seconds": 1.0,
            },
        ],
    )

    result = llm_totals(log_path)

    assert result["total_cost"] == round(0.003469134, 6)
    assert len(str(result["total_cost"]).split(".")[-1]) <= 6


def test_llm_totals_rounds_time_to_3_decimals(tmp_path: Path):
    """llm_totals rounds total_time_seconds to 3 decimal places."""
    log_path = tmp_path / "calls.yaml"
    _write_yaml_call_log(
        log_path,
        [
            {
                "total_tokens": 100,
                "total_cost": 0.001,
                "response_time_seconds": 1.1234567,
            },
            {
                "total_tokens": 100,
                "total_cost": 0.001,
                "response_time_seconds": 2.8765432,
            },
        ],
    )

    result = llm_totals(log_path)

    assert result["total_time_seconds"] == round(3.9999999, 3)


def test_llm_totals_handles_missing_fields(tmp_path: Path):
    """llm_totals treats missing fields as 0."""
    log_path = tmp_path / "calls.yaml"
    _write_yaml_call_log(
        log_path,
        [
            {
                "total_tokens": 1000,
                # Missing total_cost and response_time_seconds
                "job_url": "https://example.com/job/1",
            },
            {
                "total_cost": 0.005,
                # Missing total_tokens and response_time_seconds
                "job_url": "https://example.com/job/2",
            },
        ],
    )

    result = llm_totals(log_path)

    assert result["calls"] == 2
    assert result["total_tokens"] == 1000
    assert result["total_cost"] == 0.005
    assert result["total_time_seconds"] == 0.0


def test_job_cost_map_groups_by_url(tmp_path: Path):
    """job_cost_map aggregates cost per job_url."""
    log_path = tmp_path / "calls.yaml"
    _write_yaml_call_log(
        log_path,
        [
            {
                "total_tokens": 1000,
                "total_cost": 0.005,
                "job_url": "https://example.com/job/1",
            },
            {
                "total_tokens": 2000,
                "total_cost": 0.010,
                "job_url": "https://example.com/job/2",
            },
            {
                "total_tokens": 500,
                "total_cost": 0.0025,
                "job_url": "https://example.com/job/1",
            },
        ],
    )

    result = job_cost_map(log_path)

    assert len(result) == 2
    assert result["https://example.com/job/1"]["calls"] == 2
    assert result["https://example.com/job/1"]["tokens"] == 1500
    assert result["https://example.com/job/1"]["cost"] == 0.0075
    assert result["https://example.com/job/2"]["calls"] == 1
    assert result["https://example.com/job/2"]["tokens"] == 2000
    assert result["https://example.com/job/2"]["cost"] == 0.010


def test_job_cost_map_skips_empty_job_url(tmp_path: Path):
    """job_cost_map skips docs with empty or missing job_url."""
    log_path = tmp_path / "calls.yaml"
    _write_yaml_call_log(
        log_path,
        [
            {
                "total_tokens": 1000,
                "total_cost": 0.005,
                "job_url": "https://example.com/job/1",
            },
            {
                "total_tokens": 100,
                "total_cost": 0.001,
                "job_url": "",  # Empty string
            },
            {
                "total_tokens": 200,
                "total_cost": 0.002,
                # Missing job_url entirely
            },
        ],
    )

    result = job_cost_map(log_path)

    assert len(result) == 1
    assert "https://example.com/job/1" in result
    assert result["https://example.com/job/1"]["calls"] == 1
    assert result["https://example.com/job/1"]["tokens"] == 1000


def test_job_cost_map_rounds_cost_to_6_decimals(tmp_path: Path):
    """job_cost_map rounds cost per URL to 6 decimal places."""
    log_path = tmp_path / "calls.yaml"
    _write_yaml_call_log(
        log_path,
        [
            {
                "total_tokens": 100,
                "total_cost": 0.001234567,
                "job_url": "https://example.com/job/1",
            },
            {
                "total_tokens": 100,
                "total_cost": 0.002234567,
                "job_url": "https://example.com/job/1",
            },
        ],
    )

    result = job_cost_map(log_path)

    cost = result["https://example.com/job/1"]["cost"]
    assert cost == round(0.003469134, 6)


def test_read_call_docs_tolerates_malformed_log(tmp_path: Path):
    """A corrupt/partially-written log must not raise — it must not 500 get_jobs."""
    log_path = tmp_path / "calls.yaml"
    # Invalid YAML (unclosed flow sequence) — safe_load_all raises when iterated.
    log_path.write_text("---\njob_url: x\ntotal_cost: [unclosed\n", encoding="utf-8")

    # Neither reader raises; both degrade to empty aggregates.
    assert llm_totals(log_path) == {
        "calls": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
        "total_time_seconds": 0.0,
    }
    assert job_cost_map(log_path) == {}


def test_read_call_docs_returns_clean_prefix_before_corruption(tmp_path: Path):
    """A valid doc followed by garbage yields the valid prefix, not an error."""
    log_path = tmp_path / "calls.yaml"
    log_path.write_text(
        "---\njob_url: https://example.com/job/1\ntotal_cost: 0.01\ntotal_tokens: 100\n"
        "---\nbad: [unterminated\n",
        encoding="utf-8",
    )

    result = job_cost_map(log_path)
    assert result["https://example.com/job/1"]["cost"] == 0.01
    assert result["https://example.com/job/1"]["calls"] == 1


def test_call_log_parse_is_memoized_until_file_changes(tmp_path: Path, monkeypatch):
    """The log is parsed once and cached; re-parsed only when the file changes."""
    import src.dashboard.llm_stats as ls

    ls._docs_cache.clear()
    log_path = tmp_path / "calls.yaml"
    _write_yaml_call_log(
        log_path,
        [{"job_url": "https://example.com/job/1", "total_cost": 0.01, "total_tokens": 10}],
    )

    calls = {"n": 0}
    real = ls.yaml.safe_load_all

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(ls.yaml, "safe_load_all", counting)

    job_cost_map(log_path)
    job_cost_map(log_path)
    assert calls["n"] == 1  # second call served from cache

    # Appending changes the size → cache invalidated → one more parse.
    _write_yaml_call_log(
        log_path,
        [
            {"job_url": "https://example.com/job/1", "total_cost": 0.01, "total_tokens": 10},
            {"job_url": "https://example.com/job/2", "total_cost": 0.02, "total_tokens": 20},
        ],
    )
    job_cost_map(log_path)
    assert calls["n"] == 2
    ls._docs_cache.clear()
