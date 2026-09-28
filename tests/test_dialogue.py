"""Answer first, then ask at most one thing.

The tool's failure mode is not being wrong — it is being a receptionist: reciting
rules that are true in general and never reaching the one that governs the person
asking. Enumerating a list of follow-up questions is the same failure wearing a
different hat, since it returns the work to someone who came here because they did
not know which question mattered.

These test properties of any answer, not the wording of particular ones.
"""

from __future__ import annotations

import pytest

from visa.answer import (
    trailing_questions,
    uncited_claims,
    verify_dialogue,
    verify_grounding,
)

ANSWERED_THEN_ASKED = """\
You control the entity, so the self-employment bar in [2] governs before anything
else does.

Is your degree on the DHS STEM Designated Degree Program List?"""

ENUMERATED_QUESTIONS = """\
There are several routes available to you.

Is your degree STEM-designated? Is the startup enrolled in E-Verify? Has an H-1B
petition already been filed on your behalf?"""

NO_QUESTION = """\
Your cap-gap window closes 2026-09-30 [4]. Nothing further is contingent."""


@pytest.mark.parametrize(
    "text,expected",
    [(ANSWERED_THEN_ASKED, 1), (ENUMERATED_QUESTIONS, 3), (NO_QUESTION, 0)],
)
def test_trailing_questions_counts_the_close(text: str, expected: int) -> None:
    assert len(trailing_questions(text)) == expected


def test_one_closing_question_is_allowed() -> None:
    assert verify_dialogue(ANSWERED_THEN_ASKED) == []


def test_no_closing_question_is_allowed() -> None:
    """Nothing material missing means nothing to ask."""
    assert verify_dialogue(NO_QUESTION) == []


def test_several_closing_questions_are_flagged() -> None:
    problems = verify_dialogue(ENUMERATED_QUESTIONS)
    assert problems and "3 questions" in problems[0]


def test_a_question_earlier_in_the_answer_is_not_a_closing_question() -> None:
    """Rhetorical framing mid-answer is fine; only the close is constrained."""
    text = "What governs you here? The bar in [2] does.\n\nYou remain eligible [3]."
    assert verify_dialogue(text) == []


def test_empty_answer_does_not_crash() -> None:
    assert trailing_questions("") == []
    assert verify_dialogue("   ") == []


# --- grounding: reasoning must not become uncited advice -----------------------

UNCITED_ADVICE = """\
You are in a STEM field, so you can apply for the 24-month extension.

You must ensure your startup is enrolled in E-Verify before filing."""

GROUNDED_ADVICE = """\
You control the entity, so you cannot sign your own Form I-983 [4].

You must be paid commensurately with similarly situated U.S. workers [5]."""

DESCRIPTIVE_ONLY = """\
The regulation defines post-completion practical training but does not
define the national interest.

Two chapters of the Policy Manual address it."""


def test_uncited_advice_is_flagged() -> None:
    """Observed live: fifteen legal claims, zero citations, every check green."""
    problems = verify_grounding(UNCITED_ADVICE)
    assert problems and "2 passage" in problems[0]


def test_grounded_advice_passes() -> None:
    assert verify_grounding(GROUNDED_ADVICE) == []


def test_descriptive_prose_is_not_treated_as_advice() -> None:
    """Only claims about what the person must or may do need a source."""
    assert verify_grounding(DESCRIPTIVE_ONLY) == []


def test_list_items_are_checked_individually() -> None:
    """Uncited advice hidden among cited bullets must still surface."""
    text = "Options:\n- You must file within 60 days [2]\n- You can also work remotely"
    problems = verify_grounding(text)
    assert problems, "an uncited bullet should be caught"


# --- uncited_claims: the passages an attribution pass has to ask about ---------


def test_uncited_claims_returns_the_passages_themselves() -> None:
    """verify_grounding reports a count; attributing a claim to a source needs the
    claim text. Same selection rule, so the two cannot disagree about what counts."""
    claims = uncited_claims(UNCITED_ADVICE)
    assert len(claims) == 2
    assert "24-month extension" in claims[0]
    assert "E-Verify" in claims[1]


def test_uncited_claims_agrees_with_verify_grounding() -> None:
    """Whatever verify_grounding counts, uncited_claims must list — and vice versa."""
    for text in (UNCITED_ADVICE, GROUNDED_ADVICE, DESCRIPTIVE_ONLY):
        flagged = bool(verify_grounding(text))
        assert bool(uncited_claims(text)) == flagged, text[:40]


def test_uncited_claims_is_empty_for_empty_input() -> None:
    assert uncited_claims("") == []
    assert uncited_claims("   \n\n  ") == []
