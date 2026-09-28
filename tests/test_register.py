"""Matching retrieved citations against provisions that are not in force.

The eCFR publishes amended text and says nothing about litigation, so the corpus can be
current and wrong at once with every freshness signal green (STATE.md finding 11). The
register is the only thing that catches it, and the matching has to be exact about
boundaries: a warning on the wrong provision trains people to ignore warnings.
"""

from __future__ import annotations

import datetime as dt

import pytest

from visa import register


@pytest.mark.parametrize(
    ("provision", "citation"),
    [
        # identical, modulo the section sign and spacing the corpus uses
        ("8 CFR 214.2(f)(5)", "8 CFR § 214.2(f)(5)"),
        # the register names a sub-paragraph; the chunk is cited at paragraph level and
        # may well contain it
        ("8 CFR 214.2(f)(5)", "8 CFR § 214.2(f)"),
        # the register covers the whole paragraph; the chunk is a sub-paragraph of it
        ("8 CFR 214.1(m)", "8 CFR § 214.1(m)(1)"),
    ],
)
def test_a_provision_matches_the_citation_it_governs(provision, citation) -> None:
    assert register.covers(provision, citation)


@pytest.mark.parametrize(
    ("provision", "citation"),
    [
        ("8 CFR 214.2(f)(5)", "8 CFR § 214.2(h)"),  # different paragraph
        ("8 CFR 214.2(f)", "8 CFR § 214.20"),  # 214.2 must not prefix 214.20
        ("8 CFR 214.2(f)", "8 CFR § 214.21(f)"),  # nor 214.21
        ("8 CFR 214.2(f)", "6 CFR § 214.2(f)"),  # different title
        ("8 CFR 214.2(f)", "8 U.S.C. § 214.2(f)"),  # regulation is not statute
        ("8 CFR 214.2(f)", "USCIS PM Vol 2, Pt F, Ch 5"),  # not a CFR citation at all
        ("8 CFR 214.2(f)", ""),
    ],
)
def test_unrelated_citations_do_not_match(provision, citation) -> None:
    assert not register.covers(provision, citation)


def test_the_shipped_register_flags_the_enjoined_f1_provisions() -> None:
    """The live case: the fixed-period rule is in the corpus and is not the law."""
    entries = register.load()
    assert entries, "the shipped register must not be empty"
    hits = register.affecting(["8 CFR § 214.2(f)"], entries)
    assert hits, "214.2(f) carries the enjoined fixed-period text"
    e = hits[0]
    assert e.status == "enjoined"
    assert "1:26-cv-13799" in e.docket
    assert "duration of status" in e.instead.lower()


def test_cpt_guidance_is_not_swept_up_by_the_injunction() -> None:
    """The injunction is partial. SEVP's CPT guidance survived it, and a register that
    over-warns on surviving law is worse than none — people stop reading warnings."""
    entries = register.load()
    assert not register.affecting(
        ["SEVP / Study in the States — STEM OPT guidance"], entries
    )
    assert not register.affecting(["8 CFR § 214.2(o)"], entries)


def test_entries_report_their_own_age() -> None:
    """Staleness here is dangerous in both directions: a lifted injunction leaves a
    false warning, a new one leaves none. So the age has to be surfaceable."""
    for e in register.load():
        assert isinstance(e.checked, dt.date)
        assert e.age_days >= 0


# --- the note has to reach the prompt, and survive the budget ------------------


def test_the_not_in_force_note_reaches_the_prompt() -> None:
    """A warning the model never sees cannot stop it asserting enjoined law."""
    from visa import answer
    from visa.models import Chunk

    class _Hit:
        def __init__(self, citation: str):
            self.row = Chunk(text="word " * 300, citation=citation, tier=2, shard="t")
            self.cosine = 0.8

    msgs = answer.build_prompt("How long am I admitted for?", [_Hit("8 CFR § 214.2(f)")])
    body = msgs[1]["content"]
    assert "NOT IN FORCE" in body
    assert "1:26-cv-13799" in body


def test_the_note_sits_with_the_facts_not_the_sources() -> None:
    """It does not compete with passages, it overrides them — so it belongs in the
    tail, past SOURCES and un-truncatable, like the computed deadlines."""
    from visa import answer
    from visa.models import Chunk

    class _Hit:
        def __init__(self, citation: str):
            self.row = Chunk(text="word " * 300, citation=citation, tier=2, shard="t")
            self.cosine = 0.8

    body = answer.build_prompt("q", [_Hit("8 CFR § 214.2(f)")])[1]["content"]
    assert body.index("SOURCES:") < body.index("NOT IN FORCE")
    assert body.index("NOT IN FORCE") < body.index("QUESTION:")


def test_the_note_survives_a_full_source_slate() -> None:
    """Finding 0 again: anything added to the prompt must be budgeted, or it silently
    pushes the critical blocks out of context."""
    from visa import answer
    from visa.models import Chunk

    class _Hit:
        def __init__(self, citation: str):
            self.row = Chunk(text="word " * 900, citation=citation, tier=2, shard="t")
            self.cosine = 0.8

    hits = [_Hit("8 CFR § 214.2(f)")] + [_Hit(f"8 CFR § 100.{i}") for i in range(40)]
    body = answer.build_prompt("q", hits)[1]["content"]
    assert "NOT IN FORCE" in body


def test_an_unaffected_question_gets_no_note() -> None:
    from visa import answer
    from visa.models import Chunk

    class _Hit:
        def __init__(self, citation: str):
            self.row = Chunk(text="word " * 300, citation=citation, tier=2, shard="t")
            self.cosine = 0.8

    body = answer.build_prompt("q", [_Hit("8 CFR § 204.5(h)")])[1]["content"]
    assert "NOT IN FORCE" not in body
