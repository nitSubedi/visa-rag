"""Citations-first answers: a quote is shown only if it is the source's own text."""

from __future__ import annotations

from visa import cited

SOURCES = [
    "An F-1 student may be authorized up to 12 months of practical training. "
    "A student may not request a start date that is more than 60 days after the "
    "student\u2019s program end date.",
    "The petitioner&nbsp;may have an ownership interest in an entity based in the "
    "United States, of which the petitioner may also be the founder.",
    "Students may complete their training with a startup business, as long as all "
    "regulatory requirements are met.",
]


def _check(quote: str, source: int) -> cited.Checked:
    raw = {
        "answer": "a",
        "ask": "",
        "points": [{"source": source, "quote": quote, "applies": "x"}],
    }
    return cited.check(raw, SOURCES)


def test_an_exact_quote_is_kept_where_cited() -> None:
    c = _check(
        "An F-1 student may be authorized up to 12 months of practical training.", 1
    )
    assert [(p.source, p.moved) for p in c.points] == [(1, False)] and not c.dropped


def test_joined_sentences_are_checked_one_by_one() -> None:
    q = (
        "An F-1 student may be authorized up to 12 months of practical training. "
        "A student may not request a start date that is more than 60 days after the "
        "student's program end date."
    )
    assert len(_check(q, 1).points) == 2


def test_the_right_text_under_the_wrong_number_is_moved() -> None:
    c = _check(
        "Students may complete their training with a startup business, as long "
        "as all regulatory requirements are met.",
        1,
    )
    (p,) = c.points
    assert p.source == 3 and p.moved and p.cited == 1


def test_corpus_entities_do_not_hide_a_real_quote() -> None:
    c = _check(
        "The petitioner may have an ownership interest in an entity based in the "
        "United States.",
        2,
    )
    (p,) = c.points
    assert "&nbsp;" not in p.quote and p.quote.startswith("The petitioner may have")


def test_what_is_shown_is_the_sources_wording() -> None:
    """Folded for matching, shown as written: the source's curly apostrophe survives."""
    c = _check(
        "A student may not request a start date that is more than 60 days after "
        "the student's program end date.",
        1,
    )
    assert "\u2019" in c.points[0].quote


def test_an_invented_sentence_is_dropped_and_reported() -> None:
    c = _check("Your school may charge a fee to issue the OPT recommendation.", 1)
    assert c.points == [] and c.dropped


def test_a_fragment_too_short_to_prove_anything_is_dropped() -> None:
    assert _check("practical training.", 1).points == []


def test_render_labels_the_models_reading() -> None:
    c = _check(
        "An F-1 student may be authorized up to 12 months of practical training.", 1
    )
    out = cited.render(c, ["8 CFR § 214.2(f)", "b", "c"])
    assert "verified word for word" in out and "the tool's reading" in out
    assert '[1] 8 CFR § 214.2(f): "An F-1 student' in out


def test_an_application_that_repeats_the_quote_is_not_shown() -> None:
    q = "An F-1 student may be authorized up to 12 months of practical training."
    raw = {"answer": "a", "ask": "", "points": [{"source": 1, "quote": q, "applies": q}]}
    out = cited.render(cited.check(raw, SOURCES), ["x", "y", "z"])
    assert "the tool's reading" not in out
