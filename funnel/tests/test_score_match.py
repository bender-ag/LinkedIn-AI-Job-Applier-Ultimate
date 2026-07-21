"""Ported calibration suite from resumeMatch.test.ts and stem.test.ts."""

from funnel.score_match import score_match, stem

JD = (
    "Project management role requiring agile scrum budgeting stakeholder "
    "communication and reporting skills daily"
)

SENIOR_BA_JD = (
    "Senior Business Analyst. Acme Financial Solutions provides treasury management "
    "and liquidity management to the public sector, local governments, and school "
    "districts. As the Senior Business Analyst on the LedgerMax product POD, you will "
    "own the requirements and delivery, partner with the product owner, engineers, "
    "and designers, and translate business needs into well-defined features. Thrive "
    "in an Agile team environment. Participate in sprint ceremonies, refine the "
    "product backlog with the product owner, and serve as the bridge to stakeholders. "
    "Elicit and document business requirements through stakeholder interviews and data "
    "analysis. Translate business needs into detailed user stories, acceptance "
    "criteria, and functional specifications. Define and execute user acceptance "
    "testing for each release. Create process documentation and data-flow diagrams. "
    "Strong understanding of Agile Scrum methodologies. Experience with backlog "
    "management tools such as Jira and documentation platforms such as Confluence. "
    "Understanding of software architecture concepts including APIs, microservices, "
    "and data models. Proficiency with data analysis and visualization. Seven years "
    "as a Business Analyst, preferably in financial services."
)

STRONG_RESUME = (
    "Technical Product Leader and Product Architect with eighteen years in "
    "investments technology. Promoted from Senior Business Analyst. Owned "
    "architecture, requirements, and delivery across data-heavy products. Drove "
    "roadmap and backlog prioritization, partnered with product owners, engineers, "
    "and designers, and ran Agile sprint planning and ceremonies. Translated "
    "business needs into user stories, acceptance criteria, and functional "
    "specifications. Elicited requirements through stakeholder interviews and data "
    "analysis; defined and coordinated user acceptance testing. Built data-flow "
    "diagrams and process documentation. Bridged business and engineering "
    "stakeholders. Tools: Jira, Confluence, SQL, Python, Tableau, Snowflake. "
    "Designed APIs and data models across microservices. Financial services and "
    "capital markets domain."
)

PARTIAL_RESUME = (
    "Business analyst with three years writing user stories and acceptance "
    "criteria, refining the product backlog with product owners, and running "
    "sprint ceremonies. Gathered business requirements from stakeholders and wrote "
    "functional specifications. Performed data analysis and reporting for "
    "financial services clients. Tracked work in Jira and documentation in Confluence."
)

WEAK_RESUME = (
    "Experienced carpenter framing houses, installing cabinets, doors, and trim, "
    "pouring concrete, and operating power tools on residential construction sites "
    "for fifteen years."
)


class TestStem:
    def test_strips_plurals(self):
        assert stem("companies") == "company"
        assert stem("stakeholders") == stem("stakeholder")
        assert stem("apis") == stem("api")

    def test_unifies_verb_noun_forms(self):
        assert stem("managed") == stem("management")
        assert stem("managing") == stem("managed")
        assert stem("requirements") == stem("requirement")
        assert stem("integrations") == stem("integration")

    def test_collapses_doubled_consonants(self):
        assert stem("planning") == "plan"
        assert stem("running") == "run"

    def test_leaves_short_tokens_alone(self):
        assert stem("sql") == "sql"
        assert stem("data") == "data"


class TestScoreMatch:
    def test_identical_texts_score_100_strong(self):
        r = score_match(JD, JD)
        assert r.ok is True
        assert r.score == 100
        assert r.band == "strong"
        assert r.missing == []

    def test_zero_overlap_is_weak(self):
        resume = (
            "Experienced carpenter building wooden furniture cabinets tables "
            "chairs decks fences homes"
        )
        r = score_match(resume, JD)
        assert r.ok is True
        assert r.band == "weak"
        assert r.score < 20
        assert len(r.matched) <= 1
        assert len(r.missing) > 0

    def test_credits_stemmed_and_phrase_overlap(self):
        resume = (
            "Led project management using agile scrum methods plus stakeholder "
            "communication across teams"
        )
        r = score_match(resume, JD)
        assert r.ok is True
        assert r.band in ("partial", "strong")
        assert "agile" in r.matched
        assert "budgeting" in r.missing

    def test_ok_false_for_empty_or_too_short(self):
        assert score_match("", JD).ok is False
        assert score_match("short resume", JD).ok is False
        assert score_match(JD, "hi").ok is False

    def test_excludes_stopwords_filler_and_bare_numbers(self):
        jd = (
            "Managed 5 engineers and the 2020 budget with strong leadership "
            "experience and clear reporting"
        )
        resume = (
            "Managed engineers and budget with leadership and reporting over " "many years total"
        )
        r = score_match(resume, jd)
        all_terms = r.matched + r.missing
        for bad in ["and", "the", "with", "5", "2020", "strong", "experience"]:
            assert bad not in all_terms


class TestScoreMatchCalibration:
    def test_strong_senior_ba(self):
        r = score_match(STRONG_RESUME, SENIOR_BA_JD)
        assert r.score >= 75
        assert r.band == "strong"

    def test_partial_incomplete(self):
        r = score_match(PARTIAL_RESUME, SENIOR_BA_JD)
        assert r.band == "partial"
        assert r.score >= 45
        assert r.score < 75

    def test_weak_unrelated(self):
        r = score_match(WEAK_RESUME, SENIOR_BA_JD)
        assert r.band == "weak"
        assert r.score < 25


class TestThinJd:
    def test_flags_teaser_as_thin(self):
        r = score_match(STRONG_RESUME, JD)
        assert r.ok is True
        assert r.thin_jd is True

    def test_does_not_flag_full_jd(self):
        r = score_match(STRONG_RESUME, SENIOR_BA_JD)
        assert r.ok is True
        assert r.thin_jd is False

    def test_thin_jd_on_too_short_early_return(self):
        r = score_match(STRONG_RESUME, "hi")
        assert r.ok is False
        assert r.thin_jd is True
