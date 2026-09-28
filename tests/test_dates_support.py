"""A date is supported by the source the answer cites for it — not by coincidence.

The support branch of verify_dates asked "does this date string appear anywhere in the
retrieved text?" across ~35,000 characters of regulation. Legal text is dense with
effective dates, edition dates and transition dates, so collisions are likely rather
than rare. Observed live: the model invented a 2026-12-14 filing date and it passed
because "December 14, 2026" appears in an enjoined transition provision for a different
visa class entirely. Membership is not support — the same defect as finding 16, where a
Policy Manual footnote [3] was in range and therefore accepted while pointing elsewhere.
"""

from __future__ import annotations

import pytest

from visa import answer
from visa.models import Chunk

PROF = {"status": "F-1", "program_end_date": "2027-05-14"}


class _Hit:
    def __init__(self, text: str, citation: str = "8 CFR § 100.1"):
        self.row = Chunk(text=text, citation=citation, tier=2, shard="t")
        self.cosine = 0.8


UNRELATED = _Hit("I nonimmigrants may be admitted for a period not to exceed "
                 "December 14, 2026. Aliens needing more time must apply.")


def test_a_coincidental_date_in_an_uncited_block_is_still_accepted() -> None:
    """Deliberate, and the trade is worth stating.

    Requiring a bracket for every date would make date safety depend on citation
    compliance — the one feature this project knows to be erratic (7, then 0, then 1
    across three turns of one conversation). Most correct dates are uncited, so the check
    would fire on them constantly, which finding 14b records as worse than not checking.
    So an uncited block keeps the permissive whole-slate rule; the laundering case below
    is what gets closed. The live 2026-12-14 failure is caught by a different route: its
    source was suspended, and suspended text no longer vouches for anything."""
    assert answer.verify_dates(
        "You may file by 2026-12-14.", prof=PROF, hits=[UNRELATED]
    ) == []


def test_a_date_passes_when_the_answer_cites_the_source_carrying_it() -> None:
    """Quoting a real date from a real source is and stays legitimate."""
    problems = answer.verify_dates(
        "The provision runs to December 14, 2026 [1].", prof=PROF, hits=[UNRELATED]
    )
    assert problems == []


def test_citing_a_different_source_does_not_launder_the_date() -> None:
    """[2] does not vouch for what only [1] contains."""
    other = _Hit("Unrelated text about fees.", citation="8 CFR § 200.2")
    problems = answer.verify_dates(
        "You may file by 2026-12-14 [2].", prof=PROF, hits=[UNRELATED, other]
    )
    assert problems


def test_a_computed_boundary_never_needs_a_citation() -> None:
    """Python's arithmetic is the authority; requiring a bracket for it would cry wolf."""
    assert answer.verify_dates(
        "The window opens 2027-02-13 and closes 2027-07-13.", prof=PROF, hits=[UNRELATED]
    ) == []


def test_scoping_is_per_block() -> None:
    """A citation in one paragraph constrains that paragraph, not its neighbour."""
    other = _Hit("Unrelated text about fees.", citation="8 CFR § 200.2")
    text = (
        "The provision runs to December 14, 2026 [1].\n\n"
        "Separately, the deadline is 2026-12-14 [2]."
    )
    problems = answer.verify_dates(text, prof=PROF, hits=[UNRELATED, other])
    assert problems, "the second block cites only [2], which does not carry that date"


@pytest.mark.parametrize("n", ["[0]", "[99]"])
def test_an_out_of_range_citation_falls_back_rather_than_crashing(n: str) -> None:
    """verify_citations already flags an out-of-range bracket; this check must not
    index past the slate trying to resolve one."""
    answer.verify_dates(f"You may file by 2026-12-14 {n}.", prof=PROF, hits=[UNRELATED])
