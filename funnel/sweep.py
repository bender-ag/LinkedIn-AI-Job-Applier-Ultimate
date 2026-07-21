"""One-command sweep: collect Indeed jobs -> merge into SQLite -> write digest.

Runs the full funnel pipeline in sequence. Requires the browser bridge up and
NO other MCP client (e.g. the Claude Code `browser` server) holding it.

    uv run python -m funnel.sweep                 # HTML digest to /artifacts
    uv run python -m funnel.sweep --format md --max 10

Exit code is non-zero only if collection itself fails to run; an empty sweep
(no fresh matches) is a success.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from funnel import collect as collect_mod
from funnel import digest as digest_mod
from funnel import merge as merge_mod
from funnel.mcp_client import McpClient

DEFAULT_CONFIG = Path("funnel/queries.yaml")
DEFAULT_YAML = Path("data/output/interesting_jobs.yaml")
DEFAULT_DB = Path("data/funnel.db")
DEFAULT_RESUME = Path("data/resumes/resume_text.txt")
DEFAULT_OUT_DIR = Path("/artifacts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the full Indeed funnel sweep")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--yaml", type=Path, default=DEFAULT_YAML)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--resume", type=Path, default=DEFAULT_RESUME)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--min-score", type=int, default=0)
    parser.add_argument("--max", type=int, default=25, dest="max_per_query")
    parser.add_argument("--settle", type=float, default=1.5)
    parser.add_argument("--format", choices=["md", "html"], default="html", dest="fmt")
    args = parser.parse_args(argv)

    import yaml

    with args.config.open(encoding="utf-8") as f:
        queries = (yaml.safe_load(f) or {}).get("queries") or []

    # 1. Collect (drives the browser bridge)
    client = McpClient()
    client.connect()
    try:
        jobs = collect_mod.collect(
            queries,
            client,
            open_details=True,
            max_per_query=args.max_per_query,
            settle=args.settle,
        )
    finally:
        client.close()

    args.yaml.parent.mkdir(parents=True, exist_ok=True)
    with args.yaml.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            [collect_mod._compact(j) for j in jobs],
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
    print(f"[1/3] collected {len(jobs)} jobs across {len(queries)} queries")

    # 2. Merge into SQLite (lifecycle tracking)
    new, updated = merge_mod.merge_jobs(args.yaml, args.db)
    print(f"[2/3] merged: {new} new, {updated} updated")

    # 3. Digest the fresh, above-threshold jobs
    count = digest_mod.digest_jobs(
        args.db,
        min_score=args.min_score,
        resume_path=args.resume,
        out_dir=args.out_dir,
        fmt=args.fmt,
    )
    print(f"[3/3] digested {count} new jobs -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
