"""Matching retrieved citations against provisions that are not in force.

The eCFR publishes amended text and says nothing about litigation, so the corpus can be
current and wrong at once with every freshness signal green (STATE.md finding 18). The
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


M_TEXT = (
    "(m) Transition period from duration of status to a fixed admission date—(1) "
    "Transition from duration of status for F-1 students"
)


def _amended():
    """A 214.2(f) chunk carrying a paragraph the enjoined rule actually wrote."""
    from visa.models import Chunk

    class _H:
        def __init__(self) -> None:
            self.row = Chunk(text=F5_TEXT, citation="8 CFR § 214.2(f)", tier=2, shard="t")
            self.cosine = 0.8

    return _H()


def test_the_not_in_force_note_reaches_the_prompt() -> None:
    """A warning the model never sees cannot stop it asserting enjoined law."""
    from visa import answer
    from visa.models import Chunk

    class _Hit:
        def __init__(self, citation: str):
            self.row = Chunk(text="word " * 300, citation=citation, tier=2, shard="t")
            self.cosine = 0.8

    msgs = answer.build_prompt("How long am I admitted for?", [_amended()])
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

    body = answer.build_prompt("q", [_amended()])[1]["content"]
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

    hits = [_amended()] + [_Hit(f"8 CFR § 100.{i}") for i in range(40)]
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


# --- precision: a suspended sub-paragraph must not condemn its whole paragraph ------

F5_TEXT = (
    "8 CFR § 214.2(f) — students\n(5) Period of stay—(i) General. An F-1 student is "
    "admitted for a fixed period of time, which is the period necessary to complete the "
    "course of study indicated on the Form I-20, not to exceed a period of 4 years."
)
F4_TEXT = (
    "8 CFR § 214.2(f) — students\n(4) Temporary absence. An F-1 student returning from "
    "a temporary absence of five months or less may be readmitted if the student "
    "presents a current Form I-20."
)


def test_text_decides_which_part_of_a_paragraph_is_suspended() -> None:
    """The corpus cites at paragraph level — "8 CFR § 214.2(f)" is the whole F-1
    paragraph — while the register names sub-paragraphs. Matching on the citation alone
    condemned filing rules that were never enjoined (finding 19). The chunk text is what
    distinguishes them."""
    assert register.covers("8 CFR 214.2(f)(5)", "8 CFR § 214.2(f)", F5_TEXT)
    assert not register.covers("8 CFR 214.2(f)(5)", "8 CFR § 214.2(f)", F4_TEXT)


def test_without_text_a_more_specific_provision_still_matches() -> None:
    """Under-warning is the dangerous direction: quoting enjoined law as current is
    worse than withholding law that is in force. With nothing to inspect, warn."""
    assert register.covers("8 CFR 214.2(f)(5)", "8 CFR § 214.2(f)")


def test_a_broader_registered_provision_needs_no_text() -> None:
    """Registering all of 214.1(m) covers every chunk of it, whatever the slice."""
    assert register.covers("8 CFR 214.1(m)", "8 CFR § 214.1(m)", F4_TEXT)


# --- exclusion with visibility -----------------------------------------------------


def _hit(citation: str, text: str):
    from visa.models import Chunk

    class _H:
        def __init__(self) -> None:
            self.row = Chunk(text=text, citation=citation, tier=2, shard="t")
            self.cosine = 0.8

    return _H()


def test_suspended_text_never_reaches_the_prompt() -> None:
    """Finding 19: a 7b handed suspended law plus a warning quotes the law. 228 tokens
    of warning lost to 8,000 tokens of on-topic passage. Withhold the text instead."""
    from visa import answer

    body = answer.build_prompt("q", [_hit("8 CFR § 214.2(f)", F5_TEXT)])[1]["content"]
    assert "not to exceed a period of 4 years" not in body
    assert "SUSPENDED" in body


def test_in_force_text_under_the_same_citation_is_kept() -> None:
    """The whole point of the precision fix: (4) is in force and must still be usable."""
    from visa import answer

    body = answer.build_prompt("q", [_hit("8 CFR § 214.2(f)", F4_TEXT)])[1]["content"]
    assert "Temporary absence" in body
    assert "SUSPENDED" not in body


def test_withholding_preserves_the_source_numbering() -> None:
    """`/sources N` and verify_citations both key off the slot number, so a withheld
    passage has to keep its slot rather than shift the ones after it."""
    from visa import answer

    hits = [
        _hit("8 CFR § 204.5(h)", "extraordinary ability criteria"),
        _hit("8 CFR § 214.2(f)", F5_TEXT),
        _hit("8 CFR § 214.2(o)", "O-1 consultation requirement"),
    ]
    body = answer.build_prompt("q", hits)[1]["content"]
    assert "[1] 8 CFR § 204.5(h)" in body
    assert "[2] 8 CFR § 214.2(f)" in body
    assert "[3] 8 CFR § 214.2(o)" in body
    assert "consultation" in body


def test_suspended_text_cannot_support_a_date() -> None:
    """Withholding happens in build_prompt; verify_dates read the unfiltered hits, so a
    date could be "supported" by suspended law the model was never shown. Observed: the
    model invented a 2026-12-14 filing date, and it passed because "December 14, 2026"
    appears in 214.1(m) — an enjoined transition provision, on an unrelated visa class."""
    from visa import answer

    prof = {"status": "F-1", "program_end_date": "2027-05-14"}
    hits = [_hit("8 CFR § 214.1(m)", M_TEXT + " not to exceed December 14, 2026.")]
    problems = answer.verify_dates("you may file by 2026-12-14.", prof=prof, hits=hits)
    assert problems, "a date supported only by suspended text must still be flagged"
    assert "2026-12-14" in problems[0]


def test_withheld_chunks_do_not_consume_the_evidence_budget() -> None:
    """A withheld slot contributes no text, so counting it against top_k shrinks the
    slate. Measured: opt_filing_window spent 4 of 12 slots on withheld passages and
    dropped from 3/3 to 1/3, while niw_standard withheld nothing and held 4/4. The
    suspended passages must still be *returned* — the banner and /sources N need them —
    they just must not crowd out usable law."""
    from pathlib import Path

    from visa.config import settings
    from visa.search import Index

    if not (Path.home() / ".visa" / "corpus" / "8-cfr" / "vectors.npy").exists():
        pytest.skip("corpus not indexed")
    idx = Index.load()
    entries = register.load()
    hits = idx.search("When exactly can I file my I-765 for post-completion OPT?")
    usable = [
        h for h in hits if not register.suspended(h.row.citation, h.row.text, entries)
    ]
    assert len(usable) >= settings.top_k, (
        f"only {len(usable)} usable of {len(hits)} returned; "
        f"{len(hits) - len(usable)} withheld slots crowded out real law"
    )


def test_the_i_visa_provisions_are_registered_too() -> None:
    """The rulemaking covered F, J *and* I nonimmigrants, and the injunction reaches all
    three — the NewsGuild-CWA was a plaintiff. An earlier version of this register listed
    only the F and J provisions, leaving 214.2(i)'s 240-day fixed period quotable as
    current law."""
    entries = register.load()
    hits = register.affecting(["8 CFR § 214.2(i)"], entries)
    assert hits, "214.2(i) carries the enjoined I-visa fixed period"
    assert "214.2(i)" in " ".join(hits[0].provisions)


def test_every_corpus_definition_still_loads() -> None:
    """Regression: the register once lived in sources/, and load_defs builds a Source from
    every TOML there. `visa refresh`, `visa add --source` and every reindex crashed with
    "unexpected keyword argument 'entry'", and no test noticed for a day."""
    from visa.sources import load_defs

    slugs = {s.slug for s in load_defs()}
    assert {"8-cfr", "uscis-policy-manual", "ina-8usc"} <= slugs


def test_an_ordinary_214_2_f_chunk_is_not_suspended() -> None:
    """The regression that hid the law: every 214.2(f) chunk carrying a "(11)" marker was
    withheld, taking the in-force E-Verify rule and unemployment limits with it. Only a
    chunk that contains a paragraph the rule wrote may be suspended."""
    from visa import answer
    from visa.models import Chunk

    class _H:
        def __init__(self, t: str) -> None:
            self.row = Chunk(text=t, citation="8 CFR § 214.2(f)", tier=2, shard="t")
            self.cosine = 0.8

    ordinary = "word " * 300
    assert register.suspended("8 CFR § 214.2(f)", ordinary) is None
    body = answer.build_prompt("q", [_H(ordinary)])[1]["content"]
    assert "NOT IN FORCE" not in body and "SUSPENDED" not in body


def _corpus():
    from pathlib import Path

    from visa.search import Index

    if not (Path.home() / ".visa" / "corpus" / "8-cfr" / "vectors.npy").exists():
        pytest.skip("corpus not indexed")
    return Index.load(), register.load()


def _flat(t: str) -> str:
    return " ".join(t.split())


def test_the_in_force_stem_rules_reach_the_model() -> None:
    """The regression that hid the law. Registering whole paragraphs withheld 27 of 65
    chunks of 214.2(f), including the only chunks carrying the E-Verify rule and the
    unemployment limits. The unemployment rule shares a chunk with an amended paragraph,
    so it survives only if redaction removes that paragraph and nothing more."""
    idx, entries = _corpus()
    f = [r for r in idx.rows if r.citation.strip() == "8 CFR § 214.2(f)"]
    for phrase in ("E-Verify", "Periods of unemployment during post-completion OPT"):
        carriers = [r for r in f if phrase in _flat(r.text)]
        assert carriers, phrase
        for r in carriers:
            assert not register.suspended(r.citation, r.text, entries), phrase
            kept, _ = register.redact(r.citation, r.text, entries)
            assert phrase in _flat(kept), f"{phrase!r} was redacted"


def test_the_enjoined_text_never_reaches_the_model() -> None:
    """The other direction: the rule's own paragraphs — the fixed period in (f)(5),
    extension of stay in (f)(7), the I-visa 240-day period — are cut out wherever they
    appear, whether or not the rest of the chunk survives."""
    idx, entries = _corpus()
    for phrase in (
        "An F-1 student is admitted for a fixed period of time",
        "USCIS may grant an extension of stay to an F-1 student",
        "consistent with the I classification, not to exceed 240 days",
    ):
        carriers = [r for r in idx.rows if phrase in _flat(r.text)]
        assert carriers, phrase
        for r in carriers:
            kept, hit = register.redact(r.citation, r.text, entries)
            assert hit and phrase not in _flat(kept), (
                f"{phrase!r} survived in {r.citation}"
            )
