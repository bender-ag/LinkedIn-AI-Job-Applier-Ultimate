"""Tests for funnel.collect parsers and orchestration."""

from __future__ import annotations

from pathlib import Path

from funnel.collect import (
    _job_identity,
    collect,
    has_no_results,
    parse_job_cards,
    parse_job_detail,
)

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


def test_parse_job_cards_location_skips_salary_and_job_type():
    snap = "\n".join(
        [
            'uid=1_0 heading "full details of Backend Engineer" level="3"',
            'uid=1_1 button "full details of Backend Engineer"',
            'uid=1_2 StaticText "Acme Corp"',
            'uid=1_3 StaticText "$120,000 a year"',
            'uid=1_4 StaticText "Full-time"',
        ]
    )
    cards = parse_job_cards(snap)
    assert len(cards) == 1
    assert cards[0]["company_name"] == "Acme Corp"
    assert cards[0]["location"] is None
    assert cards[0]["salary_range"] == "$120,000 a year"

    snap2 = "\n".join(
        [
            'uid=2_0 heading "full details of Backend Engineer" level="3"',
            'uid=2_1 button "full details of Backend Engineer"',
            'uid=2_2 StaticText "Acme Corp"',
            'uid=2_3 StaticText "Remote in Austin, TX"',
            'uid=2_4 StaticText "$120,000 a year"',
        ]
    )
    cards2 = parse_job_cards(snap2)
    assert len(cards2) == 1
    assert cards2[0]["location"] == "Remote in Austin, TX"
    assert cards2[0]["salary_range"] == "$120,000 a year"


def test_parse_job_cards_skips_response_time_badge():
    # Regression: a variable Indeed badge ("Often replies in 1 day") emitted before
    # the company must not be taken as the company (which shifted every field).
    snap = "\n".join(
        [
            'uid=3_0 heading "full details of Senior Software Engineer" level="3"',
            'uid=3_1 button "full details of Senior Software Engineer"',
            'uid=3_2 StaticText "Often replies in 1 day"',
            'uid=3_3 StaticText "Hammer Media"',
            'uid=3_4 StaticText "Remote in Austin, TX"',
        ]
    )
    cards = parse_job_cards(snap)
    assert len(cards) == 1
    assert cards[0]["company_name"] == "Hammer Media"
    assert cards[0]["location"] == "Remote in Austin, TX"


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
    jobs = collect(queries, client, open_details=True, max_per_query=25, settle=0)

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


def test_collect_no_details_uses_stable_synthetic_identity():
    """With open_details=False (and no real URL), identity is a stable indeed:card: hash."""
    snap = "\n".join(
        [
            'uid=9_0 heading "full details of Synthetic Role" level="3"',
            # intentionally no button uid
            'uid=9_1 StaticText "Synth Co"',
            'uid=9_2 StaticText "Remote"',
        ]
    )
    client = FakeClient(["ok", snap])
    queries = [{"q": "x", "l": "Remote", "fromage": 1, "sort": "date"}]

    parsed = parse_job_cards(snap)
    assert len(parsed) == 1
    assert "_uid" not in parsed[0]

    jobs1 = collect(queries, client, open_details=False, max_per_query=25, settle=0)
    assert len(jobs1) == 1
    assert jobs1[0]["url"].startswith("indeed:card:")
    assert jobs1[0]["job_description"] == ""

    card = {
        "job_title": "Synthetic Role",
        "company_name": "Synth Co",
        "location": "Remote",
    }
    assert _job_identity(card, "") == jobs1[0]["url"]
    assert _job_identity(card, "") == _job_identity(card, "")


def test_job_identity_prefers_real_url():
    card = {"job_title": "T", "company_name": "C", "location": "L"}
    assert _job_identity(card, "https://www.indeed.com/viewjob?jk=abc") == (
        "https://www.indeed.com/viewjob?jk=abc"
    )
