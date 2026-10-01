"""Follow-up questions: a rule that needs a fact asks, and the reply is parsed by code."""

from __future__ import annotations

import pytest

from visa import answer, rules


@pytest.fixture(autouse=True)
def no_model(monkeypatch) -> None:
    """Extraction finds nothing, so every fact must come from the person's replies."""
    monkeypatch.setattr(answer, "extract_facts", lambda q: {})


def _script(*replies: str):
    asked: list[str] = []
    it = iter(replies)

    def ask(need: rules.Needs) -> str:
        asked.append(need.question)
        return next(it, "")

    return ask, asked


def _outcome(results, rule: str) -> rules.Outcome:
    (o,) = [r for r in results if r.rule == rule]
    assert isinstance(o, rules.Outcome), o
    return o


def test_asked_answered_and_saved() -> None:
    ask, asked = _script("12 months")
    saved: dict[str, str] = {}
    res = answer.resolve(
        "I did some CPT. Can I get OPT?", {}, ask=ask, save=saved.__setitem__
    )
    assert "full-time CPT" in asked[0]
    assert _outcome(res, "cpt_full_time_year").decision.startswith("INELIGIBLE")
    assert saved == {"cpt_full_time_months": "12"}


def test_a_rule_needing_two_facts_asks_twice() -> None:
    ask, asked = _script("70 and 40", "yes")
    saved: dict[str, str] = {}
    res = answer.resolve(
        "How many unemployment days do I have left?", {}, ask=ask, save=saved.__setitem__
    )
    assert len(asked) == 2 and "STEM" in asked[1]
    assert _outcome(res, "unemployment_limit").decision.startswith("40 days")
    assert saved == {"stem_extension": "true"}  # the day count changes; not saved


def test_a_situational_answer_is_not_saved() -> None:
    ask, _ = _script("yes, still pending")
    saved: dict[str, str] = {}
    q = "I filed a change of status. Can I travel abroad?"
    res = answer.resolve(q, {}, ask=ask, save=saved.__setitem__)
    assert "ABANDONS" in _outcome(res, "cos_travel_abandonment").decision
    assert saved == {}


@pytest.mark.parametrize("reply", ["", "not sure", "a while", "maybe 6 or 8"])
def test_an_unclear_reply_leaves_the_rule_undecided(reply) -> None:
    ask, _ = _script(reply)
    res = answer.resolve("I did some CPT. Can I get OPT?", {}, ask=ask)
    assert all(isinstance(r, rules.Needs) for r in res)


def test_without_a_person_to_ask_nothing_is_asked() -> None:
    res = answer.resolve("I did some CPT. Can I get OPT?", {}, ask=None)
    assert isinstance(res[0], rules.Needs)


def test_a_saved_fact_is_not_asked_again() -> None:
    ask, asked = _script()
    prof = {"cpt_full_time_months": "8"}
    res = answer.resolve("Can I get OPT after my CPT?", prof, ask=ask)
    assert asked == []
    assert _outcome(res, "cpt_full_time_year").decision.startswith("NOT barred")
