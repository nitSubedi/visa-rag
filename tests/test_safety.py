"""The guardrails. If these break, the tool becomes actively dangerous."""

from __future__ import annotations

import datetime as dt

import pytest

from visa import answer, dates
from visa.models import Chunk


class _Hit:
    """Minimal stand-in so these tests need no corpus."""

    def __init__(self, text: str, citation: str, cosine: float = 0.8):
        self.row = Chunk(text=text, citation=citation, tier=2, shard="test")
        self.cosine = cosine


HITS = [_Hit("the alien must demonstrate extraordinary ability", "8 CFR § 204.5(h)")]


# ------------------------------------------------------------ citation validator


@pytest.mark.parametrize(
    "text,expect_problem",
    [
        ("Per [6 CFR § 204.5(d)] you qualify.", True),  # invented title
        ("See [1] and also 8 CFR § 999.99.", True),  # unretrieved section
        ("As stated in [5], the rule applies.", True),  # out-of-range source
        ("Under 8 CFR § 204.5(h) [1], ability is required.", False),
        ("Simply [1] with no statutory cite.", False),
    ],
)
def test_verify_citations(text, expect_problem):
    problems = answer.verify_citations(text, HITS)
    assert bool(problems) is expect_problem, problems


def test_verify_citations_reports_reason():
    p = answer.verify_citations("Per [6 CFR § 204.5(d)].", HITS)
    assert any("only contains title 8" in x for x in p)


# --------------------------------------------------------------- freshness gate


@pytest.mark.parametrize(
    "q",
    [
        "Is my EB-2 priority date current for India?",
        "What does the visa bulletin say?",
        "Has EB-3 retrogressed?",
        "What are the dates for filing this month?",
    ],
)
def test_priority_date_questions_are_flagged(q):
    assert answer.needs_live_bulletin(q)


@pytest.mark.parametrize(
    "q",
    [
        "What is the standard for a national interest waiver?",
        "How long is the F-1 grace period?",
    ],
)
def test_ordinary_questions_are_not_flagged(q):
    assert not answer.needs_live_bulletin(q)


# ------------------------------------------------------------------- date math

PE = dt.date(2027, 5, 14)


def test_opt_window_is_90_before_and_60_after():
    w = dates.post_completion_opt(PE)[0]
    assert (PE - w.opens).days == 90
    assert (w.closes - PE).days == 60


def test_grace_period_is_60_days():
    g = dates.post_completion_opt(PE)[1]
    assert (g.closes - g.opens).days == 59  # inclusive of both endpoints


def test_stem_window_is_90_days_before_opt_end():
    oe = dt.date(2028, 7, 1)
    w = dates.stem_extension(oe)
    assert (oe - w.opens).days == 90


def test_window_status_transitions():
    w = dates.post_completion_opt(PE)[0]
    assert "opens in" in w.status(dt.date(2027, 2, 12))
    assert "OPEN" in w.status(dt.date(2027, 2, 14))
    assert "CLOSED" in w.status(dt.date(2027, 7, 14))


def test_compute_is_empty_without_dates():
    assert dates.compute({"status": ""}) == []


def test_unemployment_cap_reflects_stem():
    assert "90 days" in dates.unemployment_budget(dt.date(2027, 7, 2), stem=False).name
    assert "150 days" in dates.unemployment_budget(dt.date(2027, 7, 2), stem=True).name
