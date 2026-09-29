"""Prompt budgeting, date validation, and issue-decomposed retrieval."""

from __future__ import annotations

import datetime as dt

import pytest

from visa import answer
from visa.config import settings
from visa.models import Chunk


class _Hit:
    def __init__(self, text: str, citation: str, cosine: float = 0.8):
        self.row = Chunk(text=text, citation=citation, tier=2, shard="test")
        self.cosine = cosine


PROFILE = {"status": "F-1", "program_end_date": "2027-05-14"}


# ------------------------------------------------------------ prompt budgeting


def test_prompt_fits_context_window():
    """Regression: 12 chunks ran ~8.6k tokens against an 8k window, so generation
    silently dropped the computed-deadline block sitting at the front."""
    hits = [_Hit("word " * 700, f"8 CFR § 214.{i}") for i in range(12)]
    msgs = answer.build_prompt("When can I file?", hits, PROFILE)
    total = sum(answer.est_tokens(m["content"]) for m in msgs)
    assert total <= settings.num_ctx - settings.answer_reserve_tokens + 50


def test_computed_deadlines_survive_a_huge_source_set():
    """The facts must never be the thing that gets dropped."""
    hits = [_Hit("word " * 900, f"8 CFR § 214.{i}") for i in range(40)]
    msgs = answer.build_prompt("When can I file?", hits, PROFILE)
    body = msgs[1]["content"]
    assert "COMPUTED DEADLINES" in body
    assert "2027-02-13" in body


def test_facts_come_after_sources():
    """Tail placement is deliberate: un-truncated and best attended."""
    body = answer.build_prompt("q", [_Hit("t", "8 CFR § 1")], PROFILE)[1]["content"]
    assert body.index("SOURCES:") < body.index("COMPUTED DEADLINES")
    assert body.index("COMPUTED DEADLINES") < body.index("QUESTION:")


# ------------------------------------------------------------- date validation


def test_off_by_one_deadline_is_flagged():
    """Observed: quoted 2027-02-13 correctly, then concluded 2027-02-14."""
    problems = answer.verify_dates("You may file starting 2027-02-14.", PROFILE)
    assert problems and "2027-02-13" in problems[0]


def test_correct_deadline_passes():
    assert answer.verify_dates("You may file starting 2027-02-13.", PROFILE) == []


def test_unrelated_date_is_not_flagged():
    assert answer.verify_dates("Your I-20 was issued 2024-01-05.", PROFILE) == []


def test_no_profile_means_no_date_claims():
    assert answer.verify_dates("Something about 2027-02-14.", {}) == []


# ------------------------------------------------------------ situation triage


@pytest.mark.parametrize(
    "q",
    [
        "I was unemployed for 60 days of my OPT and now work at my own startup part time",
        "My H-1B was selected and my employer filed a cap-subject petition in March",
    ],
)
def test_situations_trigger_decomposition(q):
    assert answer.looks_situational(q)


@pytest.mark.parametrize(
    "q",
    [
        "What is the standard for a national interest waiver?",
        "How long is the F-1 grace period?",
        "EB-1A criteria",
    ],
)
def test_lookups_do_not(q):
    assert not answer.looks_situational(q)


def test_window_boundaries_are_exact():
    from visa import dates

    w = dates.post_completion_opt(dt.date(2027, 5, 14))[0]
    assert w.opens == dt.date(2027, 2, 13)
    assert w.closes == dt.date(2027, 7, 13)


# ------------------------------------------------------------ issue-planning determinism


def test_issue_planning_is_reproducible_by_default():
    """Finding 0d: generation was pinned to temperature 0 but `plan_issues` was not,
    so the issue list — and therefore the whole retrieval slate — was resampled on
    every run. The same question scored 13/16 and 12/16 an hour apart. A legal
    research tool that returns different sources for the same question twice is not
    reproducible for the user either, not just for the evals."""
    assert settings.plan_temperature == 0.0


def test_plan_issues_uses_the_configured_temperature(monkeypatch):
    """The temperature must be reachable from settings, not welded into the call."""
    seen: list[float] = []

    def fake_chat(messages, model=None, temperature=0.15, **_):
        seen.append(temperature)
        return iter(["unemployment accrual\nemployer eligibility\n"])

    monkeypatch.setattr(answer, "stream_chat", fake_chat)
    monkeypatch.setattr(settings, "plan_temperature", 0.42)
    answer.plan_issues("I was unemployed for 60 days", {"status": "F-1"})
    assert seen == [0.42]
