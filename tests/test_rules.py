"""Rules the code decides. Each must quote its law exactly and decide its boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from visa import answer, rules


@pytest.fixture(scope="module")
def served() -> dict[str, str]:
    """Citation -> served text (post-redaction), whitespace-normalised."""
    from visa import register
    from visa.search import Index

    if not (Path.home() / ".visa" / "corpus" / "8-cfr" / "vectors.npy").exists():
        pytest.skip("corpus not indexed")
    out: dict[str, str] = {}
    for r in Index.load().rows:
        text = " ".join(register.redact(r.citation, r.text)[0].split())
        out[r.citation] = out.get(r.citation, "") + " " + text
    return out


@pytest.mark.parametrize(
    "source",
    [s for r in rules.RULES for s in r.sources],
    ids=lambda s: s.citation,
)
def test_every_quote_is_verbatim_in_force_text(source, served) -> None:
    """Checked against the served text, so a quote from a withheld (enjoined) paragraph
    fails: a rule must not rest on law that is not in force."""
    chunks = [t for c, t in served.items() if source.citation.startswith(c)]
    assert chunks, f"nothing indexed under {source.citation}"
    assert any(" ".join(source.quote.split()) in t for t in chunks), source.quote


@pytest.mark.parametrize(
    ("months", "barred"),
    [(12, True), (12.0, True), ("14", True), (11, False), (0, False)],
)
def test_the_one_year_boundary(months, barred) -> None:
    (o,) = rules.evaluate({"cpt_full_time_months": months})
    assert isinstance(o, rules.Outcome)
    assert o.decision.startswith("INELIGIBLE") is barred


def test_an_unknown_fact_is_a_question_not_a_guess() -> None:
    (n,) = rules.evaluate({"mentions_cpt": True})
    assert isinstance(n, rules.Needs) and n.fact == "cpt_full_time_months"
    assert "Part-time CPT does not count" in n.question


def test_a_rule_that_is_not_in_play_says_nothing() -> None:
    assert rules.evaluate({"status": "F-1"}) == []


def test_the_finding_quotes_its_law_in_the_prompt() -> None:
    block = rules.render(rules.evaluate({"cpt_full_time_months": 12}))
    assert block.startswith("RULE FINDINGS")
    assert "INELIGIBLE for post-completion OPT at the same educational level" in block
    assert "8 CFR § 214.2(f)(10)(i)" in block and "USCIS PM Vol 2, Pt F, Ch 5, B" in block


def test_the_question_overrides_the_profile(monkeypatch) -> None:
    monkeypatch.setattr(answer, "extract_facts", lambda q: {"cpt_full_time_months": 12})
    (o,) = answer.decide("I did a year of full-time CPT", {"cpt_full_time_months": 3})
    assert isinstance(o, rules.Outcome) and o.decision.startswith("INELIGIBLE")


def test_an_unrelated_question_costs_no_model_call(monkeypatch) -> None:
    def boom(q: str) -> dict[str, object]:
        raise AssertionError("extraction ran")

    monkeypatch.setattr(answer, "extract_facts", boom)
    assert answer.decide("How long is the STEM OPT extension?", {}) == []


def test_the_screen_shows_the_finding_without_the_prompt_instructions() -> None:
    shown = rules.render(rules.evaluate({"cpt_full_time_months": 12}), head="")
    assert shown.lstrip().startswith("INELIGIBLE") and "Open your answer" not in shown


def test_below_the_limit_does_not_claim_eligibility() -> None:
    """The rule only lifts one bar. Saying "not barred" was read as "eligible"."""
    (o,) = rules.evaluate({"cpt_full_time_months": 9})
    assert "other OPT requirements still apply" in o.decision


def test_missing_facts_become_questions() -> None:
    qs = rules.questions(rules.evaluate({"mentions_cpt": True}))
    assert qs and "full-time CPT" in qs[0]


@pytest.mark.parametrize(
    ("value", "text", "stated"),
    [
        (12, "I did 12 months of full-time CPT", True),
        (12, "I did a year of full-time CPT", True),
        (18, "full-time for eighteen months", True),
        (8, "6 months part-time and 8 months full-time CPT", True),
        (12, "I did some CPT last year", False),  # the observed invention
        (12, "I did 12 months of CPT", False),  # full-time never said
        (14, "I did 12 months of full-time CPT", False),  # a number nobody wrote
    ],
)
def test_a_fact_counts_only_if_the_person_said_it(value, text, stated) -> None:
    (fact,) = [f for f in rules.FACTS if f.key == "cpt_full_time_months"]
    assert fact.stated(value, text) is stated


def test_an_invented_fact_is_dropped_and_asked_for(monkeypatch) -> None:
    """Extraction returned 12 full-time months for "I did some CPT last year", and the
    rule declared the person ineligible for OPT."""
    monkeypatch.setattr(
        answer, "stream_chat", lambda *a, **k: iter(['{"cpt_full_time_months": 12}'])
    )
    (r,) = answer.decide("I did some CPT last year. Can I still get OPT?", {})
    assert isinstance(r, rules.Needs)


def _one(facts: dict[str, object]) -> rules.Outcome | rules.Needs:
    (r,) = [x for x in rules.evaluate(facts) if x.rule == "unemployment_limit"]
    return r


@pytest.mark.parametrize(
    ("days", "stem", "expect"),
    [
        ([70, 40], True, "40 days of unemployment REMAIN of the 150-day"),
        ([30], False, "60 days of unemployment REMAIN of the 90-day"),
        ([90], False, "0 days of unemployment REMAIN of the 90-day"),
        ([60, 40], False, "OVER THE LIMIT: 100 days"),
        ([100, 60], True, "OVER THE LIMIT: 160 days"),
    ],
)
def test_the_aggregate_is_summed_by_code(days, stem, expect) -> None:
    o = _one({"unemployment_days": days, "stem_extension": stem})
    assert isinstance(o, rules.Outcome) and o.decision.startswith(expect), o.decision


def test_the_limit_quoted_matches_the_limit_applied() -> None:
    o = _one({"unemployment_days": [10], "stem_extension": True})
    assert isinstance(o, rules.Outcome)
    assert all("150 days" in s.quote for s in o.sources)


def test_the_question_saying_stem_beats_a_blank_profile() -> None:
    o = _one({"unemployment_days": [10], "mentions_stem": True})
    assert isinstance(o, rules.Outcome) and "150-day" in o.decision


def test_unknown_stem_status_is_asked_not_assumed() -> None:
    n = _one({"unemployment_days": [10]})
    assert isinstance(n, rules.Needs) and n.fact == "stem_extension"


@pytest.mark.parametrize(
    ("value", "text", "stated"),
    [
        ([70, 40], "I had 70 days of unemployment and 40 more days", True),
        ([70, 40, 10], "I had 70 days of unemployment and 40 more days", False),
        ([30], "thirty days unemployed? no: I was unemployed for 30 days", True),
        ([60], "I was unemployed for a while", False),
        ([60, 35], "unemployed for sixty days, then 35 days", True),
    ],
)
def test_day_counts_count_only_if_stated(value, text, stated) -> None:
    (fact,) = [f for f in rules.FACTS if f.key == "unemployment_days"]
    assert fact.stated(value, text) is stated


UNEMP = rules.evaluate({"unemployment_days": [70, 40], "stem_extension": True})
Q2 = "I had 70 days of unemployment and 40 more days since my STEM extension started."


@pytest.mark.parametrize(
    ("prose", "flagged"),
    [
        ("80 days.", True),  # the observed answer, whole
        ("You have 80 days of unemployment left.", True),
        ("You must depart within 60 days of the end of OPT.", False),  # another period
        ("You have 40 days of unemployment remaining.", False),
        ("The student has 150 days of unemployment eligibility remaining.", True),
        ("You used 70 days and then 40 days of unemployment.", False),
        ("The limit is 150 days of unemployment.", False),
    ],
)
def test_a_day_count_the_code_did_not_produce_is_flagged(prose, flagged) -> None:
    assert bool(rules.contradictions(prose, UNEMP, Q2)) is flagged


@pytest.mark.parametrize(
    ("months", "prose", "flagged"),
    [
        (12, "You are still eligible for post-completion OPT.", True),
        (12, "You are ineligible for post-completion OPT.", False),
        (12, "You cannot get post-completion OPT at this level.", False),
        (9, "You are not eligible for OPT because of your CPT.", True),
        (9, "You can still apply for OPT.", False),
    ],
)
def test_a_verdict_the_code_did_not_reach_is_flagged(months, prose, flagged) -> None:
    decided = rules.evaluate({"cpt_full_time_months": months})
    assert bool(rules.contradictions(prose, decided)) is flagged
