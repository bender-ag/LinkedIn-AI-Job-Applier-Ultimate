"""Indeed collector: drive browser MCP, parse a11y snapshots, emit interesting_jobs."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path("funnel/queries.yaml")
DEFAULT_OUT = Path("data/output/interesting_jobs.yaml")

NOISE_LABELS = {
    "Easily apply",
    "New",
    "Save job Toggle",
    "Not interested",
    "matches your preference",
    "View similar jobs with this employer",
}

CARD_HEADING_RE = re.compile(r'heading "full details of (.+)" level="3"')
CARD_BUTTON_RE = re.compile(r'uid=(\S+)\s+button "full details of')
STATIC_TEXT_RE = re.compile(r'StaticText "([^"]*)"')
SALARY_RE = re.compile(r"\$[\d,]+.*(a year|a day|an hour|hour)")
VJK_RE = re.compile(r"[?&]vjk=([^&\"\s]+)")
JK_RE = re.compile(r"[?&]jk=([^&\"\s]+)")
ROOT_URL_RE = re.compile(r'RootWebArea[^"]*"[^"]*"\s+url="([^"]+)"')


class ToolClient(Protocol):
    def call_tool(self, name: str, arguments: dict[str, Any]) -> str: ...


def has_no_results(snapshot: str) -> bool:
    """True iff the no-results message line is present in the snapshot."""
    return "We didn't find any results for this search." in snapshot


def parse_job_cards(snapshot: str) -> list[dict]:
    """Parse job cards from an Indeed search-results a11y snapshot."""
    lines = snapshot.splitlines()
    heading_indexes: list[int] = []
    for i, line in enumerate(lines):
        if CARD_HEADING_RE.search(line):
            heading_indexes.append(i)

    cards: list[dict] = []
    for idx, start in enumerate(heading_indexes):
        end = heading_indexes[idx + 1] if idx + 1 < len(heading_indexes) else len(lines)
        block = lines[start:end]
        heading_m = CARD_HEADING_RE.search(block[0])
        if not heading_m:
            continue
        title = heading_m.group(1)

        uid: str | None = None
        for line in block[1:]:
            button_m = CARD_BUTTON_RE.search(line)
            if button_m:
                uid = button_m.group(1)
                break

        static_texts: list[str] = []
        for line in block:
            for m in STATIC_TEXT_RE.finditer(line):
                text = m.group(1)
                if text in NOISE_LABELS:
                    continue
                static_texts.append(text)

        company_name = static_texts[0] if static_texts else None
        location = static_texts[1] if len(static_texts) > 1 else None

        salary_range = None
        for text in static_texts:
            if SALARY_RE.search(text):
                salary_range = text
                break

        card: dict[str, Any] = {
            "job_title": title,
            "company_name": company_name,
            "location": location,
            "salary_range": salary_range,
        }
        if uid is not None:
            card["_uid"] = uid
        cards.append(card)
    return cards


def parse_job_detail(snapshot: str) -> dict:
    """Extract job_description from a job-detail a11y snapshot."""
    lines = snapshot.splitlines()
    collecting = False
    parts: list[str] = []
    for line in lines:
        if 'heading "Full job description"' in line:
            collecting = True
            continue
        if collecting and 'button "Report job"' in line:
            break
        if not collecting:
            continue
        for m in STATIC_TEXT_RE.finditer(line):
            parts.append(m.group(1))
    description = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return {"job_description": description}


def _extract_jk(snapshot: str) -> str | None:
    """Pull Indeed job key from a RootWebArea url (vjk= or jk=)."""
    url_m = ROOT_URL_RE.search(snapshot)
    if url_m:
        url = url_m.group(1)
    else:
        # Fallback: search whole snapshot for vjk=/jk=
        url = snapshot
    vjk = VJK_RE.search(url)
    if vjk:
        return vjk.group(1)
    jk = JK_RE.search(url)
    if jk:
        return jk.group(1)
    return None


def _build_search_url(query: dict[str, Any]) -> str:
    params: dict[str, Any] = {
        "q": query["q"],
        "l": query["l"],
        "fromage": query["fromage"],
    }
    if query.get("sort"):
        params["sort"] = query["sort"]
    return "https://www.indeed.com/jobs?" + urlencode(params)


def _to_interesting_job(card: dict[str, Any], *, url: str, job_description: str) -> dict[str, Any]:
    return {
        "job_title": card["job_title"],
        "company_name": card.get("company_name"),
        "url": url,
        "location": card.get("location"),
        "salary_range": card.get("salary_range"),
        "posted_date": None,
        "job_description": job_description,
        "company_description": None,
        "interest_score": 0,
        "interest_reason": "",
        "skills": [],
    }


def _fetch_detail(client: ToolClient, uid: str | None, settle: float) -> tuple[str, str]:
    """Click a card and read its detail pane; re-snapshot once if description empty."""
    if uid:
        client.call_tool("click", {"uid": uid})
    time.sleep(settle)
    detail_snap = client.call_tool("take_snapshot", {})
    description = parse_job_detail(detail_snap).get("job_description") or ""
    if not description:
        # Detail pane may not have rendered yet; give it one more beat.
        time.sleep(settle)
        detail_snap = client.call_tool("take_snapshot", {})
        description = parse_job_detail(detail_snap).get("job_description") or ""
    jk = _extract_jk(detail_snap)
    job_url = f"https://www.indeed.com/viewjob?jk={jk}" if jk else ""
    return job_url, description


def collect(
    queries: list[dict[str, Any]],
    client: ToolClient,
    *,
    open_details: bool = True,
    max_per_query: int = 25,
    settle: float = 1.5,
) -> list[dict[str, Any]]:
    """Run queries via MCP client; return interesting_jobs-shaped records."""
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for qi, query in enumerate(queries):
        q = query.get("q", "")
        l = query.get("l", "")
        try:
            url = _build_search_url(query)
            if qi == 0:
                client.call_tool("new_page", {"url": url})
            else:
                client.call_tool("navigate_page", {"url": url})
            time.sleep(settle)  # let Indeed render the (lazy) results list
            snapshot = client.call_tool("take_snapshot", {})

            if has_no_results(snapshot):
                logger.info("0 results for %s in %s", q, l)
                print(f"0 results for {q} in {l}")
                continue

            cards = parse_job_cards(snapshot)
            for card in cards[:max_per_query]:
                # A single bad card (stale uid, slow pane) must not abort the query.
                try:
                    job_url = ""
                    job_description = ""
                    if open_details:
                        job_url, job_description = _fetch_detail(client, card.get("_uid"), settle)

                    if job_url and job_url in seen_urls:
                        continue
                    if job_url:
                        seen_urls.add(job_url)

                    results.append(
                        _to_interesting_job(card, url=job_url, job_description=job_description)
                    )
                except Exception as exc:
                    logger.warning("card failed (%s): %s", card.get("job_title"), exc)
                    continue
        except Exception as exc:
            logger.exception("query failed for %s in %s: %s", q, l, exc)
            print(f"query failed for {q} in {l}: {exc}")
            continue

    return results


def _compact(job: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in job.items() if v is not None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect Indeed jobs via browser MCP")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-details", action="store_true", help="Skip per-job description fetch")
    parser.add_argument("--max", type=int, default=25, dest="max_per_query")
    parser.add_argument(
        "--settle",
        type=float,
        default=1.5,
        help="Seconds to wait for the page/detail pane to render (default 1.5)",
    )
    args = parser.parse_args(argv)

    with args.config.open(encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    queries = config.get("queries") or []

    from funnel.mcp_client import McpClient

    client = McpClient()
    client.connect()
    try:
        jobs = collect(
            queries,
            client,
            open_details=not args.no_details,
            max_per_query=args.max_per_query,
            settle=args.settle,
        )
    finally:
        client.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            [_compact(j) for j in jobs],
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )

    print(f"collected {len(jobs)} jobs across {len(queries)} queries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
