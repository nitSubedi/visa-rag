"""What the date guard must catch.

A deadline that is almost right is the most dangerous output this tool produces,
and the original guard only looked for that: an ISO date within three days of a
computed boundary. Two live answers walked straight past it — one wrote a deadline
in prose ("October 1, 2027"), which the ISO pattern never matched, and it was wrong
by a full year, which is far outside the drift window.

The general rule is the tool's own thesis: deadlines are computed in Python and the
model explains them. A date it worked out for itself is unsupported even when it is
correct, because right-by-derivation is right by luck.
"""

from __future__ import annotations

import pytest

from visa import answer as ans
from visa.models import Chunk
from visa.search import Hit

PROFILE = {"status": "F-1", "program_end_date": "2027-05-14"}


def _source(text: str) -> Hit:
    row = Chunk(
        text=text,
        citation="8 CFR § 214.2(f)",
        title="t",
        shard="8-cfr",
        tier=2,
        kind="regulation",
        source_title="8 CFR",
    )
    return Hit(row=row, score=1.0, cosine=0.9, shard="8-cfr")


# --- drift, the original behaviour, unchanged without hits --------------------


def test_a_date_one_day_off_a_boundary_is_flagged() -> None:
    problems = ans.verify_dates("You may file starting 2027-02-14.", PROFILE)
    assert problems and "2027-02-13" in problems[0]


def test_the_boundary_itself_passes() -> None:
    assert ans.verify_dates("You may file starting 2027-02-13.", PROFILE) == []


def test_unrelated_dates_pass_when_no_sources_are_given() -> None:
    """Without hits the support check cannot run, so behaviour is as before."""
    assert ans.verify_dates("Your I-20 was issued 2024-01-05.", PROFILE) == []


# --- prose, which the ISO pattern never saw -----------------------------------


@pytest.mark.parametrize(
    "written",
    ["February 14, 2027", "Feb 14, 2027", "February 14th, 2027", "february 14 2027"],
)
def test_drift_is_caught_in_prose_too(written: str) -> None:
    problems = ans.verify_dates(f"You may file starting {written}.", PROFILE)
    assert problems, f"{written!r} was not parsed as a date"


# --- derivation: the year-wrong deadline that passed every check --------------


def test_a_date_supported_by_no_source_is_flagged() -> None:
    """The live failure: a FY2027 cap-gap written as an October 1, 2027 start."""
    problems = ans.verify_dates(
        "Your status continues to the H-1B start date of October 1, 2027.",
        PROFILE,
        hits=[_source("The H-1B petition must request an October 1 start date.")],
    )
    assert problems and "October 1, 2027" in problems[0]


def test_a_date_quoted_from_a_source_passes() -> None:
    problems = ans.verify_dates(
        "The rule took effect April 8, 2008.",
        PROFILE,
        hits=[_source("This provision took effect April 8, 2008 and remains in force.")],
    )
    assert problems == []


def test_an_iso_date_matching_a_source_in_prose_passes() -> None:
    """Source and answer may spell the same day differently."""
    problems = ans.verify_dates(
        "The rule took effect 2008-04-08.",
        PROFILE,
        hits=[_source("This provision took effect April 8, 2008.")],
    )
    assert problems == []


def test_the_same_bad_date_is_reported_once() -> None:
    problems = ans.verify_dates(
        "File by October 1, 2027. Again: October 1, 2027 is the deadline.",
        PROFILE,
        hits=[_source("no dates here")],
    )
    assert len(problems) == 1


# --- cap-gap is conditional on a filed petition -------------------------------


def test_cap_gap_is_not_asserted_without_a_filed_petition() -> None:
    """It was emitted for every F-1 profile, so "[OPEN - 36 days remaining]" sat in
    front of the model every turn and it kept recommending a cap-gap extension to
    someone who had just said they had filed nothing."""
    from visa import dates

    windows = dates.compute({"status": "F-1"})
    assert not any("cap-gap" in w.name for w in windows)


def test_cap_gap_is_computed_once_a_petition_is_filed() -> None:
    from visa import dates

    windows = dates.compute({"status": "F-1", "h1b_filed": True})
    assert any("cap-gap" in w.name for w in windows)


# --- the false positive that would have made the panel wallpaper ---------------

CAPGAP_PROFILE = {"status": "F-1", "h1b_filed": True}


def test_the_day_after_a_window_closes_is_not_drift() -> None:
    """Cap-gap ends September 30 and the new status begins October 1.

    Those are adjacent by design, so a drift check run first reported a correct
    "October 1" as one day off the boundary — on every cap-gap answer there is.
    A warning that cries wolf systematically is worse than no warning.
    """
    from visa import dates

    windows = dates.compute(CAPGAP_PROFILE)
    capgap = next(w for w in windows if "cap-gap" in w.name)
    day_after = capgap.closes.toordinal() + 1
    import datetime as dt

    start = dt.date.fromordinal(day_after)
    written = f"{start.strftime('%B')} {start.day}, {start.year}"
    problems = ans.verify_dates(
        f"Your H-1B status begins {written}.",
        CAPGAP_PROFILE,
        hits=[_source("no dates here")],
    )
    assert problems == [], f"{written} was wrongly flagged: {problems}"


def test_the_computed_note_states_the_start_year() -> None:
    """The model wrote October 1 2027 for a FY2027 cap-gap because the note said
    only "an Oct 1 start" and left it to work the year out."""
    from visa import dates

    rendered = dates.render(CAPGAP_PROFILE)
    capgap = next(w for w in dates.compute(CAPGAP_PROFILE) if "cap-gap" in w.name)
    assert f"October 1, {capgap.closes.year}" in rendered


def test_the_wrong_year_is_still_caught() -> None:
    from visa import dates

    capgap = next(w for w in dates.compute(CAPGAP_PROFILE) if "cap-gap" in w.name)
    wrong = f"October 1, {capgap.closes.year + 1}"
    problems = ans.verify_dates(
        f"Your H-1B status begins {wrong}.",
        CAPGAP_PROFILE,
        hits=[_source("no dates here")],
    )
    assert problems, f"{wrong} should be flagged"
