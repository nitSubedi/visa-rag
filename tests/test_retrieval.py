"""How the decomposed slate is merged.

Issue decomposition exists so the provision a person did not know to raise still
reaches the prompt. Pure round-robin took that too far: with six issues the
question itself held a seventh of the budget, and a founder asking about life
after OPT got L-1 and EB-5 passages while the SEVP self-employment bar — the rule
that actually decides the case — was crowded out to a single chunk.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from visa.config import settings
from visa.search import Index

QUESTION = (
    "I am a founding engineer of my startup, we are in pilot phase, what are the "
    "options for me after my one year opt ends?"
)
NIW_QUESTION = "What is the legal standard for an EB-2 national interest waiver?"
SECTION_RE = re.compile(r"(\d+ (?:CFR|U\.S\.C\.) § [\d.]+)")
ISSUES = [
    "F-1 OPT extension eligibility",
    "Post-Completion OPT employment duration",
    "Startup founder immigration options",
    "H-1B cap-exempt status for startups",
    "Entrepreneur visa programs overview",
    "L-1 visa for intra-company transferees",
]


@pytest.fixture(scope="module")
def idx() -> Index:
    if not (Path.home() / ".visa" / "corpus" / "8-cfr" / "vectors.npy").exists():
        pytest.skip("corpus not indexed — run `visa init`")
    return Index.load()


def _key(hit) -> str:
    return f"{hit.row.citation}|{hit.row.text[:80]}"


def test_question_keeps_its_reserved_share(idx: Index) -> None:
    """The user's own words must not be outvoted by the issues spotted from them."""
    primary = {_key(h) for h in idx.search(QUESTION)}
    merged = idx.search_many([QUESTION, *ISSUES])
    kept = sum(1 for h in merged if _key(h) in primary)
    floor = max(1, round(settings.top_k * settings.primary_share))
    assert kept >= floor, (
        f"only {kept} of {len(merged)} slots came from the question itself; "
        f"at least {floor} are reserved"
    )


def test_merge_is_never_shorter_than_a_single_pass(idx: Index) -> None:
    """An early version returned fewer sources than one plain search."""
    single = idx.search(QUESTION)
    merged = idx.search_many([QUESTION, *ISSUES])
    assert len(merged) >= len(single)


def test_per_citation_cap_is_honoured(idx: Index) -> None:
    """Documented as 3; was implemented as 4-then-uncapped, so one EB-1A query
    filled twelve slots with seven distinct chapters."""
    merged = idx.search_many([QUESTION, *ISSUES])
    counts: dict[str, int] = {}
    for h in merged:
        counts[h.row.citation] = counts.get(h.row.citation, 0) + 1
    worst = max(counts.values())
    assert worst <= settings.per_citation_cap, f"one citation took {worst} slots"


def test_founder_guidance_survives_decomposition(idx: Index) -> None:
    """Regression: the SEVP shard exists for exactly this question. It fell to one
    chunk of twelve once issues were interleaved round-robin."""
    merged = idx.search_many([QUESTION, *ISSUES])
    n = sum(1 for h in merged if h.row.shard == "sevp-stem-opt")
    assert n >= 2, f"founder/self-employment guidance reduced to {n} chunks"


def test_no_single_section_dominates_the_slate(idx: Index) -> None:
    """Splitting enumerated provisions let one CFR *section* quietly take 5 of 12
    slots on the NIW question: 204.12(c) x3 plus (g) and (d), each subsection inside
    the per-citation cap of 3 while the section as a whole owned 42% of the evidence.
    204.12 is the national interest waiver for *physicians*; the question was general.
    The model was handed five chunks of the wrong standard and one carrying Dhanasar,
    and answered from the wrong one — niw_standard fell 4/4 to 2/4.

    The per-citation cap cannot see this, because after a split the fragments share a
    citation and the sibling subsections have different ones."""
    merged = idx.search_many([NIW_QUESTION])
    counts: dict[str, int] = {}
    for h in merged:
        m = SECTION_RE.search(h.row.citation)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    if counts:
        worst_sec = max(counts, key=lambda s: counts[s])
        assert counts[worst_sec] <= settings.per_section_cap, (
            f"{worst_sec} took {counts[worst_sec]} of {len(merged)} slots"
        )


def test_the_section_cap_does_not_starve_a_split_provision(idx: Index) -> None:
    """The cap must still leave room for several criteria of one enumerated
    provision — splitting 204.5(h) exists precisely so more than one can be cited."""
    assert settings.per_section_cap >= settings.per_citation_cap
