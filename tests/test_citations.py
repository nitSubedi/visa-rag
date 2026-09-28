"""Citation correctness — the property this whole tool rests on.

A chunk carrying the wrong citation is worse than no answer: it tells someone the
F-1 rules say something that actually appears under a different classification. These
tests exist because the first three implementations all got this wrong.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest

from visa import chunk
from visa.sources import Source

CFR_XML = Path.home() / ".visa" / "corpus" / "8-cfr" / "raw"
SRC = Source(
    slug="8-cfr", title="8 CFR", tier=2, fetcher="ecfr-xml", chunker="ecfr", url="x"
)


def _xml() -> list[Path]:
    if not CFR_XML.exists():
        pytest.skip("corpus not fetched — run `visa init`")
    return list(CFR_XML.glob("*.xml"))


@pytest.fixture(scope="module")
def rows():
    return chunk.chunk_ecfr(_xml(), SRC)


@pytest.fixture(scope="module")
def by_cite(rows):
    d = collections.defaultdict(str)
    for r in rows:
        d[r.citation] += " " + r.text.lower()
    return d


# (citation, must contain, must NOT contain)
CASES = [
    (
        "8 CFR § 214.2(f)",
        # "duration of status" was the marker here until the 2026-09-18 edition retitled
        # (f)(5) to "Period of stay". This test asserts what the eCFR *contains*, so the
        # marker tracks the fetched text — it is not a statement about operative law.
        # And the operative law is the opposite: the fixed-period rule is enjoined
        # nationwide (Presidents' Alliance v. DHS, D. Mass., 2026-09-14) and duration of
        # status still governs. See STATE.md finding 11; the eCFR does not encode
        # injunctions, so the corpus being current does not mean it is in force.
        ["period of stay", "practical training", "60 day"],
        ["specialty occupation", "representatives of information media"],
    ),
    (
        "8 CFR § 214.2(h)",
        ["specialty occupation", "h-1b"],
        ["representatives of information media", "full course of study"],
    ),
    (
        "8 CFR § 214.2(i)",
        ["representatives of information media"],
        ["specialty occupation", "full course of study"],
    ),
    (
        "8 CFR § 214.2(o)",
        ["extraordinary ability", "consultation"],
        ["full course of study"],
    ),
    ("8 CFR § 204.5(h)", ["extraordinary ability", "one-time achievement"], []),
    ("8 CFR § 204.5(k)", ["national interest", "advanced degree"], []),
]


@pytest.mark.parametrize("cite,must,forbid", CASES)
def test_paragraph_content_matches_citation(by_cite, cite, must, forbid):
    text = by_cite.get(cite, "")
    assert text, f"{cite} produced no chunks"
    for m in must:
        assert m in text, f"{cite} should discuss '{m}'"
    for b in forbid:
        assert b not in text, f"{cite} leaked text from another paragraph: '{b}'"


def test_h1b_not_swallowed_by_roman_numeral(by_cite, rows):
    """Regression: a roman '(i)' sub-paragraph inside (h) was being read as the
    top-level paragraph (i), collapsing H-1B to 3 chunks and inflating 'information
    media' to 112."""
    n = collections.Counter(r.citation for r in rows)
    assert n["8 CFR § 214.2(h)"] > 50, "H-1B is a large paragraph"
    assert n["8 CFR § 214.2(i)"] < 15, "information media is a small paragraph"


def test_toc_disambiguation_helpers():
    assert chunk._opens_with(
        "Students in colleges, universities",
        "Students in colleges, universities, seminaries",
    )
    assert not chunk._opens_with(
        "The petition must include evidence that", "Representatives of information media"
    )


def test_no_chunk_missing_a_citation(rows):
    assert all(r.citation.strip() for r in rows)
