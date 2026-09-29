"""The abbreviation map is hard-coded, so every entry has to be provably right."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from visa.terminology import SCOPES, TERMS, excluded, expand, under


@pytest.fixture(scope="module")
def corpus_text() -> str:
    from visa.search import Index

    if not (Path.home() / ".visa" / "corpus" / "8-cfr" / "vectors.npy").exists():
        pytest.skip("corpus not indexed")
    return " \n ".join(" ".join(r.text.split()) for r in Index.load().rows).lower()


def test_every_entry_carries_a_source_and_a_quote() -> None:
    for t in TERMS:
        assert t.source_kind in ("corpus", "uscis"), t.abbr
        assert t.source and t.quote, t.abbr
        if t.source_kind == "uscis":
            assert t.source.startswith("https://www.uscis.gov/"), t.abbr


@pytest.mark.parametrize(
    "term", [t for t in TERMS if t.source_kind == "corpus"], ids=lambda t: t.abbr
)
def test_corpus_quotes_are_verbatim(term, corpus_text) -> None:
    """A corpus-sourced definition must actually be in the corpus, word for word."""
    assert " ".join(term.quote.split()).lower() in corpus_text, term.quote


@pytest.mark.parametrize("term", TERMS, ids=lambda t: t.abbr)
def test_every_expansion_occurs_in_the_corpus(term, corpus_text) -> None:
    """An expansion the corpus never uses cannot help retrieval, only distort it."""
    assert term.expansion.lower() in corpus_text, term.expansion


def test_each_expansion_is_grounded_in_its_own_quote_or_the_corpus() -> None:
    """Guards against an expansion drifting from the definition it cites: every content
    word of the expansion appears in the quoted definition."""
    stop = {"and", "or", "the", "of", "in", "for"}
    for t in TERMS:
        words = {w for w in re.findall(r"[a-z]+", t.expansion.lower()) if w not in stop}
        quoted = set(re.findall(r"[a-z]+", t.quote.lower()))
        missing = words - quoted
        assert not missing, f"{t.abbr}: {missing} not in its quoted definition"


@pytest.mark.parametrize(
    ("question", "must_contain"),
    [
        ("Can I do full-time CPT?", "curricular practical training"),
        ("i did cpt for a year", "curricular practical training"),
        ("My OPT ends soon", "optional practical training"),
        ("Do I meet EB-1A?", "extraordinary ability in the sciences, arts"),
        ("thinking about eb1a", "extraordinary ability in the sciences, arts"),
        ("EB-2 NIW for founders", "national interest waiver"),
        ("O-1A petition", "extraordinary ability in the sciences, education"),
        ("my I-94 date", "arrival-departure record"),
    ],
)
def test_abbreviations_are_expanded(question: str, must_contain: str) -> None:
    assert must_contain in expand(question)


@pytest.mark.parametrize(
    "question",
    ["Can I opt out of this?", "Problems stem from the delay", "I'd rather opt in"],
)
def test_ordinary_english_words_are_left_alone(question: str) -> None:
    """ "opt" and "stem" are words; only their capitalised abbreviations are expanded."""
    assert expand(question) == question


def test_eb1_does_not_swallow_eb1a() -> None:
    q = expand("EB-1A or EB-1?")
    assert "extraordinary ability in the sciences, arts" in q
    assert "outstanding professors or researchers, and multinational" in q


def test_dso_does_not_match_inside_pdso() -> None:
    q = expand("Ask your PDSO")
    assert "principal designated school official" in q
    assert q.count("designated school official") == 1


def test_expansion_is_idempotent() -> None:
    once = expand("Can I do full-time CPT during OPT?")
    assert expand(once) == once


def test_the_one_year_cpt_rule_is_now_retrieved(index) -> None:
    """The failure this exists for: asked with "CPT", retrieval did not return
    8 CFR 214.2(f)(10)(i) in its top 300; asked in the regulation's words, rank 1."""
    idx = index
    q = expand(
        "I did 12 months of full-time CPT during my master's. Can I still get "
        "post-completion OPT?"
    )
    hits = idx.search(q, k=12)
    rule = r"one year or more of full[- ]time curricular practical training"
    assert any(re.search(rule, " ".join(h.row.text.split()), re.I) for h in hits)


@pytest.fixture(scope="module")
def index():
    from visa.search import Index

    if not (Path.home() / ".visa" / "corpus" / "8-cfr" / "vectors.npy").exists():
        pytest.skip("corpus not indexed")
    return Index.load()


def test_scope_headings_are_verbatim(index) -> None:
    """Each provision a scope sets aside is identified by its own heading. Every one in
    the corpus must be found, word for word, in a passage under that citation."""
    for scope in SCOPES:
        for prefix, heading in scope.provisions:
            rows = [r for r in index.rows if under(r.citation, prefix)]
            assert rows, f"{scope.name}: nothing indexed under {prefix}"
            if prefix.startswith("USCIS PM") and heading.startswith("Part "):
                continue  # a Part title is not in the corpus; see SCOPES
            assert any(heading in " ".join(r.text.split()) for r in rows), heading


def test_under_does_not_confuse_neighbouring_chapters() -> None:
    assert under("USCIS PM Vol 6, Pt F, Ch 2, A.1", "USCIS PM Vol 6, Pt F, Ch 2")
    assert not under("USCIS PM Vol 6, Pt F, Ch 20", "USCIS PM Vol 6, Pt F, Ch 2")
    assert under("8 CFR § 204.5(h)(3)", "8 CFR § 204.5(h)")
    assert not under("8 CFR § 204.5(i)", "8 CFR § 204.5(h)")


@pytest.mark.parametrize(
    ("question", "sets_aside"),
    [
        ("Do I meet EB-1A?", "8 CFR § 214.2(o)"),
        ("eb1a criteria", "8 CFR § 214.2(o)"),
        ("O-1A peer group", "8 CFR § 204.5(h)"),
        ("my O1 visa", "8 CFR § 204.5(h)"),
    ],
)
def test_naming_one_sibling_sets_aside_the_other(question: str, sets_aside: str) -> None:
    assert sets_aside in excluded(question)


@pytest.mark.parametrize(
    "question",
    ["I'm on an O-1 now. Should I file EB-1A?", "What is CPT?", "Is a zoo 1 open?"],
)
def test_nothing_is_set_aside_unless_exactly_one_sibling_is_named(question) -> None:
    assert excluded(question) == ()


EB1A_QUESTIONS = [
    "For EB-1A I have 8 peer-reviewed publications and I've reviewed papers for 2 "
    "journals, but no awards and no media coverage. Do I meet the criteria?",
    "What are the EB-1A criteria?",
    "Can I self-petition for EB-1A?",
    "What counts as a major internationally recognized award for EB1A?",
]


@pytest.mark.parametrize("question", EB1A_QUESTIONS)
def test_eb1a_retrieves_its_own_rule_not_the_o1_lookalike(index, question) -> None:
    """The failure this exists for: 204.5(h) and 214.2(o) share their wording, so an
    EB-1A question was served O-1's criteria (4-7 of 12 slots) and never 204.5(h)'s
    "at least three" rule on the question the eval asks."""
    hits = index.search(expand(question), k=12, exclude=excluded(question))
    assert not any(under(h.row.citation, "8 CFR § 214.2(o)") for h in hits)
    assert not any(under(h.row.citation, "USCIS PM Vol 2, Pt M") for h in hits)
    assert any(
        under(h.row.citation, "8 CFR § 204.5(h)")
        and re.search(r"at least three", h.row.text, re.I)
        for h in hits
    )


def test_o1_retrieves_its_own_provision_not_eb1a(index) -> None:
    q = "What are the O-1 evidentiary criteria?"
    hits = index.search(expand(q), k=12, exclude=excluded(q))
    assert any(under(h.row.citation, "8 CFR § 214.2(o)") for h in hits)
    assert not any(under(h.row.citation, "8 CFR § 204.5(h)") for h in hits)
