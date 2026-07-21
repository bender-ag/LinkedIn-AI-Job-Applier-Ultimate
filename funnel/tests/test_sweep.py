"""Smoke test for the sweep orchestrator wiring (no live browser)."""

from __future__ import annotations

from pathlib import Path

import pytest

from funnel import sweep


def test_sweep_wires_stages(tmp_path, monkeypatch):
    cfg = tmp_path / "q.yaml"
    cfg.write_text("queries:\n  - {q: react, l: Remote, fromage: 1}\n")

    class FakeClient:
        def connect(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sweep, "McpClient", lambda: FakeClient())
    monkeypatch.setattr(
        sweep.collect_mod,
        "collect",
        lambda *a, **k: [
            {
                "job_title": "React Engineer",
                "company_name": "Acme",
                "url": "https://www.indeed.com/viewjob?jk=x1",
                "location": "Remote",
                "job_description": "react typescript node",
                "interest_score": 0,
                "skills": [],
            }
        ],
    )

    rc = sweep.main(
        [
            "--config",
            str(cfg),
            "--yaml",
            str(tmp_path / "jobs.yaml"),
            "--db",
            str(tmp_path / "f.db"),
            "--resume",
            str(tmp_path / "none.txt"),
            "--out-dir",
            str(tmp_path),
            "--format",
            "html",
            "--min-score",
            "0",
        ]
    )
    assert rc == 0
    assert (tmp_path / "jobs.yaml").exists()
    assert (tmp_path / "f.db").exists()
    html = list(tmp_path.glob("digest-*.html"))
    assert html and "React Engineer" in html[0].read_text()
