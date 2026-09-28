"""Attributing uncited claims to sources, and refusing to invent an attribution.

The measured bug is omission: the same conversation produced 7, then 0, then 1 inline
citations. A grammar that mandates a citation per sentence would fix the count and
create a worse problem — with no way to say "nothing here supports this", the model
must emit a plausible in-range number instead, turning a visible gap into an invisible
false attribution. So attribution is asked as a separate typed question in which null
is a legal answer.
"""

from __future__ import annotations

import json

import pytest

from visa.answer import parse_attributions


def test_a_valid_reply_maps_claims_to_sources() -> None:
    raw = json.dumps(
        {"attributions": [{"claim": 0, "source": 3}, {"claim": 1, "source": None}]}
    )
    assert parse_attributions(raw, n_claims=2, n_sources=12) == {0: 3, 1: None}


@pytest.mark.parametrize("bad", [64, 13, 0, -1, 999])
def test_a_source_outside_the_slate_becomes_unsupported(bad: int) -> None:
    """The exact failure this project has already recorded twice: a real-looking source
    number welded onto nothing (`[6 CFR 204.5(d)]`), and a Policy Manual footnote [3]
    copied into an answer where it is in range but points somewhere unrelated. An
    out-of-slate number must degrade to "unsupported", never to a citation."""
    raw = json.dumps({"attributions": [{"claim": 0, "source": bad}]})
    assert parse_attributions(raw, n_claims=1, n_sources=12) == {0: None}


def test_a_claim_index_that_was_never_asked_about_is_dropped() -> None:
    raw = json.dumps(
        {"attributions": [{"claim": 0, "source": 1}, {"claim": 7, "source": 2}]}
    )
    assert parse_attributions(raw, n_claims=1, n_sources=12) == {0: 1}


@pytest.mark.parametrize(
    "raw",
    ["", "not json", "{}", '{"attributions": null}', '{"attributions": [{"claim": 0}]}'],
)
def test_malformed_replies_yield_nothing_rather_than_crashing(raw: str) -> None:
    """A verification step that can crash the answer is worse than no verification."""
    assert parse_attributions(raw, n_claims=2, n_sources=12) == {}


def test_the_first_answer_for_a_claim_wins() -> None:
    """Duplicates are the model contradicting itself; take the first and stay
    deterministic rather than letting order decide silently."""
    raw = json.dumps(
        {"attributions": [{"claim": 0, "source": 2}, {"claim": 0, "source": 5}]}
    )
    assert parse_attributions(raw, n_claims=1, n_sources=12) == {0: 2}


def test_no_sources_means_nothing_can_be_attributed() -> None:
    """A gated or sourceless answer must not attribute anything to source 1."""
    raw = json.dumps({"attributions": [{"claim": 0, "source": 1}]})
    assert parse_attributions(raw, n_claims=1, n_sources=0) == {0: None}
