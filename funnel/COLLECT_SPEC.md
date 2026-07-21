# Collector MVP — implementation spec

Build an automatable Indeed collector that drives the user's real Chrome via the
browser MCP bridge, extracts jobs, and emits the `interesting_jobs.yaml` the
existing pipeline (`funnel/merge.py` → `funnel/digest.py`) already consumes.
Stdlib-only + PyYAML (already a dep). Python 3.12, black/isort clean, type hints.
Everything lives under `funnel/`. Add pytest tests in `funnel/tests/`.

## Files to create

### 1. `funnel/mcp_client.py` — minimal streamable-HTTP MCP client

A small client for the Chrome DevTools MCP bridge. Proven working handshake:
POST JSON-RPC to `http://host.docker.internal:8814/mcp` with headers
`Content-Type: application/json`, `Accept: application/json, text/event-stream`,
`X-API-Key: <env RESEARCH_BROWSER_KEY>`. Responses come back as SSE
(`event: message\n data: {json}\n`). Parse the `data:` line(s) as JSON-RPC.

Class `McpClient`:
- `__init__(self, url=None, api_key=None)`: url defaults to env `BROWSER_MCP_URL`
  or `http://host.docker.internal:8814/mcp`; api_key defaults to env
  `RESEARCH_BROWSER_KEY`.
- `connect(self)`: POST `initialize` (protocolVersion "2024-11-05", empty
  capabilities, clientInfo name "funnel"), capture the `Mcp-Session-Id` response
  header, then POST the `notifications/initialized` notification (no id) with the
  session header. Store the session id; send it on every subsequent request.
- `call_tool(self, name, arguments) -> str`: POST `tools/call` with
  `{name, arguments}`; parse the SSE response; return the concatenated text of
  the result's `content[*].text` items. Raise `McpError` on JSON-RPC error.
- `_post(self, payload) -> dict|None`: shared urllib POST + SSE parse. Use
  `urllib.request`; timeout 30s; on HTTP error raise `McpError` with the body.
- Provide a small SSE parser `parse_sse(text) -> list[dict]` (module-level, unit
  tested) that returns the JSON objects from all `data:` lines.

Do NOT hardcode the key. Keep the transport testable: `_post` should be the only
network method, so tests can monkeypatch it.

### 2. `funnel/collect.py` — orchestration + snapshot parsing

Two pure, unit-tested parsers (operate on the a11y snapshot TEXT that
`take_snapshot` returns — see `funnel/fixtures/*.txt` for real samples):

- `has_no_results(snapshot: str) -> bool`: True iff the line
  `We didn't find any results for this search.` is present. When True the caller
  records ZERO jobs for that query (never ingest the padding cards).
- `parse_job_cards(snapshot: str) -> list[dict]`: find each line
  `heading "full details of {TITLE}" level="3"` and, from the lines up to the
  next such heading (or the results/footer), collect the following in order:
  - company_name = first `StaticText` after the card heading that is NOT one of
    the noise labels {"Easily apply","New","Save job Toggle","Not interested",
    "matches your preference","View similar jobs with this employer"} — in the
    fixtures the company is the first non-noise StaticText.
  - location = the next non-noise StaticText after company (e.g. "Remote in
    Sunnyvale, CA", "Remote", "Austin, TX 78705").
  - salary_range = a StaticText matching `\$[\d,]+.*(a year|a day|an hour|hour)`
    if present near the card, else None.
  Return dicts: {job_title, company_name, location, salary_range}. Skip the
  no-results padding by having the CALLER check `has_no_results` first.
- `parse_job_detail(snapshot: str) -> dict`: from a job-detail snapshot, return
  {job_description}: concatenate every `StaticText` that appears AFTER the
  `heading "Full job description"` line and BEFORE the `button "Report job"`
  line, joined with spaces (collapse whitespace). Ignore inline `LineBreak`.

Orchestration `collect(queries, client, *, open_details=True, max_per_query=25)`:
- `queries` is a list of dicts {q, l, fromage, sort?} (see config below).
- For each query: build the Indeed URL
  `https://www.indeed.com/jobs?q=<q>&l=<l>&fromage=<n>[&sort=date]`
  (urlencode q and l). `client.call_tool("new_page", {"url": url})` for the first,
  `navigate_page` for the rest; then `take_snapshot`.
- If `has_no_results` → log "0 results for {q} in {l}", continue.
- Else `parse_job_cards`. For each card (up to max_per_query), if open_details:
  click it — `call_tool("click", {"uid": <card button uid>})` — then
  `take_snapshot`, read the current page URL from the snapshot's RootWebArea line
  (`url="...vjk=<jk>..."`), set url = `https://www.indeed.com/viewjob?jk=<jk>`,
  and `parse_job_detail` for the description. (Card-heading→button uid: the
  `button "full details of {TITLE}"` line directly after the heading carries the
  uid — capture it in parse_job_cards as `_uid`.)
- Populate each record to the interesting_jobs shape: job_title, company_name,
  url, location, salary_range, posted_date=None, job_description,
  company_description=None, interest_score=0, interest_reason="", skills=[].
- Dedupe by url within the run. Return the list.

CLI `main(argv)`:
- `--config funnel/queries.yaml` (see below), `--out data/output/interesting_jobs.yaml`,
  `--no-details` (skip per-job description fetch), `--max N`.
- Write the collected list to the out YAML (list of dicts, exclude None values to
  keep it compact). Print "collected N jobs across M queries".
- Wrap the whole run so a single query failure logs and continues.

### 3. `funnel/queries.yaml` — the validated query set

```yaml
# Remote is where the real inventory is; Austin returns near-nothing for this
# profile. Keep one Austin+hybrid query anyway for local roles (often empty).
queries:
  - {q: "software engineer react", l: "Remote", fromage: 1, sort: date}
  - {q: "frontend engineer", l: "Remote", fromage: 1, sort: date}
  - {q: "full stack typescript", l: "Remote", fromage: 1, sort: date}
  - {q: "ai engineer", l: "Remote", fromage: 1, sort: date}
  - {q: "senior frontend engineer", l: "Austin, TX", fromage: 3, sort: date}
```

## Tests (funnel/tests/test_collect.py, test_mcp_client.py)

- parse_job_cards over `funnel/fixtures/search_results.txt`: 3 cards; first is
  {job_title: "Senior / Staff Full Stack Software Engineer – Remote",
   company_name: "Glint Tech Solutions LLC", location: "Remote in Sunnyvale, CA",
   salary_range: None}; DreamWorks card has salary "$190,000 - $230,000 a year".
- has_no_results: True for `no_results.txt`, False for `search_results.txt`.
- parse_job_detail over `job_detail.txt`: description contains "TypeScript",
  "React", "Node.js", and the opening sentence; excludes "Report job".
- collect(): pass a fake client (monkeypatched call_tool returning fixture text in
  sequence) and assert it yields the expected records and skips a no-results query.
- parse_sse: given the real SSE sample
  `event: message\ndata: {"result":{"x":1},"jsonrpc":"2.0","id":1}\n`, returns
  `[{"result":{"x":1},...}]`.
- McpClient.call_tool: monkeypatch `_post` to return a tools/call result with
  content=[{type:text,text:"hello"}]; assert returns "hello".

Run: `uv run python -m pytest funnel/tests/test_collect.py funnel/tests/test_mcp_client.py -q`

Do not touch files outside funnel/.
