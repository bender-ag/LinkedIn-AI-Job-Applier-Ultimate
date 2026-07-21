# Funnel glue scripts — implementation spec

Build three stdlib-only Python 3.12 modules in this directory (`funnel/`), plus
pytest tests in `funnel/tests/`. No new dependencies (PyYAML is already a
project dependency and may be used). Follow the repo's existing style: black
(line length per pyproject.toml), isort, type hints, loguru NOT required —
these scripts are standalone and may use plain logging or print.

## 1. `funnel/score_match.py` — port of scoreMatch()

Port the TypeScript `scoreMatch(resumeText, jdText)` function from
`/tmp/claude-1000/-workspace-main/b8738bd6-25f9-4f66-b291-e89838cc635b/scratchpad/resume-match/src/`
(files: `resumeMatch.ts`, `stem.ts`, `stopwords.ts`) to Python, faithfully:

- Same tokenization, stopword/JD-filler dropping, bare-number dropping.
- Same stemming rules (port `stem.ts` exactly).
- Acronyms preserved as first-class tokens.
- Top 40 JD terms by frequency, bigram phrases 1.2x boost, weighted coverage.
- Same output shape as a dataclass/dict: score (int 0-100), band
  ("strong" >= 75, "partial" >= 45, else "weak"), matched (list), missing
  (list), ok (bool), thin_jd (bool, JD < 60 meaningful tokens).
- Port the calibration tests from `tests/resumeMatch.test.ts` and
  `tests/stem.test.ts` in that clone into `funnel/tests/test_score_match.py`.
  The ported tests MUST assert the same expected values as the TS suite.

## 2. `funnel/merge.py` — YAML -> SQLite lifecycle merge

CLI: `python -m funnel.merge [--yaml data/output/interesting_jobs.yaml] [--db data/funnel.db]`

- Create table `jobs` if absent: url TEXT PRIMARY KEY, job_title, company_name,
  location, job_description, company_description, salary_range, posted_date,
  interest_score INTEGER, interest_reason, skills (JSON-encoded list),
  first_seen TEXT (ISO), last_seen TEXT (ISO), digested INTEGER DEFAULT 0.
- For each entry in the YAML list: upsert by url. New url -> insert with
  first_seen = last_seen = now (UTC ISO). Existing url -> update last_seen and
  refresh mutable fields (score, reason, skills, salary, description) but
  NEVER overwrite first_seen.
- Rows missing `url` are skipped with a warning; malformed/missing YAML file
  exits 0 with a message (empty sweep is not an error).
- Print a one-line summary: N new, M updated.

## 3. `funnel/digest.py` — new-jobs digest

CLI: `python -m funnel.digest [--db data/funnel.db] [--min-score 70] [--resume data/resumes/resume_text.txt] [--out-dir /artifacts]`

- Select rows where digested = 0 AND interest_score >= min-score, ordered by
  interest_score desc.
- If the resume file exists, run score_match(resume_text, job_description)
  for each row and include: deterministic score/band + up to 15 missing
  keywords. If the resume file is missing, skip that section per-job with a
  single note at the top of the digest ("resume_text.txt not found - keyword
  scoring skipped").
- Write markdown to `<out-dir>/digest-YYYY-MM-DD.md` (today's date; if the
  file exists, append a `## Run 2 (HH:MM)` style section rather than
  overwriting). Per job: title, company, location, salary (if any),
  LLM score + reason, deterministic score + band, missing keywords, url,
  first_seen.
- Mark included rows digested = 1 only after the file is written successfully.
- If there are no new rows, still print "0 new jobs" and write nothing.

## Tests (pytest, in funnel/tests/)

- test_score_match.py: ported TS calibration suite (see above).
- test_merge.py: tmp_path SQLite + YAML fixtures — first insert sets
  first_seen; re-merge updates last_seen but not first_seen; missing url
  skipped; missing YAML file handled.
- test_digest.py: seeds a tmp DB — digest writes expected markdown, marks
  digested, respects min-score, handles missing resume file, appends on
  second run same day, "0 new jobs" path.

Run tests with: `uv run python -m pytest funnel/tests/ -q` (from repo root).
An empty `funnel/__init__.py` and `funnel/tests/__init__.py` are fine.
Do not modify anything outside `funnel/` except nothing — no other files.
