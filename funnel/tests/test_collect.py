"""Tests for funnel.collect parsers and orchestration."""

from __future__ import annotations

from pathlib import Path

from funnel.collect import collect, has_no_results, parse_job_cards, parse_job_detail

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_job_cards_search_results():
    cards = parse_job_cards(_load("search_results.txt"))
    assert len(cards) == 3

    first = cards[0]
    assert first["job_title"] == "Senior / Staff Full Stack Software Engineer – Remote"
    assert first["company_name"] == "Glint Tech Solutions LLC"
    assert first["location"] == "Remote in Sunnyvale, CA"
    assert first["salary_range"] is None
    assert first["_uid"] == "3_53"

    dreamworks = cards[2]
    assert dreamworks["job_title"] == "DreamWorks Technology - Principal Engineer, AI"
    assert dreamworks["company_name"] == "DreamWorks Animation"
    assert dreamworks["salary_range"] == "$190,000 - $230,000 a year"
    assert dreamworks["_uid"] == "3_74"


def test_has_no_results():
    assert has_no_results(_load("no_results.txt")) is True
    assert has_no_results(_load("search_results.txt")) is False


def test_parse_job_detail():
    detail = parse_job_detail(_load("job_detail.txt"))
    desc = detail["job_description"]
    assert "TypeScript" in desc
    assert "React" in desc
    assert "Node.js" in desc
    assert desc.startswith(
        "We are looking for a highly experienced Senior/Staff Full Stack Software Engineer"
    )
    assert "Report job" not in desc


class FakeClient:
    """Sequenced call_tool stub — no network."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        if not self._responses:
            raise AssertionError(f"unexpected call_tool({name!r}, {arguments!r})")
        return self._responses.pop(0)


def _detail_with_jk(jk: str, body: str | None = None) -> str:
    base = body if body is not None else _load("job_detail.txt")
    root = (
        f'uid=0_0 RootWebArea "Indeed" '
        f'url="https://www.indeed.com/viewjob?jk={jk}&from=vjs&vjk={jk}"'
    )
    return root + "\n" + base


def test_collect_yields_jobs_and_skips_no_results():
    search = _load("search_results.txt")
    no_results = _load("no_results.txt")
    d1 = _detail_with_jk("aaa111")
    d2 = _detail_with_jk("bbb222")
    d3 = _detail_with_jk("ccc333")

    # query1: new_page, snapshot(search), click+snapshot x3
    # query2: navigate, snapshot(no_results)
    client = FakeClient(
        [
            "ok",  # new_page
            search,  # take_snapshot
            "ok",  # click 1
            d1,
            "ok",  # click 2
            d2,
            "ok",  # click 3
            d3,
            "ok",  # navigate_page
            no_results,
        ]
    )

    queries = [
        {"q": "software engineer react", "l": "Remote", "fromage": 1, "sort": "date"},
        {"q": "senior frontend engineer", "l": "Austin, TX", "fromage": 3, "sort": "date"},
    ]
    jobs = collect(queries, client, open_details=True, max_per_query=25)

    assert len(jobs) == 3
    assert jobs[0]["job_title"] == "Senior / Staff Full Stack Software Engineer – Remote"
    assert jobs[0]["company_name"] == "Glint Tech Solutions LLC"
    assert jobs[0]["url"] == "https://www.indeed.com/viewjob?jk=aaa111"
    assert "TypeScript" in jobs[0]["job_description"]
    assert jobs[0]["interest_score"] == 0
    assert jobs[0]["skills"] == []
    assert jobs[0]["posted_date"] is None

    assert jobs[2]["salary_range"] == "$190,000 - $230,000 a year"
    assert jobs[2]["url"] == "https://www.indeed.com/viewjob?jk=ccc333"

    tool_names = [c[0] for c in client.calls]
    assert tool_names[0] == "new_page"
    assert "navigate_page" in tool_names
    assert client.calls[2] == ("click", {"uid": "3_53"})
    assert not client._responses
