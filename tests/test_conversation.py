"""Conversation state.

The REPL answered every question as if asked by a stranger: `_repl` looped on
`_answer_once` and kept nothing between turns. A tool that cannot hold context
cannot guide anyone through anything, so a follow-up like "what if it isn't
E-Verify enrolled?" was answered cold, without the subject the pronoun refers to.

History competes with sources for the same context window, which is exactly how
finding 0 happened, so the budget guard here matters as much as the feature.
"""

from __future__ import annotations

import pytest

from visa import answer as ans
from visa.config import settings
from visa.models import Chunk
from visa.search import Hit

PROFILE = {"status": "F-1", "program_end_date": "2027-05-14"}


def _hit(i: int, words: int = 500) -> Hit:
    row = Chunk(
        text=f"chunk {i} " + "regulation text " * words,
        citation=f"8 CFR § 214.{i}",
        title="t",
        shard="8-cfr",
        tier=2,
        kind="regulation",
        source_title="8 CFR",
    )
    return Hit(row=row, score=1.0, cosine=0.9, shard="8-cfr")


def _turns(n: int, answer_words: int = 400) -> list[ans.Turn]:
    return [
        ans.Turn(
            question=f"question number {i} about STEM OPT and E-Verify enrollment",
            answer=" ".join(
                [f"Sentence {i} about the self-employment bar."] * answer_words
            ),
        )
        for i in range(n)
    ]


def test_history_is_empty_without_turns() -> None:
    assert ans.render_history([]) == ""


def test_history_carries_the_previous_exchange() -> None:
    h = ans.render_history(_turns(1))
    assert "question number 0" in h and "self-employment bar" in h


def test_history_keeps_only_the_recent_turns() -> None:
    h = ans.render_history(_turns(10))
    assert "question number 9" in h
    assert "question number 0" not in h


def test_history_respects_its_token_budget() -> None:
    """History must not be allowed to eat the window sources need."""
    h = ans.render_history(_turns(settings.history_turns), budget=settings.history_tokens)
    assert ans.est_tokens(h) <= settings.history_tokens * 1.15


def test_condense_keeps_whole_sentences() -> None:
    """A conclusion truncated mid-clause can invert its meaning."""
    text = "You cannot sign your own I-983. You may still qualify otherwise."
    out = ans.condense(text, max_tokens=8)
    assert out.endswith(".") and "You cannot sign your own I-983." in out


def test_follow_up_retrieval_carries_the_prior_subject() -> None:
    """"what if it isn't?" embeds toward nothing on its own."""
    turns = [ans.Turn(question="Can I work at my own startup on STEM OPT?", answer="…")]
    q = ans.retrieval_query("what if it isn't E-Verify enrolled?", turns)
    assert "startup" in q and "E-Verify" in q


def test_first_question_retrieves_on_its_own_words() -> None:
    assert ans.retrieval_query("when can I file?", []) == "when can I file?"


@pytest.mark.parametrize("n_turns", [0, 1, settings.history_turns])
def test_computed_deadlines_survive_history_plus_a_full_source_set(n_turns: int) -> None:
    """The finding-0 guard, extended.

    Twelve chunks already ran ~8.3k tokens. Adding conversation state on top is
    precisely the silent overflow that dropped the deadline block before, and
    generation keeps the tail, so anything appended must be budgeted.
    """
    msgs = ans.build_prompt(
        "What are my options after OPT?",
        [_hit(i) for i in range(1, 13)],
        prof=PROFILE,
        turns=_turns(n_turns),
    )
    body = msgs[1]["content"]
    assert "COMPUTED DEADLINES" in body, "deadlines pushed out by history"
    assert "QUESTION:" in body
    assert body.index("COMPUTED DEADLINES") < body.index("QUESTION:")
    total = ans.est_tokens(msgs[0]["content"]) + ans.est_tokens(body)
    assert total <= settings.num_ctx - settings.answer_reserve_tokens


def test_history_sits_above_the_facts_block() -> None:
    """Profile and deadlines stay closest to the question; history yields first."""
    msgs = ans.build_prompt(
        "and what about travel?", [_hit(1)], prof=PROFILE, turns=_turns(2)
    )
    body = msgs[1]["content"]
    assert body.index("EARLIER IN THIS CONVERSATION") < body.index("USER PROFILE")
